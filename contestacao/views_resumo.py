"""Tela do resumo do ciclo no WhatsApp: só SUPERADMIN. A regra está em resumo_whatsapp.py."""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from core.telefone import normalizar, problema
from users.models import User

from . import resumo_whatsapp as resumo
from .models import DestinatarioResumoCiclo, EnvioResumoCiclo, EnvioResumoDestinatario


def e_superadmin(user):
    return bool(user and user.is_authenticated
                and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def _exige_superadmin(view):
    @wraps(view)
    @login_required
    def _view(request, *args, **kwargs):
        if not e_superadmin(request.user):
            messages.error(request, 'O resumo do ciclo no WhatsApp é só para o SUPERADMIN.')
            return redirect('contestacao:exclusion_list')
        return view(request, *args, **kwargs)
    return _view


def _para_inteiro(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


@_exige_superadmin
def resumo_whatsapp(request):
    if request.method == 'POST':
        ids = {i for i in (_para_inteiro(v) for v in request.POST.getlist('usuarios')) if i}
        validos = set(User.objects.filter(pk__in=ids, is_active=True).values_list('pk', flat=True))
        with transaction.atomic():
            DestinatarioResumoCiclo.objects.exclude(usuario_id__in=validos).delete()
            DestinatarioResumoCiclo.objects.bulk_create(
                [DestinatarioResumoCiclo(usuario_id=pk, incluido_por=request.user) for pk in validos],
                ignore_conflicts=True)
        messages.success(request, f'Seleção salva: {len(validos)} pessoa(s) recebem o resumo do ciclo.')
        return redirect('contestacao:resumo_whatsapp')

    selecionados = set(DestinatarioResumoCiclo.objects.values_list('usuario_id', flat=True))
    pessoas = []
    for u in User.objects.filter(is_active=True).select_related('sector').order_by('first_name', 'last_name'):
        marcada = u.pk in selecionados
        pessoas.append({
            'id': u.pk,
            'nome': resumo._nome_pessoa(u),
            'setor': u.sector.name if u.sector_id else '',
            'marcada': marcada,
            'numero': normalizar(u.phone or ''),
            'problema': problema(u.phone or '') or 'Sem telefone no cadastro',
        })
    pessoas.sort(key=lambda p: (not p['marcada'], p['nome'].lower()))

    rotulos = dict(EnvioResumoDestinatario.SITUACOES)
    envios = list(EnvioResumoCiclo.objects.select_related('lote', 'disparado_por')
                  .prefetch_related('linhas')[:12])
    for envio in envios:
        envio.linhas_tela = list(envio.linhas.all())
        envio.qtd_enviados = sum(1 for l in envio.linhas_tela if l.situacao == EnvioResumoDestinatario.ENVIADO)
        envio.qtd_faltam = len(envio.linhas_tela) - envio.qtd_enviados
        for linha in envio.linhas_tela:
            linha.tentativas_tela = [
                {'em': parse_datetime(t.get('em') or ''), 'rotulo': rotulos.get(t.get('situacao'), t.get('situacao')),
                 'detalhe': t.get('detalhe')}
                for t in linha.historico or []]

    estado = resumo.estado_do_ciclo()
    marcadas = [p for p in pessoas if p['marcada']]
    return render(request, 'contestacao/resumo_whatsapp.html', {
        'estado': estado,
        'lojas_prontas': sum(1 for loja in estado['lojas'] if loja['pronta']),
        'pessoas': pessoas,
        'qtd_marcadas': len(marcadas),
        'marcadas_sem_telefone': [p for p in marcadas if not p['numero']],
        'envios': envios,
    })


@_exige_superadmin
@require_POST
def resumo_whatsapp_enviar(request):
    ok, mensagem = resumo.enviar_manual(
        request.user,
        alvo='todos' if request.POST.get('alvo') == 'todos' else 'faltantes',
        envio_id=_para_inteiro(request.POST.get('envio')),
        linha_id=_para_inteiro(request.POST.get('linha')),
    )
    (messages.success if ok else messages.error)(request, mensagem)
    return redirect('contestacao:resumo_whatsapp')


@login_required
def resumo_whatsapp_pdf(request, pk):
    """O link da mensagem. Abre para o SUPERADMIN e para quem está na lista daquele envio."""
    envio = get_object_or_404(EnvioResumoCiclo, pk=pk)
    if not (e_superadmin(request.user) or envio.linhas.filter(usuario=request.user).exists()):
        return HttpResponseForbidden('Este resumo não foi enviado para você.')
    if not envio.pdf:
        raise Http404('O PDF deste resumo ainda não foi gerado.')
    return FileResponse(envio.pdf.open('rb'), content_type='application/pdf',
                        filename=f'resumo_contestacoes_ciclo_{envio.lote_id}.pdf')
