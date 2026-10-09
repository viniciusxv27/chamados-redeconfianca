"""Contas a pagar · PIS/Cofins: lançar os documentos do mês e gerar a planilha.

Quem cuida das contas lança no dia a dia a nota, o boleto ou o recibo de
aluguel com competência, fornecedor (CNPJ) e valor; o cadastro do fornecedor
vem da Receita. No fechamento, a planilha de PIS/Cofins da competência sai
pronta — e o pacote .zip leva junto todos os arquivos, para a contabilidade.
"""
from datetime import date
from decimal import Decimal
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.core.files.base import ContentFile
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from . import cnpj as receita
from .models import DocumentoPisCofins, FornecedorPisCofins, NotaRecebida, SincronizacaoDFe
from .permissions import lojas, pode_piscofins
from .views import ValorInvalido, _para_decimal

ZERO = Decimal('0.00')
TAMANHO_MAXIMO = 15 * 1024 * 1024
EXTENSOES = ('.pdf', '.jpg', '.jpeg', '.png', '.webp', '.heic', '.xml')
# Cadastro do fornecedor mais velho que isto é consultado de novo na Receita.
DIAS_PARA_RECONSULTAR = 30
MESES_NO_SELETOR = 18


def _liberado(view):
    @wraps(view)
    def _view(request, *args, **kwargs):
        if not pode_piscofins(request.user):
            if request.headers.get('Accept', '').startswith('application/json') or '/cnpj/' in request.path:
                return JsonResponse({'ok': False, 'erro': 'Sem acesso ao PIS/Cofins.'}, status=403)
            messages.error(request, 'Contas a pagar · PIS/Cofins não está liberado para o seu usuário.')
            return redirect('home')
        return view(request, *args, **kwargs)
    return _view


def _competencia(texto, padrao=None):
    """'2026-10' (ou '2026-10-01') → date(2026, 10, 1)."""
    texto = (texto or '').strip()
    try:
        ano, mes = int(texto[:4]), int(texto[5:7])
        return date(ano, mes, 1)
    except (ValueError, IndexError):
        return padrao


def _meses(ate, n):
    ano, mes = ate.year, ate.month
    for _ in range(n):
        yield date(ano, mes, 1)
        ano, mes = (ano, mes - 1) if mes > 1 else (ano - 1, 12)


def _voltar(request, competencia=None):
    destino = request.POST.get('voltar') or ''
    if destino.startswith('/contagem-caixa/pis-cofins/'):
        return redirect(destino)
    url = reverse('contagem_caixa:piscofins')
    return redirect(f'{url}?competencia={competencia:%Y-%m}' if competencia else url)


# ── Fornecedor ──────────────────────────────────────────────────────────────
def _gravar_fornecedor(dados):
    campos = {k: dados.get(k) for k in ('razao_social', 'nome_fantasia', 'situacao', 'atividade',
                                         'simples', 'municipio', 'uf')}
    campos = {k: (v[:200] if isinstance(v, str) else v) for k, v in campos.items()}
    campos['atividade'] = (campos['atividade'] or '')[:255]
    campos['situacao'] = (campos['situacao'] or '')[:40]
    campos['municipio'] = (campos['municipio'] or '')[:80]
    campos['uf'] = (campos['uf'] or '')[:2]
    fornecedor, _ = FornecedorPisCofins.objects.update_or_create(
        cnpj=dados['cnpj'], defaults=dict(campos, consultado_em=timezone.now()))
    return fornecedor


def fornecedor_do_cnpj(cnpj, razao_digitada=''):
    """O fornecedor do CNPJ: do cadastro, da Receita ou (sem Receita) com a razão digitada.

    Devolve (fornecedor, erro).
    """
    digitos = receita.so_digitos(cnpj)
    if not receita.valido(digitos):
        return None, f'CNPJ {cnpj or "(vazio)"} não é válido: confira os 14 números.'
    fornecedor = FornecedorPisCofins.objects.filter(cnpj=digitos).first()
    velho = (fornecedor is None or fornecedor.consultado_em is None
             or (timezone.now() - fornecedor.consultado_em).days >= DIAS_PARA_RECONSULTAR)
    if velho:
        try:
            dados = receita.buscar(digitos)
        except receita.CnpjIndisponivel:
            dados = False
        if dados:
            return _gravar_fornecedor(dados), ''
        if dados is None and fornecedor is None:
            return None, f'O CNPJ {receita.formatar(digitos)} não consta na Receita Federal.'
    if fornecedor:
        return fornecedor, ''
    razao = (razao_digitada or '').strip()[:200]
    if not razao:
        return None, ('A Receita não respondeu agora. Digite a razão social do fornecedor para lançar '
                      'mesmo assim — o cadastro é conferido de novo na próxima consulta.')
    return FornecedorPisCofins.objects.create(cnpj=digitos, razao_social=razao), ''


def _fornecedor_json(f, origem):
    return {
        'cnpj': f.cnpj, 'cnpj_formatado': f.cnpj_formatado, 'razao_social': f.razao_social,
        'nome_fantasia': f.nome_fantasia, 'situacao': f.situacao, 'situacao_ok': f.situacao_ok,
        'atividade': f.atividade, 'simples': f.simples, 'municipio': f.municipio, 'uf': f.uf,
        'link_receita': f.link_receita, 'origem': origem,
        'consultado_em': f.consultado_em.strftime('%d/%m/%Y') if f.consultado_em else '',
    }


@login_required
@_liberado
def piscofins_cnpj(request):
    """Consulta do CNPJ para o formulário: preenche a razão social e mostra a situação."""
    cnpj = request.GET.get('cnpj') or ''
    digitos = receita.so_digitos(cnpj)
    if not receita.valido(digitos):
        return JsonResponse({'ok': False, 'erro': 'CNPJ inválido: confira os 14 números.'})
    fornecedor = FornecedorPisCofins.objects.filter(cnpj=digitos).first()
    try:
        dados = receita.buscar(digitos)
    except receita.CnpjIndisponivel:
        if fornecedor:
            return JsonResponse({'ok': True, 'fornecedor': _fornecedor_json(fornecedor, 'cadastro')})
        return JsonResponse({'ok': False, 'indisponivel': True,
                             'erro': 'A Receita não respondeu agora. Digite a razão social para seguir.',
                             'link_receita': receita.LINK_RECEITA.format(cnpj=digitos)})
    if dados is None:
        return JsonResponse({'ok': False, 'erro': 'Este CNPJ não consta na Receita Federal.'})
    return JsonResponse({'ok': True, 'fornecedor': _fornecedor_json(_gravar_fornecedor(dados), 'receita')})


@login_required
@_liberado
@require_POST
def piscofins_ler(request):
    """Lê o arquivo escolhido no formulário e devolve os campos para pré-preencher.

    Não grava nada: o arquivo vai de novo no envio do formulário.
    """
    from .leitura_documento import ler_documento

    arquivo = request.FILES.get('arquivo')
    if not arquivo:
        return JsonResponse({'ok': False, 'erro': 'Escolha o arquivo.'}, status=400)
    if arquivo.size > TAMANHO_MAXIMO:
        return JsonResponse({'ok': False, 'erro': 'O arquivo passa de 15 MB.'})
    if not arquivo.name.lower().endswith(EXTENSOES):
        return JsonResponse({'ok': False, 'erro': 'Envie PDF, foto (JPG, PNG, WEBP, HEIC) ou o XML da nota.'})
    lido = ler_documento(arquivo.read(), arquivo.name, getattr(arquivo, 'content_type', '') or '')
    campos = lido['campos']
    # A competência não pode passar do mês atual (o formulário recusaria).
    hoje = timezone.localdate()
    if campos.get('competencia') and campos['competencia'] > f'{hoje:%Y-%m}':
        campos.pop('competencia')
    if campos.get('cnpj'):
        fornecedor = FornecedorPisCofins.objects.filter(cnpj=campos['cnpj']).first()
        if fornecedor and campos.get('numero') and campos.get('tipo'):
            repetido = DocumentoPisCofins.objects.filter(
                fornecedor=fornecedor, tipo=campos['tipo'], numero__iexact=campos['numero']).first()
            if repetido:
                lido['avisos'].insert(0, f'Este documento já foi lançado (competência {repetido.competencia:%m/%Y}, '
                                         f'R$ {repetido.valor}).')
    if campos.get('valor'):
        campos['valor'] = campos['valor'].replace('.', ',')
    return JsonResponse({'ok': True, 'campos': campos, 'fonte': lido['fonte'], 'avisos': lido['avisos']})


# ── Tela ────────────────────────────────────────────────────────────────────
def _consulta(competencia, request):
    qs = DocumentoPisCofins.objects.filter(competencia=competencia)
    tipo = request.GET.get('tipo') or ''
    if tipo in dict(DocumentoPisCofins.TIPOS):
        qs = qs.filter(tipo=tipo)
    q = (request.GET.get('q') or '').strip()
    if q:
        digitos = receita.so_digitos(q)
        filtro = (Q(fornecedor__razao_social__icontains=q) | Q(fornecedor__nome_fantasia__icontains=q)
                  | Q(numero__icontains=q) | Q(descricao__icontains=q))
        if len(digitos) >= 4:
            filtro |= Q(fornecedor__cnpj__contains=digitos)
        qs = qs.filter(filtro)
    return qs, {'tipo': tipo if tipo in dict(DocumentoPisCofins.TIPOS) else '', 'q': q}


def _duplicados(qs):
    """Ids de documentos que parecem o mesmo lançado duas vezes no mês.

    Mesmo fornecedor e mesmo número; ou mesmo fornecedor e valor quando algum
    dos dois veio sem número (aí não há como saber se é outro documento).
    """
    por_numero, por_valor = {}, {}
    for d in qs.values('id', 'fornecedor_id', 'numero', 'valor'):
        numero = (d['numero'] or '').strip().lower().lstrip('0')
        if numero:
            por_numero.setdefault((d['fornecedor_id'], numero), []).append(d['id'])
        por_valor.setdefault((d['fornecedor_id'], d['valor']), []).append((d['id'], bool(numero)))
    repetidos = set()
    for ids in por_numero.values():
        if len(ids) > 1:
            repetidos.update(ids)
    for itens in por_valor.values():
        if len(itens) > 1 and not all(tem_numero for _, tem_numero in itens):
            repetidos.update(i for i, _ in itens)
    return repetidos


@login_required
@_liberado
def piscofins(request):
    hoje = timezone.localdate()
    atual = date(hoje.year, hoje.month, 1)
    competencia = _competencia(request.GET.get('competencia'), atual)
    qs, filtro = _consulta(competencia, request)
    do_mes = DocumentoPisCofins.objects.filter(competencia=competencia)

    totais = do_mes.aggregate(total=Sum('valor'), n=Count('id'))
    por_tipo = {t['tipo']: t for t in do_mes.values('tipo').annotate(total=Sum('valor'), n=Count('id'))}
    tipos = [{'chave': k, 'rotulo': r, 'total': por_tipo.get(k, {}).get('total') or ZERO,
              'n': por_tipo.get(k, {}).get('n') or 0} for k, r in DocumentoPisCofins.TIPOS]
    por_fornecedor = list(do_mes.values('fornecedor__razao_social', 'fornecedor__cnpj')
                          .annotate(total=Sum('valor'), n=Count('id')).order_by('-total')[:8])

    contagem = dict(DocumentoPisCofins.objects.filter(competencia__gte=date(atual.year - 2, 1, 1))
                    .values_list('competencia').annotate(n=Count('id')))
    meses = [{'data': m, 'n': contagem.get(m, 0)} for m in _meses(atual, MESES_NO_SELETOR)]
    if competencia not in [m['data'] for m in meses]:
        meses.append({'data': competencia, 'n': do_mes.count()})

    documentos = list(qs.select_related('fornecedor', 'loja', 'registrado_por')
                      .prefetch_related('notas_sefaz').order_by('-criado_em'))
    repetidos = _duplicados(do_mes)
    for d in documentos:
        d.possivel_duplicado = d.id in repetidos
    alertas = {
        'inativos': do_mes.exclude(fornecedor__situacao__in=['', 'ATIVA']).values('fornecedor').distinct().count(),
        'duplicados': len(repetidos),
        'sem_numero': do_mes.filter(numero='').exclude(tipo='ALUGUEL').count(),
    }

    parametros = request.GET.copy()
    parametros['competencia'] = f'{competencia:%Y-%m}'
    return render(request, 'contagem_caixa/piscofins.html', {
        'aba': 'piscofins',
        'competencia': competencia,
        'competencia_atual': atual,
        'meses': meses,
        'filtro': filtro,
        'tipos': tipos,
        'tipos_choices': DocumentoPisCofins.TIPOS,
        'total': totais['total'] or ZERO,
        'quantidade': totais['n'] or 0,
        'por_fornecedor': por_fornecedor,
        'documentos': documentos,
        'alertas': alertas,
        'lojas_todas': lojas(),
        'hoje': hoje,
        'query': parametros.urlencode(),
        'extensoes': ','.join(EXTENSOES),
        **_contexto_sefaz(request, competencia),
    })


def _contexto_sefaz(request, competencia):
    """A visão das notas emitidas contra o CNPJ (SEFAZ) para a competência."""
    from . import sefaz

    visao = 'sefaz' if request.GET.get('visao') == 'sefaz' else 'lancados'
    proximo = date(competencia.year + (competencia.month == 12), competencia.month % 12 + 1, 1)
    do_mes = NotaRecebida.objects.filter(emissao__date__gte=competencia, emissao__date__lt=proximo)
    validas = do_mes.exclude(situacao='CANCELADA')
    a_lancar = validas.filter(documento__isnull=True, ignorada=False)
    contexto = {
        'visao': visao,
        'sefaz_configurado': sefaz.configurado(),
        'sefaz_a_lancar': a_lancar.count(),
        'sefaz_a_lancar_valor': a_lancar.aggregate(s=Sum('valor'))['s'] or ZERO,
    }
    if visao != 'sefaz':
        return contexto
    situacao = request.GET.get('nf') or ''
    notas = do_mes
    if situacao == 'a_lancar':
        notas = a_lancar
    elif situacao == 'lancadas':
        notas = validas.filter(documento__isnull=False)
    elif situacao == 'fora':
        notas = validas.filter(ignorada=True, documento__isnull=True)
    elif situacao == 'canceladas':
        notas = do_mes.filter(situacao='CANCELADA')
    q = (request.GET.get('q') or '').strip()
    if q:
        digitos = receita.so_digitos(q)
        filtro = Q(emitente_nome__icontains=q) | Q(numero=digitos.lstrip('0') or q)
        if len(digitos) >= 4:
            filtro |= Q(emitente_cnpj__contains=digitos) | Q(chave__contains=digitos)
        notas = notas.filter(filtro)
    sincronizacoes = {s.cnpj: s for s in SincronizacaoDFe.objects.all()}
    contexto.update({
        'nf_situacao': situacao,
        'notas': list(notas.select_related('documento').order_by('-emissao')[:500]),
        'notas_total': validas.count(),
        'notas_valor': validas.aggregate(s=Sum('valor'))['s'] or ZERO,
        'notas_lancadas': validas.filter(documento__isnull=False).count(),
        'notas_fora': validas.filter(ignorada=True, documento__isnull=True).count(),
        'notas_canceladas': do_mes.filter(situacao='CANCELADA').count(),
        'sefaz_cnpjs': [{'cnpj': receita.formatar(c), 'sinc': sincronizacoes.get(c)} for c in sefaz.cnpjs_monitorados()],
        'sefaz_certificado': sefaz.info_do_certificado() if contexto['sefaz_configurado'] else None,
    })
    return contexto


@login_required
@_liberado
@require_POST
def piscofins_sefaz_buscar(request):
    """Busca agora as notas novas na SEFAZ (respeitando a espera de 1 hora)."""
    from . import sefaz

    if not sefaz.configurado():
        messages.error(request, 'A busca na SEFAZ ainda não foi ligada: falta o certificado digital A1 da empresa.')
        return _voltar(request)
    novas, esperando, erros = 0, [], []
    for sinc in sefaz.sincronizar_todos():
        if getattr(sinc, 'pulada', False):
            esperando.append(f'{receita.formatar(sinc.cnpj)} (de novo às {timezone.localtime(sinc.proxima_consulta):%H:%M})')
        elif sinc.ultimo_cstat == 'ERRO' or sinc.ultimo_cstat not in ('137', '138', '656', ''):
            erros.append(f'{receita.formatar(sinc.cnpj)}: {sinc.ultima_mensagem}')
        else:
            novas += sinc.notas_novas
    if novas or not (esperando or erros):
        messages.success(request, f'{novas} nota(s) nova(s) da SEFAZ.' if novas else 'Nenhuma nota nova na SEFAZ.')
    if esperando:
        messages.info(request, 'A SEFAZ só aceita uma consulta por hora sem novidade. Aguardando: ' + '; '.join(esperando))
    for erro in erros:
        messages.error(request, f'SEFAZ — {erro}')
    return _voltar(request)


@login_required
@_liberado
@require_POST
def piscofins_nota_fora(request, pk):
    """Marca (ou desmarca) a nota como fora do PIS/Cofins — some da lista "a lançar"."""
    nota = get_object_or_404(NotaRecebida, pk=pk)
    nota.ignorada = not nota.ignorada
    nota.ignorada_por = request.user if nota.ignorada else None
    nota.save(update_fields=['ignorada', 'ignorada_por', 'atualizada_em'])
    messages.success(request, f'NF {nota.numero} de {nota.emitente_nome} '
                              + ('marcada como fora do PIS/Cofins.' if nota.ignorada else 'voltou para "a lançar".'))
    return _voltar(request)


# ── Lançar, editar, apagar ──────────────────────────────────────────────────
def _ler_formulario(request, documento=None):
    """Valida o formulário. Devolve (dados, erro)."""
    tipo = request.POST.get('tipo') or ''
    if tipo not in dict(DocumentoPisCofins.TIPOS):
        return None, 'Escolha o tipo: nota fiscal, boleto, recibo de aluguel ou outro.'
    competencia = _competencia(request.POST.get('competencia'))
    if competencia is None:
        return None, 'Informe o mês de competência.'
    hoje = timezone.localdate()
    if competencia > date(hoje.year, hoje.month, 1):
        return None, 'A competência não pode ser um mês que ainda não começou.'
    try:
        valor = _para_decimal(request.POST.get('valor'))
    except ValorInvalido:
        return None, f'"{request.POST.get("valor")}" não é um valor válido.'
    if valor is None or valor <= 0:
        return None, 'Informe o valor do documento.'
    data_documento = None
    if request.POST.get('data_documento'):
        data_documento = parse_date(request.POST['data_documento'])
        if data_documento is None or data_documento > hoje:
            return None, 'A data de emissão não pode ser no futuro.'
    loja = None
    if request.POST.get('loja'):
        loja = lojas().filter(id=request.POST['loja']).first()
    arquivo = request.FILES.get('arquivo')
    if arquivo:
        if arquivo.size > TAMANHO_MAXIMO:
            return None, 'O arquivo passa de 15 MB.'
        if not arquivo.name.lower().endswith(EXTENSOES):
            return None, 'Envie PDF, foto (JPG, PNG, WEBP, HEIC) ou o XML da nota.'
    nota = None
    if request.POST.get('nota'):
        nota = NotaRecebida.objects.filter(id=request.POST['nota']).first()
    if not arquivo and documento is None:
        if nota is None:
            return None, 'Anexe o arquivo: a nota, o boleto ou o recibo.'
        # Lançada a partir da nota da SEFAZ: o XML dela é o arquivo.
        arquivo = ContentFile((nota.xml_completo or nota.xml_resumo).encode('utf-8'), name=f'NFe{nota.chave}.xml')
    fornecedor, erro = fornecedor_do_cnpj(request.POST.get('cnpj'), request.POST.get('razao_social'))
    if erro:
        return None, erro
    numero = (request.POST.get('numero') or '').strip()[:60]
    if numero:
        mesmo = DocumentoPisCofins.objects.filter(
            fornecedor=fornecedor, tipo=tipo, numero__iexact=numero)
        if documento is not None:
            mesmo = mesmo.exclude(id=documento.id)
        repetido = mesmo.first()
        if repetido:
            return None, (f'{repetido.get_tipo_display()} nº {numero} de {fornecedor.razao_social} já foi lançado '
                          f'(competência {repetido.competencia:%m/%Y}, R$ {repetido.valor}).')
    return {'tipo': tipo, 'competencia': competencia, 'valor': valor, 'numero': numero,
            'data_documento': data_documento, 'loja': loja, 'fornecedor': fornecedor,
            'descricao': (request.POST.get('descricao') or '').strip()[:255], 'arquivo': arquivo,
            'nota': nota}, ''


@login_required
@_liberado
@require_POST
def piscofins_registrar(request):
    dados, erro = _ler_formulario(request)
    if erro:
        messages.error(request, erro)
        return _voltar(request, _competencia(request.POST.get('competencia')))
    arquivo = dados.pop('arquivo')
    nota = dados.pop('nota')
    documento = DocumentoPisCofins(registrado_por=request.user, nome_arquivo=arquivo.name[:255], **dados)
    documento.arquivo = arquivo
    documento.save()
    if nota is not None and nota.documento_id is None:
        nota.documento = documento
        nota.ignorada = False
        nota.save(update_fields=['documento', 'ignorada', 'atualizada_em'])
    else:
        from .sefaz import vincular_lancados
        vincular_lancados(NotaRecebida.objects.filter(emitente_cnpj=documento.fornecedor.cnpj))
    aviso = ''
    if not documento.fornecedor.situacao_ok:
        aviso = f' Atenção: o CNPJ está {documento.fornecedor.situacao} na Receita.'
    messages.success(request, f'{documento.get_tipo_display()} de {documento.fornecedor.razao_social} '
                              f'(R$ {documento.valor}) lançado na competência {documento.competencia:%m/%Y}.{aviso}')
    return _voltar(request, documento.competencia)


@login_required
@_liberado
@require_POST
def piscofins_editar(request, pk):
    documento = get_object_or_404(DocumentoPisCofins, pk=pk)
    dados, erro = _ler_formulario(request, documento)
    if erro:
        messages.error(request, erro)
        return _voltar(request, documento.competencia)
    arquivo = dados.pop('arquivo')
    dados.pop('nota')
    for campo, valor in dados.items():
        setattr(documento, campo, valor)
    if arquivo:
        antigo = documento.arquivo
        documento.arquivo = arquivo
        documento.nome_arquivo = arquivo.name[:255]
        if antigo:
            try:
                antigo.storage.delete(antigo.name)
            except Exception:  # noqa: BLE001 — o novo arquivo vale mesmo assim
                pass
    documento.save()
    messages.success(request, 'Documento atualizado.')
    return _voltar(request, documento.competencia)


@login_required
@_liberado
@require_POST
def piscofins_apagar(request, pk):
    documento = get_object_or_404(DocumentoPisCofins, pk=pk)
    competencia = documento.competencia
    if documento.arquivo:
        try:
            documento.arquivo.delete(save=False)
        except Exception:  # noqa: BLE001
            pass
    documento.delete()
    messages.success(request, 'Documento apagado.')
    return _voltar(request, competencia)


# ── Planilha e pacote ───────────────────────────────────────────────────────
def _documentos_do_mes(competencia):
    return list(DocumentoPisCofins.objects.filter(competencia=competencia)
                .select_related('fornecedor', 'loja', 'registrado_por')
                .order_by('fornecedor__razao_social', 'data_documento', 'id'))


@login_required
@_liberado
def piscofins_planilha(request):
    from .exportacao_piscofins import planilha_piscofins

    competencia = _competencia(request.GET.get('competencia'))
    if competencia is None:
        messages.error(request, 'Escolha a competência da planilha.')
        return redirect('contagem_caixa:piscofins')
    conteudo = planilha_piscofins(competencia, _documentos_do_mes(competencia))
    resposta = HttpResponse(conteudo, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resposta['Content-Disposition'] = f'attachment; filename="pis_cofins_{competencia:%Y-%m}.xlsx"'
    return resposta


@login_required
@_liberado
def piscofins_pacote(request):
    """Planilha + todos os arquivos do mês num .zip, para mandar à contabilidade."""
    from .exportacao_piscofins import pacote_piscofins

    competencia = _competencia(request.GET.get('competencia'))
    if competencia is None:
        messages.error(request, 'Escolha a competência do pacote.')
        return redirect('contagem_caixa:piscofins')
    conteudo = pacote_piscofins(competencia, _documentos_do_mes(competencia))
    resposta = HttpResponse(conteudo, content_type='application/zip')
    resposta['Content-Disposition'] = f'attachment; filename="pis_cofins_{competencia:%Y-%m}.zip"'
    return resposta
