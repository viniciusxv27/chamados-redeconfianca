"""Regras das solicitações ao DP. As views só repassam; tudo que muda o estado passa por aqui."""
import logging
import re
from datetime import date, time, timedelta

from django.core.files.base import ContentFile
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import Anexo, Evento, Solicitacao
from .permissoes import destinatarios, e_analista

logger = logging.getLogger(__name__)
S = Solicitacao

EXTENSOES = ('.pdf', '.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif')
TAMANHO_MAX_MB = 15
ANEXOS_MAX = 5
DIAS_PARA_TRAS_AJUSTE = 90
DIAS_ATESTADO_MAX = 180
ROTULOS_MARCACAO = ('Entrada', 'Saída para o intervalo', 'Volta do intervalo', 'Saída', 'Entrada extra', 'Saída extra')


class Regra(Exception):
    """Mensagem para mostrar na tela."""


def _nome(u):
    return (u.get_full_name() or u.username) if u else 'sistema'


# ── Avisos no sino ──────────────────────────────────────────────────────────
def avisar(usuarios, titulo, mensagem, url, autor):
    """Notificação no sino do portal (sem push). Aviso nunca derruba a solicitação."""
    usuarios = [u for u in usuarios if u]
    if not usuarios:
        return 0
    try:
        from notifications.models import NotificationCategory, NotificationPreference, PushNotification, UserNotification
        desligados = set(NotificationPreference.objects.filter(user__in=usuarios, in_app_enabled=False)
                         .values_list('user_id', flat=True))
        usuarios = [u for u in usuarios if u.pk not in desligados]
        if not usuarios:
            return 0
        categoria = NotificationCategory.objects.filter(name='Departamento Pessoal').order_by('id').first() \
            or NotificationCategory.objects.create(name='Departamento Pessoal', icon='fas fa-user-clock', color='cyan')
        aviso = PushNotification.objects.create(
            title=titulo[:200], message=mensagem, category=categoria, notification_type='TASK', priority='NORMAL',
            icon='fas fa-user-clock', action_url=url, action_text='Abrir solicitação', created_by=autor, is_sent=True,
            sent_at=timezone.now(), extra_data={'origem': 'solicitacoes_dp'})
        UserNotification.objects.bulk_create([UserNotification(notification=aviso, user=u) for u in usuarios],
                                             ignore_conflicts=True)
        return len(usuarios)
    except Exception:  # noqa: BLE001
        logger.warning('Solicitações ao DP: aviso não enviado', exc_info=True)
        return 0


def _url(sol):
    return reverse('solicitacoes_dp:detalhe', args=[sol.pk])


def _avisar_dp(sol, titulo):
    alvo = [sol.responsavel] if sol.responsavel_id and sol.responsavel.is_active else destinatarios()
    avisar([u for u in alvo if u.pk != sol.colaborador_id], titulo,
           f'{_nome(sol.colaborador)}: {sol.titulo}.', _url(sol), sol.colaborador)


def _avisar_colaborador(sol, titulo, autor, texto=''):
    avisar([sol.colaborador], titulo, f'{sol.titulo}.' + (f' {texto[:300]}' if texto else ''), _url(sol), autor)


# ── Leitura e validação do formulário ───────────────────────────────────────
def _data(valor):
    try:
        return date.fromisoformat(str(valor or '').strip())
    except ValueError:
        return None


def _hora(valor):
    texto = str(valor or '').strip()
    if not re.fullmatch(r'\d{1,2}:\d{2}', texto):
        return None
    h, m = (int(x) for x in texto.split(':'))
    return time(h, m) if h < 24 and m < 60 else None


def ler(tipo, post):
    """Os campos do formulário já convertidos (sem validar regra)."""
    dados = {'tipo': tipo, 'justificativa': (post.get('justificativa') or '').strip()[:2000],
             'data': _data(post.get('data'))}
    if tipo == S.AJUSTE:
        marcacoes = []
        for i, rotulo in enumerate(ROTULOS_MARCACAO):
            bruto = (post.get(f'hora_{i}') or '').strip()
            if bruto:
                hora = _hora(bruto)
                marcacoes.append({'rotulo': rotulo, 'hora': hora.strftime('%H:%M') if hora else bruto})
        dados.update(marcacoes=marcacoes, motivo_ajuste=post.get('motivo_ajuste') or '', especie='',
                     data_fim=None, hora_inicio=None, hora_fim=None)
    else:
        dados.update(especie=post.get('especie') or '', data_fim=_data(post.get('data_fim')),
                     hora_inicio=_hora(post.get('hora_inicio')), hora_fim=_hora(post.get('hora_fim')),
                     marcacoes=[], motivo_ajuste='')
    return dados


def _erro_do_arquivo(arquivo):
    nome = (getattr(arquivo, 'name', '') or '').lower()
    if not nome.endswith(EXTENSOES):
        return f'"{arquivo.name}": envie PDF ou foto (JPG, PNG, WEBP, HEIC).'
    if (arquivo.size or 0) > TAMANHO_MAX_MB * 1024 * 1024:
        return f'"{arquivo.name}" passa de {TAMANHO_MAX_MB} MB.'
    if not arquivo.size:
        return f'"{arquivo.name}" está vazio.'
    return ''


def validar(dados, arquivos, anexos_que_ficam=0, colaborador=None, ignorar=None):
    """Lista de erros (vazia = pode gravar)."""
    erros = []
    tipo, hoje = dados['tipo'], timezone.localdate()
    if tipo not in dict(S.TIPOS):
        return ['Escolha o tipo de solicitação.']
    if not dados['data']:
        erros.append('Informe a data.')
    if len(dados['justificativa']) < 10:
        erros.append('Explique o que aconteceu na justificativa (pelo menos uma frase).')
    if tipo == S.AJUSTE:
        if dados['data'] and dados['data'] > hoje:
            erros.append('O ajuste é de um dia que já passou (ou de hoje).')
        if dados['data'] and dados['data'] < hoje - timedelta(days=DIAS_PARA_TRAS_AJUSTE):
            erros.append(f'Ajuste só dos últimos {DIAS_PARA_TRAS_AJUSTE} dias. Para antes disso, fale com o DP.')
        if dados['motivo_ajuste'] not in dict(S.MOTIVOS_AJUSTE):
            erros.append('Diga o que aconteceu (motivo do ajuste).')
        horas = [m['hora'] for m in dados['marcacoes']]
        if not horas:
            erros.append('Informe os horários certos do dia.')
        elif any(_hora(h) is None for h in horas):
            erros.append('Use horários no formato 08:00.')
        elif len(set(horas)) != len(horas):
            erros.append('Há horários repetidos.')
        elif horas != sorted(horas):
            erros.append('Os horários precisam estar em ordem (entrada antes da saída). Turno que passa da '
                         'meia-noite: explique na justificativa.')
        if colaborador is not None and dados['data']:
            repetida = S.objects.filter(colaborador=colaborador, tipo=S.AJUSTE, data=dados['data'], status__in=S.ABERTOS)
            if ignorar is not None:
                repetida = repetida.exclude(pk=ignorar.pk)
            if repetida.exists():
                erros.append('Você já tem uma solicitação de ajuste em aberto para esse dia: corrija aquela.')
    else:
        especies = dict(S.ESPECIES_ATESTADO if tipo == S.ATESTADO else S.ESPECIES_DECLARACAO)
        if dados['especie'] not in especies:
            erros.append('Escolha o tipo do documento.')
        if dados['data'] and dados['data'] > hoje + timedelta(days=30):
            erros.append('A data do documento está longe demais no futuro.')
        if dados['data_fim'] and dados['data'] and dados['data_fim'] < dados['data']:
            erros.append('A data final não pode ser antes da inicial.')
        if dados['data_fim'] and dados['data'] and (dados['data_fim'] - dados['data']).days + 1 > DIAS_ATESTADO_MAX:
            erros.append(f'Período maior que {DIAS_ATESTADO_MAX} dias: fale com o DP.')
        if bool(dados['hora_inicio']) != bool(dados['hora_fim']):
            erros.append('Informe as duas horas (das / até as) ou nenhuma.')
        elif dados['hora_inicio'] and dados['hora_fim'] <= dados['hora_inicio']:
            erros.append('A hora final precisa ser depois da inicial.')
        if not arquivos and not anexos_que_ficam:
            erros.append('Anexe o documento (foto ou PDF).')
    if len(arquivos) + anexos_que_ficam > ANEXOS_MAX:
        erros.append(f'No máximo {ANEXOS_MAX} arquivos por solicitação.')
    erros += [e for e in (_erro_do_arquivo(a) for a in arquivos) if e]
    return erros


def _guardar_anexos(sol, arquivos, por):
    for arquivo in arquivos:
        Anexo.objects.create(solicitacao=sol, arquivo=arquivo, nome_original=arquivo.name[:255],
                             content_type=(getattr(arquivo, 'content_type', '') or '')[:120], tamanho=arquivo.size,
                             enviado_por=por)


# ── Colaborador ─────────────────────────────────────────────────────────────
@transaction.atomic
def criar(colaborador, dados, arquivos):
    erros = validar(dados, arquivos, colaborador=colaborador)
    if erros:
        raise Regra(erros)
    campos = {k: dados[k] for k in ('tipo', 'data', 'data_fim', 'hora_inicio', 'hora_fim', 'especie', 'motivo_ajuste',
                                    'marcacoes', 'justificativa')}
    if campos['tipo'] == S.AJUSTE or campos['data_fim'] == campos['data']:
        campos['data_fim'] = None if campos['tipo'] == S.AJUSTE else campos['data_fim']
    sol = S.objects.create(colaborador=colaborador, **campos)
    _guardar_anexos(sol, arquivos, colaborador)
    Evento.objects.create(solicitacao=sol, autor=colaborador, acao=Evento.CRIADA,
                          texto=f'{len(arquivos)} arquivo(s) anexado(s).' if arquivos else '')
    transaction.on_commit(lambda: _avisar_dp(sol, f'Nova solicitação: {sol.get_tipo_display().lower()}'))
    return sol


ROTULOS_CAMPOS = {'data': 'Data', 'data_fim': 'Até', 'hora_inicio': 'Das', 'hora_fim': 'Até as', 'especie': 'Tipo de documento',
                  'motivo_ajuste': 'O que aconteceu', 'marcacoes': 'Horários', 'justificativa': 'Justificativa'}


def _texto_campo(sol, campo, valor):
    if valor in (None, '', []):
        return '—'
    if campo == 'marcacoes':
        return ' · '.join(f'{m["rotulo"]} {m["hora"]}' for m in valor)
    if campo in ('data', 'data_fim'):
        return valor.strftime('%d/%m/%Y')
    if campo in ('hora_inicio', 'hora_fim'):
        return valor.strftime('%H:%M')
    if campo == 'especie':
        return dict(S.ESPECIES).get(valor, valor)
    if campo == 'motivo_ajuste':
        return dict(S.MOTIVOS_AJUSTE).get(valor, valor)
    return str(valor)


@transaction.atomic
def corrigir(sol, colaborador, dados, arquivos, remover_ids=()):
    """O colaborador corrige (devolvida, ou ainda aguardando o DP) e reenvia. O que mudou vai para o histórico."""
    sol = S.objects.select_for_update().get(pk=sol.pk)
    if sol.colaborador_id != colaborador.pk:
        raise Regra(['Só quem pediu corrige a solicitação.'])
    if sol.status not in (S.DEVOLVIDA, S.ENVIADA):
        raise Regra(['Esta solicitação não pode mais ser corrigida.'])
    dados['tipo'] = sol.tipo
    remover = set(int(i) for i in remover_ids if str(i).isdigit())
    ficam = sol.anexos.filter(removido=False).exclude(pk__in=remover)
    erros = validar(dados, arquivos, anexos_que_ficam=ficam.count(), colaborador=colaborador, ignorar=sol)
    if erros:
        raise Regra(erros)
    if sol.tipo == S.AJUSTE:
        dados['data_fim'] = None
    alteracoes = []
    for campo in ROTULOS_CAMPOS:
        antes, depois = getattr(sol, campo), dados.get(campo)
        if antes != depois:
            alteracoes.append({'campo': ROTULOS_CAMPOS[campo], 'antes': _texto_campo(sol, campo, antes),
                               'depois': _texto_campo(sol, campo, depois)})
            setattr(sol, campo, depois)
    tirados = list(sol.anexos.filter(removido=False, pk__in=remover))
    for anexo in tirados:
        anexo.removido = True
        anexo.save(update_fields=['removido'])
        alteracoes.append({'campo': 'Arquivo retirado', 'antes': anexo.nome_original, 'depois': '—'})
    for arquivo in arquivos:
        alteracoes.append({'campo': 'Arquivo novo', 'antes': '—', 'depois': arquivo.name[:120]})
    _guardar_anexos(sol, arquivos, colaborador)
    estava_devolvida = sol.status == S.DEVOLVIDA
    sol.status, sol.enviada_em = S.ENVIADA, timezone.now()
    sol.save()
    Evento.objects.create(solicitacao=sol, autor=colaborador, acao=Evento.REENVIADA,
                          texto=(dados.get('comentario') or '').strip()[:1000], alteracoes=alteracoes)
    if estava_devolvida:
        transaction.on_commit(lambda: _avisar_dp(sol, 'Solicitação corrigida e reenviada'))
    return sol


@transaction.atomic
def cancelar(sol, colaborador, texto=''):
    sol = S.objects.select_for_update().get(pk=sol.pk)
    if sol.colaborador_id != colaborador.pk or sol.status not in (S.ENVIADA, S.DEVOLVIDA):
        raise Regra(['Só dá para cancelar a própria solicitação que ainda não foi analisada.'])
    sol.status = S.CANCELADA
    sol.save(update_fields=['status', 'atualizado_em'])
    Evento.objects.create(solicitacao=sol, autor=colaborador, acao=Evento.CANCELADA, texto=texto[:1000])
    return sol


# ── DP ──────────────────────────────────────────────────────────────────────
def _exige_dp(por):
    if not e_analista(por):
        raise Regra(['Só o DP analisa as solicitações.'])


@transaction.atomic
def assumir(sol, por):
    _exige_dp(por)
    sol = S.objects.select_for_update().get(pk=sol.pk)
    if sol.status != S.ENVIADA:
        raise Regra(['Esta solicitação não está aguardando o DP.'])
    sol.status, sol.responsavel = S.EM_ANALISE, por
    sol.save(update_fields=['status', 'responsavel', 'atualizado_em'])
    Evento.objects.create(solicitacao=sol, autor=por, acao=Evento.EM_ANALISE)
    transaction.on_commit(lambda: _avisar_colaborador(sol, 'Sua solicitação está em análise no DP', por))
    return sol


def _arquivar_documentos(sol, por):
    """Atestado/declaração aprovado: cada arquivo vira documento do colaborador, na categoria certa."""
    from users.models import CATEGORIA_DOC_ATESTADO, CATEGORIA_DOC_DECLARACAO, UserDocument
    categoria = CATEGORIA_DOC_ATESTADO if sol.tipo == S.ATESTADO else CATEGORIA_DOC_DECLARACAO
    nome = f'{sol.get_especie_display() or sol.get_tipo_display()} — {sol.periodo}'[:150]
    criados = []
    for anexo in sol.anexos.filter(removido=False):
        try:
            with anexo.arquivo.open('rb') as arquivo:
                conteudo = arquivo.read()
        except (FileNotFoundError, OSError):
            logger.warning('Solicitação %s: anexo %s sumiu do storage', sol.pk, anexo.pk)
            continue
        doc = UserDocument(user=sol.colaborador, document_name=nome, original_filename=anexo.nome_original,
                           file_size=anexo.tamanho, content_type=anexo.content_type, uploaded_by=por,
                           origem=UserDocument.ORIGEM_POSTERIOR, categoria=categoria)
        doc.file.save(anexo.nome_original or 'documento', ContentFile(conteudo), save=False)
        doc.save()
        criados.append(doc)
    sol.documentos.add(*criados)
    return criados


@transaction.atomic
def aprovar(sol, por, texto='', arquivar=True, ajustado=False):
    _exige_dp(por)
    sol = S.objects.select_for_update().get(pk=sol.pk)
    if sol.status not in (S.ENVIADA, S.EM_ANALISE):
        raise Regra(['Esta solicitação não está aguardando análise.'])
    agora = timezone.now()
    sol.status, sol.aprovada_em = S.APROVADA, agora
    sol.responsavel = sol.responsavel or por
    sol.resposta = (texto or '').strip()[:2000]
    if sol.tipo == S.AJUSTE and ajustado:
        sol.ajustado_no_ponto, sol.ajustado_em = True, agora
    sol.save()
    detalhe = sol.resposta
    if sol.tipo != S.AJUSTE and arquivar:
        criados = _arquivar_documentos(sol, por)
        detalhe = (detalhe + '\n' if detalhe else '') + f'{len(criados)} arquivo(s) guardado(s) nos documentos do colaborador.'
    Evento.objects.create(solicitacao=sol, autor=por, acao=Evento.APROVADA, texto=detalhe)
    if sol.ajustado_no_ponto:
        Evento.objects.create(solicitacao=sol, autor=por, acao=Evento.AJUSTADA)
    transaction.on_commit(lambda: _avisar_colaborador(sol, 'Solicitação aprovada pelo DP', por, sol.resposta))
    return sol


@transaction.atomic
def devolver(sol, por, texto):
    _exige_dp(por)
    sol = S.objects.select_for_update().get(pk=sol.pk)
    if sol.status not in (S.ENVIADA, S.EM_ANALISE):
        raise Regra(['Esta solicitação não está aguardando análise.'])
    texto = (texto or '').strip()
    if len(texto) < 5:
        raise Regra(['Diga ao colaborador o que corrigir.'])
    sol.status, sol.resposta = S.DEVOLVIDA, texto[:2000]
    sol.responsavel = sol.responsavel or por
    sol.save(update_fields=['status', 'resposta', 'responsavel', 'atualizado_em'])
    Evento.objects.create(solicitacao=sol, autor=por, acao=Evento.DEVOLVIDA, texto=texto[:2000])
    transaction.on_commit(lambda: _avisar_colaborador(sol, 'O DP devolveu sua solicitação para correção', por, texto))
    return sol


@transaction.atomic
def lancar_ajuste(sol, por):
    _exige_dp(por)
    sol = S.objects.select_for_update().get(pk=sol.pk)
    if sol.tipo != S.AJUSTE or sol.status != S.APROVADA or sol.ajustado_no_ponto:
        raise Regra(['Só ajuste aprovado e ainda não lançado.'])
    sol.ajustado_no_ponto, sol.ajustado_em = True, timezone.now()
    sol.save(update_fields=['ajustado_no_ponto', 'ajustado_em', 'atualizado_em'])
    Evento.objects.create(solicitacao=sol, autor=por, acao=Evento.AJUSTADA)
    transaction.on_commit(lambda: _avisar_colaborador(sol, 'Seu ajuste de ponto foi lançado', por))
    return sol


@transaction.atomic
def comentar(sol, por, texto):
    texto = (texto or '').strip()
    if len(texto) < 2:
        raise Regra(['Escreva o comentário.'])
    dp = e_analista(por)
    if sol.colaborador_id != por.pk and not dp:
        raise Regra(['Sem acesso a esta solicitação.'])
    Evento.objects.create(solicitacao=sol, autor=por, acao=Evento.COMENTARIO, texto=texto[:2000])
    if sol.colaborador_id == por.pk:
        transaction.on_commit(lambda: _avisar_dp(sol, 'Comentário do colaborador'))
    else:
        transaction.on_commit(lambda: _avisar_colaborador(sol, 'O DP comentou na sua solicitação', por, texto))
    return sol


# ── Ponto registrado (espelho do Tangerino) ──────────────────────────────────
def ponto_registrado(colaborador, dia):
    """[HH:MM] do que o relógio registrou no dia (espelho sincronizado), ou None se não há espelho."""
    try:
        from tangerino.models import MarcacaoPonto
    except ImportError:
        return None
    linha = MarcacaoPonto.objects.filter(usuario=colaborador, data=dia).first()
    if linha is None:
        return None
    campos = (linha.entrada1, linha.saida1, linha.entrada2, linha.saida2, linha.entrada3, linha.saida3)
    horas = [timezone.localtime(c).strftime('%H:%M') if timezone.is_aware(c) else c.strftime('%H:%M') for c in campos if c]
    for extra in linha.marcacoes_extras or []:
        if isinstance(extra, str):
            horas.append(extra[-5:])
    return horas

