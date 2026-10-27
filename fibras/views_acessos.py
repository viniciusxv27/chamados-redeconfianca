"""/fibras/acessos/ — o SUPERADMIN libera, pessoa por pessoa, cada parte de Fibras."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import redirect, render

from users.models import User

from .models import AcessoFibras, HistoricoAcessoFibras
from .permissoes import FUNCOES, ROTULOS, completar, is_superadmin


def _rotulos(chaves, ativo=True):
    texto = ', '.join(ROTULOS.get(c, c) for c in chaves) or '(sem acesso)'
    return texto if ativo else f'{texto} (suspenso)'


def _registrar(pessoa, antes, depois, por):
    if antes != depois:
        HistoricoAcessoFibras.objects.create(pessoa=pessoa, pessoa_nome=pessoa.get_full_name() or pessoa.username,
                                             antes=antes, depois=depois, por=por,
                                             por_nome=por.get_full_name() or por.username)


@login_required
def acessos(request):
    if not is_superadmin(request.user):
        messages.error(request, 'Só o SUPERADMIN define quem entra em Fibras.')
        return redirect('home')
    if request.method == 'POST':
        p = request.POST
        pessoa = User.objects.filter(pk=p.get('user') if (p.get('user') or '').isdigit() else None,
                                     is_active=True).first()
        if pessoa is None:
            messages.error(request, 'Escolha a pessoa.')
        elif is_superadmin(pessoa):
            messages.info(request, 'SUPERADMIN já tem Fibras inteiro.')
        elif p.get('remover'):
            a = AcessoFibras.objects.filter(user=pessoa).first()
            if a:
                _registrar(pessoa, _rotulos(a.permissoes, a.ativo), '(sem acesso)', request.user)
                a.delete()
            messages.success(request, f'{pessoa.get_full_name()} não entra mais em Fibras.')
        else:
            chaves = completar(p.getlist('permissoes'))
            if not chaves:
                messages.error(request, 'Marque ao menos uma função (ou use "Tirar acesso").')
            else:
                a, criado = AcessoFibras.objects.get_or_create(user=pessoa, defaults={'concedido_por': request.user})
                antes = '(sem acesso)' if criado else _rotulos(a.permissoes, a.ativo)
                a.permissoes, a.ativo = chaves, p.get('ativo', 'on') == 'on'
                a.observacao, a.concedido_por = (p.get('observacao') or '').strip()[:200], request.user
                a.save()
                _registrar(pessoa, antes, _rotulos(chaves, a.ativo), request.user)
                messages.success(request, f'Acesso de {pessoa.get_full_name()} salvo: {len(chaves)} função(ões).')
        return redirect('fibras:acessos')

    # Todo mundo ativo aparece (e quem tem liberação mesmo inativo), para o SUPERADMIN escolher qualquer um.
    acessos_ = {a.user_id: a for a in AcessoFibras.objects.select_related('concedido_por')}
    usuarios = (User.objects.filter(Q(is_active=True) | Q(id__in=list(acessos_))).select_related('sector')
                .order_by('first_name', 'last_name', 'username'))
    pessoas, contagem = [], {'todos': 0, 'com': 0, 'sem': 0, 'super': 0}
    for u in usuarios:
        a = acessos_.get(u.id)
        grupo = 'super' if is_superadmin(u) else ('com' if a else 'sem')
        contagem['todos'] += 1
        contagem[grupo] += 1
        pessoas.append({'u': u, 'acesso': a, 'grupo': grupo, 'chaves': set(a.permissoes or []) if a else set()})
    return render(request, 'fibras/acessos.html', {
        'pessoas': pessoas,
        'contagem': contagem,
        'funcoes': FUNCOES,
        'historico': HistoricoAcessoFibras.objects.all()[:40],
    })
