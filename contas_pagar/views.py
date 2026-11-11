import json
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from users.models import User

from . import mysql, regras, servicos
from .models import (AcessoContasPagar, Categoria, ConfiguracaoContasPagar, ContaBancaria, LogContasPagar,
                     Rascunho)
from .permissoes import (CONFIGURADOR, CONSULTA, OPERADOR, SUPERADMIN, contexto, e_superadmin, exige,
                         pode_operar)

MESES = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro',
         'outubro', 'novembro', 'dezembro']
DIAS = ['segunda-feira', 'terça-feira', 'quarta-feira', 'quinta-feira', 'sexta-feira', 'sábado', 'domingo']
DIAS_CURTOS = ['seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom']
MAX_DIAS_PAINEL = 400
MAX_ARQUIVO = 15 * 1024 * 1024


def _ctx(request, aba, **extra):
    c = contexto(request.user)
    c['aba'] = aba
    c.update(extra)
    return c


def _data(v):
    try:
        return date.fromisoformat(str(v or '')[:10])
    except ValueError:
        return None


# ── Painel ─────────────────────────────────────────────────────────────────
def _rotulo_periodo(modo, ini, fim, hoje):
    if ini == fim:
        texto = f'{DIAS[ini.weekday()]}, {ini.day:02d} de {MESES[ini.month - 1]} de {ini.year}'
        return ('Hoje · ' if ini == hoje else '') + texto[:1].upper() + texto[1:]
    if modo == 'mes':
        return f'{MESES[ini.month - 1].capitalize()} de {ini.year}'
    return f'{ini:%d/%m/%Y} a {fim:%d/%m/%Y}'


def _periodo_pedido(request, hoje):
    modo = request.GET.get('modo') or 'hoje'
    if modo not in regras.MODOS:
        modo = 'hoje'
    ini, fim = _data(request.GET.get('ini')), _data(request.GET.get('fim'))
    if modo == 'hoje' and not ini:
        ini = fim = hoje
    ini = ini or hoje
    fim = fim or ini
    if fim < ini:
        ini, fim = fim, ini
    if modo in ('semana', 'quinzena', 'mes'):
        ini, fim = regras.periodo(modo, ini)
    elif modo == 'hoje' and not (ini == fim == hoje):
        modo = 'custom'
    if (fim - ini).days > MAX_DIAS_PAINEL:
        fim = ini + timedelta(days=MAX_DIAS_PAINEL)
    return modo, ini, fim


@login_required
@exige(CONSULTA)
def painel(request):
    hoje = timezone.localdate()
    modo, ini, fim = _periodo_pedido(request, hoje)
    filtros = {k: (request.GET.get(k) or '').strip() for k in ('banco', 'categoria', 'filial')}

    def link(**mudar):
        q = {'modo': modo, 'ini': ini.isoformat(), 'fim': fim.isoformat(), **{k: v for k, v in filtros.items() if v}}
        q.update(mudar)
        q = {k: v for k, v in q.items() if v not in (None, '')}
        return reverse('contas_pagar:painel') + '?' + urlencode(q)

    def alternar(chave, valor):
        return link(**{chave: '' if filtros[chave] == valor else valor})

    modos = []
    for m, rotulo in (('hoje', 'Hoje'), ('semana', 'Semana'), ('quinzena', 'Quinzena'), ('mes', 'Mês')):
        a, b = regras.periodo(m, hoje if m == 'hoje' else ini)
        modos.append({'modo': m, 'rotulo': rotulo, 'ativo': m == modo,
                      'url': reverse('contas_pagar:painel') + '?' + urlencode({'modo': m, 'ini': a.isoformat(),
                                                                               'fim': b.isoformat()})})
    setas = {}
    for nome, d in (('anterior', -1), ('seguinte', 1)):
        m2, a, b = regras.deslocar(modo, ini, fim, d, hoje)
        setas[nome] = link(modo=m2, ini=a.isoformat(), fim=b.isoformat())

    c = _ctx(request, 'painel', modo=modo, ini=ini, fim=fim, hoje=hoje, modos=modos, setas=setas, filtros=filtros,
             rotulo=_rotulo_periodo(modo, ini, fim, hoje), limpar=link(banco='', categoria='', filial=''))
    try:
        dados = servicos.painel(ini, fim)
        dias_programados = [h['data_pagamento'].isoformat() for h in servicos.historico() if h['data_pagamento']]
    except mysql.ContasPagarIndisponivel as exc:
        c['erro'] = str(exc)
        return render(request, 'contas_pagar/painel.html', c)

    todos = dados['titulos']
    titulos = sorted(regras.filtrar_titulos(todos, **filtros), key=lambda t: t['valor'])  # como o original: por valor
    tot = regras.totais_bancos(dados['bancos'])
    multi = len(dados['dias']) > 1 or ini != fim
    for b in dados['bancos']:
        b['url'] = alternar('banco', b['banco'])
        b['ativo'] = filtros['banco'] == b['banco']
        b['em_conta'] = b['saldo'] + b['garantida']
    # categorias e filiais: somam os títulos já filtrados pelos outros dois cliques
    por_cat = [{'rotulo': k, 'qtd': q, 'valor': v, 'url': alternar('categoria', k), 'ativo': filtros['categoria'] == k}
               for k, q, v in regras.agrupar(regras.filtrar_titulos(todos, banco=filtros['banco'],
                                                                    filial=filtros['filial']), 'tipo_macro')]
    por_fil = [{'rotulo': k, 'qtd': q, 'valor': v, 'url': alternar('filial', k), 'ativo': filtros['filial'] == k}
               for k, q, v in regras.agrupar(regras.filtrar_titulos(todos, banco=filtros['banco'],
                                                                    categoria=filtros['categoria']), 'filial')]
    for t in titulos:
        t['url_cat'] = alternar('categoria', t['tipo_macro'])
        t['url_fil'] = alternar('filial', t['filial'])
        t['url_banco'] = alternar('banco', t['banco'])
        t['banco_nome'] = regras.separar_banco(t['banco'])[0]
    dias = []
    if multi:
        por_dia = {}
        for t in todos:
            por_dia[t['data_pagamento']] = por_dia.get(t['data_pagamento'], regras.ZERO) + t['valor']
        maior = max(por_dia.values(), default=regras.ZERO) or 1
        d = ini
        while d <= fim and len(dias) < 62:
            v = por_dia.get(d.isoformat(), regras.ZERO)
            dias.append({'data': d, 'valor': v, 'pct': float(v / maior * 100) if v else 0,
                         'semana': DIAS_CURTOS[d.weekday()], 'url': link(modo='hoje' if d == hoje else 'custom',
                                                                         ini=d.isoformat(), fim=d.isoformat())})
            d += timedelta(days=1)
    c.update({'dados': dados, 'titulos': titulos, 'tot': tot, 'multi': multi, 'por_cat': por_cat, 'por_fil': por_fil,
              'dias': dias, 'dias_curtos': len(dias) <= 16, 'qtd_todos': len(todos),
              'valor_filtrado': sum((t['valor'] for t in titulos), regras.ZERO),
              'algum_filtro': any(filtros.values()), 'dias_programados': dias_programados,
              'banco_filtro_nome': regras.separar_banco(filtros['banco'])[0]})
    return render(request, 'contas_pagar/painel.html', c)


@login_required
@exige(CONSULTA, api=True)
@require_GET
def api_painel(request):
    """O painel em JSON (mesmos parâmetros da tela: ini, fim, banco, categoria, filial, busca)."""
    hoje = timezone.localdate()
    _, ini, fim = _periodo_pedido(request, hoje)
    try:
        d = servicos.painel(ini, fim)
    except mysql.ContasPagarIndisponivel as exc:
        return JsonResponse({'erro': str(exc)}, status=503)
    d['titulos'] = regras.filtrar_titulos(d['titulos'], banco=request.GET.get('banco'),
                                          categoria=request.GET.get('categoria'), filial=request.GET.get('filial'),
                                          busca=request.GET.get('busca', ''))
    d['totais'] = regras.totais_bancos(d['bancos'])
    return JsonResponse(_json(d))


def _json(v):
    if isinstance(v, dict):
        return {k: _json(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json(x) for x in v]
    if isinstance(v, regras.Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


# ── Pagar hoje ─────────────────────────────────────────────────────────────
def _meus_rascunhos(user):
    qs = Rascunho.objects.select_related('user').order_by('-id')
    return qs if e_superadmin(user) else qs.filter(user=user)


def _rascunho(request, pk):
    r = Rascunho.objects.filter(pk=pk).first()
    if r is None or not (r.user_id == request.user.id or e_superadmin(request.user)):
        raise Http404('Rascunho não encontrado.')
    return r


@login_required
@exige(OPERADOR)
def pagar_hoje(request):
    hoje = timezone.localdate()
    pub_hoje = None
    try:
        pub_hoje = next((h for h in servicos.historico() if h['data_pagamento'] == hoje), None)
    except mysql.ContasPagarIndisponivel as exc:
        messages.warning(request, str(exc))
    return render(request, 'contas_pagar/pagar_hoje.html', _ctx(
        request, 'pagar', rascunhos=list(_meus_rascunhos(request.user)[:10]), pub_hoje=pub_hoje))


@login_required
@exige(OPERADOR)
def pagar_hoje_montar(request, pk):
    r = _rascunho(request, pk)
    return render(request, 'contas_pagar/montar.html', _ctx(request, 'pagar', rascunho=r))


@login_required
@exige(OPERADOR, api=True)
@require_POST
def api_importar(request):
    arq = request.FILES.get('arquivo')
    if not arq:
        return JsonResponse({'erro': 'Escolha o arquivo da BASE VIVOGO.'}, status=400)
    if arq.size > MAX_ARQUIVO:
        return JsonResponse({'erro': 'Arquivo grande demais (máx. 15 MB).'}, status=400)
    nome = arq.name or 'arquivo.csv'
    if not nome.lower().endswith(('.csv', '.xlsx', '.xlsm', '.txt')):
        return JsonResponse({'erro': 'Envie o relatório em .csv ou .xlsx.'}, status=400)
    try:
        r, aviso = servicos.importar(request.user, nome, arq.read())
    except regras.ArquivoInvalido as exc:
        return JsonResponse({'erro': str(exc)}, status=400)
    return JsonResponse({'id': r.id, 'aviso': aviso,
                         'url': reverse('contas_pagar:pagar_hoje_montar', args=[r.id])})


@login_required
@exige(OPERADOR, api=True)
def api_rascunho(request, pk):
    r = _rascunho(request, pk)
    if request.method == 'GET':
        return JsonResponse(servicos.detalhe(r))
    if request.method != 'POST':
        return JsonResponse({'erro': 'Método não permitido.'}, status=405)
    try:
        dados = json.loads(request.body or b'{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'erro': 'Envio inválido.'}, status=400)
    try:
        servicos.salvar(r, dados)
    except ValueError as exc:
        return JsonResponse({'erro': str(exc)}, status=400)
    if dados.get('status') == 'publicado':
        r.refresh_from_db()
        try:
            res = servicos.publicar(r, request.user)
        except mysql.ContasPagarIndisponivel as exc:
            return JsonResponse({'erro': str(exc) + ' O rascunho foi salvo.'}, status=503)
        res['ok'] = True
        res['url'] = reverse('contas_pagar:painel') + '?' + urlencode(
            {'modo': 'hoje' if res['data_pagamento'] == timezone.localdate().isoformat() else 'custom',
             'ini': res['data_pagamento'], 'fim': res['data_pagamento']})
        return JsonResponse(res)
    return JsonResponse({'ok': True})


@login_required
@exige(OPERADOR, api=True)
@require_POST
def api_descartar(request, pk):
    r = _rascunho(request, pk)
    r.delete()
    return JsonResponse({'ok': True})


@login_required
@exige(OPERADOR)
@require_POST
def descartar(request, pk):
    r = _rascunho(request, pk)
    r.delete()
    messages.success(request, 'Rascunho descartado.')
    return redirect(request.POST.get('volta') == 'historico' and 'contas_pagar:historico' or 'contas_pagar:pagar_hoje')


# ── Histórico ──────────────────────────────────────────────────────────────
@login_required
@exige(CONSULTA)
def historico(request):
    c = _ctx(request, 'historico', dias=[], rascunhos=[])
    try:
        dias = servicos.historico()
    except mysql.ContasPagarIndisponivel as exc:
        c['erro'] = str(exc)
        dias = []
    for h in dias:
        d = h['data_pagamento']
        h['semana'] = DIAS_CURTOS[d.weekday()] if d else ''
        h['url'] = reverse('contas_pagar:painel') + '?' + urlencode(
            {'modo': 'custom', 'ini': d.isoformat(), 'fim': d.isoformat()}) if d else ''
        p = h.get('publicado_em')
        if isinstance(p, str):
            try:
                h['publicado_em'] = datetime.fromisoformat(p)
            except ValueError:
                pass
    c['dias'] = dias
    if pode_operar(request.user):
        c['rascunhos'] = list(_meus_rascunhos(request.user)[:50])
    c['log'] = LogContasPagar.objects.select_related('user')[:30]
    return render(request, 'contas_pagar/historico.html', c)


@login_required
@exige(OPERADOR)
@require_POST
def historico_excluir(request):
    dia = _data(request.POST.get('dia'))
    if not dia:
        messages.error(request, 'Data inválida.')
        return redirect('contas_pagar:historico')
    try:
        antes = servicos.excluir_dia(dia, request.user)
    except mysql.ContasPagarIndisponivel as exc:
        messages.error(request, str(exc))
        return redirect('contas_pagar:historico')
    messages.success(request, f'{dia:%d/%m/%Y} excluído do histórico ({int(antes.get("qtd") or 0)} títulos).')
    return redirect('contas_pagar:historico')


# ── Configurações ──────────────────────────────────────────────────────────
ABAS_CONFIG = ('bancos', 'categorias', 'regras')


@login_required
@exige(CONFIGURADOR)
def configuracoes(request):
    aba = request.GET.get('aba') or request.POST.get('aba') or 'bancos'
    if aba not in ABAS_CONFIG:
        aba = 'bancos'
    if request.method == 'POST':
        _configuracoes_post(request)
        return redirect(reverse('contas_pagar:configuracoes') + '?aba=' + aba)
    cats = list(Categoria.objects.order_by('tipo'))
    c = _ctx(request, 'config', aba_config=aba, bancos=list(ContaBancaria.objects.order_by('ordem', 'id')),
             categorias=cats, opcoes=sorted({x.categoria for x in cats}), cfg=ConfiguracaoContasPagar.atual())
    if aba == 'categorias':
        c['faltando'] = servicos.sem_categoria()
    return render(request, 'contas_pagar/configuracoes.html', c)


def _configuracoes_post(request):
    acao = request.POST.get('acao')
    P = request.POST
    if acao == 'conta_adicionar':
        banco, conta = (P.get('banco') or '').strip().upper()[:200], (P.get('conta') or '').strip()[:30]
        if not banco or not conta:
            messages.error(request, 'Informe o banco e o número da conta.')
            return
        ordem = (ContaBancaria.objects.aggregate(m=Max('ordem'))['m'] or 0) + 1
        ContaBancaria.objects.create(banco=banco, agencia=(P.get('agencia') or '').strip()[:20], conta=conta,
                                     ordem=ordem)
        messages.success(request, 'Conta adicionada.')
    elif acao in ('conta_salvar', 'conta_remover', 'conta_subir', 'conta_descer'):
        conta = get_object_or_404(ContaBancaria, pk=P.get('id'))
        if acao == 'conta_salvar':
            banco = (P.get('banco') or '').strip().upper()[:200]
            if not banco:
                messages.error(request, 'Informe o nome do banco.')
                return
            conta.banco = banco
            conta.agencia = (P.get('agencia') or '').strip()[:20]
            conta.conta = (P.get('conta') or '').strip()[:30]
            conta.ativo = P.get('ativo') == 'on'
            conta.save()
            messages.success(request, 'Conta atualizada.')
        elif acao == 'conta_remover':
            conta.delete()
            messages.success(request, 'Conta removida. Os títulos dela passam a aparecer como “Não informado”.')
        else:
            contas = list(ContaBancaria.objects.order_by('ordem', 'id'))
            i = contas.index(conta)
            j = i - 1 if acao == 'conta_subir' else i + 1
            if 0 <= j < len(contas):
                contas[i], contas[j] = contas[j], contas[i]
                for n, ct in enumerate(contas):
                    if ct.ordem != n:
                        ContaBancaria.objects.filter(pk=ct.pk).update(ordem=n)
    elif acao == 'categoria_salvar':
        tipo, cat = (P.get('tipo') or '').strip()[:255], (P.get('categoria') or '').strip()[:120]
        if not tipo or not cat:
            messages.error(request, 'Informe o tipo de conta e a categoria.')
            return
        with transaction.atomic():
            antigo = (P.get('tipo_antigo') or '').strip()
            if antigo and antigo != tipo:
                Categoria.objects.filter(tipo=antigo).delete()
            Categoria.objects.update_or_create(tipo=tipo, defaults={'categoria': cat})
        messages.success(request, 'Categoria salva.')
    elif acao == 'categoria_remover':
        Categoria.objects.filter(tipo=P.get('tipo') or '').delete()
        messages.success(request, 'Categoria removida.')
    elif acao == 'categoria_importar':
        arq = request.FILES.get('arquivo')
        if not arq:
            messages.error(request, 'Escolha a planilha.')
            return
        try:
            pares = regras.ler_aba_dados(arq.read())
        except regras.ArquivoInvalido as exc:
            messages.error(request, str(exc))
            return
        with transaction.atomic():
            for tipo, cat in pares:
                Categoria.objects.update_or_create(tipo=tipo[:255], defaults={'categoria': cat[:120]})
        messages.success(request, f'{len(pares)} categorias importadas.')
    elif acao == 'regras_salvar':
        cfg = ConfiguracaoContasPagar.atual()
        cfg.agging_despesa_contem = (P.get('agging_despesa_contem') or '')[:120]
        cfg.agging_fornecedor_contem = (P.get('agging_fornecedor_contem') or '')[:120]
        cfg.atualizado_por = request.user
        cfg.save()
        messages.success(request, 'Regras salvas.')
    else:
        messages.error(request, 'Ação desconhecida.')


# ── Acessos ────────────────────────────────────────────────────────────────
@login_required
@exige(SUPERADMIN)
def acessos(request):
    papeis = dict(AcessoContasPagar.PAPEIS)
    if request.method == 'POST':
        acao = request.POST.get('acao')
        novo = request.POST.get('papel')
        if novo not in papeis:
            novo = AcessoContasPagar.CONSULTA
        if acao == 'adicionar':
            pk = request.POST.get('user') or ''
            if pk.isdigit() and User.objects.filter(pk=pk, is_active=True).exists():
                try:
                    AcessoContasPagar.objects.update_or_create(user_id=int(pk),
                                                               defaults={'papel': novo, 'criado_por': request.user})
                    messages.success(request, 'Pessoa liberada.')
                except IntegrityError:
                    messages.error(request, 'Não deu para liberar essa pessoa.')
            else:
                messages.error(request, 'Escolha uma pessoa.')
        elif acao in ('papel', 'remover'):
            a = get_object_or_404(AcessoContasPagar, pk=request.POST.get('acesso'))
            if acao == 'remover':
                a.delete()
                messages.success(request, 'Acesso removido.')
            else:
                a.papel = novo
                a.save(update_fields=['papel', 'atualizado_em'])
                messages.success(request, 'Papel atualizado.')
        return redirect('contas_pagar:acessos')
    lista = list(AcessoContasPagar.objects.select_related('user', 'user__sector').order_by('user__first_name',
                                                                                         'user__last_name'))
    usados = {a.user_id for a in lista}
    return render(request, 'contas_pagar/acessos.html', _ctx(
        request, 'acessos', acessos=lista, papeis=AcessoContasPagar.PAPEIS,
        usuarios=User.objects.filter(is_active=True).exclude(pk__in=usados).select_related('sector')
                             .order_by('first_name', 'last_name')))
