"""Quadro "Projetos da Inteligência" ao lado do Kanban de metas (10/11/2026).

O setor Inteligência anota os projetos que tem em mente e em que estágio está
cada um (Ideação → Desenvolvimento → Testes → Conclusão). A lista é uma só para
o setor inteiro; quem criou e quem mexeu por último fica gravado. Fora do setor
(e sem ser SUPERADMIN) os endereços respondem 404, como se não existissem.
"""
from functools import wraps

from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from .models import ProjetoInteligencia
from .utils import ve_projetos_inteligencia

ESTAGIOS = [{'valor': v, 'rotulo': r} for v, r in ProjetoInteligencia.Estagio.choices]
_LIMITE_NOME = ProjetoInteligencia._meta.get_field('nome').max_length
_LIMITE_NOTA = ProjetoInteligencia._meta.get_field('nota').max_length


def projetos_do_quadro(user):
    """Os projetos para o quadro, numa consulta — ou None para quem não usa o quadro (sem consulta)."""
    if not ve_projetos_inteligencia(user):
        return None
    return [p.como_dict() for p in ProjetoInteligencia.objects.only('id', 'nome', 'nota', 'estagio')]


def _do_quadro(view):
    """Só POST, só para quem usa o quadro. O 404 vem antes do 405: para os outros, o endereço não existe."""
    so_post = require_POST(view)

    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if not ve_projetos_inteligencia(request.user):
            raise Http404
        return so_post(request, *args, **kwargs)
    return _wrapped


def _erro(msg):
    return JsonResponse({'ok': False, 'error': msg}, status=400)


def _ler_campos(request, projeto=None):
    """Valida nome, nota e estágio do POST. Campo ausente mantém o valor atual (edição parcial)."""
    dados = {}
    if 'nome' in request.POST or projeto is None:
        nome = ' '.join((request.POST.get('nome') or '').split())
        if not nome:
            return None, 'Dê um nome ao projeto.'
        if len(nome) > _LIMITE_NOME:
            return None, f'Nome muito longo (máximo {_LIMITE_NOME} caracteres).'
        dados['nome'] = nome
    if 'nota' in request.POST:
        nota = ' '.join((request.POST.get('nota') or '').split())
        if len(nota) > _LIMITE_NOTA:
            return None, f'Nota muito longa (máximo {_LIMITE_NOTA} caracteres).'
        dados['nota'] = nota
    if 'estagio' in request.POST:
        estagio = request.POST.get('estagio')
        if estagio not in ProjetoInteligencia.Estagio.values:
            return None, 'Estágio inválido.'
        dados['estagio'] = estagio
    return dados, None


@_do_quadro
def projeto_intel_criar(request):
    dados, erro = _ler_campos(request)
    if erro:
        return _erro(erro)
    p = ProjetoInteligencia.objects.create(criado_por=request.user, atualizado_por=request.user, **dados)
    return JsonResponse({'ok': True, 'projeto': p.como_dict()})


@_do_quadro
def projeto_intel_editar(request, projeto_id):
    p = get_object_or_404(ProjetoInteligencia, pk=projeto_id)
    dados, erro = _ler_campos(request, p)
    if erro:
        return _erro(erro)
    for campo, valor in dados.items():
        setattr(p, campo, valor)
    p.atualizado_por = request.user
    p.save(update_fields=[*dados, 'atualizado_por', 'atualizado_em'])
    return JsonResponse({'ok': True, 'projeto': p.como_dict()})


@_do_quadro
def projeto_intel_excluir(request, projeto_id):
    get_object_or_404(ProjetoInteligencia, pk=projeto_id).delete()
    return JsonResponse({'ok': True})
