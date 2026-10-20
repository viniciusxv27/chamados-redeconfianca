"""/vendas/acessos/ — o SUPERADMIN libera, pessoa por pessoa, cada parte de Vendas."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import redirect, render

from users.models import User

from . import slv
from .models import AcessoVendas, RegistroAlteracao
from .permissions import FUNCOES, ROTULOS, completar, is_superadmin


def _rotulos(chaves):
    return ', '.join(ROTULOS.get(c, c) for c in chaves) or '(nenhuma)'


@login_required
def acessos(request):
    if not is_superadmin(request.user):
        messages.error(request, 'Só o SUPERADMIN define quem entra em Vendas.')
        return redirect('vendas:dashboard')
    if request.method == 'POST':
        p = request.POST
        pessoa = User.objects.filter(pk=p.get('user') if (p.get('user') or '').isdigit() else None,
                                     is_active=True).first()
        if pessoa is None:
            messages.error(request, 'Escolha a pessoa.')
        elif is_superadmin(pessoa):
            messages.info(request, 'SUPERADMIN já tem Vendas inteiro.')
        elif p.get('remover'):
            a = AcessoVendas.objects.filter(user=pessoa).first()
            if a:
                slv.registrar_alteracoes('ACESSO', pessoa, [('permissoes', 'Funções', _rotulos(a.permissoes), '(sem acesso)')],
                                         request.user, rotulo=pessoa.get_full_name())
                a.delete()
            messages.success(request, f'{pessoa.get_full_name()} não entra mais em Vendas.')
        else:
            chaves = completar(p.getlist('permissoes'))
            if not chaves:
                messages.error(request, 'Marque ao menos uma função (ou use "Tirar acesso").')
            else:
                a, criado = AcessoVendas.objects.get_or_create(user=pessoa, defaults={'concedido_por': request.user})
                antes_chaves, antes_ativo = ([] if criado else list(a.permissoes or [])), (True if criado else a.ativo)
                a.permissoes, a.ativo = chaves, p.get('ativo', 'on') == 'on'
                a.observacao, a.concedido_por = (p.get('observacao') or '').strip()[:200], request.user
                a.save()
                slv.registrar_alteracoes('ACESSO', pessoa, [
                    ('permissoes', 'Funções', '(sem acesso)' if criado else _rotulos(antes_chaves), _rotulos(chaves)),
                    ('ativo', 'Situação', 'ativo' if antes_ativo else 'suspenso', 'ativo' if a.ativo else 'suspenso'),
                ], request.user, rotulo=pessoa.get_full_name())
                messages.success(request, f'Acesso de {pessoa.get_full_name()} salvo: {len(chaves)} função(ões).')
        return redirect('vendas:acessos')

    # Todo mundo ativo aparece (e quem tem liberação mesmo inativo), para o SUPERADMIN escolher qualquer um.
    acessos_ = {a.user_id: a for a in AcessoVendas.objects.select_related('concedido_por')}
    usuarios = (User.objects.filter(Q(is_active=True) | Q(id__in=list(acessos_))).select_related('sector')
                .order_by('first_name', 'last_name', 'username'))
    pessoas, contagem = [], {'todos': 0, 'com': 0, 'sem': 0, 'super': 0}
    for u in usuarios:
        a = acessos_.get(u.id)
        if is_superadmin(u):
            grupo = 'super'
        elif a:
            grupo = 'com'
        else:
            grupo = 'sem'
        contagem['todos'] += 1
        contagem[grupo] += 1
        pessoas.append({'u': u, 'acesso': a, 'grupo': grupo, 'chaves': set(a.permissoes or []) if a else set()})
    return render(request, 'vendas/acessos.html', {
        'aba': 'acessos',
        'pessoas': pessoas,
        'contagem': contagem,
        'funcoes': FUNCOES,
        'historico': RegistroAlteracao.objects.filter(tipo='ACESSO').select_related('usuario')[:40],
    })
