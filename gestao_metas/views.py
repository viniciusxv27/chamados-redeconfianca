import json
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from communications.models import CommunicationGroup
from users.models import Sector, User

from . import exportar, importacao, servicos
from .models import (PILARES, REGIOES, AcessoGestaoMetas, Competencia, HistoricoMeta, ImportacaoMetas, LojaMeta,
                     MetaLoja, Realizado)
from .permissoes import SUPERADMIN, e_superadmin, exige, papel, pode_editar


def _base(request, **extra):
    p = papel(request.user)
    contexto = {'gm_papel': p, 'gm_editor': p in (SUPERADMIN, 'EDITOR'), 'gm_superadmin': p == SUPERADMIN}
    contexto.update(extra)
    return contexto


def _competencia(codigo):
    try:
        mes = servicos.mes_do_codigo(codigo)
    except (ValueError, TypeError):
        from django.http import Http404
        raise Http404
    return get_object_or_404(Competencia, mes=mes)


# ---------------------------------------------------------------------------
# Início: competências
# ---------------------------------------------------------------------------
@login_required
@exige()
def inicio(request):
    servicos.garantir_lojas()
    competencias = list(Competencia.objects.annotate(n_metas=Count('metas')).order_by('-mes'))
    hoje = timezone.localdate()
    sugestao = servicos.somar_meses(competencias[0].mes, 1) if competencias else date(hoje.year, hoje.month, 1)
    meses_com_dado = (Realizado.objects.values('mes').annotate(n=Count('id')).order_by('-mes')[:24])
    return render(request, 'gestao_metas/inicio.html', _base(
        request, aba='inicio', competencias=competencias, sugestao=sugestao.strftime('%Y-%m'),
        meses_com_dado=meses_com_dado, n_lojas=LojaMeta.objects.filter(ativa=True).count(),
        importacoes=ImportacaoMetas.objects.select_related('competencia', 'criado_por')[:6]))


@login_required
@exige(editar=True)
@require_POST
def abrir(request):
    try:
        mes = servicos.mes_do_codigo(request.POST.get('mes'))
    except (ValueError, TypeError):
        messages.error(request, 'Escolha o mês.')
        return redirect('gestao_metas:inicio')
    competencia, criada = servicos.abrir_competencia(mes, request.user)
    if criada:
        messages.success(request, 'Competência {} aberta.'.format(servicos.rotulo_mes(mes, True)))
    return redirect('gestao_metas:competencia', codigo=competencia.codigo)


# ---------------------------------------------------------------------------
# Tela da competência
# ---------------------------------------------------------------------------
@login_required
@exige()
def competencia(request, codigo):
    comp = _competencia(codigo)
    dados, ctx = servicos.montar(comp)
    pilares = [{'chave': p, 'nome': nome, **dados[p]} for p, nome in PILARES]
    anterior = Competencia.objects.filter(mes__lt=comp.mes).order_by('-mes').first()
    seguinte = Competencia.objects.filter(mes__gt=comp.mes).order_by('mes').first()
    faltando = []
    for mes in ctx['meses_quad'] + [ctx['mes_ano']]:
        if not Realizado.objects.filter(mes=mes).exists():
            faltando.append(mes)
    return render(request, 'gestao_metas/competencia.html', _base(
        request, aba='competencia', comp=comp, pilares=pilares, ctx=ctx,
        titulo_mes=servicos.rotulo_mes(comp.mes, True), anterior=anterior, seguinte=seguinte,
        meses_faltando=faltando, origens=servicos.ORIGENS.items(),
        pode_editar=pode_editar(request.user) and not comp.fechada,
        rotulos_quad=[servicos.rotulo_mes(m) for m in ctx['meses_quad']],
        rotulo_ano=servicos.rotulo_mes(ctx['mes_ano']), rotulo_comp=servicos.rotulo_mes(comp.mes), rotulo_ant=servicos.rotulo_mes(ctx['mes_anterior'])))


def _json_linha(linha):
    if linha is None:
        return None
    chaves = ('meta', 'alvo', 'mxm', 'yxy', 'mxquad', 'mxmeta', 'qtd', 'cima', 'baixo', 'nova_meta', 'qtd_cns',
              'decisao', 'ticket')
    return {k: linha.get(k) for k in chaves if k in linha}


@login_required
@exige(editar=True)
@require_POST
def salvar(request, codigo):
    comp = _competencia(codigo)
    try:
        corpo = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'ok': False, 'erro': 'Pedido inválido.'}, status=400)
    if comp.fechada:
        return JsonResponse({'ok': False, 'erro': 'A competência está fechada: reabra para mudar.'}, status=409)
    loja = LojaMeta.objects.filter(pk=corpo.get('loja')).first()
    if loja is None:
        return JsonResponse({'ok': False, 'erro': 'Loja não encontrada.'}, status=400)
    try:
        servicos.salvar_campo(comp, loja, corpo.get('pilar'), corpo.get('campo'), corpo.get('valor'), request.user)
    except (ValueError, PermissionError) as exc:
        return JsonResponse({'ok': False, 'erro': str(exc)}, status=400)
    linha = servicos.linha_da_loja(comp, loja, corpo.get('pilar'))
    return JsonResponse({'ok': True, 'linha': _json_linha(linha),
                         'salvo_em': timezone.localtime().strftime('%H:%M:%S')})


@login_required
@exige(editar=True)
@require_POST
def preencher(request, codigo):
    comp = _competencia(codigo)
    pilar = request.POST.get('pilar')
    destino = reverse('gestao_metas:competencia', args=[comp.codigo]) + '#' + (pilar or '')
    try:
        percentual = float((request.POST.get('percentual') or '0').replace(',', '.'))
        arredondar = int(request.POST.get('arredondar') or 0)
        if pilar not in dict(PILARES):
            raise ValueError('Pilar desconhecido.')
        n = servicos.preencher(comp, pilar, request.POST.get('origem'), percentual, arredondar,
                               request.POST.get('somente_vazias') == '1', request.user)
    except (ValueError, PermissionError) as exc:
        messages.error(request, str(exc))
        return redirect(destino)
    messages.success(request, '{} meta(s) preenchida(s) em {}.'.format(n, dict(PILARES)[pilar]))
    return redirect(destino)


@login_required
@exige(editar=True)
@require_POST
def fechar(request, codigo):
    comp = _competencia(codigo)
    if request.POST.get('acao') == 'reabrir':
        if not e_superadmin(request.user):
            messages.error(request, 'Só o SUPERADMIN reabre uma competência fechada.')
        else:
            servicos.reabrir(comp, request.user)
            messages.success(request, 'Competência reaberta.')
    elif not comp.fechada:
        servicos.fechar(comp, request.user)
        messages.success(request, 'Competência fechada: as metas dela viram a "meta mês anterior" da próxima.')
    return redirect('gestao_metas:competencia', codigo=comp.codigo)


@login_required
@exige()
def baixar_excel(request, codigo):
    comp = _competencia(codigo)
    conteudo = exportar.gerar(comp)
    nome = 'METAS {:02d}.{} {}.xlsx'.format(comp.mes.month, comp.mes.year,
                                             servicos.MESES_LONGOS[comp.mes.month - 1].capitalize())
    resposta = HttpResponse(conteudo,
                            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resposta['Content-Disposition'] = 'attachment; filename="{}"'.format(nome)
    return resposta


@login_required
@exige()
def historico(request, codigo):
    comp = _competencia(codigo)
    itens = HistoricoMeta.objects.filter(competencia=comp)
    pilar = request.GET.get('pilar') or ''
    if pilar:
        itens = itens.filter(pilar=pilar)
    return render(request, 'gestao_metas/historico.html', _base(
        request, aba='competencia', comp=comp, itens=itens[:500], pilar=pilar, pilares=PILARES,
        titulo_mes=servicos.rotulo_mes(comp.mes, True)))


# ---------------------------------------------------------------------------
# Importações
# ---------------------------------------------------------------------------
@login_required
@exige()
def importar(request):
    competencias = Competencia.objects.order_by('-mes')
    escolhida = request.GET.get('competencia') or request.POST.get('competencia') or ''
    if request.method == 'POST':
        if not pode_editar(request.user):
            messages.error(request, 'Seu acesso é só de visualização.')
            return redirect('gestao_metas:importar')
        tipo = request.POST.get('tipo')
        substituir = request.POST.get('substituir') == '1'
        try:
            if tipo == 'base':
                arquivos = request.FILES.getlist('arquivos')
                if not arquivos:
                    raise ValueError('Escolha o arquivo da base.')
                imp = importacao.previa_bases(arquivos, request.user, substituir=substituir)
            elif tipo == 'banco':
                meses = [servicos.mes_do_codigo(m) for m in request.POST.getlist('meses') if m]
                if not meses:
                    raise ValueError('Marque pelo menos um mês.')
                if len(meses) > 13:
                    raise ValueError('No máximo 13 meses por vez.')
                imp = importacao.previa_banco(meses, request.user, substituir=substituir)
            elif tipo == 'meta_vivo':
                arquivo = request.FILES.get('arquivo')
                comp = Competencia.objects.filter(pk=request.POST.get('competencia_id')).first()
                if not arquivo or not comp:
                    raise ValueError('Escolha o arquivo e a competência.')
                imp = importacao.previa_meta_vivo(arquivo, comp, request.user)
            elif tipo == 'planilha':
                arquivo = request.FILES.get('arquivo')
                if not arquivo:
                    raise ValueError('Escolha a planilha.')
                codigo = request.POST.get('competencia_planilha') or None
                if codigo:
                    servicos.mes_do_codigo(codigo)
                imp = importacao.previa_planilha(arquivo, request.user, codigo, substituir=substituir)
            else:
                raise ValueError('Escolha o que importar.')
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect(reverse('gestao_metas:importar') + ('?competencia=' + escolhida if escolhida else ''))
        return redirect('gestao_metas:importacao', pk=imp.pk)

    comp = None
    if escolhida:
        try:
            comp = Competencia.objects.filter(mes=servicos.mes_do_codigo(escolhida)).first()
        except (ValueError, TypeError):
            comp = None
    comp = comp or competencias.first()
    meses_banco = []
    if comp:
        alvo = [servicos.somar_meses(comp.mes, -i) for i in (1, 2, 3, 4)] + [servicos.somar_meses(comp.mes, -12)]
        com_dado = set(Realizado.objects.filter(mes__in=alvo).values_list('mes', flat=True))
        meses_banco = [{'codigo': m.strftime('%Y-%m'), 'rotulo': servicos.rotulo_mes(m), 'tem': m in com_dado}
                       for m in sorted(alvo)]
    hoje = timezone.localdate()
    outros = [servicos.somar_meses(date(hoje.year, hoje.month, 1), -i) for i in range(0, 20)]
    return render(request, 'gestao_metas/importar.html', _base(
        request, aba='importar', competencias=competencias, comp=comp, meses_banco=meses_banco,
        outros_meses=[{'codigo': m.strftime('%Y-%m'), 'rotulo': servicos.rotulo_mes(m)} for m in outros],
        importacoes=ImportacaoMetas.objects.select_related('competencia', 'criado_por')[:30]))


@login_required
@exige()
def importacao_ver(request, pk):
    imp = get_object_or_404(ImportacaoMetas, pk=pk)
    if request.method == 'POST':
        if not pode_editar(request.user):
            messages.error(request, 'Seu acesso é só de visualização.')
            return redirect('gestao_metas:importacao', pk=imp.pk)
        if request.POST.get('acao') == 'descartar' and imp.status == ImportacaoMetas.PREVIA:
            imp.status = ImportacaoMetas.DESCARTADA
            imp.save(update_fields=['status'])
            messages.success(request, 'Importação descartada; nada foi gravado.')
            return redirect('gestao_metas:importar')
        if request.POST.get('acao') == 'aplicar':
            try:
                texto = importacao.aplicar(imp, request.user)
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect('gestao_metas:importacao', pk=imp.pk)
            messages.success(request, texto)
            imp.refresh_from_db()
            if imp.competencia_id:
                return redirect('gestao_metas:competencia', codigo=imp.competencia.codigo)
            return redirect('gestao_metas:importar')
    contexto = _base(request, aba='importar', imp=imp, pilares=PILARES)
    if imp.tipo in (ImportacaoMetas.BASE, ImportacaoMetas.BANCO):
        contexto['grades'] = importacao.comparar_realizado(imp)
        contexto['pilares_da_previa'] = [(p, n) for p, n in PILARES if p in imp.dados.get('pilares', [])]
    elif imp.tipo == ImportacaoMetas.META_VIVO:
        mapa = servicos.MapaLojas()
        atuais = {}
        if imp.competencia_id:
            atuais = {(m.loja_id, m.pilar): m.meta_vivo for m in MetaLoja.objects.filter(competencia=imp.competencia)}
        linhas = {}
        for nome, pilar, valor in imp.dados.get('metas_vivo', []):
            loja = mapa.achar(nome)
            chave = loja.nome if loja else nome
            linha = linhas.setdefault(chave, {'loja': loja, 'pilares': {}})
            atual = atuais.get((loja.id, pilar)) if loja else None
            linha['pilares'][pilar] = {'valor': valor, 'atual': float(atual) if atual is not None else None}
        contexto['linhas_vivo'] = sorted(linhas.items(), key=lambda kv: (kv[1]['loja'] is None,
                                                                         kv[1]['loja'].ordem if kv[1]['loja'] else 0))
        contexto['pilares_da_previa'] = [(p, n) for p, n in PILARES if p in imp.dados.get('pilares', [])]
    elif imp.tipo == ImportacaoMetas.PLANILHA:
        dados = imp.dados
        contexto['mes_planilha'] = servicos.rotulo_mes(servicos.mes_do_codigo(dados['competencia']), True)
        contexto['mes_anterior_planilha'] = servicos.rotulo_mes(
            servicos.somar_meses(servicos.mes_do_codigo(dados['competencia']), -1), True)
        por_pilar = {}
        for nome, mes, pilar, valor, tv, fx, total in dados['realizados']:
            por_pilar[pilar] = por_pilar.get(pilar, 0) + 1
        contexto['por_pilar'] = [(n, por_pilar.get(p, 0)) for p, n in PILARES]
        contexto['n_vivo'] = sum(1 for m in dados['metas'] if m[3])
        contexto['n_metas'] = sum(1 for m in dados['metas'] if m[2])
        contexto['n_anteriores'] = len(dados.get('anteriores', []))
    return render(request, 'gestao_metas/importacao.html', contexto)


# ---------------------------------------------------------------------------
# Lojas e acessos
# ---------------------------------------------------------------------------
@login_required
@exige()
def lojas(request):
    servicos.garantir_lojas()
    if request.method == 'POST':
        if not pode_editar(request.user):
            messages.error(request, 'Seu acesso é só de visualização.')
            return redirect('gestao_metas:lojas')
        if request.POST.get('acao') == 'nova':
            nome = ' '.join((request.POST.get('nome') or '').split())
            if not nome or LojaMeta.objects.filter(chave=servicos.normalizar(nome)).exists():
                messages.error(request, 'Informe um nome de loja que ainda não está na lista.')
            else:
                servicos.MapaLojas().criar(nome, ordem=(LojaMeta.objects.count() + 1) * 10)
                messages.success(request, 'Loja incluída.')
            return redirect('gestao_metas:lojas')
        setores = {s.id: s for s in Sector.objects.all()}
        for loja in LojaMeta.objects.all():
            prefixo = 'l{}_'.format(loja.id)
            if prefixo + 'nome' not in request.POST:
                continue
            loja.nome = (request.POST.get(prefixo + 'nome') or loja.nome).strip()[:120] or loja.nome
            loja.pdv_vivo = (request.POST.get(prefixo + 'pdv_vivo') or '').strip()[:160]
            loja.apelidos = (request.POST.get(prefixo + 'apelidos') or '').strip()[:400]
            regiao = request.POST.get(prefixo + 'regiao') or ''
            loja.regiao = regiao if regiao in dict(REGIOES) else ''
            try:
                loja.ordem = max(0, int(request.POST.get(prefixo + 'ordem') or 0))
            except ValueError:
                pass
            setor_id = request.POST.get(prefixo + 'setor') or ''
            loja.setor = setores.get(int(setor_id)) if setor_id.isdigit() else None
            loja.ativa = request.POST.get(prefixo + 'ativa') == '1'
            loja.save()
        messages.success(request, 'Lojas salvas.')
        return redirect('gestao_metas:lojas')
    return render(request, 'gestao_metas/lojas.html', _base(
        request, aba='lojas', lojas=LojaMeta.objects.select_related('setor').order_by('-ativa', 'ordem', 'nome'),
        setores=Sector.objects.filter(name__istartswith='loja').order_by('name'), regioes=REGIOES))


@login_required
@exige(superadmin=True)
def acessos(request):
    if request.method == 'POST':
        acao = request.POST.get('acao')
        papel_novo = request.POST.get('papel')
        if papel_novo not in dict(AcessoGestaoMetas.PAPEIS):
            papel_novo = AcessoGestaoMetas.VISUALIZADOR
        if acao == 'adicionar':
            tipo, _, pk = (request.POST.get('alvo') or '').partition(':')
            if tipo == 'u' and pk.isdigit() and User.objects.filter(pk=pk, is_active=True).exists():
                AcessoGestaoMetas.objects.update_or_create(
                    user_id=int(pk), defaults={'papel': papel_novo, 'criado_por': request.user})
                messages.success(request, 'Pessoa liberada.')
            elif tipo == 'g' and pk.isdigit() and CommunicationGroup.objects.filter(pk=pk).exists():
                AcessoGestaoMetas.objects.update_or_create(
                    grupo_id=int(pk), defaults={'papel': papel_novo, 'criado_por': request.user})
                messages.success(request, 'Grupo liberado.')
            else:
                messages.error(request, 'Escolha uma pessoa ou um grupo.')
        elif acao in ('papel', 'remover'):
            acesso = get_object_or_404(AcessoGestaoMetas, pk=request.POST.get('acesso'))
            if acao == 'remover':
                acesso.delete()
                messages.success(request, 'Acesso removido.')
            else:
                acesso.papel = papel_novo
                acesso.save(update_fields=['papel', 'atualizado_em'])
                messages.success(request, 'Papel atualizado.')
        return redirect('gestao_metas:acessos')
    lista = list(AcessoGestaoMetas.objects.select_related('user', 'user__sector', 'grupo').order_by('-papel', 'id'))
    for a in lista:
        if a.grupo_id:
            a.membros = a.grupo.members.filter(is_active=True).count()
    return render(request, 'gestao_metas/acessos.html', _base(
        request, aba='acessos', acessos=lista, papeis=AcessoGestaoMetas.PAPEIS,
        usuarios=User.objects.filter(is_active=True).exclude(pk__in=[a.user_id for a in lista if a.user_id])
                             .select_related('sector').order_by('first_name', 'last_name'),
        grupos=CommunicationGroup.objects.exclude(pk__in=[a.grupo_id for a in lista if a.grupo_id]).order_by('name')))
