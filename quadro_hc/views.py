"""Telas do Quadro de HC (só SUPERADMIN)."""
import functools
import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from users.models import Sector, User

from . import servicos
from .models import FUNCOES, Carteira, MovimentacaoHC, ObservacaoHC, VagaHC
from .servicos import ErroQuadro

logger = logging.getLogger(__name__)


def _ip(request):
    enc = (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0].strip()
    return enc or request.META.get('REMOTE_ADDR') or None


def _api(fn):
    """Só SUPERADMIN; JSON no corpo; erros viram {erro}."""
    @functools.wraps(fn)
    @login_required
    @require_POST
    def envolto(request, *args, **kwargs):
        if not servicos.pode_acessar(request.user):
            return JsonResponse({'erro': 'Só o SUPERADMIN acessa o Quadro de HC.'}, status=403)
        try:
            dados = json.loads(request.body or b'{}') if request.content_type == 'application/json' else request.POST
            return fn(request, dados, *args, **kwargs)
        except ErroQuadro as e:
            return JsonResponse({'erro': str(e)}, status=400)
        except Exception as e:  # noqa: BLE001
            logger.exception('Erro no Quadro de HC')
            return JsonResponse({'erro': f'{type(e).__name__}: {e}'}, status=500)
    return envolto


@login_required
def quadro(request):
    if not servicos.pode_acessar(request.user):
        messages.error(request, 'O Quadro de HC é só para o SUPERADMIN.')
        return redirect('dashboard')
    hoje = timezone.localdate()
    dados = servicos.montar_quadro(hoje)
    futuras = list(MovimentacaoHC.objects.filter(situacao=MovimentacaoHC.AGENDADA)
                   .select_related('colaborador', 'criado_por', 'novo_setor', 'setor_origem').order_by('data', 'id'))
    historico = list(MovimentacaoHC.objects.exclude(situacao=MovimentacaoHC.AGENDADA)
                     .select_related('colaborador', 'aplicado_por', 'cancelado_por', 'criado_por')
                     .order_by('-criado_em')[:200])
    setores = list(Sector.objects.order_by('name'))
    pessoas = [{'id': p['id'], 'nome': p['nome'], 'cargo': p['cargo'], 'setor': p['setor_id']}
               for c in dados['carteiras'] for b in c['lojas'] for p in b['pessoas']] + \
              [{'id': p['id'], 'nome': p['nome'], 'cargo': p['cargo'], 'setor': None} for p in dados['sem_loja']]
    return render(request, 'quadro_hc/quadro.html', {
        'q': dados, 'hoje': hoje, 'futuras': futuras, 'historico': historico,
        'para_hoje': sum(1 for m in futuras if m.data <= hoje),
        'cargos': servicos.cargos_disponiveis(), 'setores': setores,
        'carteiras': Carteira.objects.prefetch_related('lojas').select_related('coordenador'),
        'coordenadores': User.objects.filter(is_active=True).filter(job_title__icontains='COORDENADOR')
        .order_by('first_name'),
        'funcoes': [f for f, _r in FUNCOES],
        'json_js': {'setores': [{'id': s.id, 'nome': s.name} for s in setores], 'pessoas': pessoas,
                    'funcoes': [f for f, _r in FUNCOES],
                    'cargos': servicos.cargos_disponiveis(), 'hoje': hoje.isoformat()},
        'aba': request.GET.get('aba') or 'quadro',
    })


@_api
def simular(request, dados):
    m = servicos.ler_movimentacao(dict(dados), request.user)
    return JsonResponse(servicos.simular(m))


@_api
def movimentar(request, dados):
    m, sim = servicos.registrar_movimentacao(dict(dados), request.user, ip=_ip(request))
    msg = (f'Agendada para {m.data:%d/%m/%Y}: {m.resumo}' if m.situacao == MovimentacaoHC.AGENDADA
           else f'Aplicada no cadastro: {m.resumo}')
    return JsonResponse({'ok': True, 'msg': msg, 'situacao': m.situacao, 'id': m.pk})


@_api
def mov_aplicar(request, dados, pk):
    m = get_object_or_404(MovimentacaoHC.objects.select_related('colaborador'), pk=pk)
    servicos.aplicar_agendada(m, request.user, ip=_ip(request))
    return JsonResponse({'ok': True, 'msg': f'Aplicada no cadastro: {m.resumo}'})


@_api
def mov_cancelar(request, dados, pk):
    m = get_object_or_404(MovimentacaoHC, pk=pk)
    servicos.cancelar(m, request.user)
    return JsonResponse({'ok': True, 'msg': 'Movimentação cancelada.'})


@_api
def observacao(request, dados):
    pessoa = User.objects.filter(pk=dados.get('colaborador') or 0).first()
    if not pessoa:
        raise ErroQuadro('Colaborador não encontrado.')
    texto = str(dados.get('texto') or '').strip()[:500]
    ObservacaoHC.objects.update_or_create(colaborador=pessoa, defaults={'texto': texto, 'atualizado_por': request.user})
    return JsonResponse({'ok': True, 'texto': texto})


@_api
def vaga_abrir(request, dados):
    setor = Sector.objects.filter(pk=dados.get('setor') or 0).first()
    cargo = ' '.join(str(dados.get('cargo') or '').split())[:100]
    if not setor or not cargo:
        raise ErroQuadro('Escolha a loja e o cargo da vaga.')
    VagaHC.objects.create(setor=setor, cargo=cargo, observacao=str(dados.get('observacao') or '').strip()[:300],
                          criado_por=request.user)
    return JsonResponse({'ok': True, 'msg': f'Vaga de {cargo} aberta na {setor.name}.'})


@_api
def vaga_fechar(request, dados, pk):
    vaga = get_object_or_404(VagaHC, pk=pk, fechada_em__isnull=True)
    vaga.fechada_em = timezone.now()
    vaga.fechada_por = request.user
    vaga.save(update_fields=['fechada_em', 'fechada_por'])
    return JsonResponse({'ok': True, 'msg': 'Vaga fechada.'})


@_api
def carteira_salvar(request, dados):
    pk = dados.get('id')
    carteira = get_object_or_404(Carteira, pk=pk) if pk else Carteira()
    nome = str(dados.get('nome') or '').strip()[:120]
    if not nome:
        raise ErroQuadro('Dê um nome à carteira.')
    carteira.nome = nome
    carteira.coordenador = User.objects.filter(pk=dados.get('coordenador') or 0).first()
    carteira.coordenador_nome = str(dados.get('coordenador_nome') or '').strip()[:120]
    try:
        carteira.ordem = int(dados.get('ordem') or 0)
    except (TypeError, ValueError):
        carteira.ordem = 0
    carteira.save()
    lojas = dados.get('lojas') or []
    if not isinstance(lojas, list):
        lojas = [lojas]
    ids = [int(x) for x in lojas if str(x).isdigit()]
    # Uma loja fica em uma carteira só.
    for outra in Carteira.objects.exclude(pk=carteira.pk).filter(lojas__in=ids).distinct():
        outra.lojas.remove(*ids)
    carteira.lojas.set(Sector.objects.filter(id__in=ids))
    return JsonResponse({'ok': True, 'msg': f'Carteira {carteira.nome} salva.'})


@_api
def carteira_excluir(request, dados, pk):
    carteira = get_object_or_404(Carteira, pk=pk)
    carteira.delete()
    return JsonResponse({'ok': True, 'msg': 'Carteira excluída (as lojas voltam para "sem carteira").'})


@_api
def ajuste(request, dados):
    """Ajuste manual de uma pessoa no quadro (função, loja, contar ou não) — o cadastro não muda."""
    feito = servicos.salvar_ajuste(dict(dados), request.user)
    msg = 'Ajuste manual salvo.' if feito else 'Ajuste manual retirado: a pessoa volta a seguir o cadastro.'
    return JsonResponse({'ok': True, 'msg': msg})


@_api
def previsto(request, dados):
    """HC previsto da loja por função (o quadro ideal), digitado à mão."""
    total = servicos.salvar_previsto(dados.get('setor'), dados.get('quantidades') or {}, request.user)
    return JsonResponse({'ok': True, 'msg': f'HC previsto salvo: {total} pessoa(s).' if total else 'HC previsto zerado.'})
