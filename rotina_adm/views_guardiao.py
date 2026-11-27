"""Telas da Missão Guardião (26/11/2026) dentro da Rotina ADM. Regras em ``guardiao.py``.

Acesso igual ao resto da Rotina ADM: o Visualizador vê a própria missão (a loja é o setor principal) e
registra o que é da loja dele; Gestor e SUPERADMIN escolhem o Guardião (?pessoa=) e lançam o Estoque Total.
"""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from users.models import User

from . import guardiao, ranking, servicos
from .models import ContagemEstoque, LancamentoGuardiao, MissaoAgenda
from .permissoes import e_superadmin, exige_acesso, papel, pode_gerir

ABAS = (
    ('visao', 'Visão geral', 'fa-gem'),
    ('missoes', 'Minhas missões', 'fa-check'),
    ('lvut', 'Proteção LVUT', 'fa-shield-alt'),
    ('agenda', 'Agenda de Missões', 'fa-calendar-alt'),
    ('destaque', 'Guardiões em Destaque', 'fa-star'),
)


def _pessoa(request):
    """Visualizador: ele mesmo. Gestor/SUPERADMIN: o pedido, ou ele (se é de loja), ou o primeiro Guardião."""
    if not pode_gerir(request.user):
        return request.user
    pedido = request.GET.get('pessoa') or request.POST.get('pessoa') or ''
    if pedido.isdigit():
        p = User.objects.filter(pk=int(pedido), is_active=True).select_related('sector').first()
        if p:
            return p
    if servicos.e_loja(request.user.sector):
        return request.user
    return guardiao.guardioes().order_by('first_name', 'last_name').first() or request.user


def _mes(request):
    return (request.GET.get('mes') or request.POST.get('mes') or '').strip()


def _url(aba, pessoa=None, mes='', ancora=''):
    nome = 'rotina_adm:guardiao' if aba == 'visao' else f'rotina_adm:guardiao_{aba}'
    params = {}
    if pessoa is not None:
        params['pessoa'] = pessoa.id
    if mes:
        params['mes'] = mes
    return reverse(nome) + (f'?{urlencode(params)}' if params else '') + (f'#{ancora}' if ancora else '')


def _volta(request, pessoa, padrao='visao'):
    aba = request.POST.get('aba') or padrao
    if aba not in dict((a[0], a) for a in ABAS):
        aba = padrao
    return redirect(_url(aba, pessoa if pode_gerir(request.user) else None, _mes(request),
                         request.POST.get('ancora') or ''))


def _pode_marcar(user, loja):
    return pode_gerir(user) or (loja is not None and user.sector_id == loja.id)


@login_required
@exige_acesso()
def guardiao_tela(request, aba='visao'):
    pessoa = _pessoa(request)
    mes = _mes(request)
    gestor = pode_gerir(request.user)
    dados = guardiao.calcular(pessoa, mes)
    mes_escolhido = f'{dados["inicio"].year:04d}-{dados["inicio"].month:02d}'
    contexto = {
        **dados, 'aba': 'guardiao', 'aba_g': aba, 'abas': ABAS, 'gestor': gestor,
        'superadmin': e_superadmin(request.user), 'papel': papel(request.user),
        'mes': mes_escolhido, 'meses': ranking.meses_para_escolher(),
        'mes_atual': dados['inicio'] == timezone.localdate().replace(day=1),
        'q': urlencode({**({'pessoa': pessoa.id} if gestor else {}), **({'mes': mes} if mes else {})}),
        'proprio': pessoa.id == request.user.id,
        'pode_marcar': _pode_marcar(request.user, dados['loja']),
        'categorias': ContagemEstoque.CATEGORIAS, 'origens': MissaoAgenda.ORIGENS,
        'por_contagem': guardiao.fmt(guardiao.LVUT_POR_CONTAGEM),
        'pilares_def': guardiao.PILARES,
    }
    sufixo = f'?{contexto["q"]}' if contexto['q'] else ''
    for c in dados.get('cartoes', []):
        if c['manual']:
            c['href'] = reverse('rotina_adm:guardiao_agenda') + sufixo
        elif c['link'] == 'lvut':
            c['href'] = reverse('rotina_adm:guardiao_lvut') + sufixo
        elif c['link'].startswith('missoes#'):
            c['href'] = reverse('rotina_adm:guardiao_missoes') + sufixo + '#' + c['link'][8:]
        else:
            c['href'] = c['link']
    if gestor:
        contexto['guardioes'] = list(guardiao.guardioes().order_by('first_name', 'last_name'))
        if pessoa not in contexto['guardioes']:
            contexto['guardioes'].insert(0, pessoa)
    if aba == 'destaque':
        rk = guardiao.ranking_do_mes(mes)
        contexto['ranking'] = rk['linhas']
        contexto['podio'] = [l for l in rk['linhas'][:3] if l['total'] > 0]
        contexto['minha_linha'] = next((l for l in rk['linhas'] if l['pessoa'].id == pessoa.id), None)
        # Embaixo do pódio, as barras dos demais (com a pessoa em destaque); ao lado, as faixas (NIVEIS).
        contexto['demais'] = rk['linhas'][len(contexto['podio']):]
        contexto['faixas'] = guardiao.faixas(dados['total'])
        contexto['marcos'] = [n[0] for n in guardiao.NIVEIS[1:] if n[0] < 100]  # 100 é a borda da barra
        lider = rk['linhas'][0]['total'] if rk['linhas'] else 0
        for l in rk['linhas']:
            l['atras_do_lider'] = guardiao.fmt(max(lider - l['total'], 0))
            l['pct_total'] = round(min(max(l['total'], 0), 100), 1)
            for pl in l['pilares']:
                pl['w'] = round(pl['pct'] * pl['maximo'] / 100, 2)  # largura na barra de 0 a 100 pontos
    if aba == 'lvut' and dados['loja'] is not None:
        contexto['estoque_semana'] = servicos.contagem_estoque(dados['loja'])
    if aba == 'agenda':
        hoje = timezone.localdate()
        if dados['loja'] is not None:
            contexto['checklist_hoje'] = servicos.itens_do_dia(dados['loja'], hoje)
            contexto['checklist_feitos'] = sum(1 for l in contexto['checklist_hoje'] if l['marcacao'])
        agenda = servicos.agenda_da_semana(pessoa, hoje - timedelta(days=hoje.weekday()))
        contexto['rotina_hoje'] = next((d for d in agenda['dias'] if d['hoje']), None) if agenda else None
    if not dados['loja']:
        contexto['sem_loja'] = True
    return render(request, f'rotina_adm/guardiao/{aba}.html', contexto)


def guardiao_codigo(request):
    """/rotina-adm/guardiao/codigo/ saiu em 27/11/2026: o link antigo cai na Visão geral (com a pessoa e o mês)."""
    destino = reverse('rotina_adm:guardiao')
    return redirect(destino + (f'?{request.GET.urlencode()}' if request.GET else ''))


@login_required
@exige_acesso()
@require_POST
def guardiao_lancar(request):
    """CheckLojas (a loja registra) e Estoque Total (quem gere lança o %)."""
    pessoa = _pessoa(request)
    loja = pessoa.sector if servicos.e_loja(pessoa.sector) else None
    acao = request.POST.get('acao') or ''
    gestor = pode_gerir(request.user)
    inicio, fim, hoje = guardiao.periodo(_mes(request))
    if loja is None:
        messages.error(request, 'Este Guardião não tem loja no setor principal.')
        return _volta(request, pessoa, 'missoes')

    if acao == 'checklojas':
        if not _pode_marcar(request.user, loja):
            messages.error(request, 'Você só registra o CheckLojas da sua loja.')
        elif inicio != hoje.replace(day=1) and not gestor:
            messages.error(request, 'O CheckLojas se registra dentro do próprio mês.')
        else:
            data = hoje if inicio == hoje.replace(day=1) else _data(request.POST.get('data'), fim)
            LancamentoGuardiao.objects.update_or_create(
                loja=loja, competencia=inicio, missao=LancamentoGuardiao.CHECKLOJAS,
                defaults={'data': min(data, fim), 'observacao': (request.POST.get('observacao') or '').strip()[:2000],
                          'lancado_por': request.user})
            messages.success(request, f'CheckLojas de {inicio:%m/%Y} registrado: +5 pontos na Missão Guardião!')
    elif acao == 'estoque_total':
        if not gestor:
            messages.error(request, 'Só quem gere lança o resultado do inventário.')
        else:
            try:
                valor = Decimal((request.POST.get('valor') or '').replace(',', '.').strip())
            except InvalidOperation:
                valor = None
            if valor is None or not (0 <= valor <= 100):
                messages.error(request, 'Informe a conformidade em % (de 0 a 100).')
            else:
                LancamentoGuardiao.objects.update_or_create(
                    loja=loja, competencia=inicio, missao=LancamentoGuardiao.ESTOQUE_TOTAL,
                    defaults={'data': _data(request.POST.get('data'), min(hoje, fim)), 'valor': valor.quantize(Decimal('0.01')),
                              'observacao': (request.POST.get('observacao') or '').strip()[:2000],
                              'lancado_por': request.user})
                messages.success(request, f'Inventário de {loja.name} em {inicio:%m/%Y}: {guardiao.fmt(valor)}% lançado.')
    elif acao in ('remover_checklojas', 'remover_estoque_total'):
        if not gestor:
            messages.error(request, 'Só quem gere remove um lançamento.')
        else:
            missao = LancamentoGuardiao.CHECKLOJAS if acao == 'remover_checklojas' else LancamentoGuardiao.ESTOQUE_TOTAL
            LancamentoGuardiao.objects.filter(loja=loja, competencia=inicio, missao=missao).delete()
            messages.success(request, 'Lançamento removido.')
    else:
        messages.error(request, 'Ação inválida.')
    return _volta(request, pessoa, 'missoes')


def _data(texto, padrao):
    try:
        return date.fromisoformat(texto or '')
    except ValueError:
        return padrao


@login_required
@exige_acesso()
@require_POST
def guardiao_contagem(request):
    """Registra a contagem LVUT de uma categoria (hoje), pela tela da Proteção LVUT."""
    pessoa = _pessoa(request)
    loja = pessoa.sector if servicos.e_loja(pessoa.sector) else None
    if loja is None or not _pode_marcar(request.user, loja):
        messages.error(request, 'Você só registra a contagem da sua loja.')
        return _volta(request, pessoa, 'lvut')
    categoria = request.POST.get('categoria') or ''
    itens, divergencias, erro = servicos.numeros_da_contagem(request.POST)
    if categoria not in dict(ContagemEstoque.CATEGORIAS):
        messages.error(request, 'Escolha a categoria contada.')
    elif erro:
        messages.error(request, erro)
    else:
        c = ContagemEstoque.objects.create(loja=loja, categoria=categoria, data=timezone.localdate(),
                                           divergencias=divergencias, itens_contados=itens, feito_por=request.user,
                                           observacao=(request.POST.get('observacao') or '').strip()[:2000])
        if c.divergencias:
            messages.success(request, f'Contagem de {c.get_categoria_display()} registrada com {c.divergencias} '
                                      'divergência(s): a conquista fica bloqueada e o Protocolo de Recuperação perde pontos até a '
                                      'regularização.')
        else:
            messages.success(request, f'Contagem de {c.get_categoria_display()} registrada sem divergência: '
                                      f'+{guardiao.fmt(guardiao.LVUT_POR_CONTAGEM)} ponto conquistado!')
    return _volta(request, pessoa, 'lvut')


@login_required
@exige_acesso()
@require_POST
def guardiao_regularizar(request, contagem_id):
    """Dá as divergências de uma contagem como regularizadas (o número original fica)."""
    contagem = get_object_or_404(ContagemEstoque, pk=contagem_id)
    pessoa = _pessoa(request)
    if not _pode_marcar(request.user, contagem.loja):
        messages.error(request, 'Você só regulariza a contagem da sua loja.')
    elif not contagem.divergencias or contagem.regularizada_em:
        messages.error(request, 'Esta contagem não tem divergência em aberto.')
    else:
        texto = (request.POST.get('regularizacao') or '').strip()[:2000]
        if not texto:
            messages.error(request, 'Conte o que foi feito para regularizar (é a evidência).')
        else:
            contagem.regularizada_em = timezone.now()
            contagem.regularizada_por = request.user
            contagem.regularizacao = texto
            contagem.save(update_fields=['regularizada_em', 'regularizada_por', 'regularizacao'])
            messages.success(request, f'Divergências de {contagem.get_categoria_display()} regularizadas: '
                                      'conquista recuperada!')
    return _volta(request, pessoa, 'lvut')


@login_required
@exige_acesso()
@require_POST
def guardiao_missao(request):
    """Agenda de Missões: incluir, mover entre colunas e excluir missão manual."""
    pessoa = _pessoa(request)
    acao = request.POST.get('acao') or ''
    gestor = pode_gerir(request.user)
    if acao == 'nova':
        if pessoa.id != request.user.id and not gestor:
            messages.error(request, 'Você só inclui missões na sua agenda.')
            return _volta(request, pessoa, 'agenda')
        titulo = (request.POST.get('titulo') or '').strip()[:200]
        prazo = _data(request.POST.get('prazo'), None)
        origem = request.POST.get('origem') if request.POST.get('origem') in dict(MissaoAgenda.ORIGENS) else MissaoAgenda.GERENTE
        if not titulo or prazo is None:
            messages.error(request, 'Escreva a missão e o prazo.')
        else:
            MissaoAgenda.objects.create(pessoa=pessoa, titulo=titulo, origem=origem, prazo=prazo, criado_por=request.user)
            messages.success(request, 'Missão incluída na agenda.')
        return _volta(request, pessoa, 'agenda')

    obj = get_object_or_404(MissaoAgenda, pk=request.POST.get('missao') or 0)
    if obj.pessoa_id != request.user.id and not gestor:
        messages.error(request, 'Esta missão não é sua.')
    elif acao == 'mover':
        status = request.POST.get('status')
        if status in dict(MissaoAgenda.STATUS):
            obj.status = status
            obj.cumprida_em = timezone.now() if status == MissaoAgenda.CUMPRIDA else None
            obj.save(update_fields=['status', 'cumprida_em', 'atualizado_em'])
    elif acao == 'excluir':
        obj.delete()
        messages.success(request, 'Missão excluída.')
    return _volta(request, obj.pessoa, 'agenda')
