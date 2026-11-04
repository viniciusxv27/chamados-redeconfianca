"""Resumo do ciclo de contestação no WhatsApp de quem o SUPERADMIN escolheu.

**O ciclo** é o lote mais recente da BASE_EXCLUSAO (``ExclusionSyncBatch``): cada
"Sincronizar Planilha" abre um. A base de pagamento (contestação de valores) tem
ciclo próprio e não entra aqui.

**Quando fecha** (``estado_do_ciclo``), tudo ao mesmo tempo:

1. *as lojas enviaram*: o portal não tem um "terminei de enviar" por loja, então
   quem define isso é o prazo. Com a janela de 3 dias vencida e sem liberação
   avulsa em vigor, ninguém mais consegue mandar. **Loja sem contestação conta
   como pronta nesse momento**, e sai no PDF como "sem contestação"; antes do
   prazo ela ainda pode enviar;
2. *deram o de acordo*: o de acordo do portal é por contestação. Nenhuma pode
   estar sem análise do gestor (``pending``) nem esperando a resposta de quem
   abriu (``accepted``, ou ``rejected`` sem ``confirmed_at``). Negar também é
   resposta. Rejeitada que já foi contestada de novo não espera: a loja seguiu
   em frente na contestação mais nova;
3. pelo menos uma contestação no ciclo: sem nada enviado não há o que resumir.

**Uma vez por ciclo**: ``EnvioResumoCiclo.lote`` é único, e quem cria a linha
é quem dispara. Por pessoa, um UPDATE condicional decide quem manda.

**Sem segurar ninguém**: a verificação e o envio rodam numa thread. Thread morre
quando o .py é salvo no runserver ou o worker reinicia; o que estava na fila
continua ``pendente`` e sai no agendador diário (core/agendador_diario.py) ou pelo
botão da tela. Quem estava exatamente no meio do envio vira ``falhou`` com o aviso
de que pode ter chegado: reenviar isso sozinho seria arriscar mensagem dupla.

A integração (``core.evolution``) só manda texto, então o PDF vai por link que
pede login. Mandar pelo módulo: o dublê dos testes troca
``core.evolution.enviar_texto``.
"""
import logging
import os
import threading
import uuid
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import close_old_connections, transaction
from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from core.utils import processo_de_teste

from .models import (Contestation, DestinatarioResumoCiclo, EnvioResumoCiclo, EnvioResumoDestinatario,
                     ExclusionSyncBatch, LiberacaoContestacao, TipoBase)
from .resumo_pdf import _brl, gerar_pdf, mascarar_documento, mascarar_texto_livre, MAX_MOTIVO

logger = logging.getLogger(__name__)

Linha = EnvioResumoDestinatario

# Mais que isso em "enviando" é thread que morreu: o envio em si leva até 20 s.
ENVIANDO_PRESO = timedelta(minutes=10)
# Reenviar a quem acabou de receber: é clique duplo, não decisão.
INTERVALO_REENVIO = timedelta(minutes=5)


def _nome_loja(filial):
    return ' '.join(str(filial or '').split()).upper() or '(sem loja)'


def _nome_pessoa(user):
    return (user.get_full_name() or user.username or '').strip()


def contestacoes_do_lote(lote):
    return Contestation.objects.filter(exclusion__sync_batch=lote, exclusion__record_type=TipoBase.EXCLUSAO)


def _superadas(contestacoes):
    """Ids das contestações que têm outra mais nova para a mesma venda."""
    ultima, superadas = {}, set()
    for c in sorted(contestacoes, key=lambda c: (c.created_at, c.pk)):
        anterior = ultima.get(c.exclusion_id)
        if anterior is not None:
            superadas.add(anterior)
        ultima[c.exclusion_id] = c.pk
    return superadas


def _aguarda_gestor(c):
    return c.status == 'pending'


def _aguarda_de_acordo(c, superadas):
    if c.status == 'accepted':
        return True
    return c.status == 'rejected' and c.confirmed_at is None and c.pk not in superadas


def _pendencias(contestacoes):
    superadas = _superadas(contestacoes)
    return (sum(1 for c in contestacoes if _aguarda_gestor(c)),
            sum(1 for c in contestacoes if _aguarda_de_acordo(c, superadas)))


def _lojas_do_lote(lote):
    from .views import _exclusoes
    return {_nome_loja(f) for f in _exclusoes().filter(sync_batch=lote).values_list('filial', flat=True).distinct()}


def estado_do_ciclo():
    """Como está o ciclo atual e, loja a loja, o que falta para fechar."""
    from .views import _get_latest_sync_batch, _get_sync_window_state

    lote = _get_latest_sync_batch()
    if lote is None:
        return {'lote': None, 'fechado': False, 'lojas': [], 'motivos': ['Nenhuma planilha sincronizada ainda.'],
                'liberacoes': [], 'janela': None, 'contestacoes': 0, 'falta_analise': 0, 'falta_de_acordo': 0,
                'envio': None}

    janela = _get_sync_window_state()
    liberacoes = list(LiberacaoContestacao.objects.filter(ativa=True, prazo__gt=timezone.now())
                      .select_related('setor'))
    aberta = not janela['is_blocked'] or bool(liberacoes)

    contestacoes = list(contestacoes_do_lote(lote).select_related('exclusion', 'requester')
                        .only('id', 'exclusion_id', 'created_at', 'status', 'confirmed_at', 'exclusion__filial',
                              'exclusion__receita', 'requester__first_name', 'requester__last_name',
                              'requester__username'))
    superadas = _superadas(contestacoes)

    lojas = {nome: {'filial': nome, 'contestacoes': 0, 'valor': Decimal('0'), 'falta_analise': 0,
                    'falta_de_acordo': 0, 'quem': set()} for nome in _lojas_do_lote(lote)}
    for c in contestacoes:
        loja = lojas.setdefault(_nome_loja(c.exclusion.filial), {
            'filial': _nome_loja(c.exclusion.filial), 'contestacoes': 0, 'valor': Decimal('0'),
            'falta_analise': 0, 'falta_de_acordo': 0, 'quem': set()})
        loja['contestacoes'] += 1
        loja['valor'] += c.exclusion.receita or Decimal('0')
        if _aguarda_gestor(c):
            loja['falta_analise'] += 1
        elif _aguarda_de_acordo(c, superadas):
            loja['falta_de_acordo'] += 1
            loja['quem'].add(_nome_pessoa(c.requester))

    for loja in lojas.values():
        loja['quem'] = sorted(loja['quem'])
        if loja['falta_analise'] or loja['falta_de_acordo']:
            loja['pronta'], loja['rotulo'] = False, 'Em aberto'
        elif aberta:
            loja['pronta'] = False
            loja['rotulo'] = 'Enviou; ainda pode mandar mais' if loja['contestacoes'] else 'Ainda pode enviar'
        else:
            loja['pronta'] = True
            loja['rotulo'] = 'Pronta' if loja['contestacoes'] else 'Pronta: sem contestação'

    falta_analise = sum(loja['falta_analise'] for loja in lojas.values())
    falta_de_acordo = sum(loja['falta_de_acordo'] for loja in lojas.values())

    motivos = []
    if not janela['is_blocked']:
        motivos.append(f"A janela de contestação fica aberta até "
                       f"{timezone.localtime(janela['sync_deadline_at']):%d/%m/%Y às %H:%M}.")
    if liberacoes:
        nomes = ', '.join(sorted({lib.setor.name for lib in liberacoes}))
        motivos.append(f'Liberação avulsa em vigor: {nomes}.')
    if falta_analise:
        motivos.append(f'{falta_analise} contestação(ões) sem análise do gestor.')
    if falta_de_acordo:
        motivos.append(f'{falta_de_acordo} contestação(ões) esperando o de acordo da loja.')
    if not contestacoes:
        motivos.append('Nenhuma contestação neste ciclo: não há o que resumir.')

    return {
        'lote': lote,
        'janela': janela,
        'liberacoes': liberacoes,
        'lojas': sorted(lojas.values(), key=lambda loja: (loja['pronta'], loja['filial'])),
        'contestacoes': len(contestacoes),
        'falta_analise': falta_analise,
        'falta_de_acordo': falta_de_acordo,
        'motivos': motivos,
        'fechado': not motivos,
        'envio': EnvioResumoCiclo.objects.filter(lote=lote).first(),
    }


# ---------------------------------------------------------------------------
# O conteúdo: PDF e mensagem
# ---------------------------------------------------------------------------
def situacao_da_contestacao(c):
    if c.status == 'pending':
        return 'Aguardando análise'
    if c.status == 'accepted':
        return 'Aprovada, aguardando de acordo'
    if c.status == 'rejected':
        return 'Rejeitada, de acordo dado' if c.confirmed_at else 'Rejeitada, aguardando de acordo'
    if c.status == 'denied':
        return 'Negada pelo gerente'
    if c.payment_status == 'paid':
        return 'Aprovada, paga'
    if c.payment_status == 'pending_payment':
        return 'Aprovada, aguardando pagamento'
    return 'Aprovada'


def _motivo_curto(texto):
    limpo = ' '.join(mascarar_texto_livre(texto).split())
    return limpo if len(limpo) <= MAX_MOTIVO else limpo[:MAX_MOTIVO - 1].rstrip() + '…'


def dados_do_resumo(lote):
    """Tudo o que vai no PDF e na mensagem. Já com CPF mascarado."""
    contestacoes = list(contestacoes_do_lote(lote).select_related('exclusion')
                        .order_by('exclusion__filial', 'exclusion__vendedor', 'created_at', 'pk'))
    superadas = _superadas(contestacoes)

    lojas = {nome: {'filial': nome, 'contestacoes': 0, 'valor': Decimal('0'), 'aprovadas': 0, 'rejeitadas': 0,
                    'negadas': 0, 'em_aberto': 0, 'sem_evidencia': 0, 'vendas': []}
             for nome in _lojas_do_lote(lote)}
    totais = {'contestacoes': 0, 'valor': Decimal('0'), 'aprovadas': 0, 'rejeitadas': 0, 'negadas': 0,
              'em_aberto': 0, 'com_evidencia': 0, 'sem_evidencia': 0}
    for c in contestacoes:
        venda = c.exclusion
        nome = _nome_loja(venda.filial)
        loja = lojas.setdefault(nome, {'filial': nome, 'contestacoes': 0, 'valor': Decimal('0'), 'aprovadas': 0,
                                       'rejeitadas': 0, 'negadas': 0, 'em_aberto': 0, 'sem_evidencia': 0,
                                       'vendas': []})
        valor = venda.receita or Decimal('0')
        chave = {'confirmed': 'aprovadas', 'rejected': 'rejeitadas', 'denied': 'negadas'}.get(c.status)
        em_aberto = _aguarda_gestor(c) or _aguarda_de_acordo(c, superadas)
        tem_evidencia = bool(c.attachment)
        for alvo in (loja, totais):
            alvo['contestacoes'] += 1
            alvo['valor'] += valor
            if chave:
                alvo[chave] += 1
            if em_aberto:
                alvo['em_aberto'] += 1
            if not tem_evidencia:
                alvo['sem_evidencia'] += 1
        if tem_evidencia:
            totais['com_evidencia'] += 1
        loja['vendas'].append({
            'vendedor': venda.vendedor,
            'cliente': venda.nome_cliente,
            'documento': mascarar_documento(venda.cpf_cnpj),
            'numero_venda': venda.numero_venda,
            'pilar': venda.pilar,
            'motivo': _motivo_curto(c.reason),
            'valor': valor,
            'evidencia': ('Errada' if c.attachment_wrong else 'Sim') if tem_evidencia else 'Não',
            'situacao': situacao_da_contestacao(c),
        })

    ordenadas = sorted(lojas.values(), key=lambda loja: loja['filial'])
    totais['lojas_com_envio'] = sum(1 for loja in ordenadas if loja['contestacoes'])
    totais['lojas_sem_envio'] = sum(1 for loja in ordenadas if not loja['contestacoes'])

    aviso = ''
    if totais['em_aberto']:
        aviso = (f"Atenção: quando este resumo foi gerado, {totais['em_aberto']} contestação(ões) ainda "
                 f"estavam sem análise ou sem o de acordo da loja.")
    return {
        'sincronizado_em': f'{timezone.localtime(lote.created_at):%d/%m/%Y %H:%M}',
        'lojas': ordenadas,
        'totais': totais,
        'aviso': aviso,
    }


def url_do_pdf(envio):
    base = (getattr(settings, 'BASE_URL', '') or '').rstrip('/')
    return f"{base}{reverse('contestacao:resumo_whatsapp_pdf', args=[envio.pk])}"


def montar_texto(dados, envio):
    """A parte comum da mensagem. Só totais: nome de cliente fica no PDF, atrás do login."""
    t = dados['totais']
    com_envio = [loja for loja in dados['lojas'] if loja['contestacoes']]
    sem_envio = [loja['filial'] for loja in dados['lojas'] if not loja['contestacoes']]
    linhas = ['📊 *Contestações: resumo do ciclo*',
              f"Planilha sincronizada em {dados['sincronizado_em']}.", '']
    linhas.append(dados['aviso'] or 'Todas as lojas enviaram e os de acordo foram dados.')
    linhas += ['',
               f"*{t['contestacoes']} contestação(ões)* · {_brl(t['valor'])}",
               f"✅ {t['aprovadas']} aprovada(s) · ❌ {t['rejeitadas']} rejeitada(s) · 🚫 {t['negadas']} negada(s)"]
    if t['em_aberto']:
        linhas.append(f"⏳ {t['em_aberto']} em aberto")
    linhas += ['', '*Por loja*']
    linhas += [f"• {loja['filial']}: {loja['contestacoes']} · {_brl(loja['valor'])}" for loja in com_envio]
    if sem_envio:
        linhas += ['', f"Sem contestação: {', '.join(sem_envio)}."]
    linhas += ['', '📄 PDF com o detalhe de cada venda (entre com o seu login do portal):', url_do_pdf(envio)]
    return '\n'.join(linhas)


def _preparar(envio_id, refazer=False):
    """Gera PDF e mensagem uma vez só. ``refazer`` usa os dados de agora (reenvio a todos)."""
    with transaction.atomic():
        envio = EnvioResumoCiclo.objects.select_for_update().get(pk=envio_id)
        if envio.pdf and envio.texto and not refazer:
            return envio
        dados = dados_do_resumo(envio.lote)
        conteudo = gerar_pdf(dados)
        anterior = envio.pdf.name if envio.pdf else ''
        envio.pdf.save(f'resumo_ciclo_{envio.lote_id}_{uuid.uuid4().hex[:12]}.pdf', ContentFile(conteudo), save=False)
        envio.texto = montar_texto(dados, envio)
        t = dados['totais']
        envio.totais = {**t, 'valor': str(t['valor'])}
        envio.pdf_gerado_em = timezone.now()
        envio.erro = ''
        envio.save(update_fields=['pdf', 'texto', 'totais', 'pdf_gerado_em', 'erro'])
    if anterior:
        try:
            envio.pdf.storage.delete(anterior)
        except Exception:  # noqa: BLE001 — arquivo velho sobrando não atrapalha ninguém
            logger.warning('Resumo do ciclo: não apagou o PDF anterior %s', anterior)
    return envio


# ---------------------------------------------------------------------------
# O envio
# ---------------------------------------------------------------------------
def usuarios_selecionados():
    from users.models import User
    return User.objects.filter(destinatario_resumo_contestacao__isnull=False, is_active=True)


def _criar_linhas(envio, usuarios):
    Linha.objects.bulk_create(
        [Linha(envio=envio, usuario=u, nome=_nome_pessoa(u)) for u in usuarios],
        ignore_conflicts=True)


def reivindicar(lote, origem=EnvioResumoCiclo.AUTOMATICO, por=None, fechado=True):
    """Tenta ser quem manda o resumo deste ciclo. Devolve (envio, criou).

    A linha única por lote é a trava: no ``get_or_create`` concorrente, o banco
    recusa o segundo e ele recebe a linha do primeiro com ``criou=False``.
    """
    usuarios = list(usuarios_selecionados())
    if not usuarios:
        return EnvioResumoCiclo.objects.filter(lote=lote).first(), False
    with transaction.atomic():
        envio, criou = EnvioResumoCiclo.objects.get_or_create(
            lote=lote, defaults={'origem': origem, 'disparado_por': por, 'ciclo_fechado': fechado})
        if criou:
            _criar_linhas(envio, usuarios)
    return envio, criou


def _registrar(linha_id, situacao, detalhe, numero):
    linha = Linha.objects.get(pk=linha_id)
    agora = timezone.now()
    linha.situacao = situacao
    linha.detalhe = str(detalhe or '')[:300]
    linha.numero = numero
    linha.enviando_desde = None
    if situacao == Linha.ENVIADO:
        linha.enviado_em = agora
    linha.historico = (linha.historico or []) + [
        {'em': agora.isoformat(), 'situacao': situacao, 'detalhe': linha.detalhe}]
    linha.save(update_fields=['situacao', 'detalhe', 'numero', 'enviando_desde', 'enviado_em', 'historico'])


def _enviar_linha(envio, linha_id):
    from core import evolution
    from core.telefone import problema

    ganhou = Linha.objects.filter(pk=linha_id, situacao=Linha.PENDENTE).update(
        situacao=Linha.ENVIANDO, enviando_desde=timezone.now(), tentativas=F('tentativas') + 1)
    if not ganhou:
        return

    try:
        usuario = Linha.objects.select_related('usuario').get(pk=linha_id).usuario
        if usuario is None or not usuario.is_active:
            _registrar(linha_id, Linha.FALHOU, 'Usuário inativo ou removido.', '')
            return
        telefone = usuario.phone or ''
        numero = evolution.normalizar_numero(telefone)
        if not numero:
            _registrar(linha_id, Linha.SEM_TELEFONE, problema(telefone) or 'Sem telefone no cadastro.', '')
            return
        primeiro_nome = (usuario.first_name or _nome_pessoa(usuario)).split(' ')[0].capitalize()
        ok, detalhe = evolution.enviar_texto(numero, f'Olá, {primeiro_nome}!\n\n{envio.texto}', timeout=20)
        if ok:
            _registrar(linha_id, Linha.ENVIADO, '', numero)
        else:
            _registrar(linha_id, Linha.FALHOU, detalhe or 'A integração recusou o envio.', numero)
    except Exception as exc:  # noqa: BLE001 — a falha de um não pode impedir os outros
        logger.exception('Resumo do ciclo: falha ao enviar a linha %s', linha_id)
        Linha.objects.filter(pk=linha_id, situacao=Linha.ENVIANDO).update(
            situacao=Linha.FALHOU, detalhe=f'Erro inesperado: {exc}'[:300], enviando_desde=None)


def processar(envio_id, refazer_pdf=False):
    """Manda para quem está na fila. Pode rodar repetido: só quem ganha a linha envia."""
    try:
        envio = _preparar(envio_id, refazer=refazer_pdf)
    except Exception as exc:  # noqa: BLE001 — sem PDF ninguém é avisado; a fila fica para a próxima
        logger.exception('Resumo do ciclo: não conseguiu preparar o envio %s', envio_id)
        EnvioResumoCiclo.objects.filter(pk=envio_id).update(erro=f'{type(exc).__name__}: {exc}'[:500])
        return

    for linha_id in list(envio.linhas.filter(situacao=Linha.PENDENTE).values_list('pk', flat=True)):
        _enviar_linha(envio, linha_id)

    if not envio.linhas.filter(situacao__in=[Linha.PENDENTE, Linha.ENVIANDO]).exists():
        EnvioResumoCiclo.objects.filter(pk=envio_id).update(concluido_em=timezone.now())


def _em_thread(alvo, *args, **kwargs):
    try:
        alvo(*args, **kwargs)
    except Exception:  # noqa: BLE001 — a thread nunca derruba nada
        logger.exception('Resumo do ciclo: tarefa em segundo plano quebrou')
    finally:
        close_old_connections()


def _disparar(envio_id, em_thread=True, refazer_pdf=False):
    if em_thread:
        threading.Thread(target=_em_thread, args=(processar, envio_id), kwargs={'refazer_pdf': refazer_pdf},
                         name=f'resumo-contestacao-{envio_id}', daemon=True).start()
    else:
        processar(envio_id, refazer_pdf=refazer_pdf)


def verificar_e_enviar(em_thread=True):
    """Se o ciclo atual fechou e ainda não foi mandado, manda. Devolve o envio criado ou None."""
    from .views import _get_latest_sync_batch

    if not DestinatarioResumoCiclo.objects.exists():
        return None
    lote = _get_latest_sync_batch()
    if lote is None or EnvioResumoCiclo.objects.filter(lote=lote).exists():
        return None
    if not estado_do_ciclo()['fechado']:
        return None
    envio, criou = reivindicar(lote)
    if not criou:
        return None
    logger.info('Resumo do ciclo: lote %s fechou, envio %s criado.', lote.pk, envio.pk)
    _disparar(envio.pk, em_thread=em_thread)
    return envio


def _verificar_depois_de_salvar():
    try:
        # Sem ninguém escolhido, a contestação não paga nem a thread.
        if DestinatarioResumoCiclo.objects.exists():
            threading.Thread(target=_em_thread, args=(verificar_e_enviar,), kwargs={'em_thread': False},
                             name='resumo-contestacao-verificar', daemon=True).start()
    except Exception:  # noqa: BLE001 — banco sem o migrate ainda: o de acordo da loja segue
        logger.warning('Resumo do ciclo: verificação ignorada.', exc_info=True)


def ao_salvar_contestacao(contestacao):
    """Chamado pelo sinal. O ciclo só fecha numa análise ou num de acordo."""
    if contestacao.status == 'pending' or contestacao.payment_status == 'paid':
        return
    if processo_de_teste() or os.environ.get('RC_SEM_TAREFAS_DIARIAS') == '1':
        return
    transaction.on_commit(_verificar_depois_de_salvar, robust=True)


def recuperar_interrompidos():
    """Quem ficou em "enviando": não dá para saber se chegou, então não reenvia sozinho."""
    limite = timezone.now() - ENVIANDO_PRESO
    presos = Linha.objects.filter(situacao=Linha.ENVIANDO, enviando_desde__lt=limite)
    return presos.update(
        situacao=Linha.FALHOU, enviando_desde=None,
        detalhe='Interrompido no meio do envio: pode ter chegado ou não. Confira antes de reenviar.')


def tarefa_diaria():
    """Agendador diário: recupera a fila de thread que morreu e cobre o que não tem evento.

    A janela de 3 dias vence sozinha, sem ninguém salvar nada: quando esse é o último
    passo que faltava, o ciclo só é percebido aqui.
    """
    interrompidos = recuperar_interrompidos()
    na_fila = list(EnvioResumoCiclo.objects.filter(linhas__situacao=Linha.PENDENTE)
                   .values_list('pk', flat=True).distinct())
    for envio_id in na_fila:
        processar(envio_id)
    novo = verificar_e_enviar(em_thread=False)
    return (f'{interrompidos} interrompido(s), {len(na_fila)} envio(s) retomado(s), '
            f'{"ciclo novo enviado" if novo else "nenhum ciclo novo"}')


def enviar_manual(por, alvo='faltantes', envio_id=None, linha_id=None, em_thread=True):
    """Botão da tela. Devolve (ok, mensagem).

    - sem envio ainda para o ciclo atual: cria (mesmo com o ciclo aberto) e manda;
    - ``faltantes``: quem está na fila, falhou ou ficou sem telefone, mais quem
      entrou na seleção depois;
    - ``todos``: refaz o PDF com os dados de agora e manda a toda a seleção;
    - ``linha_id``: só aquela pessoa.
    """
    from .views import _get_latest_sync_batch

    recuperar_interrompidos()
    selecionados = list(usuarios_selecionados())

    if envio_id:
        envio = EnvioResumoCiclo.objects.filter(pk=envio_id).first()
        if envio is None:
            return False, 'Envio não encontrado.'
    else:
        lote = _get_latest_sync_batch()
        if lote is None:
            return False, 'Nenhuma planilha sincronizada ainda.'
        if not selecionados and not EnvioResumoCiclo.objects.filter(lote=lote).exists():
            return False, 'Escolha quem recebe antes de enviar.'
        if not contestacoes_do_lote(lote).exists():
            return False, 'Nenhuma contestação neste ciclo: não há o que resumir.'
        estado = estado_do_ciclo()
        envio, criou = reivindicar(lote, EnvioResumoCiclo.MANUAL, por, fechado=estado['fechado'])
        if criou:
            _disparar(envio.pk, em_thread=em_thread)
            return True, f'Envio iniciado para {len(selecionados)} pessoa(s).'

    terminadas = [Linha.ENVIADO, Linha.FALHOU, Linha.SEM_TELEFONE]
    recente = timezone.now() - INTERVALO_REENVIO

    if linha_id:
        fila = envio.linhas.filter(pk=linha_id, situacao__in=terminadas)
        fila = fila.exclude(situacao=Linha.ENVIADO, enviado_em__gt=recente)
        reabertas = fila.update(situacao=Linha.PENDENTE, detalhe='')
        if not reabertas:
            return False, 'Esta pessoa acabou de receber ou já está na fila.'
        _disparar(envio.pk, em_thread=em_thread)
        return True, 'Reenvio iniciado.'

    ids = [u.pk for u in selecionados]
    if alvo == 'todos':
        fila = envio.linhas.filter(usuario_id__in=ids, situacao__in=terminadas)
        fila = fila.exclude(situacao=Linha.ENVIADO, enviado_em__gt=recente)
    else:
        fila = envio.linhas.filter(usuario_id__in=ids, situacao__in=[Linha.FALHOU, Linha.SEM_TELEFONE])
    reabertas = fila.update(situacao=Linha.PENDENTE, detalhe='')

    ja_tem = set(envio.linhas.values_list('usuario_id', flat=True))
    novos = [u for u in selecionados if u.pk not in ja_tem]
    _criar_linhas(envio, novos)

    na_fila = envio.linhas.filter(situacao=Linha.PENDENTE).count()
    if not na_fila:
        return False, 'Ninguém para reenviar: todos os selecionados já receberam.'
    EnvioResumoCiclo.objects.filter(pk=envio.pk).update(concluido_em=None)
    _disparar(envio.pk, em_thread=em_thread, refazer_pdf=(alvo == 'todos'))
    return True, f'Reenvio iniciado para {na_fila} pessoa(s).'
