"""Tabela de preços pelo e-mail: todo dia às 6h (core/agendador_diario.py).

Pedido (06/11/2026): "todo dia 6 horas da manhã entre no e-mail …. Quando vier um
e-mail com o assunto que contém "Tabela de Preços >>", deve baixar o anexo que o nome
contém "Demais Canais" e importar como tabela de preço".

- A caixa e a senha ficam em ConfiguracaoVendas, a senha cifrada como a do /email/
  (webmail/seguranca.py) — nunca no código nem na tela.
- A caixa da tabela (alcides@redeconfianca.com) fica na Uni5, não no poste.io do /email/:
  o servidor IMAP é ``ConfiguracaoVendas.tabela_email_servidor`` (padrão ``SERVIDOR``,
  porta 993, certificado verificado de verdade — ele vale para *.uni5.net, por isso o
  nome é imap-vip.uni5.net e não imap.redeconfianca.com). Em branco = servidor do /email/.
  Só leitura:
  a caixa é aberta em modo somente-leitura e a mensagem baixada com BODY.PEEK, então
  nada é marcado como lido nem apagado.
- Pega a mensagem mais nova dos últimos ``DIAS_PARA_TRAS`` dias com o assunto; se ela
  já foi importada (mesmo Message-ID), não faz nada — a mesma tabela não entra duas vezes.
- O aviso (WhatsApp para a seleção salva e popup) sai como na importação pela tela,
  se ``tabela_email_avisar`` estiver ligado.
"""
import email as email_lib
import imaplib
import logging
import re
import unicodedata
from datetime import timedelta
from email import policy
from email.header import decode_header, make_header
from io import BytesIO

from django.db import DatabaseError, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

ASSUNTO = 'tabela de precos >>'
ANEXO = 'demais canais'
EXTENSOES = ('.xlsx', '.xlsm')
DIAS_PARA_TRAS = 10
PASTA = 'INBOX'
SERVIDOR = 'imap-vip.uni5.net'
PORTA = 993
TIMEOUT = 25


def normalizar(texto):
    texto = unicodedata.normalize('NFKD', str(texto or ''))
    texto = ''.join(c for c in texto if not unicodedata.combining(c)).casefold()
    return ' '.join(texto.split())


def _cabecalho(valor):
    try:
        return str(make_header(decode_header(valor or '')))
    except Exception:  # noqa: BLE001
        return str(valor or '')


def assunto_confere(assunto):
    return ASSUNTO in normalizar(assunto)


def anexo_confere(nome):
    n = normalizar(nome)
    return ANEXO in n and n.endswith(EXTENSOES)


def _separar(servidor):
    servidor = (servidor or '').strip()
    servidor = re.sub(r'^[a-z]+://', '', servidor, flags=re.I).strip('/')
    if ':' in servidor:
        host, _, porta = servidor.rpartition(':')
        if porta.isdigit():
            return host, int(porta)
    return servidor, PORTA


def _conectar(servidor):
    import socket
    import ssl

    from webmail.cliente import ServidorFora
    host, porta = _separar(servidor)
    try:
        return imaplib.IMAP4_SSL(host, porta, ssl_context=ssl.create_default_context(), timeout=TIMEOUT)
    except ssl.SSLCertVerificationError as exc:
        raise ServidorFora(f'O certificado de {host} não confere ({getattr(exc, 'verify_message', None) or exc}).') from exc
    except (OSError, socket.timeout) as exc:
        raise ServidorFora(f'Não consegui falar com {host}:{porta} ({exc.__class__.__name__}).') from exc


def _entrar(conta, senha, servidor=None):
    from webmail import cliente
    con = _conectar(servidor) if (servidor or '').strip() else cliente._conectar_imap()
    try:
        cliente._entrar_imap(con, conta, senha)
    except Exception:
        _sair(con)
        raise
    return con


def _sair(con):
    try:
        con.logout()
    except (imaplib.IMAP4.error, OSError):
        pass


def testar_conexao(conta, senha, servidor=None):
    """Só confere o login (levanta webmail.cliente.ErroEmail se não der)."""
    _sair(_entrar(conta, senha, servidor))


def _uid_de(cabeca):
    m = re.search(rb'UID (\d+)', cabeca or b'')
    return int(m.group(1)) if m else None


def procurar(con, agora=None):
    """A mensagem mais nova com o assunto, já com o anexo certo. None se não houver.

    {'uid', 'message_id', 'assunto', 'data', 'anexo_nome', 'conteudo' (bytes ou None), 'anexos'}
    """
    agora = agora or timezone.now()
    typ, _ = con.select(PASTA, readonly=True)
    if typ != 'OK':
        raise RuntimeError(f'Não abri a pasta {PASTA}.')
    desde = (timezone.localtime(agora) - timedelta(days=DIAS_PARA_TRAS)).strftime('%d-%b-%Y')
    typ, dados = con.uid('SEARCH', None, 'SINCE', desde)
    uids = sorted((int(u) for u in (dados[0] or b'').split()), reverse=True)
    if not uids:
        return None
    typ, resp = con.uid('FETCH', ','.join(map(str, uids)),
                        '(BODY.PEEK[HEADER.FIELDS (SUBJECT MESSAGE-ID DATE)])')
    candidatos = []
    for item in resp or []:
        if not isinstance(item, tuple):
            continue
        uid = _uid_de(item[0])
        cab = email_lib.message_from_bytes(item[1] or b'', policy=policy.compat32)
        assunto = _cabecalho(cab.get('Subject'))
        if uid and assunto_confere(assunto):
            candidatos.append((uid, assunto, (cab.get('Message-ID') or '').strip(), cab.get('Date') or ''))
    if not candidatos:
        return None
    uid, assunto, message_id, data = max(candidatos)
    typ, resp = con.uid('FETCH', str(uid), '(BODY.PEEK[])')
    bruto = next((i[1] for i in resp or [] if isinstance(i, tuple)), b'')
    msg = email_lib.message_from_bytes(bruto or b'', policy=policy.default)
    anexos, escolhido = [], None
    for parte in msg.iter_attachments():
        nome = parte.get_filename() or ''
        anexos.append(nome)
        if escolhido is None and anexo_confere(nome):
            escolhido = (nome, parte.get_content())
    return {'uid': uid, 'message_id': message_id or f'uid:{uid}', 'assunto': assunto, 'data': data,
            'anexo_nome': escolhido[0] if escolhido else '', 'conteudo': escolhido[1] if escolhido else None,
            'anexos': anexos}


def _gravar_estado(config, estado):
    from .models import ConfiguracaoVendas
    try:
        with transaction.atomic():
            ConfiguracaoVendas.objects.filter(pk=config.pk).update(tabela_email_estado=estado)
    except DatabaseError:
        logger.warning('Tabela pelo e-mail: não gravei o estado (falta migrate?).', exc_info=True)


def buscar_e_importar(forcar=False, usuario=None):
    """Confere a caixa e importa a tabela nova. Devolve o estado gravado (dict).

    ``forcar``: roda mesmo com a busca automática desligada (botão "Buscar agora").
    """
    from webmail.seguranca import decifrar

    from . import aviso_precos
    from .importacao import registrar_importacao
    from .models import ConfiguracaoVendas

    config = ConfiguracaoVendas.atual()
    estado = dict(config.tabela_email_estado or {})
    agora = timezone.now()
    estado['checado_em'] = timezone.localtime(agora).strftime('%d/%m/%Y %H:%M')
    conta, senha = (config.tabela_email or '').strip(), decifrar(config.tabela_email_senha or '')
    if not (forcar or config.tabela_email_ativo):
        estado.update(situacao='desligada', detalhe='A busca automática está desligada.')
        return estado
    if not conta or not senha:
        estado.update(situacao='sem_credencial', detalhe='Falta o e-mail ou a senha da caixa.')
        _gravar_estado(config, estado)
        return estado

    try:
        con = _entrar(conta, senha, config.tabela_email_servidor)
        try:
            achado = procurar(con, agora)
        finally:
            _sair(con)
    except Exception as exc:  # noqa: BLE001 — servidor fora, senha recusada, certificado
        logger.warning('Tabela pelo e-mail: falha ao ler a caixa', exc_info=True)
        estado.update(situacao='erro', detalhe=f'Não consegui ler a caixa: {exc}'[:300])
        _gravar_estado(config, estado)
        return estado

    if achado is None:
        estado.update(situacao='sem_email',
                      detalhe=f'Nenhum e-mail com "Tabela de Preços >>" nos últimos {DIAS_PARA_TRAS} dias.')
    elif achado['message_id'] == estado.get('ultimo_message_id'):
        estado.update(situacao='ja_importada',
                      detalhe=f'O e-mail mais novo ("{achado["assunto"][:120]}") já foi importado.')
    elif not achado['conteudo']:
        estado.update(situacao='sem_anexo',
                      detalhe=(f'O e-mail "{achado["assunto"][:120]}" não tem anexo com "Demais Canais" (.xlsx). '
                               f'Anexos: {", ".join(achado["anexos"]) or "nenhum"}.')[:300])
    else:
        avisar = bool(config.tabela_email_avisar)
        destinatarios, grupos = [], []
        if avisar:
            from users.models import User
            ids = aviso_precos.destinatarios_salvos()
            destinatarios = list(User.objects.filter(pk__in=ids, is_active=True).order_by('first_name', 'last_name'))
            grupos = aviso_precos.grupos_salvos()
        r = registrar_importacao(BytesIO(achado['conteudo']), achado['anexo_nome'], usuario=usuario, origem='email',
                                 destinatarios=destinatarios, grupos=grupos, popup=avisar)
        if r['ok']:
            resumo = r['resumo']
            estado.update(situacao='importada', ultimo_message_id=achado['message_id'],
                          importacao_id=r['registro'].pk, assunto=achado['assunto'][:200],
                          anexo=achado['anexo_nome'][:200], importada_em=estado['checado_em'],
                          detalhe=(f'{resumo["importados"]} produto(s): {resumo["incluidos"]} novo(s), '
                                   f'{resumo["alterados"]} com preço alterado, {r["registro"].removidos} fora da tabela.'))
        else:
            # Planilha com erro: não marca como importada — amanhã (ou no "Buscar agora") tenta de novo.
            estado.update(situacao='erro_importacao', importacao_id=r['registro'].pk,
                          detalhe=f'O anexo "{achado["anexo_nome"]}" não importou: {r["erro"]}'[:300])
    _gravar_estado(config, estado)
    return estado


def tarefa_diaria():
    """Chamada pelo agendador às 6h. Devolve uma linha para o registro da tarefa."""
    estado = buscar_e_importar()
    return f"{estado.get('situacao')}: {estado.get('detalhe', '')}"[:500]
