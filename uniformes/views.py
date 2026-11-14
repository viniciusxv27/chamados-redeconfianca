"""Telas do módulo de Uniformes (/uniformes/).

Só o SUPERADMIN e quem ele liberou entram; para os demais o módulo não
existe (404). Toda escrita de estoque passa por `servicos.py`.
"""
import logging
from datetime import date
from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import DatabaseError, IntegrityError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import importacao, servicos
from .models import TIPOS_SUGERIDOS, AcessoUniformes, ItemUniforme, MovimentoUniforme
from .permissoes import e_superadmin, pode_administrar_acessos, pode_gerir
from .servicos import ErroEstoque

logger = logging.getLogger(__name__)
User = get_user_model()
M = MovimentoUniforme

# v2 (14/11/2026): a prévia passou a trazer novos e usados na mesma linha; a antiga não vale mais.
SESSAO_IMPORTACAO = 'uniformes_importacao_v2'


def gestor_required(view):
    """Login + estar na gestão do módulo. Para quem não está, a página não existe."""
    @login_required
    @wraps(view)
    def _view(request, *args, **kwargs):
        if not pode_gerir(request.user):
            raise Http404('Página não encontrada')
        return view(request, *args, **kwargs)
    return _view


def _voltar(request, padrao='uniformes:estoque'):
    destino = request.POST.get('next') or request.GET.get('next') or ''
    if destino and url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()},
                                                   require_https=request.is_secure()):
        return redirect(destino)
    return redirect(padrao)


def _data(valor):
    try:
        return date.fromisoformat((valor or '').strip())
    except ValueError:
        return None


def _nome(user):
    return (user.get_full_name() or user.email) if user else ''


def _base_ctx(request, **extra):
    ctx = {'is_superadmin': e_superadmin(request.user), 'hoje': timezone.localdate()}
    ctx.update(extra)
    return ctx


def _itens_json(itens):
    return [{'id': i.id, 'nome': i.nome, 'tipo': i.tipo, 'tamanho': i.tamanho, 'saldo': i.saldo,
             'saldo_usado': i.saldo_usado} for i in itens]


def _colaboradores_json(qs):
    return [{'id': u.id, 'nome': u.get_full_name() or u.email,
             'setor': u.sector.name if u.sector_id else '',
             'camisa': u.uniform_size_shirt or '', 'calca': u.uniform_size_pants or ''}
            for u in qs]


def _colaboradores_ativos():
    return (User.objects.filter(is_active=True).select_related('sector')
            .only('id', 'first_name', 'last_name', 'email', 'sector__name',
                  'uniform_size_shirt', 'uniform_size_pants')
            .order_by('first_name', 'last_name'))


# ---------------------------------------------------------------------------
# Estoque
# ---------------------------------------------------------------------------
@gestor_required
def estoque(request):
    itens = list(servicos.filtrar_itens(request.GET))
    em_posse = servicos.em_posse_por_item()
    for item in itens:
        item.em_posse = em_posse.get(item.id, 0)
    if request.GET.get('baixo') == '1':
        itens = [i for i in itens if i.abaixo_do_minimo or i.total_estoque == 0]
    todos = ItemUniforme.objects.filter(ativo=True)
    tipos = sorted({t for t in todos.values_list('tipo', flat=True)} | set(TIPOS_SUGERIDOS))
    tamanhos = sorted({t for t in todos.values_list('tamanho', flat=True) if t})
    ativos = list(todos.order_by('tipo', 'tamanho', 'descricao'))
    resumo = {
        'itens': len(ativos),
        'novos': sum(i.saldo for i in ativos),
        'usados': sum(i.saldo_usado for i in ativos),
        'com_colaboradores': sum(v for v in em_posse.values() if v > 0),
        'baixo': sum(1 for i in ativos if i.abaixo_do_minimo or i.total_estoque == 0),
    }
    resumo['pecas'] = resumo['novos'] + resumo['usados']
    ultimos = list(servicos.filtrar_movimentos({})[:8])
    return render(request, 'uniformes/estoque.html', _base_ctx(
        request, itens=itens, tipos=tipos, tamanhos=tamanhos, resumo=resumo, ultimos=ultimos,
        itens_json=_itens_json(ativos), filtro=request.GET, tipos_sugeridos=TIPOS_SUGERIDOS))


@gestor_required
@require_POST
def item_novo(request):
    try:
        condicao = servicos.condicao_valida(request.POST.get('condicao'))
        # "CN Feminino - Usado" não vira item à parte: é o mesmo item, com a entrada no saldo de usados.
        descricao, eh_usado = servicos.separar_usado(request.POST.get('descricao'))
        if eh_usado:
            condicao = servicos.USADO
        item, novo = servicos.criar_item(
            tipo=request.POST.get('tipo'), tamanho=request.POST.get('tamanho'), descricao=descricao,
            estoque_minimo=int(request.POST.get('estoque_minimo') or 0), por=request.user)
        qtd = (request.POST.get('quantidade') or '').strip()
        lancou = bool(qtd) and qtd != '0'
        if lancou:
            servicos.registrar_entrada(item_id=item.id, quantidade=qtd, por=request.user, condicao=condicao,
                                       data=_data(request.POST.get('data')),
                                       nota_fiscal=request.POST.get('nota_fiscal', ''),
                                       observacao=request.POST.get('observacao', ''))
        rotulo = servicos.rotulo_condicao(condicao, plural=True)
        if novo:
            messages.success(request, f'{item.nome} cadastrado' + (
                f' com {qtd} peça(s) {rotulo} em estoque.' if lancou else '.'))
        else:
            messages.info(request, f'{item.nome} já existia — ' + (
                f'entrada de {qtd} peça(s) {rotulo} lançada nele.' if lancou else 'nada novo cadastrado.'))
    except (ErroEstoque, ValueError) as exc:
        messages.error(request, str(exc))
    return _voltar(request)


@gestor_required
@require_POST
def item_editar(request, pk):
    item = get_object_or_404(ItemUniforme, pk=pk)
    tipo = servicos.limpar_tipo(request.POST.get('tipo') or item.tipo)
    tamanho = servicos.limpar_tamanho(request.POST.get('tamanho', item.tamanho))
    descricao = servicos.limpar_descricao(request.POST.get('descricao', item.descricao))
    outro = servicos.achar_item(tipo, tamanho, descricao)
    if outro and outro.pk != item.pk:
        messages.error(request, f'Já existe o item {outro.nome}.')
        return _voltar(request)
    item.tipo, item.tamanho, item.descricao = tipo, tamanho, descricao
    try:
        item.estoque_minimo = max(0, int(request.POST.get('estoque_minimo') or 0))
    except ValueError:
        pass
    item.ativo = request.POST.get('ativo', 'on') in ('on', '1', 'true')
    item.atualizado_por = request.user
    try:
        item.save(update_fields=['tipo', 'tamanho', 'descricao', 'estoque_minimo', 'ativo',
                                 'atualizado_por', 'atualizado_em'])
        messages.success(request, f'{item.nome} atualizado.')
    except IntegrityError:
        messages.error(request, 'Já existe um item com esse tipo, tamanho e modelo.')
    return _voltar(request)


@gestor_required
@require_POST
def entrada(request):
    try:
        mov = servicos.registrar_entrada(
            item_id=int(request.POST.get('item') or 0), quantidade=request.POST.get('quantidade'),
            por=request.user, condicao=request.POST.get('condicao'), data=_data(request.POST.get('data')),
            nota_fiscal=request.POST.get('nota_fiscal', ''), observacao=request.POST.get('observacao', ''))
        rotulo = servicos.rotulo_condicao(mov.condicao, plural=True)
        messages.success(request, f'Entrada de {mov.quantidade} × {mov.item.nome} ({rotulo}). '
                                  f'Saldo de {rotulo} agora: {mov.saldo_apos}.')
    except (ErroEstoque, ValueError) as exc:
        messages.error(request, str(exc))
    return _voltar(request)


@gestor_required
@require_POST
def ajuste(request):
    try:
        item_id = int(request.POST.get('item') or 0)
        if 'novos' in request.POST or 'usados' in request.POST:
            movs = servicos.registrar_inventario(
                item_id=item_id, novos=request.POST.get('novos'), usados=request.POST.get('usados'),
                por=request.user, motivo=request.POST.get('motivo'))
        else:   # uma condição só (contagem + condicao)
            movs = [servicos.registrar_ajuste(
                item_id=item_id, contagem=request.POST.get('contagem'), por=request.user,
                motivo=request.POST.get('motivo'), condicao=request.POST.get('condicao'))]
        partes = []
        for mov in movs:
            rotulo = servicos.rotulo_condicao(mov.condicao, plural=True)
            partes.append(f'{rotulo} {"+" if mov.delta > 0 else ""}{mov.delta} → {mov.saldo_apos}')
        messages.success(request, f'{movs[0].item.nome} ajustado: {"; ".join(partes)}.')
    except (ErroEstoque, ValueError) as exc:
        messages.error(request, str(exc))
    return _voltar(request)


# ---------------------------------------------------------------------------
# Retirada e devolução
# ---------------------------------------------------------------------------
def _linhas_do_post(request):
    """[(item_id, qtd, condição)] das linhas do formulário; condição vazia = padrão do serviço."""
    itens = request.POST.getlist('item')
    qtds = request.POST.getlist('quantidade')
    condicoes = request.POST.getlist('condicao')
    linhas = []
    for pos, (item_id, qtd) in enumerate(zip(itens, qtds)):
        if (item_id or '').isdigit() and (qtd or '').strip() not in ('', '0'):
            linhas.append((int(item_id), qtd, condicoes[pos] if pos < len(condicoes) else ''))
    return linhas


def _colaborador_do_post(request, nome_campo='colaborador'):
    valor = request.POST.get(nome_campo) or ''
    return User.objects.filter(pk=int(valor)).first() if valor.isdigit() else None


@gestor_required
def retirada(request):
    if request.method == 'POST':
        colaborador = _colaborador_do_post(request)
        try:
            if colaborador is not None and not colaborador.is_active:
                raise ErroEstoque('Este colaborador está inativo; retirada só para quem está ativo.')
            movs = servicos.registrar_retirada(
                linhas=_linhas_do_post(request), colaborador=colaborador, por=request.user,
                data_entrega=_data(request.POST.get('data_entrega')),
                observacao=request.POST.get('observacao', ''))
        except (ErroEstoque, ValueError) as exc:
            messages.error(request, str(exc))
        else:
            pecas = sum(m.quantidade for m in movs)
            messages.success(request, f'Retirada de {pecas} peça(s) para {_nome(colaborador)} lançada e '
                                      f'abatida do estoque.')
            return redirect(reverse('uniformes:colaborador', args=[colaborador.pk]) + f'?termo={movs[0].lote}')
    itens = list(ItemUniforme.objects.filter(ativo=True).order_by('tipo', 'tamanho', 'descricao'))
    inicial = request.GET.get('colaborador') or request.POST.get('colaborador') or ''
    return render(request, 'uniformes/retirada.html', _base_ctx(
        request, itens_json=_itens_json(itens), colaboradores_json=_colaboradores_json(_colaboradores_ativos()),
        colaborador_inicial=int(inicial) if str(inicial).isdigit() else None,
        item_inicial=int(request.GET['item']) if (request.GET.get('item') or '').isdigit() else None))


@gestor_required
def devolucao(request):
    if request.method == 'POST':
        colaborador = _colaborador_do_post(request)
        try:
            movs = servicos.registrar_devolucao(
                linhas=_linhas_do_post(request), colaborador=colaborador, por=request.user,
                data=_data(request.POST.get('data')), observacao=request.POST.get('observacao', ''))
        except (ErroEstoque, ValueError) as exc:
            messages.error(request, str(exc))
            destino = reverse('uniformes:devolucao')
            if colaborador:
                destino += f'?colaborador={colaborador.pk}'
            return redirect(destino)
        pecas = sum(m.quantidade for m in movs)
        usadas = sum(m.quantidade for m in movs if m.usado)
        destino = ('como usadas' if usadas == pecas else 'como novas' if not usadas
                   else f'{usadas} como usada(s) e {pecas - usadas} como nova(s)')
        messages.success(request, f'Devolução de {pecas} peça(s) de {_nome(colaborador)} — voltaram ao estoque {destino}.')
        return redirect('uniformes:colaborador', colaborador.pk)

    com_posse = servicos.colaboradores_com_posse()
    pessoas = (User.objects.filter(pk__in=com_posse).select_related('sector')
               .order_by('first_name', 'last_name'))
    escolhido, pecas = None, []
    valor = request.GET.get('colaborador') or ''
    if valor.isdigit():
        escolhido = User.objects.filter(pk=int(valor)).select_related('sector').first()
        if escolhido:
            pecas = servicos.uniformes_da_ficha(escolhido)['pecas']
    return render(request, 'uniformes/devolucao.html', _base_ctx(
        request, pessoas=[(p, com_posse.get(p.pk, 0)) for p in pessoas], escolhido=escolhido, pecas=pecas))


@gestor_required
def colaborador(request, pk):
    pessoa = get_object_or_404(User.objects.select_related('sector'), pk=pk)
    ficha = servicos.uniformes_da_ficha(pessoa)
    return render(request, 'uniformes/colaborador.html', _base_ctx(
        request, pessoa=pessoa, ficha=ficha, termo_novo=request.GET.get('termo', '')))


@gestor_required
@require_POST
def data_entrega(request, pk):
    mov = get_object_or_404(MovimentoUniforme, pk=pk, tipo=M.RETIRADA)
    nova = _data(request.POST.get('data_entrega'))
    if request.POST.get('data_entrega') and nova is None:
        messages.error(request, 'Data inválida.')
    else:
        servicos.alterar_data_entrega(mov.pk, nova, request.user)
        messages.success(request, 'Data de entrega ' + (f'registrada: {nova:%d/%m/%Y}.' if nova else 'apagada.'))
    return _voltar(request) if request.POST.get('next') else redirect('uniformes:colaborador', mov.colaborador_id)


@gestor_required
def termo(request, lote):
    movs = list(MovimentoUniforme.objects.filter(lote=lote, tipo=M.RETIRADA)
                .select_related('item', 'colaborador', 'colaborador__sector', 'registrado_por')
                .order_by('item__tipo', 'item__tamanho', 'condicao'))
    if not movs:
        raise Http404('Retirada não encontrada')
    primeiro = movs[0]
    return render(request, 'uniformes/termo.html', {
        'movs': movs, 'pessoa': primeiro.colaborador, 'mov': primeiro,
        'total': sum(m.quantidade for m in movs), 'impresso_em': timezone.now(),
        'tem_usada': any(m.usado for m in movs),
    })


# ---------------------------------------------------------------------------
# Histórico e exportações
# ---------------------------------------------------------------------------
@gestor_required
def historico(request):
    qs = servicos.filtrar_movimentos(request.GET)
    pagina = Paginator(qs, 50).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'uniformes/historico.html', _base_ctx(
        request, pagina=pagina, filtro=request.GET, params=params.urlencode(),
        itens=ItemUniforme.objects.order_by('tipo', 'tamanho', 'descricao'),
        colaboradores=User.objects.filter(pk__in=MovimentoUniforme.objects.exclude(colaborador=None)
                                          .values('colaborador_id')).order_by('first_name', 'last_name'),
        lancadores=User.objects.filter(pk__in=MovimentoUniforme.objects.values('registrado_por_id'))
        .order_by('first_name', 'last_name'),
        tipos=M.TIPO_CHOICES, condicoes=M.CONDICAO_CHOICES))


def _xlsx(conteudo, nome):
    resposta = HttpResponse(conteudo, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resposta['Content-Disposition'] = f'attachment; filename="{nome}"'
    return resposta


@gestor_required
def historico_exportar(request):
    movs = servicos.filtrar_movimentos(request.GET)[:20000]
    return _xlsx(importacao.exportar_historico(movs),
                 f'uniformes_movimentos_{timezone.localdate():%Y%m%d}.xlsx')


@gestor_required
def estoque_exportar(request):
    itens = servicos.filtrar_itens(request.GET)
    return _xlsx(importacao.exportar_estoque(itens, servicos.em_posse_por_item()),
                 f'uniformes_estoque_{timezone.localdate():%Y%m%d}.xlsx')


@gestor_required
def importar(request):
    previa = request.session.get(SESSAO_IMPORTACAO)
    if request.method == 'POST' and request.POST.get('acao') == 'ler':
        arquivo = request.FILES.get('planilha')
        if not arquivo:
            messages.error(request, 'Escolha a planilha.')
            return redirect('uniformes:importar')
        try:
            lida = importacao.ler_planilha(arquivo, arquivo.name)
        except ErroEstoque as exc:
            messages.error(request, str(exc))
            return redirect('uniformes:importar')
        request.session[SESSAO_IMPORTACAO] = {'arquivo': arquivo.name, **lida}
        return redirect('uniformes:importar')
    if request.method == 'POST' and request.POST.get('acao') == 'confirmar':
        if not previa:
            messages.error(request, 'A prévia expirou; envie a planilha de novo.')
            return redirect('uniformes:importar')
        modo = request.POST.get('modo')
        if modo not in (importacao.MODO_ENTRADA, importacao.MODO_ACERTAR):
            messages.error(request, 'Escolha como lançar as quantidades.')
            return redirect('uniformes:importar')
        try:
            resumo = importacao.aplicar(previa['linhas'], modo=modo, por=request.user,
                                        nome_arquivo=previa.get('arquivo', ''))
        except (ErroEstoque, DatabaseError) as exc:
            logger.warning('Importação de uniformes recusada', exc_info=True)
            messages.error(request, f'Nada foi importado: {exc}')
            return redirect('uniformes:importar')
        request.session.pop(SESSAO_IMPORTACAO, None)
        messages.success(request, (
            f'Importação concluída: {resumo["itens_novos"]} item(ns) novo(s), {resumo["entradas"]} entrada(s), '
            f'{resumo["ajustes"]} ajuste(s), {resumo["sem_mudanca"]} sem mudança.'))
        return redirect('uniformes:estoque')
    if request.method == 'POST' and request.POST.get('acao') == 'cancelar':
        request.session.pop(SESSAO_IMPORTACAO, None)
        return redirect('uniformes:importar')
    return render(request, 'uniformes/importar.html', _base_ctx(request, previa=previa))


# ---------------------------------------------------------------------------
# Quem cuida (lista do SUPERADMIN)
# ---------------------------------------------------------------------------
@gestor_required
def acessos(request):
    if not pode_administrar_acessos(request.user):
        messages.error(request, 'Só o SUPERADMIN define quem cuida dos uniformes.')
        return redirect('uniformes:estoque')
    if request.method == 'POST':
        valor = request.POST.get('user') or ''
        escolhido = User.objects.filter(pk=int(valor), is_active=True).first() if valor.isdigit() else None
        if not escolhido:
            messages.error(request, 'Escolha a pessoa que vai cuidar dos uniformes.')
        elif e_superadmin(escolhido):
            messages.info(request, f'{_nome(escolhido)} já é SUPERADMIN e cuida dos uniformes.')
        else:
            _, criado = AcessoUniformes.objects.get_or_create(
                user=escolhido, defaults={'liberado_por': request.user,
                                          'observacao': (request.POST.get('observacao') or '').strip()[:200]})
            if criado:
                messages.success(request, f'{_nome(escolhido)} agora cuida dos uniformes.')
            else:
                messages.info(request, 'Esta pessoa já estava na lista.')
        return redirect('uniformes:acessos')
    liberados = list(AcessoUniformes.objects.select_related('user', 'user__sector', 'liberado_por'))
    candidatos = (User.objects.filter(is_active=True).exclude(pk__in=[a.user_id for a in liberados])
                  .select_related('sector').order_by('first_name', 'last_name'))
    return render(request, 'uniformes/acessos.html', _base_ctx(
        request, liberados=liberados, candidatos=candidatos))


@gestor_required
@require_POST
def acesso_remover(request, pk):
    if not pode_administrar_acessos(request.user):
        messages.error(request, 'Só o SUPERADMIN define quem cuida dos uniformes.')
        return redirect('uniformes:estoque')
    acesso = get_object_or_404(AcessoUniformes.objects.select_related('user'), pk=pk)
    nome = _nome(acesso.user)
    acesso.delete()
    messages.success(request, f'{nome} não cuida mais dos uniformes.')
    return redirect('uniformes:acessos')
