"""Telas da NF-e em /financeiro/notas/: lista, rascunho, envio, consulta, XML, DANFE,
cancelamento, carta de correção e a configuração (só SUPERADMIN).

A regra fica em ``financeiro/nfe.py``; aqui só se lê o formulário, chama a
regra e registra tudo no LogFinanceiro.
"""
import secrets
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.core.cache import cache
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import auditoria, danfe, nfe
from .models_nfe import ConfiguracaoNFe, EmitenteNFe, EventoNotaFiscal, ItemNotaFiscal, NotaFiscalEmitida
from .permissoes import e_superadmin, exige, filtrar_por_loja, lojas_permitidas
from .views import _contexto_base, lojas_do

CAMPOS_NOTA = ('tipo_operacao', 'finalidade', 'natureza', 'chave_referenciada', 'dest_documento', 'dest_nome',
               'dest_ie', 'dest_ind_ie', 'dest_email', 'dest_telefone', 'dest_logradouro', 'dest_numero',
               'dest_complemento', 'dest_bairro', 'dest_codigo_municipio', 'dest_municipio', 'dest_uf', 'dest_cep',
               'presenca', 'modalidade_frete', 'forma_pagamento', 'info_complementar')
SO_DIGITOS = ('chave_referenciada', 'dest_documento', 'dest_ie', 'dest_telefone', 'dest_codigo_municipio', 'dest_cep')
CAMPOS_ITEM = ('codigo', 'descricao', 'ean', 'ncm', 'cest', 'cfop', 'unidade', 'quantidade', 'valor_unitario',
               'valor_desconto', 'origem', 'cst_icms', 'reducao_bc_icms', 'aliquota_icms', 'cst_pis', 'aliquota_pis',
               'cst_cofins', 'aliquota_cofins', 'info_adicional')
NUMERICOS = {'quantidade', 'valor_unitario', 'valor_desconto', 'reducao_bc_icms', 'aliquota_icms', 'aliquota_pis',
             'aliquota_cofins'}
UFS = sorted(nfe.CODIGOS_UF)


# ── Apoio ───────────────────────────────────────────────────────────────────
def _notas_visiveis(user):
    return filtrar_por_loja(NotaFiscalEmitida.objects.select_related('emitente', 'loja'), user)


def _nota_ou_404(request, pk):
    return get_object_or_404(_notas_visiveis(request.user), pk=pk)


def _emitentes_do(user, so_ativos=True):
    qs = EmitenteNFe.objects.select_related('loja')
    if so_ativos:
        qs = qs.filter(ativo=True)
    ids = lojas_permitidas(user)
    return qs if ids is None else qs.filter(loja_id__in=ids)


def _registrar(request, acao, descricao, nota):
    auditoria.registrar(request, acao, descricao, area='notas', obj=nota, valor=nota.valor_total, loja=nota.loja)


def _contexto(request, **extra):
    ctx = _contexto_base(request, 'notas')
    ctx['config'] = ConfiguracaoNFe.atual()
    ctx.update(extra)
    return ctx


def _escolhas():
    N = NotaFiscalEmitida
    return {'tipos': N.TIPOS, 'finalidades': N.FINALIDADES, 'ind_ie': N.IND_IE, 'presencas': N.PRESENCA,
            'fretes': N.FRETES, 'pagamentos': N.PAGAMENTOS, 'ufs': UFS + ['EX'],
            'cst_normal': nfe.CST_NORMAL.items(), 'csosn': nfe.CSOSN.items(), 'cst_pis': nfe.CST_PIS,
            'origens': [('0', '0 · Nacional'), ('1', '1 · Estrangeira, importação direta'),
                        ('2', '2 · Estrangeira, mercado interno'), ('3', '3 · Nacional, importado > 40%'),
                        ('4', '4 · Nacional, processo produtivo básico'), ('5', '5 · Nacional, importado ≤ 40%'),
                        ('6', '6 · Estrangeira direta, sem similar (CAMEX)'),
                        ('7', '7 · Estrangeira interna, sem similar (CAMEX)'),
                        ('8', '8 · Nacional, importado > 70%')]}


def _ler_formulario(request, nota, emitentes):
    """Aplica o POST na nota e devolve os itens (sem gravar)."""
    post = request.POST
    try:
        emitente = emitentes.get(pk=int(post.get('emitente') or 0))
    except (ValueError, EmitenteNFe.DoesNotExist):
        raise nfe.NfeInvalida('Escolha o emitente (CNPJ que emite a nota).')
    if nota.numero is not None and nota.emitente_id and emitente.pk != nota.emitente_id:
        raise nfe.NfeInvalida('A nota já tem número deste emitente: não dá para trocar o emitente.')
    nota.emitente, nota.loja = emitente, emitente.loja
    for campo in CAMPOS_NOTA:
        valor = (post.get(campo) or '').strip()
        if campo in SO_DIGITOS:
            valor = nfe.so_digitos(valor)
        elif campo == 'info_complementar':
            valor = nfe.texto(valor, 5000)
        else:
            valor = nfe.texto(valor, NotaFiscalEmitida._meta.get_field(campo).max_length)
        if campo == 'dest_uf':
            valor = valor.upper()
        setattr(nota, campo, valor)
    nota.dest_ind_ie = nota.dest_ind_ie if nota.dest_ind_ie in ('1', '2', '9') else '9'
    nota.consumidor_final = post.get('consumidor_final') == '1'
    for campo in ('valor_frete', 'valor_seguro', 'valor_outras'):
        setattr(nota, campo, nfe.r2(max(nfe.dec(post.get(campo)), Decimal('0'))))
    if nota.finalidade != '4' and not nota.chave_referenciada:
        nota.chave_referenciada = ''

    listas = {c: post.getlist(f'item_{c}') for c in CAMPOS_ITEM}
    itens = []
    for i in range(len(listas['descricao'])):
        bruto = {c: (listas[c][i] if i < len(listas[c]) else '').strip() for c in CAMPOS_ITEM}
        if not any(bruto[c] for c in ('codigo', 'descricao', 'ncm', 'quantidade', 'valor_unitario')):
            continue
        item = ItemNotaFiscal(ordem=len(itens) + 1)
        for campo in CAMPOS_ITEM:
            valor = bruto[campo]
            if campo in NUMERICOS:
                try:
                    valor = nfe.dec(valor)
                except nfe.NfeInvalida:
                    raise nfe.NfeInvalida(f'Item {item.ordem}: "{valor}" não é número ({campo.replace("_", " ")}).')
            elif campo in ('ncm', 'cest', 'cfop', 'ean'):
                valor = nfe.so_digitos(valor)
            else:
                valor = nfe.texto(valor, ItemNotaFiscal._meta.get_field(campo).max_length)
            setattr(item, campo, valor)
        item.unidade = (item.unidade or 'UN').upper()
        item.origem = item.origem or '0'
        item.cst_pis = item.cst_pis or '49'
        item.cst_cofins = item.cst_cofins or '49'
        itens.append(item)
    return itens


def _gravar(nota, itens, usuario, nova):
    nfe.calcular(nota, itens)
    with transaction.atomic():
        if nova:
            nota.criado_por = usuario
        nota.save()
        nota.itens.all().delete()
        for item in itens:
            item.nota = nota
        ItemNotaFiscal.objects.bulk_create(itens)
    return nota


def _item_vazio(emitente=None):
    simples = bool(emitente and emitente.simples)
    return {'quantidade': '1', 'origem': '0', 'unidade': 'UN', 'cst_icms': '102' if simples else '00',
            'cst_pis': '49', 'cst_cofins': '49', 'aliquota_icms': '0', 'reducao_bc_icms': '0', 'aliquota_pis': '0',
            'aliquota_cofins': '0', 'valor_desconto': '0'}


def _numero_tela(valor):
    """Decimal → "1234,5" (sem zeros à toa), como a pessoa digita."""
    if valor in (None, ''):
        return ''
    try:
        numero = nfe.dec(valor) if not isinstance(valor, Decimal) else valor
    except nfe.NfeInvalida:
        return str(valor)
    texto = format(numero.normalize(), 'f')
    return texto.replace('.', ',')


def _item_dict(item):
    dados = item if isinstance(item, dict) else {c: getattr(item, c) for c in CAMPOS_ITEM}
    return {c: (_numero_tela(dados.get(c)) if c in NUMERICOS else dados.get(c, '')) for c in CAMPOS_ITEM}


# ── Lista ───────────────────────────────────────────────────────────────────
@login_required
@exige('notas.ver')
def notas(request):
    qs = _notas_visiveis(request.user)
    contagem = dict(qs.values_list('status').annotate(n=Count('id')))
    status = request.GET.get('status', '')
    if status in dict(NotaFiscalEmitida.STATUS):
        qs = qs.filter(status=status)
    tipo = request.GET.get('tipo', '')
    if tipo == 'saida':
        qs = qs.filter(tipo_operacao='1').exclude(finalidade='4')
    elif tipo == 'entrada':
        qs = qs.filter(tipo_operacao='0').exclude(finalidade='4')
    elif tipo == 'devolucao':
        qs = qs.filter(finalidade='4')
    busca = (request.GET.get('q') or '').strip()
    if busca:
        digitos = nfe.so_digitos(busca)
        filtro = Q(dest_nome__icontains=busca) | Q(natureza__icontains=busca)
        if digitos:
            filtro |= Q(dest_documento__contains=digitos) | Q(chave__contains=digitos)
            if len(digitos) <= 9:
                filtro |= Q(numero=int(digitos))
        qs = qs.filter(filtro)
    pagina = Paginator(qs, 50).get_page(request.GET.get('pagina'))
    return render(request, 'financeiro/notas.html', _contexto(
        request, pagina=pagina, contagem=contagem, total=sum(contagem.values()), filtro_status=status,
        filtro_tipo=tipo, busca=busca,
        status_opcoes=[(v, r, contagem.get(v, 0)) for v, r in NotaFiscalEmitida.STATUS],
        tem_emitente=_emitentes_do(request.user).exists()))


# ── Rascunho (nova e editar) ────────────────────────────────────────────────
def _formulario(request, nota, nova):
    emitentes = _emitentes_do(request.user)
    if request.method == 'POST':
        try:
            itens = _ler_formulario(request, nota, emitentes)
        except nfe.NfeInvalida as exc:
            messages.error(request, str(exc))
            itens_form = [{c: v for c, v in zip(CAMPOS_ITEM, linha)}
                          for linha in zip(*[request.POST.getlist(f'item_{c}') for c in CAMPOS_ITEM])]
            return _tela_formulario(request, nota, nova, emitentes, itens_form or [_item_vazio()])
        antes = None if nova else auditoria.foto(NotaFiscalEmitida.objects.get(pk=nota.pk))
        _gravar(nota, itens, request.user, nova)
        if nova:
            _registrar(request, 'CRIAR', f'Rascunho de NF-e para {nota.dest_nome} ({nota.rotulo_tipo})', nota)
        else:
            a, d = auditoria.diferenca(antes, auditoria.foto(nota))
            auditoria.registrar(request, 'EDITAR', f'Editou o rascunho da NF-e {nota.pk}', area='notas', obj=nota,
                                valor=nota.valor_total, loja=nota.loja, antes=a, depois=d)
        problemas = nfe.validar(nota, itens)
        if problemas:
            messages.warning(request, f'Rascunho salvo. Antes de enviar, falta acertar {len(problemas)} '
                                      f'ponto{"s" if len(problemas) > 1 else ""} (lista na nota).')
        else:
            messages.success(request, 'Rascunho salvo e conferido: pode enviar para a SEFAZ.')
        return redirect('financeiro:nota', pk=nota.pk)

    itens_form = [_item_dict(i) for i in nota.itens.all()] if nota.pk else []
    original = None
    if nova and request.GET.get('devolver'):
        try:
            dados = nfe.dados_para_devolucao(request.GET['devolver'], list(emitentes))
        except nfe.NfeInvalida as exc:
            messages.error(request, str(exc))
        else:
            original = dados['nota'].pop('original')
            for campo, valor in dados['nota'].items():
                if campo == 'emitente':
                    nota.emitente = emitentes.get(pk=valor)
                else:
                    setattr(nota, campo, valor)
            itens_form = [_item_dict(i) for i in dados['itens']]
            messages.info(request, 'Itens e destinatário vieram da nota original. Confira quantidades (devolução '
                                   'parcial), CFOP e impostos antes de salvar.')
    elif nova and request.GET.get('xml'):
        importado = _rascunho_do_xml(request, nota, emitentes)
        if importado:
            itens_form, original = importado
    elif nova and request.GET.get('copiar'):
        base = _notas_visiveis(request.user).filter(pk=request.GET['copiar']).first()
        if base:
            for campo in CAMPOS_NOTA + ('consumidor_final', 'valor_frete', 'valor_seguro', 'valor_outras'):
                setattr(nota, campo, getattr(base, campo))
            nota.emitente_id = base.emitente_id
            itens_form = [_item_dict(i) for i in base.itens.all()]
    return _tela_formulario(request, nota, nova, emitentes, itens_form or [_item_vazio()], original)


def _rascunho_do_xml(request, nota, emitentes):
    """Preenche a nota nova com o XML que a pessoa subiu (guardado no cache pelo importar_xml)."""
    guardado = cache.get(_chave_cache_xml(request.GET['xml']))
    if not guardado or guardado.get('usuario') != request.user.pk:
        messages.error(request, 'O XML enviado expirou (fica guardado por 2 horas). Suba o arquivo de novo.')
        return None
    acao = request.GET.get('acao') or 'devolucao'
    try:
        dados = nfe.dados_do_xml(guardado['xml'], list(emitentes), acao)
    except nfe.NfeInvalida as exc:
        messages.error(request, str(exc))
        return None
    original = dados['nota'].pop('original')
    original['acao'] = acao
    original['arquivo'] = guardado.get('nome', '')
    for campo, valor in dados['nota'].items():
        if campo == 'emitente':
            nota.emitente = emitentes.get(pk=valor)
        else:
            setattr(nota, campo, valor)
    if acao == 'devolucao':
        messages.info(request, 'Destinatário, itens e CFOP de devolução vieram do XML. Confira quantidades (devolução '
                               'parcial) e impostos antes de salvar.')
    else:
        messages.info(request, 'Destinatário, itens e impostos vieram do XML. Confira tudo antes de salvar.')
    return [_item_dict(i) for i in dados['itens']], original


def _chave_cache_xml(token):
    return f'financeiro:nfe-xml:{nfe.texto(token, 40)}'


@login_required
@exige('notas.emitir')
@require_POST
def importar_xml(request):
    """Recebe o XML de uma NF-e, diz o que ela é e o que dá para fazer; guarda o arquivo por 2 h.

    Com JavaScript a resposta é JSON (a tela mostra as opções ou vai direto quando só há uma);
    sem, segue direto para a primeira opção.
    """
    quer_json = 'application/json' in request.headers.get('Accept', '')

    def erro(texto, status=400, resumo=None):
        if quer_json:
            return JsonResponse({'erro': texto, 'resumo': resumo}, status=status)
        messages.error(request, texto)
        return redirect('financeiro:notas')

    arquivo = request.FILES.get('xml')
    if not arquivo:
        return erro('Escolha o arquivo XML da nota.')
    if not arquivo.name.lower().endswith('.xml'):
        return erro('O arquivo precisa ser o XML da NF-e (.xml).')
    if arquivo.size > nfe.TAMANHO_MAXIMO_XML:
        return erro('O XML passa de 5 MB: não parece uma NF-e.')
    bruto = arquivo.read()
    emitentes = list(_emitentes_do(request.user))
    try:
        resumo = nfe.resumo_do_xml(bruto, emitentes)
    except nfe.NfeInvalida as exc:
        return erro(str(exc))
    tela = {c: resumo[c] for c in ('chave', 'numero', 'serie', 'emissao', 'natureza', 'entrada', 'devolucao',
                                   'emitente_nome', 'emitente_doc', 'dest_nome', 'dest_doc', 'valor', 'itens',
                                   'autorizada', 'lado')}
    tela['nosso'] = str(resumo['nosso']) if resumo['nosso'] else ''
    if not resumo['opcoes']:
        return erro('Nenhum CNPJ emitente cadastrado (que você possa usar) aparece nesta nota, nem como emitente nem '
                    'como destinatário. O SUPERADMIN cadastra os CNPJs em Notas fiscais → Configuração.', 422, tela)
    token = secrets.token_urlsafe(18)
    cache.set(_chave_cache_xml(token), {'xml': bruto, 'usuario': request.user.pk, 'nome': arquivo.name[:120]}, 7200)
    base = reverse('financeiro:nota_nova')
    opcoes = [{'acao': a, 'titulo': t, 'texto': x, 'url': f'{base}?xml={token}&acao={a}'} for a, t, x in resumo['opcoes']]
    avisos = []
    if not resumo['autorizada']:
        avisos.append('Este XML não traz o protocolo de autorização da SEFAZ. Se a nota não foi autorizada, '
                      'a devolução vai ser rejeitada.')
    ja = (_notas_visiveis(request.user).filter(chave_referenciada=resumo['chave'], finalidade='4')
          .exclude(status__in=('CANCELADA', 'REJEITADA')).order_by('-id').first())
    if ja:
        avisos.append(f'Já existe devolução desta nota: {"nº " + str(ja.numero) if ja.numero else "rascunho #" + str(ja.pk)} '
                      f'({ja.get_status_display().lower()}).')
    if not quer_json:
        return redirect(opcoes[0]['url'])
    return JsonResponse({'resumo': tela, 'opcoes': opcoes, 'avisos': avisos,
                         'ja_existe': reverse('financeiro:nota', args=[ja.pk]) if ja else ''})


def _tela_formulario(request, nota, nova, emitentes, itens_form, original=None):
    return render(request, 'financeiro/nota_form.html', _contexto(
        request, nota=nota, nova=nova, emitentes=emitentes, itens=itens_form, original=original,
        emitentes_simples=[e.pk for e in emitentes if e.simples],
        emitentes_uf={e.pk: e.uf for e in emitentes}, **_escolhas()))


@login_required
@exige('notas.emitir')
def nota_nova(request):
    if not _emitentes_do(request.user).exists():
        messages.error(request, 'Nenhum emitente cadastrado para as suas lojas. O SUPERADMIN cadastra em '
                                'Notas fiscais → Configuração.')
        return redirect('financeiro:notas')
    tipo = request.GET.get('tipo')
    nota = NotaFiscalEmitida(
        tipo_operacao='0' if tipo == 'entrada' else '1', finalidade='4' if tipo == 'devolucao' else '1',
        presenca='0' if tipo == 'devolucao' else '1', ambiente=ConfiguracaoNFe.atual().ambiente,
        natureza={'entrada': 'Entrada de mercadoria', 'devolucao': 'Devolucao de mercadoria'}.get(tipo, 'Venda de mercadoria'))
    if tipo == 'devolucao':
        nota.forma_pagamento = '90'
    return _formulario(request, nota, nova=True)


@login_required
@exige('notas.emitir')
def nota_editar(request, pk):
    nota = _nota_ou_404(request, pk)
    if not nota.editavel:
        messages.error(request, f'Nota "{nota.get_status_display()}" não se edita.')
        return redirect('financeiro:nota', pk=nota.pk)
    return _formulario(request, nota, nova=False)


# ── A nota ──────────────────────────────────────────────────────────────────
@login_required
@exige('notas.ver')
def nota(request, pk):
    nota_obj = _nota_ou_404(request, pk)
    itens = list(nota_obj.itens.all())
    problemas = nfe.validar(nota_obj, itens) if nota_obj.editavel else []
    eventos = list(nota_obj.eventos.select_related('enviado_por'))
    return render(request, 'financeiro/nota.html', _contexto(
        request, nota=nota_obj, itens=itens, problemas=problemas, eventos=eventos,
        historico=_historico_para_tela(nota_obj),
        correcoes=[e for e in eventos if e.tipo == '110110' and e.sucesso],
        pode_excluir=nota_obj.status == 'RASCUNHO' and nota_obj.numero is None,
        id_dest=nfe.destino(nota_obj) if nota_obj.emitente.uf in nfe.CODIGOS_UF else ''))


def _historico_para_tela(nota_obj):
    from datetime import datetime
    linhas = []
    for h in reversed(nota_obj.historico or []):
        cstat = str(h.get('cstat', ''))
        try:
            quando = timezone.localtime(datetime.fromisoformat(h.get('quando', ''))).strftime('%d/%m/%Y %H:%M:%S')
        except (TypeError, ValueError):
            quando = h.get('quando', '')
        if cstat in ('100', '150', '135', '136', '155', '107'):
            classe = 'nfe-ok'
        elif cstat in ('103', '104', '105', ''):
            classe = ''
        else:
            classe = 'nfe-erro'
        linhas.append({**h, 'quando': quando, 'classe': classe})
    return linhas


@login_required
@exige('notas.emitir')
@require_POST
def nota_enviar(request, pk):
    nota_obj = _nota_ou_404(request, pk)
    try:
        nota_obj = nfe.enviar(nota_obj, usuario=request.user)
    except nfe.NfeInvalida as exc:
        messages.error(request, str(exc))
        return redirect('financeiro:nota', pk=pk)
    _registrar(request, 'EMITIR', f'Enviou a NF-e nº {nota_obj.numero} série {nota_obj.serie} à SEFAZ: '
                                  f'{nota_obj.cstat} {nota_obj.motivo}'[:300], nota_obj)
    _mensagem_da_situacao(request, nota_obj)
    return redirect('financeiro:nota', pk=pk)


def _mensagem_da_situacao(request, nota_obj):
    if nota_obj.status == 'AUTORIZADA':
        messages.success(request, f'NF-e nº {nota_obj.numero} autorizada · protocolo {nota_obj.protocolo}.')
    elif nota_obj.status == 'REJEITADA':
        messages.error(request, f'A SEFAZ recusou ({nota_obj.cstat}): {nota_obj.motivo}. Corrija e envie de novo '
                                f'(o número {nota_obj.numero} continua reservado para esta nota).')
    elif nota_obj.status == 'DENEGADA':
        messages.error(request, f'Uso denegado ({nota_obj.cstat}): {nota_obj.motivo}.')
    elif nota_obj.status == 'CANCELADA':
        messages.info(request, 'A nota consta como cancelada na SEFAZ.')
    else:
        messages.warning(request, f'A SEFAZ ainda não respondeu a autorização ({nota_obj.cstat}: {nota_obj.motivo}). '
                                  'Use "Consultar na SEFAZ" em instantes.')


@login_required
@exige('notas.ver')
@require_POST
def nota_consultar(request, pk):
    nota_obj = _nota_ou_404(request, pk)
    try:
        nota_obj = nfe.consultar(nota_obj)
    except (nfe.NfeInvalida, nfe.SefazErro) as exc:
        messages.error(request, str(exc))
        return redirect('financeiro:nota', pk=pk)
    _registrar(request, 'EDITAR', f'Consultou a NF-e {nota_obj.chave} na SEFAZ: '
                                  f'{nota_obj.get_status_display()}', nota_obj)
    _mensagem_da_situacao(request, nota_obj)
    return redirect('financeiro:nota', pk=pk)


@login_required
@exige('notas.cancelar')
@require_POST
def nota_cancelar(request, pk):
    nota_obj = _nota_ou_404(request, pk)
    try:
        evento = nfe.cancelar(nota_obj, request.POST.get('justificativa', ''), usuario=request.user)
    except (nfe.NfeInvalida, nfe.SefazErro) as exc:
        messages.error(request, str(exc))
        return redirect('financeiro:nota', pk=pk)
    _registrar(request, 'CANCELAR', f'Pediu o cancelamento da NF-e {nota_obj.chave}: {evento.cstat} '
                                    f'{evento.motivo}'[:300], nota_obj)
    if evento.sucesso:
        messages.success(request, f'NF-e cancelada · protocolo {evento.protocolo}.')
    else:
        messages.error(request, f'A SEFAZ não registrou o cancelamento ({evento.cstat}): {evento.motivo}')
    return redirect('financeiro:nota', pk=pk)


@login_required
@exige('notas.emitir')
@require_POST
def nota_correcao(request, pk):
    nota_obj = _nota_ou_404(request, pk)
    try:
        evento = nfe.corrigir(nota_obj, request.POST.get('correcao', ''), usuario=request.user)
    except (nfe.NfeInvalida, nfe.SefazErro) as exc:
        messages.error(request, str(exc))
        return redirect('financeiro:nota', pk=pk)
    _registrar(request, 'CORRIGIR', f'Carta de correção {evento.sequencia} na NF-e {nota_obj.chave}: '
                                    f'{evento.cstat} {evento.motivo}'[:300], nota_obj)
    if evento.sucesso:
        messages.success(request, f'Carta de correção nº {evento.sequencia} registrada · protocolo {evento.protocolo}.')
    else:
        messages.error(request, f'A SEFAZ não registrou a carta de correção ({evento.cstat}): {evento.motivo}')
    return redirect('financeiro:nota', pk=pk)


@login_required
@exige('notas.emitir')
@require_POST
def nota_excluir(request, pk):
    nota_obj = _nota_ou_404(request, pk)
    if nota_obj.status != 'RASCUNHO' or nota_obj.numero is not None:
        messages.error(request, 'Só rascunho que nunca foi enviado pode ser excluído.')
        return redirect('financeiro:nota', pk=pk)
    _registrar(request, 'EXCLUIR', f'Excluiu o rascunho de NF-e para {nota_obj.dest_nome}', nota_obj)
    nota_obj.delete()
    messages.success(request, 'Rascunho excluído.')
    return redirect('financeiro:notas')


def _baixar(conteudo, nome, tipo, inline=False):
    resposta = HttpResponse(conteudo, content_type=tipo)
    resposta['Content-Disposition'] = f'{"inline" if inline else "attachment"}; filename="{nome}"'
    resposta['Cache-Control'] = 'private, no-store'
    return resposta


@login_required
@exige('notas.ver')
def nota_xml(request, pk):
    nota_obj = _nota_ou_404(request, pk)
    if nota_obj.xml_autorizado:
        conteudo, nome = nota_obj.xml_autorizado, f'{nota_obj.chave}-procNFe.xml'
    elif nota_obj.xml_assinado:
        conteudo = '<?xml version="1.0" encoding="UTF-8"?>' + nota_obj.xml_assinado
        nome = f'{nota_obj.chave}-NAO-AUTORIZADA.xml'
    else:
        raise Http404('A nota ainda não tem XML (é rascunho).')
    auditoria.registrar(request, 'EXPORTAR', f'Baixou o XML da NF-e {nota_obj.chave}', area='notas', obj=nota_obj,
                        loja=nota_obj.loja)
    return _baixar(conteudo.encode('utf-8'), nome, 'application/xml')


@login_required
@exige('notas.ver')
def evento_xml(request, pk):
    evento = get_object_or_404(EventoNotaFiscal.objects.select_related('nota'), pk=pk)
    _nota_ou_404(request, evento.nota_id)
    conteudo = evento.xml_retorno if evento.sucesso else evento.xml_enviado
    if not conteudo:
        raise Http404
    nome = f'{evento.nota.chave}-{evento.tipo}-{evento.sequencia:02d}{"" if evento.sucesso else "-NAO-REGISTRADO"}.xml'
    return _baixar(conteudo.encode('utf-8'), nome, 'application/xml')


@login_required
@exige('notas.ver')
def nota_danfe(request, pk):
    nota_obj = _nota_ou_404(request, pk)
    if nota_obj.xml_autorizado:
        previa = 'NF-E CANCELADA' if nota_obj.status == 'CANCELADA' else ''
        pdf = danfe.gerar(nota_obj.xml_autorizado, previa=previa)
        nome = f'DANFE-{nota_obj.chave}.pdf'
    else:
        pdf = danfe.gerar(_xml_de_previa(nota_obj), previa='PRÉVIA — NOTA NÃO AUTORIZADA, SEM VALOR FISCAL')
        nome = f'DANFE-previa-{nota_obj.pk}.pdf'
    return _baixar(pdf, nome, 'application/pdf', inline=request.GET.get('baixar') != '1')


def _xml_de_previa(nota_obj):
    """O XML como sairia, só em memória (sem número reservado, sem assinatura)."""
    config = ConfiguracaoNFe.atual()
    copia = NotaFiscalEmitida.objects.get(pk=nota_obj.pk)
    itens = list(copia.itens.all())
    nfe.calcular(copia, itens)
    copia.serie = copia.serie or copia.emitente.serie
    copia.numero = copia.numero or 0
    copia.codigo_numerico = copia.codigo_numerico or '00000000'
    if copia.emitente.uf not in nfe.CODIGOS_UF or len(copia.emitente.cnpj) != 14:
        raise Http404('Emitente incompleto para montar a prévia.')
    momento = copia.emitida_em or timezone.now()
    copia.chave = nfe.gerar_chave(copia, momento) if not copia.chave else copia.chave
    config.ambiente = copia.ambiente or config.ambiente
    return etree_texto(nfe.montar_xml(copia, itens, config, momento))


def etree_texto(elemento):
    from lxml import etree
    return etree.tostring(elemento, encoding='unicode')


# ── Configuração (SUPERADMIN) ───────────────────────────────────────────────
def _so_superadmin(view):
    def _view(request, *args, **kwargs):
        if not e_superadmin(request.user):
            messages.error(request, 'A configuração da NF-e é só do SUPERADMIN.')
            return redirect('financeiro:notas')
        return view(request, *args, **kwargs)
    _view.__name__ = view.__name__
    return _view


CAMPOS_EMITENTE = ('cnpj', 'razao_social', 'nome_fantasia', 'ie', 'crt', 'logradouro', 'numero', 'complemento',
                   'bairro', 'codigo_municipio', 'municipio', 'uf', 'cep', 'telefone')


@login_required
@_so_superadmin
def notas_configuracao(request):
    config = ConfiguracaoNFe.atual()
    if request.method == 'POST':
        acao = request.POST.get('acao')
        try:
            with transaction.atomic():
                _configurar(request, config, acao)
        except nfe.NfeInvalida as exc:
            messages.error(request, str(exc))
        return redirect(request.path + ('#emitentes' if acao and acao.startswith('emitente') else ''))
    emitentes = EmitenteNFe.objects.select_related('loja').annotate(n_notas=Count('notas'))
    editar = None
    if request.GET.get('emitente'):
        editar = emitentes.filter(pk=request.GET['emitente']).first()
    return render(request, 'financeiro/notas_configuracao.html', _contexto(
        request, config=config, emitentes=emitentes, editar=editar, lojas=lojas_do(request.user),
        crts=EmitenteNFe.CRTS, ufs=UFS, ufs_atendidas=sorted(nfe.UFS_SVRS | {'SP'}),
        dias_certificado=((config.certificado_validade - timezone.now()).days if config.certificado_validade else None)))


def _configurar(request, config, acao):
    antes = auditoria.foto(config, ['ligado', 'ambiente', 'certificado_titular', 'certificado_validade',
                                    'resp_tec_cnpj', 'resp_tec_contato', 'resp_tec_email', 'resp_tec_fone'])
    post = request.POST
    if acao == 'geral':
        ligar = post.get('ligado') == '1'
        ambiente = post.get('ambiente') if post.get('ambiente') in ('1', '2') else '2'
        if ambiente == '1' and config.ambiente != '1' and post.get('confirmar_producao') != 'PRODUCAO':
            raise nfe.NfeInvalida('Para passar para produção, digite PRODUCAO na confirmação: ali as notas valem.')
        if ligar and not config.tem_certificado:
            raise nfe.NfeInvalida('Suba o certificado A1 antes de ligar a emissão.')
        config.ligado, config.ambiente = ligar, ambiente
        config.resp_tec_cnpj = nfe.so_digitos(post.get('resp_tec_cnpj'))[:14]
        config.resp_tec_contato = nfe.texto(post.get('resp_tec_contato'), 60)
        config.resp_tec_email = nfe.texto(post.get('resp_tec_email'), 60)
        config.resp_tec_fone = nfe.so_digitos(post.get('resp_tec_fone'))[:14]
        config.atualizado_por = request.user
        config.save()
        messages.success(request, f'Configuração salva: emissão {"LIGADA" if config.ligado else "desligada"}, '
                                  f'{config.get_ambiente_display().lower()}.')
    elif acao == 'certificado':
        arquivo = request.FILES.get('certificado')
        if not arquivo:
            raise nfe.NfeInvalida('Escolha o arquivo do certificado (.pfx ou .p12).')
        if arquivo.size > 50 * 1024:
            raise nfe.NfeInvalida('O arquivo é grande demais para um certificado A1.')
        nfe.guardar_certificado(config, arquivo.read(), post.get('senha', ''), usuario=request.user)
        messages.success(request, f'Certificado guardado (cifrado): {config.certificado_titular}, vale até '
                                  f'{timezone.localtime(config.certificado_validade):%d/%m/%Y}.')
    elif acao == 'remover_certificado':
        config.certificado_cifrado = config.senha_cifrada = ''
        config.certificado_titular = config.certificado_cnpj = ''
        config.certificado_validade = None
        config.ligado = False
        config.atualizado_por = request.user
        config.save()
        messages.success(request, 'Certificado apagado e emissão desligada.')
    elif acao == 'testar':
        uf = post.get('uf', 'ES')
        if not config.ligado:
            raise nfe.NfeInvalida('Ligue a emissão para testar a conexão com a SEFAZ.')
        try:
            resposta = nfe.status_servico(config, uf)
        except nfe.SefazErro as exc:
            raise nfe.NfeInvalida(f'Sem resposta da SEFAZ ({uf}): {exc}')
        nivel = messages.success if resposta['cstat'] == '107' else messages.warning
        nivel(request, f'SEFAZ {uf} ({config.get_ambiente_display().split(" ")[0].lower()}): '
                       f'{resposta["cstat"]} {resposta["motivo"]}.')
        return
    elif acao == 'emitente':
        _salvar_emitente(request)
        return
    elif acao == 'emitente_ativar':
        emitente = get_object_or_404(EmitenteNFe, pk=post.get('id'))
        emitente.ativo = not emitente.ativo
        emitente.save(update_fields=['ativo', 'atualizado_em'])
        auditoria.registrar(request, 'CONFIGURAR', f'{"Ativou" if emitente.ativo else "Desativou"} o emitente '
                                                   f'{emitente}', area='notas', obj=emitente, loja=emitente.loja)
        messages.success(request, f'Emitente {"ativado" if emitente.ativo else "desativado"}.')
        return
    else:
        raise nfe.NfeInvalida('Ação desconhecida.')
    a, d = auditoria.diferenca(antes, auditoria.foto(config, list(antes)))
    auditoria.registrar(request, 'CONFIGURAR', f'Configuração da NF-e: {acao}', area='notas', obj=config,
                        antes=a, depois=d)


def _salvar_emitente(request):
    post = request.POST
    emitente = EmitenteNFe.objects.filter(pk=post.get('id') or 0).first() or EmitenteNFe()
    nova = emitente.pk is None
    antes = None if nova else auditoria.foto(emitente)
    for campo in CAMPOS_EMITENTE:
        valor = (post.get(campo) or '').strip()
        if campo in ('cnpj', 'ie', 'codigo_municipio', 'cep', 'telefone'):
            valor = nfe.so_digitos(valor)
        else:
            valor = nfe.texto(valor, EmitenteNFe._meta.get_field(campo).max_length)
        setattr(emitente, campo, valor)
    emitente.uf = emitente.uf.upper()
    erros = []
    from contagem_caixa import cnpj as receita
    if not receita.valido(emitente.cnpj):
        erros.append('CNPJ inválido.')
    elif EmitenteNFe.objects.filter(cnpj=emitente.cnpj).exclude(pk=emitente.pk).exists():
        erros.append('Este CNPJ já está cadastrado.')
    if not emitente.ie:
        erros.append('Informe a inscrição estadual (só números).')
    if emitente.crt not in dict(EmitenteNFe.CRTS):
        erros.append('Escolha o regime tributário.')
    if emitente.uf not in nfe.CODIGOS_UF:
        erros.append('UF inválida.')
    if len(emitente.codigo_municipio) != 7:
        erros.append('O código IBGE do município tem 7 números.')
    elif emitente.uf in nfe.CODIGOS_UF and emitente.codigo_municipio[:2] != nfe.CODIGOS_UF[emitente.uf]:
        erros.append(f'O código do município não é de {emitente.uf} (começa com {nfe.CODIGOS_UF[emitente.uf]}).')
    if len(emitente.cep) != 8:
        erros.append('O CEP tem 8 números.')
    if not (emitente.razao_social and emitente.logradouro and emitente.bairro and emitente.municipio):
        erros.append('Razão social e endereço (logradouro, bairro, município) são obrigatórios.')
    try:
        emitente.serie = int(post.get('serie') or 1)
        emitente.proximo_numero = int(post.get('proximo_numero') or 1)
        emitente.proximo_numero_homologacao = int(post.get('proximo_numero_homologacao') or 1)
    except ValueError:
        erros.append('Série e próximo número são números inteiros.')
    if not (0 <= emitente.serie <= 889):
        erros.append('A série vai de 0 a 889.')
    if emitente.proximo_numero < 1 or emitente.proximo_numero_homologacao < 1:
        erros.append('O próximo número começa em 1.')
    loja_id = post.get('loja') or None
    emitente.loja = lojas_do(request.user).filter(pk=loja_id).first() if loja_id else None
    if erros:
        raise nfe.NfeInvalida(' · '.join(erros))
    emitente.save()
    a, d = auditoria.diferenca(antes, auditoria.foto(emitente))
    auditoria.registrar(request, 'CONFIGURAR', f'{"Cadastrou" if nova else "Editou"} o emitente {emitente}',
                        area='notas', obj=emitente, loja=emitente.loja, antes=a, depois=d)
    messages.success(request, f'Emitente {"cadastrado" if nova else "salvo"}: {emitente}.')
