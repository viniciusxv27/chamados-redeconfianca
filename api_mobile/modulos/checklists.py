"""Módulo Checklists ADM no app (/checklists/ do portal).

Entrada: a mesma do portal — ``users.module_access.tem_acesso(user, 'checklists')``, que
é o gate do menu (templates/base.html:878) e a mesma conta do ``@fechado_para_padrao``
de todas as views web (checklists/views.py:95…): fecha só para o PADRÃO fora do grupo
GERENTES, e a liberação individual devolve.

O que vem para o celular:

- meus checklists (hoje, pendentes, enviados) — sempre ``assignment__assigned_to=user``,
  como no painel e em /checklists/my/;
- abrir um checklist (quem vê: checklists/views.py:736, a mesma conta de view_execution)
  e preencher item a item;
- fotos/arquivos de evidência, que sobem na hora (como o "Arquivos são salvos
  automaticamente" da web);
- acompanhamento do dia por loja (só leitura), para quem a web abre a visão macro
  (checklists/views.py:2593, admin_executions_macro).

Gravação: NADA de regra reescrita. Enviar, subir e excluir evidência chamam as
próprias views do portal (``view_execution``, ``api_upload_evidence``,
``api_delete_evidence``) com o request da pessoa — mesma validação de obrigatória,
mesma regra de "descrição OU evidência", mesmo status final (Aguardando Aprovação).
O app só traduz o JSON para os campos do formulário web e lê os avisos que a view dá.
Nenhuma dessas views manda WhatsApp/e-mail.

Ficam no portal: criar/editar modelos, atribuir, aprovar/reprovar, relatórios, exportar.
"""
import copy
import logging
import mimetypes
import os
import re
from datetime import datetime, timedelta

from django.contrib.messages import constants as niveis
from django.contrib.messages.storage.base import BaseStorage
from django.contrib.sessions.backends.base import SessionBase
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, QueryDict
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils import timezone
from django.utils.datastructures import MultiValueDict
from rest_framework.decorators import api_view, authentication_classes, parser_classes, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from .. import dados
from ..arquivos import link, registrar
from ..autenticacao import TokenMobileAuthentication
from . import permissao

logger = logging.getLogger(__name__)


def pode_entrar(user):
    """Gate do menu (templates/base.html:878 → users/module_access.py:296 ``_gate_checklists``)."""
    from users.module_access import tem_acesso
    return tem_acesso(user, 'checklists')


MODULO = {'rotulo': 'Checklists', 'descricao': 'Checklists do dia, evidências e acompanhamento',
          'icone': 'checkbox-outline', 'cor': '#14B8A6', 'pode_entrar': pode_entrar}

# Status em que o executor ainda preenche (checklists/views.py:774-777).
ABERTOS = ('pending', 'in_progress', 'overdue')
ENVIADOS = ('completed', 'awaiting_approval')
DIAS_ATRASO_NO_INICIO = 30


def _m():
    from checklists import models
    return models


def _v():
    from checklists import views
    return views


# ── Regras do portal (sem reescrever) ────────────────────────────────────────

def _pode_ver(user, execucao):
    """checklists/views.py:736-741 (view_execution): executor, quem atribuiu, superuser ou admin do checklist."""
    a = execucao.assignment
    return (user.pk == a.assigned_to_id or user.pk == a.assigned_by_id or user.is_superuser
            or _v().has_checklist_admin_permission(user))


def _pode_preencher(user, execucao):
    """checklists/views.py:774-777: só o executor, enquanto pendente/em andamento/atrasado."""
    return user.pk == execucao.assignment.assigned_to_id and execucao.status in ABERTOS


def _pode_acompanhar(user):
    """checklists/views.py:2593-2596 (admin_executions_macro): superuser ou SUPERVISOR/ADMIN/SUPERADMIN/ADMINISTRATIVO."""
    return bool(user.is_superuser or getattr(user, 'hierarchy', None) in ['SUPERVISOR', 'ADMIN', 'SUPERADMIN', 'ADMINISTRATIVO'])


def _garantir_tarefas(execucao):
    """checklists/views.py:748-758: a execução ganha as tarefas do modelo que ainda não têm linha."""
    m = _m()
    existentes = set(execucao.task_executions.values_list('task_id', flat=True))
    novas = [m.ChecklistTaskExecution(execution=execucao, task=t, is_completed=False)
             for t in execucao.assignment.template.tasks.all() if t.id not in existentes]
    for nova in novas:
        nova.save()
    return bool(novas)


def _execucao_visivel(request, pk):
    """A execução que a pessoa pode abrir; de outra pessoa (sem ser gestor) → 404, como pede o app."""
    m = _m()
    execucao = get_object_or_404(
        m.ChecklistExecution.objects.select_related(
            'assignment__template', 'assignment__template__sector',
            'assignment__assigned_to', 'assignment__assigned_to__sector',
            'assignment__assigned_by', 'assignment__assigned_by__sector'),
        pk=pk)
    if not _pode_ver(request.user, execucao):
        raise Http404
    return execucao


# ── A view do portal chamada pelo app ────────────────────────────────────────

class _Recados(BaseStorage):
    """Recolhe o ``messages.success/error`` da view do portal (não vai para cookie nem sessão)."""

    def _get(self, *args, **kwargs):
        return [], True

    def _store(self, messages, response, *args, **kwargs):
        return []


def _limpar(texto):
    return re.sub(r'^[^\w•"(]+', '', str(texto)).strip()


def _chamar_portal(request, view, *args, post=None, files=None):
    """Roda a view web com o usuário do token e os campos do formulário web.

    Cópia do request: sessão descartável (nada de sessão nova gravada para a API) e
    os recados da view recolhidos aqui. Devolve (resposta, [(nivel, texto)]).
    """
    bruto = copy.copy(request._request)
    bruto.method = 'POST'
    campos = QueryDict('', mutable=True)
    for chave, valor in (post or {}).items():
        campos.setlist(chave, valor if isinstance(valor, list) else [valor])
    bruto._post = campos
    bruto._files = files if files is not None else MultiValueDict()
    bruto.session = SessionBase()
    recados = _Recados(bruto)
    bruto._messages = recados
    erro_render = None
    try:
        resposta = view(bruto, *args)
    except Http404:
        raise
    except Exception as exc:  # noqa: BLE001 — a tela de erro do portal falhou ao desenhar
        erro_render, resposta = exc, None
    # A tela de erro do portal já leu os recados (iterar move os da fila para os 'carregados').
    textos = [(r.level, _limpar(r.message)) for r in [*recados._loaded_messages, *recados._queued_messages]]
    if resposta is None and not any(n >= niveis.ERROR for n, _ in textos):
        raise erro_render
    return resposta, textos


# ── Formato ──────────────────────────────────────────────────────────────────

def _atrasado(execucao, hoje=None):
    return execucao.status in ABERTOS and execucao.execution_date < (hoje or timezone.localdate())


def _resumo(request, e, com_pessoa=False, hoje=None):
    t = e.assignment.template
    total, feitas = e.total_tarefas, e.tarefas_feitas
    saida = {
        'id': e.pk,
        'checklist': t.name,
        'setor': t.sector.name if t.sector_id else None,
        'data': e.execution_date.isoformat(),
        'periodo': e.period,
        'periodo_nome': e.get_period_display(),
        'status': e.status,
        'status_nome': e.get_status_display(),
        'total': total,
        'feitas': feitas,
        'progresso': round(feitas / total * 100) if total else 0,
        'atrasado': _atrasado(e, hoje),
        'pode_preencher': _pode_preencher(request.user, e),
        'enviado_em': dados.iso(e.submitted_at),
    }
    if com_pessoa:
        saida['responsavel'] = dados.pessoa(request, e.assignment.assigned_to)
    return saida


def _nome_arquivo(campo, padrao='arquivo'):
    return os.path.basename(getattr(campo, 'name', '') or '') or padrao


def _evidencias(request, te):
    saida = []
    for ev in te.evidences.all():
        nome = ev.original_filename or _nome_arquivo(ev.file)
        saida.append({'id': ev.pk, 'tipo': ev.evidence_type, 'nome': nome, 'imagem': ev.evidence_type == 'image',
                      'enviada_em': dados.iso(ev.uploaded_at), 'url': link(request, 'checklists_evidencia', ev.pk),
                      'removivel': True})
    # Campos antigos (uma imagem/um vídeo por tarefa): só leitura.
    for campo, tipo in (('evidence_image', 'image'), ('evidence_video', 'video')):
        if getattr(te, campo):
            saida.append({'id': None, 'tipo': tipo, 'nome': _nome_arquivo(getattr(te, campo)), 'imagem': tipo == 'image',
                          'enviada_em': None, 'url': link(request, 'checklists_evidencia_antiga', f'{te.pk}.{campo}'),
                          'removivel': False})
    return saida


def _instrucoes(request, execucao, tarefa):
    saida = []
    for campo, tipo in (('instruction_image', 'image'), ('instruction_video', 'video'), ('instruction_document', 'document')):
        if getattr(tarefa, campo):
            saida.append({'tipo': tipo, 'titulo': {'image': 'Imagem', 'video': 'Vídeo', 'document': 'Documento'}[tipo],
                          'url': link(request, 'checklists_instrucao', f'{execucao.pk}.{tarefa.pk}.{campo}')})
    for i, midia in enumerate(tarefa.instruction_media.all(), 1):
        saida.append({'tipo': midia.media_type, 'titulo': midia.title or f'{midia.get_media_type_display()} {i}',
                      'url': link(request, 'checklists_instrucao', f'{execucao.pk}.{tarefa.pk}.m{midia.pk}')})
    return saida


def _tarefa(request, execucao, te):
    tarefa = te.task
    return {
        'id': te.pk,
        'tarefa_id': tarefa.pk,
        'titulo': tarefa.title,
        'descricao': tarefa.description or '',
        'tipo': tarefa.task_type,
        'obrigatoria': tarefa.is_required,
        'pontos': tarefa.points,
        'feita': te.is_completed,
        'resposta': None if te.yes_no_answer is None else ('yes' if te.yes_no_answer else 'no'),
        'situacao': te.dropdown_answer or None,
        'observacao': te.notes or '',
        'concluida_em': dados.iso(te.completed_at),
        'aprovacao': {'status': te.approval_status, 'nome': te.get_approval_status_display(),
                      'observacao': te.approval_notes or None, 'em': dados.iso(te.approved_at)},
        'evidencias': _evidencias(request, te),
        'instrucoes': _instrucoes(request, execucao, tarefa),
    }


def _tarefas_da(execucao):
    return list(execucao.task_executions.select_related('task').prefetch_related('evidences', 'task__instruction_media')
                .order_by('task__order', 'task_id'))


def _detalhe(request, execucao):
    tarefas = _tarefas_da(execucao)
    execucao._total_tarefas = len(tarefas)
    execucao._tarefas_feitas = sum(1 for te in tarefas if te.is_completed)
    t = execucao.assignment.template
    user = request.user
    return {
        **_resumo(request, execucao, com_pessoa=True),
        'descricao': t.description or '',
        'atribuido_por': dados.pessoa(request, execucao.assignment.assigned_by),
        'iniciado_em': dados.iso(execucao.started_at),
        'concluido_em': dados.iso(execucao.completed_at),
        'observacoes': execucao.notes or '',
        'sou_executor': user.pk == execucao.assignment.assigned_to_id,
        'tarefas': [_tarefa(request, execucao, te) for te in tarefas],
        'url_web': f'/checklists/view/{execucao.pk}/',
    }


# ── Arquivos (link assinado: confere de novo quem abre) ──────────────────────

def _tipo(nome):
    return mimetypes.guess_type(nome or '')[0] or 'application/octet-stream'


@registrar('checklists_evidencia')
def _arquivo_evidencia(user, obj_id):
    ev = _m().ChecklistTaskEvidence.objects.select_related(
        'task_execution__execution__assignment').get(pk=int(obj_id))
    if not pode_entrar(user) or not _pode_ver(user, ev.task_execution.execution):
        raise PermissionDenied
    nome = ev.original_filename or _nome_arquivo(ev.file)
    return ev.file, nome, _tipo(nome)


@registrar('checklists_evidencia_antiga')
def _arquivo_evidencia_antiga(user, obj_id):
    te_id, campo = obj_id.split('.', 1)
    if campo not in ('evidence_image', 'evidence_video'):
        raise PermissionDenied
    te = _m().ChecklistTaskExecution.objects.select_related('execution__assignment').get(pk=int(te_id))
    if not pode_entrar(user) or not _pode_ver(user, te.execution) or not getattr(te, campo):
        raise PermissionDenied
    arquivo = getattr(te, campo)
    return arquivo, _nome_arquivo(arquivo), _tipo(arquivo.name)


@registrar('checklists_instrucao')
def _arquivo_instrucao(user, obj_id):
    """Material de instrução: abre para quem pode ver uma execução daquele modelo."""
    m = _m()
    exec_id, tarefa_id, campo = obj_id.split('.', 2)
    execucao = m.ChecklistExecution.objects.select_related('assignment').get(pk=int(exec_id))
    if not pode_entrar(user) or not _pode_ver(user, execucao):
        raise PermissionDenied
    tarefa = m.ChecklistTask.objects.get(pk=int(tarefa_id), template_id=execucao.assignment.template_id)
    if campo.startswith('m'):
        arquivo = m.ChecklistTaskInstructionMedia.objects.get(pk=int(campo[1:]), task=tarefa).file
    elif campo in ('instruction_image', 'instruction_video', 'instruction_document'):
        arquivo = getattr(tarefa, campo)
    else:
        raise PermissionDenied
    if not arquivo:
        raise PermissionDenied
    return arquivo, _nome_arquivo(arquivo), _tipo(arquivo.name)


# ── Endpoints: meus checklists ───────────────────────────────────────────────

def _minhas(user):
    return _m().ChecklistExecution.objects.filter(assignment__assigned_to=user)


@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('checklists')])
def inicio(request):
    """O dia de hoje (como o painel, checklists/views.py:136) e o que ficou para trás."""
    user = request.user
    hoje = timezone.localdate()
    minhas = _minhas(user)
    do_dia = list(minhas.filter(execution_date=hoje).para_listagem().order_by('period', 'assignment__template__name'))
    atrasados = minhas.filter(status__in=ABERTOS, execution_date__lt=hoje,
                              execution_date__gte=hoje - timedelta(days=DIAS_ATRASO_NO_INICIO))
    return Response({
        'hoje': hoje.isoformat(),
        'execucoes': [_resumo(request, e, hoje=hoje) for e in do_dia],
        'contadores': {
            'hoje_abertos': sum(1 for e in do_dia if e.status in ABERTOS),
            'hoje_enviados': sum(1 for e in do_dia if e.status in ENVIADOS),
            'atrasados': atrasados.count(),
            'atrasados_dias': DIAS_ATRASO_NO_INICIO,
            'aguardando_aprovacao': minhas.filter(status='awaiting_approval').count(),
        },
        'gestor': _pode_acompanhar(user),
        'url_web': '/checklists/',
    })


FILTROS = ('hoje', 'pendentes', 'enviados', 'todos')


@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('checklists')])
def execucoes(request):
    """Meus checklists: ``pendentes`` (ainda dá para preencher, até hoje), ``enviados`` e ``todos`` (histórico)."""
    from ..views import _pagina
    hoje = timezone.localdate()
    filtro = request.query_params.get('filtro', 'pendentes')
    if filtro not in FILTROS:
        filtro = 'pendentes'
    lista = _minhas(request.user).filter(execution_date__lte=hoje)
    if filtro == 'hoje':
        lista = lista.filter(execution_date=hoje)
    elif filtro == 'pendentes':
        lista = lista.filter(status__in=ABERTOS)
    elif filtro == 'enviados':
        lista = lista.filter(status__in=ENVIADOS)
    itens, pagina = _pagina(request, lista.para_listagem().order_by('-execution_date', 'period', 'assignment__template__name', 'id'))
    return Response({**pagina, 'filtro': filtro, 'resultados': [_resumo(request, e, hoje=hoje) for e in itens]})


@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('checklists')])
def execucao(request, pk):
    """Um checklist com as tarefas (o que view_execution, checklists/views.py:717, mostra)."""
    e = _execucao_visivel(request, pk)
    _garantir_tarefas(e)
    return Response(_detalhe(request, e))


def _campos_do_formulario(execucao, tarefas, respostas):
    """JSON do app → os campos do formulário web (templates checklists/view_execution.html:62-223).

    Tarefa que o app não mandou vai com o que já está gravado — como o formulário web,
    que já abre preenchido.
    """
    campos = {}
    for te in tarefas:
        r = respostas.get(te.pk, {})
        sufixo = f'{execucao.pk}_{te.task_id}'
        tipo = te.task.task_type
        if tipo == 'yes_no':
            atual = None if te.yes_no_answer is None else ('yes' if te.yes_no_answer else 'no')
            resposta = r.get('resposta', atual)
            if resposta in ('yes', 'no'):
                campos[f'yes_no_{sufixo}'] = resposta
        elif tipo != 'dropdown':
            if bool(r.get('feita', te.is_completed)):
                campos[f'task_{sufixo}'] = 'on'
        situacao = r.get('situacao', te.dropdown_answer)
        if situacao:
            campos[f'dropdown_{sufixo}'] = str(situacao)
        campos[f'notes_{sufixo}'] = str(r.get('observacao', te.notes) or '')
    return campos


@api_view(['POST'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('checklists')])
@parser_classes([JSONParser])
def enviar(request, pk):
    """Envia para aprovação pela própria view do portal (checklists/views.py:793, POST de view_execution).

    Corpo: ``{"respostas": [{"id": <tarefa da execução>, "feita": bool, "resposta": "yes"|"no",
    "situacao": "yes"|"no"|"not_applicable"|"", "observacao": "..."}]}``.
    Se o portal recusa, nada fica gravado (a view grava tarefa a tarefa; aqui vai tudo numa transação).
    """
    e = _execucao_visivel(request, pk)
    if not _pode_preencher(request.user, e):
        msg = ('Este checklist já foi enviado.' if e.status in ENVIADOS
               else 'Só quem recebeu o checklist pode preenchê-lo.')
        return Response({'erro': msg, 'erros': [msg]}, status=400)
    _garantir_tarefas(e)
    tarefas = _tarefas_da(e)
    respostas = {}
    for item in request.data.get('respostas') or []:
        try:
            respostas[int(item.get('id'))] = item
        except (TypeError, ValueError, AttributeError):
            continue
    campos = _campos_do_formulario(e, tarefas, respostas)
    with transaction.atomic():
        resposta, recados = _chamar_portal(request, _v().view_execution, e.pk, post=campos)
        erros = [t for n, t in recados if n >= niveis.ERROR]
        ok = resposta is not None and resposta.status_code in (301, 302) and not erros
        if not ok:
            transaction.set_rollback(True)
    if not ok:
        erros = erros or ['O portal não aceitou o envio. Confira as tarefas e tente de novo.']
        return Response({'erro': '\n'.join(erros), 'erros': erros}, status=400)
    e = _execucao_visivel(request, pk)
    sucesso = [t for n, t in recados if n == niveis.SUCCESS]
    return Response({'mensagem': sucesso[0] if sucesso else 'Checklist enviado para aprovação.', 'execucao': _detalhe(request, e)})


def _tarefa_visivel(request, pk):
    te = get_object_or_404(_m().ChecklistTaskExecution.objects.select_related('execution'), pk=pk)
    return _execucao_visivel(request, te.execution_id), te


def _resposta_json(resposta):
    import json
    try:
        return json.loads(resposta.content or b'{}')
    except ValueError:
        return {}


@api_view(['POST'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('checklists')])
@parser_classes([MultiPartParser, FormParser])
def evidencias(request, pk):
    """Sobe fotos/arquivos (campo ``files``) pela view do portal (checklists/views.py:2337, api_upload_evidence)."""
    e, te = _tarefa_visivel(request, pk)
    if not request.FILES.getlist('files'):
        return Response({'erro': 'Escolha uma foto ou um arquivo.'}, status=400)
    resposta, _ = _chamar_portal(request, _v().api_upload_evidence, te.pk, files=request.FILES)
    corpo = _resposta_json(resposta)
    if resposta.status_code != 200 or not corpo.get('success'):
        return Response({'erro': corpo.get('error') or 'Não deu para enviar o arquivo.'}, status=resposta.status_code if resposta.status_code >= 400 else 400)
    te = [x for x in _tarefas_da(e) if x.pk == te.pk][0]
    return Response({'mensagem': corpo.get('message'), 'tarefa': _tarefa(request, e, te)})


@api_view(['POST'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('checklists')])
def excluir_evidencia(request, pk):
    """Exclui pela view do portal (checklists/views.py:2409, api_delete_evidence)."""
    ev = get_object_or_404(_m().ChecklistTaskEvidence.objects.select_related('task_execution'), pk=pk)
    e, te = _tarefa_visivel(request, ev.task_execution_id)
    resposta, _ = _chamar_portal(request, _v().api_delete_evidence, ev.pk)
    corpo = _resposta_json(resposta)
    if resposta.status_code != 200 or not corpo.get('success'):
        return Response({'erro': corpo.get('error') or 'Não deu para excluir.'}, status=resposta.status_code if resposta.status_code >= 400 else 400)
    te = [x for x in _tarefas_da(e) if x.pk == te.pk][0]
    return Response({'mensagem': corpo.get('message'), 'tarefa': _tarefa(request, e, te)})


# ── Acompanhamento (gestor, só leitura) ──────────────────────────────────────
# Mesmas consultas de admin_executions_macro (checklists/views.py:2590-2740): execuções de
# modelos dos setores visíveis, por pessoa e turno, e atrasados (2 dias ou mais, ainda abertos).

FEITO = ['completed', 'awaiting_approval']
ATRASAVEL = ['pending', 'in_progress', 'overdue']


def _gestor_ou_403(user):
    if not _pode_acompanhar(user):
        raise PermissionDenied('O acompanhamento é para supervisão e administração.')


def _data(texto):
    try:
        return datetime.strptime(texto or '', '%Y-%m-%d').date()
    except ValueError:
        return timezone.localdate()


def _escopo(user, setor_id=None):
    """checklists/views.py:2628-2636 (execuções) e 2645-2660 (pessoas com atribuição)."""
    from users.models import User
    m = _m()
    execs = m.ChecklistExecution.objects.select_related('assignment__template', 'assignment__template__sector',
                                                        'assignment__assigned_to')
    pessoas = User.objects.filter(is_active=True, checklist_assignments__isnull=False)
    if not user.is_superuser:
        setores = _v().get_user_visible_sector_ids(user, include_adm_for_admin_plus=True)
        execs = execs.filter(assignment__template__sector_id__in=setores) if setores else execs.none()
        pessoas = pessoas.filter(checklist_assignments__template__sector_id__in=setores)
    if setor_id:
        execs = execs.filter(assignment__template__sector_id=setor_id)
        pessoas = pessoas.filter(checklist_assignments__template__sector_id=setor_id)
    return execs, pessoas.select_related('sector').distinct()


@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('checklists')])
def acompanhamento(request):
    """Quem fez e quem não fez no dia, por loja (setor principal da pessoa)."""
    _gestor_ou_403(request.user)
    dia = _data(request.query_params.get('data'))
    limite = dia - timedelta(days=2)
    execs, pessoas = _escopo(request.user, request.query_params.get('setor') or None)

    def conta(turno):
        return {'total': Count('id', filter=Q(period=turno)),
                'pendentes': Count('id', filter=Q(period=turno, status='pending')),
                'andamento': Count('id', filter=Q(period=turno, status='in_progress')),
                'feitos': Count('id', filter=Q(period=turno, status__in=FEITO))}

    do_dia = {linha['assignment__assigned_to']: linha for linha in
              execs.filter(execution_date=dia).values('assignment__assigned_to')
              .annotate(**{f'manha_{k}': v for k, v in conta('morning').items()},
                        **{f'tarde_{k}': v for k, v in conta('afternoon').items()})}
    atrasados = dict(execs.filter(execution_date__lte=limite, status__in=ATRASAVEL)
                     .values_list('assignment__assigned_to').annotate(n=Count('id')))

    lojas = {}
    for p in pessoas.order_by('first_name', 'last_name'):
        n = do_dia.get(p.pk, {})
        manha = {k: n.get(f'manha_{k}', 0) for k in ('total', 'pendentes', 'andamento', 'feitos')}
        tarde = {k: n.get(f'tarde_{k}', 0) for k in ('total', 'pendentes', 'andamento', 'feitos')}
        atraso = atrasados.get(p.pk, 0)
        total, feitos = manha['total'] + tarde['total'], manha['feitos'] + tarde['feitos']
        if not total and not atraso:
            continue
        loja = p.sector.name if p.sector_id else 'Sem setor'
        lojas.setdefault(loja, []).append({
            'pessoa': dados.pessoa(request, p), 'manha': manha, 'tarde': tarde, 'atrasados': atraso,
            'total': total, 'feitos': feitos, 'taxa': round(feitos / total * 100) if total else 0,
        })

    grupos = []
    for loja in sorted(lojas):
        linhas = lojas[loja]
        total, feitos = sum(x['total'] for x in linhas), sum(x['feitos'] for x in linhas)
        grupos.append({'loja': loja, 'pessoas': linhas, 'total': total, 'feitos': feitos,
                       'faltam': total - feitos, 'atrasados': sum(x['atrasados'] for x in linhas),
                       'taxa': round(feitos / total * 100) if total else 0})
    total = sum(g['total'] for g in grupos)
    feitos = sum(g['feitos'] for g in grupos)
    return Response({
        'data': dia.isoformat(),
        'limite_atraso': limite.isoformat(),
        'totais': {'pessoas': sum(len(g['pessoas']) for g in grupos), 'total': total, 'feitos': feitos,
                   'faltam': total - feitos, 'atrasados': sum(g['atrasados'] for g in grupos),
                   'taxa': round(feitos / total * 100) if total else 0},
        'lojas': grupos,
        'url_web': f'/checklists/admin/executions/macro/?date={dia.isoformat()}',
    })


@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([permissao('checklists')])
def acompanhamento_pessoa(request, user_id):
    """Os checklists de uma pessoa no dia e os atrasados dela, dentro do mesmo recorte do acompanhamento."""
    _gestor_ou_403(request.user)
    dia = _data(request.query_params.get('data'))
    limite = dia - timedelta(days=2)
    execs, pessoas = _escopo(request.user)
    pessoa = get_object_or_404(pessoas, pk=user_id)
    dela = execs.filter(assignment__assigned_to=pessoa).com_progresso()
    hoje = timezone.localdate()
    return Response({
        'data': dia.isoformat(),
        'pessoa': dados.pessoa(request, pessoa),
        'do_dia': [_resumo(request, e, hoje=hoje) for e in dela.filter(execution_date=dia)
                   .order_by('period', 'assignment__template__name')],
        'atrasados': [_resumo(request, e, hoje=hoje) for e in dela.filter(execution_date__lte=limite, status__in=ATRASAVEL)
                      .order_by('-execution_date', 'period')[:50]],
    })


urlpatterns = [
    path('inicio/', inicio, name='checklists_inicio'),
    path('execucoes/', execucoes, name='checklists_execucoes'),
    path('execucoes/<int:pk>/', execucao, name='checklists_execucao'),
    path('execucoes/<int:pk>/enviar/', enviar, name='checklists_enviar'),
    path('tarefas/<int:pk>/evidencias/', evidencias, name='checklists_evidencias'),
    path('evidencias/<int:pk>/excluir/', excluir_evidencia, name='checklists_excluir_evidencia'),
    path('acompanhamento/', acompanhamento, name='checklists_acompanhamento'),
    path('acompanhamento/pessoas/<int:user_id>/', acompanhamento_pessoa, name='checklists_acompanhamento_pessoa'),
]
