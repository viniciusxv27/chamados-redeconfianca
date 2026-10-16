"""PIS/Cofins · a nota fiscal em si: ver, manifestar, importar pela chave e dar entrada."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import cnpj as receita
from . import manifestacao, nota_fiscal, sefaz
from .models import DocumentoPisCofins, ManifestacaoNFe, NotaRecebida
from .permissions import lojas
from .views import ValorInvalido, _para_decimal
from .views_piscofins import _competencia, _liberado, fornecedor_do_cnpj


def _registrar(request, acao, descricao, obj=None, valor=None, depois=None):
    """Rastreabilidade no mesmo livro do Financeiro."""
    try:
        from financeiro import auditoria
        auditoria.registrar(request, acao, descricao, area='piscofins', obj=obj, valor=valor, depois=depois)
    except Exception:  # noqa: BLE001 — o financeiro pode não estar migrado noutro servidor
        pass


def _pode_lancar_titulos(user):
    try:
        from financeiro.permissoes import pode
        return pode(user, 'pagar.editar')
    except Exception:  # noqa: BLE001
        return False


@login_required
@_liberado
def nota(request, pk):
    nota = get_object_or_404(NotaRecebida.objects.select_related('documento', 'documento__fornecedor'), pk=pk)
    dados = None
    if nota.tipo_documento == 'NFE':
        dados = nota_fiscal.ler_nfe(nota.xml_completo) if nota.xml_completo else None
    else:
        dados = nota_fiscal.ler_nfse(nota.xml_completo or nota.xml_resumo)
    manifestacoes = list(nota.manifestacoes.select_related('enviado_por'))
    atual = next((m for m in manifestacoes if m.sucesso), None)
    contexto = {
        'aba': 'piscofins', 'nota': nota, 'dados': dados, 'manifestacoes': manifestacoes, 'manifestacao_atual': atual,
        'tipos_manifestacao': ManifestacaoNFe.TIPOS, 'sefaz_configurado': sefaz.configurado(),
        'lojas_todas': lojas(), 'hoje': timezone.localdate(),
        'xml_texto': nota_fiscal.xml_bonito(nota.xml_completo or nota.xml_resumo),
        'pode_titulos': _pode_lancar_titulos(request.user),
        'titulos': list(nota.titulos.all()) if hasattr(nota, 'titulos') else [],
        'competencia_sugerida': (nota.competencia or (nota.emissao and timezone.localtime(nota.emissao).date())
                                 or timezone.localdate()).strftime('%Y-%m'),
    }
    if contexto['pode_titulos']:
        from financeiro.models import ContaBancaria, PlanoConta
        contexto['planos'] = PlanoConta.objects.filter(ativa=True, analitica=True, tipo='DESPESA')
        contexto['contas'] = ContaBancaria.objects.filter(ativa=True)
        duplicatas = (dados or {}).get('duplicatas') if nota.tipo_documento == 'NFE' else None
        if duplicatas:
            contexto['parcelas'] = [{'vencimento': d['vencimento'], 'valor': d['valor'], 'numero': d['numero']}
                                    for d in duplicatas]
        else:
            base = timezone.localtime(nota.emissao).date() if nota.emissao else timezone.localdate()
            contexto['parcelas'] = [{'vencimento': base + timedelta(days=30), 'valor': nota.valor_liquido or nota.valor,
                                     'numero': ''}]
    return render(request, 'contagem_caixa/piscofins_nota.html', contexto)


@login_required
@_liberado
def nota_xml(request, pk):
    nota = get_object_or_404(NotaRecebida, pk=pk)
    conteudo = nota.xml_completo or nota.xml_resumo
    resposta = HttpResponse(conteudo, content_type='application/xml; charset=utf-8')
    prefixo = 'NFSe' if nota.tipo_documento == 'NFSE' else 'NFe'
    resposta['Content-Disposition'] = f'attachment; filename="{prefixo}{nota.chave}.xml"'
    return resposta


@login_required
@_liberado
@require_POST
def manifestar(request, pk):
    nota = get_object_or_404(NotaRecebida, pk=pk)
    tipo = request.POST.get('tipo') or ''
    if not sefaz.configurado():
        messages.error(request, 'A SEFAZ não está ligada: falta o certificado A1.')
        return redirect('contagem_caixa:piscofins_nota', pk)
    try:
        registro = manifestacao.manifestar(nota, tipo, request.POST.get('justificativa') or '', usuario=request.user)
    except (manifestacao.ManifestacaoInvalida, sefaz.SefazErro) as exc:
        messages.error(request, str(exc))
        return redirect('contagem_caixa:piscofins_nota', pk)
    _registrar(request, 'MANIFESTAR', f'{registro.get_tipo_display()} da NF {nota.numero} de {nota.emitente_nome}: '
               f'{registro.cstat} {registro.motivo}', obj=registro, valor=nota.valor,
               depois={'evento': registro.tipo, 'cstat': registro.cstat, 'protocolo': registro.protocolo})
    if registro.sucesso:
        aviso = ' O XML completo fica disponível em alguns minutos — use "Baixar XML completo".' \
            if tipo == '210210' and not nota.xml_completo else ''
        messages.success(request, f'{registro.get_tipo_display()} registrada na SEFAZ'
                         + (f' (protocolo {registro.protocolo}).' if registro.protocolo else '.') + aviso)
    else:
        messages.error(request, f'A SEFAZ não registrou: {registro.cstat} — {registro.motivo}')
    return redirect('contagem_caixa:piscofins_nota', pk)


@login_required
@_liberado
@require_POST
def importar_chave(request):
    chave = receita.so_digitos(request.POST.get('chave'))
    cnpj = receita.so_digitos(request.POST.get('cnpj')) or (sefaz.cnpjs_monitorados() or [''])[0]
    existente = NotaRecebida.objects.filter(chave=chave).first()
    if existente and existente.xml_completo:
        messages.info(request, 'Esta nota já está no portal, com o XML completo.')
        return redirect('contagem_caixa:piscofins_nota', existente.pk)
    try:
        nota, mensagem = manifestacao.importar_por_chave(chave, cnpj)
    except (manifestacao.ManifestacaoInvalida, sefaz.SefazErro) as exc:
        messages.error(request, str(exc))
        return redirect(reverse('contagem_caixa:piscofins') + '?visao=sefaz')
    _registrar(request, 'IMPORTAR', f'Importou pela chave {chave}: {mensagem}', obj=nota,
               valor=nota.valor if nota else None)
    if nota is None:
        messages.error(request, mensagem)
        return redirect(reverse('contagem_caixa:piscofins') + '?visao=sefaz')
    messages.success(request, f'NF {nota.numero} de {nota.emitente_nome}: {mensagem}')
    return redirect('contagem_caixa:piscofins_nota', nota.pk)


@login_required
@_liberado
@require_POST
def baixar_xml(request, pk):
    nota = get_object_or_404(NotaRecebida, pk=pk)
    try:
        nota, mensagem = manifestacao.importar_por_chave(nota.chave, nota.cnpj_destinatario)
    except (manifestacao.ManifestacaoInvalida, sefaz.SefazErro) as exc:
        messages.error(request, str(exc))
        return redirect('contagem_caixa:piscofins_nota', pk)
    (messages.success if nota and nota.xml_completo else messages.info)(request, mensagem)
    return redirect('contagem_caixa:piscofins_nota', pk)


def _parcelas_do_post(request):
    vencimentos = request.POST.getlist('parcela_vencimento')
    valores = request.POST.getlist('parcela_valor')
    saida = []
    for venc, valor in zip(vencimentos, valores):
        if not venc and not valor:
            continue
        try:
            dia = date.fromisoformat(venc)
            quanto = _para_decimal(valor)
        except (ValueError, ValorInvalido):
            raise ValueError(f'Parcela inválida: {venc or "sem data"} / {valor or "sem valor"}.')
        if quanto is None or quanto <= 0:
            raise ValueError('Toda parcela precisa de valor maior que zero.')
        saida.append((dia, quanto))
    if not saida:
        raise ValueError('Informe ao menos um vencimento e valor.')
    return saida


@login_required
@_liberado
@require_POST
def dar_entrada(request, pk):
    """Lança a nota no PIS/Cofins e, se pedido, gera as contas a pagar no Financeiro."""
    nota = get_object_or_404(NotaRecebida.objects.select_related('documento'), pk=pk)
    if nota.situacao == 'CANCELADA':
        messages.error(request, 'A nota foi cancelada pelo emitente: não dá entrada.')
        return redirect('contagem_caixa:piscofins_nota', pk)
    competencia = _competencia(request.POST.get('competencia'))
    hoje = timezone.localdate()
    if competencia is None or competencia > date(hoje.year, hoje.month, 1):
        messages.error(request, 'Informe um mês de competência que já começou.')
        return redirect('contagem_caixa:piscofins_nota', pk)
    loja = lojas().filter(pk=request.POST.get('loja')).first() if (request.POST.get('loja') or '').isdigit() else None
    descricao = (request.POST.get('descricao') or '').strip()[:255]
    gerar = request.POST.get('gerar_titulos') == '1'
    if gerar and not _pode_lancar_titulos(request.user):
        messages.error(request, 'Lançar contas a pagar no Financeiro não está liberado para o seu usuário.')
        return redirect('contagem_caixa:piscofins_nota', pk)
    try:
        parcelas = _parcelas_do_post(request) if gerar else []
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect('contagem_caixa:piscofins_nota', pk)
    if gerar and nota.titulos.exclude(status='CANCELADO').exists():
        messages.error(request, 'Esta nota já tem contas a pagar no Financeiro.')
        return redirect('contagem_caixa:piscofins_nota', pk)

    fornecedor, erro = fornecedor_do_cnpj(nota.emitente_cnpj, nota.emitente_nome)
    if erro:
        messages.error(request, erro)
        return redirect('contagem_caixa:piscofins_nota', pk)

    with transaction.atomic():
        documento = nota.documento
        criou_documento = False
        if documento is None:
            prefixo = 'NFSe' if nota.tipo_documento == 'NFSE' else 'NFe'
            arquivo = ContentFile((nota.xml_completo or nota.xml_resumo).encode('utf-8'), name=f'{prefixo}{nota.chave}.xml')
            documento = DocumentoPisCofins(
                tipo='NF', competencia=competencia, fornecedor=fornecedor, valor=nota.valor, numero=nota.numero,
                data_documento=timezone.localtime(nota.emissao).date() if nota.emissao else None, loja=loja,
                descricao=descricao or nota.descricao[:255], registrado_por=request.user, nome_arquivo=arquivo.name)
            documento.arquivo = arquivo
            documento.save()
            nota.documento, nota.ignorada = documento, False
            nota.save(update_fields=['documento', 'ignorada', 'atualizada_em'])
            criou_documento = True
        titulos = []
        if gerar:
            from financeiro import servicos
            from financeiro.models import ContaBancaria, PlanoConta
            plano = PlanoConta.objects.filter(pk=request.POST.get('plano'), analitica=True).first() \
                if (request.POST.get('plano') or '').isdigit() else None
            conta = ContaBancaria.objects.filter(pk=request.POST.get('conta_prevista')).first() \
                if (request.POST.get('conta_prevista') or '').isdigit() else None
            forma = request.POST.get('forma') or 'BOLETO'
            titulos = servicos.criar_titulos(
                {'tipo': 'PAGAR', 'descricao': (descricao or f'NF {nota.numero} — {fornecedor.razao_social}')[:200],
                 'pessoa_nome': fornecedor.razao_social[:200], 'pessoa_documento': fornecedor.cnpj,
                 'numero_documento': nota.numero, 'emissao': documento.data_documento or timezone.localdate(),
                 'vencimento': parcelas[0][0], 'competencia': competencia, 'valor': sum(v for _d, v in parcelas),
                 'plano': plano, 'loja': loja or documento.loja, 'conta_prevista': conta, 'forma': forma,
                 'origem': 'NFE', 'nota': nota},
                usuario=request.user, request=request,
                vencimentos=[d for d, _v in parcelas], valores=[v for _d, v in parcelas])
    _registrar(request, 'CRIAR', f'Deu entrada na NF {nota.numero} de {fornecedor.razao_social}'
               + (' (PIS/Cofins)' if criou_documento else '') + (f' e gerou {len(titulos)} conta(s) a pagar' if titulos else ''),
               obj=documento, valor=nota.valor)
    partes = []
    if criou_documento:
        partes.append(f'lançada no PIS/Cofins ({competencia:%m/%Y})')
    if titulos:
        partes.append(f'{len(titulos)} conta(s) a pagar criada(s) no Financeiro')
    messages.success(request, f'Entrada da NF {nota.numero}: ' + (' e '.join(partes) if partes else 'nada novo a fazer') + '.')
    return redirect('contagem_caixa:piscofins_nota', pk)
