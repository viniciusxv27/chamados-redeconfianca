"""As regras do dinheiro: lançar, parcelar, baixar, estornar, cancelar e lançar no banco.

As telas só chamam daqui — assim a regra (e a auditoria) é uma só, venha o
pedido do formulário, do lote ou da entrada de nota fiscal.
"""
import calendar
import uuid
from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from . import auditoria
from .models import Baixa, MovimentoBancario, Titulo

ZERO = Decimal('0.00')
CENTAVO = Decimal('0.01')


class RegraFinanceira(Exception):
    """A operação não pode ser feita (a mensagem vai para a tela)."""


def somar_meses(dia, meses):
    mes = dia.month - 1 + meses
    ano = dia.year + mes // 12
    mes = mes % 12 + 1
    return date(ano, mes, min(dia.day, calendar.monthrange(ano, mes)[1]))


def dividir(valor, partes):
    """Divide em partes iguais; os centavos que sobram vão para a última."""
    base = (valor / partes).quantize(CENTAVO, rounding=ROUND_DOWN)
    valores = [base] * partes
    valores[-1] = valor - base * (partes - 1)
    return valores


def _area(titulo):
    return 'pagar' if titulo.tipo == 'PAGAR' else 'receber'


# ── Títulos ─────────────────────────────────────────────────────────────────
@transaction.atomic
def criar_titulos(dados, usuario=None, request=None, parcelas=1, intervalo='MENSAL', vencimentos=None,
                  valores=None, anexo=None):
    """Cria o título (ou as parcelas). `dados` são os campos do Titulo.

    `vencimentos`/`valores` (listas) mandam quando vêm de fora — as duplicatas
    da nota fiscal, por exemplo. Senão o valor é dividido em `parcelas` iguais,
    de mês em mês (ou a cada 7/15/30 dias) a partir do vencimento.
    """
    valor = Decimal(dados['valor'])
    if valor <= ZERO:
        raise RegraFinanceira('O valor precisa ser maior que zero.')
    parcelas = max(1, min(int(parcelas or 1), 120))
    if valores:
        parcelas = len(valores)
    elif parcelas > 1:
        valores = dividir(valor, parcelas)
    else:
        valores = [valor]
    if not vencimentos:
        primeiro = dados['vencimento']
        vencimentos = []
        for i in range(parcelas):
            if intervalo == 'MENSAL':
                vencimentos.append(somar_meses(primeiro, i))
            else:
                vencimentos.append(primeiro + timedelta(days=int(intervalo) * i))
    grupo = uuid.uuid4()
    criados = []
    for i, (venc, parte) in enumerate(zip(vencimentos, valores), start=1):
        campos = dict(dados, valor=parte, vencimento=venc, parcela=i, parcelas=parcelas, grupo=grupo,
                      criado_por=usuario)
        titulo = Titulo(**campos)
        if anexo is not None and i == 1:
            titulo.anexo = anexo
            titulo.anexo_nome = getattr(anexo, 'name', '')[:255]
        elif anexo is not None and criados:
            titulo.anexo = criados[0].anexo.name
            titulo.anexo_nome = criados[0].anexo_nome
        titulo.save()
        criados.append(titulo)
        auditoria.registrar(request, 'CRIAR', f'Lançou {titulo.get_tipo_display().lower()}: {titulo.descricao}'
                            + (f' ({titulo.rotulo_parcela})' if titulo.rotulo_parcela else ''),
                            area=_area(titulo), obj=titulo, valor=titulo.valor, depois=auditoria.foto(titulo),
                            usuario=usuario)
    return criados


@transaction.atomic
def editar_titulo(titulo, campos, usuario=None, request=None):
    titulo = Titulo.objects.select_for_update().get(pk=titulo.pk)
    if titulo.status == 'CANCELADO':
        raise RegraFinanceira('Título cancelado não se edita.')
    antes = auditoria.foto(titulo)
    novo_valor = Decimal(campos.get('valor', titulo.valor))
    if novo_valor < titulo.valor_baixado:
        raise RegraFinanceira(f'O valor não pode ficar abaixo do que já foi baixado (R$ {titulo.valor_baixado}).')
    for nome, valor in campos.items():
        setattr(titulo, nome, valor)
    titulo.save()
    atualizar_status(titulo)
    a, d = auditoria.diferenca(antes, auditoria.foto(titulo))
    if d:
        auditoria.registrar(request, 'EDITAR', f'Editou {titulo.get_tipo_display().lower()}: {titulo.descricao}',
                            area=_area(titulo), obj=titulo, valor=titulo.valor, antes=a, depois=d, usuario=usuario)
    return titulo


def atualizar_status(titulo):
    """Recalcula o baixado e a situação a partir das baixas válidas."""
    if titulo.status == 'CANCELADO':
        return titulo
    baixado = titulo.baixas.filter(estornada=False).aggregate(s=Sum('valor'))['s'] or ZERO
    titulo.valor_baixado = baixado
    if baixado <= ZERO:
        titulo.status = 'ABERTO'
    elif baixado >= titulo.valor:
        titulo.status = 'QUITADO'
    else:
        titulo.status = 'PARCIAL'
    titulo.save(update_fields=['valor_baixado', 'status', 'atualizado_em'])
    return titulo


@transaction.atomic
def cancelar_titulo(titulo, motivo, usuario=None, request=None):
    titulo = Titulo.objects.select_for_update().get(pk=titulo.pk)
    motivo = (motivo or '').strip()
    if len(motivo) < 5:
        raise RegraFinanceira('Diga o motivo do cancelamento.')
    if titulo.status == 'CANCELADO':
        raise RegraFinanceira('O título já está cancelado.')
    if titulo.baixas.filter(estornada=False).exists():
        raise RegraFinanceira('O título tem baixa. Estorne a baixa antes de cancelar.')
    antes = auditoria.foto(titulo, ['status'])
    titulo.status = 'CANCELADO'
    titulo.cancelado_por, titulo.cancelado_em, titulo.motivo_cancelamento = usuario, timezone.now(), motivo[:255]
    titulo.save()
    auditoria.registrar(request, 'CANCELAR', f'Cancelou {titulo.get_tipo_display().lower()}: {titulo.descricao} — {motivo}',
                        area=_area(titulo), obj=titulo, valor=titulo.valor, antes=antes,
                        depois={'status': 'CANCELADO', 'motivo': motivo}, usuario=usuario)
    return titulo


@transaction.atomic
def excluir_titulo(titulo, usuario=None, request=None):
    """Só some o que nunca teve baixa (nem estornada): o resto se cancela."""
    if titulo.baixas.exists():
        raise RegraFinanceira('Título que já teve baixa não se exclui — cancele (o histórico fica).')
    foto, descricao, valor, loja = auditoria.foto(titulo), titulo.descricao, titulo.valor, titulo.loja
    area = _area(titulo)
    auditoria.registrar(request, 'EXCLUIR', f'Excluiu {titulo.get_tipo_display().lower()}: {descricao}', area=area,
                        obj=titulo, valor=valor, loja=loja, antes=foto, usuario=usuario)
    titulo.delete()


# ── Baixas ──────────────────────────────────────────────────────────────────
def _sinal(titulo):
    return Decimal('-1') if titulo.tipo == 'PAGAR' else Decimal('1')


def _validar_valores(valor, juros, multa, desconto):
    for nome, v in (('valor', valor), ('juros', juros), ('multa', multa), ('desconto', desconto)):
        if v < ZERO:
            raise RegraFinanceira(f'O campo {nome} não pode ser negativo.')
    if valor <= ZERO:
        raise RegraFinanceira('O valor da baixa precisa ser maior que zero.')
    if valor + juros + multa - desconto <= ZERO:
        raise RegraFinanceira('O desconto não pode ser maior que o valor pago.')


@transaction.atomic
def baixar(titulo, data, valor, conta, forma, juros=ZERO, multa=ZERO, desconto=ZERO, observacao='',
           comprovante=None, usuario=None, request=None):
    """Paga (ou recebe) `valor` do título na conta. Parcial é permitido."""
    titulo = Titulo.objects.select_for_update().get(pk=titulo.pk)
    if titulo.status == 'CANCELADO':
        raise RegraFinanceira('Título cancelado não recebe baixa.')
    if titulo.status == 'QUITADO':
        raise RegraFinanceira('Este título já está quitado.')
    if not conta or not conta.ativa:
        raise RegraFinanceira('Escolha uma conta bancária ativa.')
    if data > timezone.localdate() + timedelta(days=1):
        raise RegraFinanceira('A data da baixa não pode ser no futuro.')
    _validar_valores(valor, juros, multa, desconto)
    if valor > titulo.saldo:
        raise RegraFinanceira(f'O valor passa do saldo do título (R$ {titulo.saldo}). Juros e multa vão nos campos próprios.')
    baixa = Baixa(titulo=titulo, data=data, valor=valor, juros=juros, multa=multa, desconto=desconto, conta=conta,
                  forma=forma or titulo.forma or 'OUTRO', observacao=(observacao or '')[:255], criado_por=usuario)
    if comprovante is not None:
        baixa.comprovante = comprovante
        baixa.comprovante_nome = getattr(comprovante, 'name', '')[:255]
    baixa.save()
    verbo = 'Pagamento' if titulo.tipo == 'PAGAR' else 'Recebimento'
    MovimentoBancario.objects.create(
        conta=conta, data=data, valor=_sinal(titulo) * baixa.total, origem='BAIXA', baixa=baixa,
        descricao=f'{verbo}: {titulo.descricao}'[:200], plano=titulo.plano, criado_por=usuario)
    atualizar_status(titulo)
    auditoria.registrar(request, 'BAIXAR',
                        f'{"Pagou" if titulo.tipo == "PAGAR" else "Recebeu"} {titulo.descricao}'
                        f'{" (" + titulo.rotulo_parcela + ")" if titulo.rotulo_parcela else ""} em {conta.nome}',
                        area='baixas', obj=baixa, valor=baixa.total, loja=titulo.loja,
                        depois=auditoria.foto(baixa), usuario=usuario)
    return baixa


def _desconciliar(movimento, usuario=None, request=None, motivo=''):
    linha = getattr(movimento, 'extrato_linha', None)
    if linha is not None:
        linha.movimento = None
        linha.conciliada_por = linha.conciliada_em = None
        linha.save(update_fields=['movimento', 'conciliada_por', 'conciliada_em'])
        auditoria.registrar(request, 'DESCONCILIAR', f'Desfez a conciliação de "{linha.descricao}" ({motivo})',
                            area='conciliacao', obj=linha, valor=linha.valor, loja=movimento.conta.loja,
                            usuario=usuario)
    if movimento.conciliado:
        movimento.conciliado = False
        movimento.save(update_fields=['conciliado'])


@transaction.atomic
def estornar_baixa(baixa, motivo, usuario=None, request=None):
    """"Cancelar" uma conta paga/recebida: a baixa fica no histórico, estornada."""
    baixa = Baixa.objects.select_for_update().select_related('titulo').get(pk=baixa.pk)
    motivo = (motivo or '').strip()
    if len(motivo) < 5:
        raise RegraFinanceira('Diga o motivo do estorno.')
    if baixa.estornada:
        raise RegraFinanceira('Esta baixa já foi estornada.')
    baixa.estornada, baixa.estornada_por, baixa.estornada_em = True, usuario, timezone.now()
    baixa.motivo_estorno = motivo[:255]
    baixa.save()
    movimento = getattr(baixa, 'movimento', None)
    if movimento is not None:
        _desconciliar(movimento, usuario, request, 'baixa estornada')
        movimento.estornado = True
        movimento.save(update_fields=['estornado'])
    atualizar_status(baixa.titulo)
    auditoria.registrar(request, 'ESTORNAR', f'Estornou a baixa de {baixa.titulo.descricao} — {motivo}',
                        area='baixas', obj=baixa, valor=baixa.total, loja=baixa.titulo.loja,
                        antes={'estornada': False}, depois={'estornada': True, 'motivo': motivo}, usuario=usuario)
    return baixa


@transaction.atomic
def editar_baixa(baixa, campos, usuario=None, request=None):
    """Corrige data, conta, forma, valores ou observação de uma conta paga/recebida."""
    baixa = Baixa.objects.select_for_update().select_related('titulo').get(pk=baixa.pk)
    if baixa.estornada:
        raise RegraFinanceira('Baixa estornada não se edita.')
    antes = auditoria.foto(baixa)
    for nome, valor in campos.items():
        setattr(baixa, nome, valor)
    _validar_valores(baixa.valor, baixa.juros, baixa.multa, baixa.desconto)
    outras = baixa.titulo.baixas.filter(estornada=False).exclude(pk=baixa.pk).aggregate(s=Sum('valor'))['s'] or ZERO
    if outras + baixa.valor > baixa.titulo.valor:
        raise RegraFinanceira('Com esse valor, as baixas passam do valor do título.')
    if not baixa.conta.ativa:
        raise RegraFinanceira('Escolha uma conta bancária ativa.')
    baixa.save()
    movimento = getattr(baixa, 'movimento', None)
    if movimento is not None:
        novo = _sinal(baixa.titulo) * baixa.total
        if (movimento.valor, movimento.data, movimento.conta_id) != (novo, baixa.data, baixa.conta_id):
            _desconciliar(movimento, usuario, request, 'baixa editada')
            movimento.valor, movimento.data, movimento.conta = novo, baixa.data, baixa.conta
            movimento.save(update_fields=['valor', 'data', 'conta'])
    atualizar_status(baixa.titulo)
    a, d = auditoria.diferenca(antes, auditoria.foto(baixa))
    if d:
        auditoria.registrar(request, 'EDITAR_BAIXA', f'Editou a baixa de {baixa.titulo.descricao}', area='baixas',
                            obj=baixa, valor=baixa.total, loja=baixa.titulo.loja, antes=a, depois=d, usuario=usuario)
    return baixa


# ── Banco ───────────────────────────────────────────────────────────────────
@transaction.atomic
def lancar_avulso(conta, data, valor, descricao, plano=None, usuario=None, request=None):
    """Tarifa, rendimento, ajuste: entra direto na conta (+ entrada, − saída)."""
    if not valor:
        raise RegraFinanceira('Informe um valor diferente de zero.')
    if not (descricao or '').strip():
        raise RegraFinanceira('Descreva o lançamento.')
    mov = MovimentoBancario.objects.create(conta=conta, data=data, valor=valor, descricao=descricao.strip()[:200],
                                           origem='AJUSTE', plano=plano, criado_por=usuario)
    auditoria.registrar(request, 'CRIAR', f'Lançou avulso em {conta.nome}: {mov.descricao}', area='bancos',
                        obj=mov, valor=valor, loja=conta.loja, depois=auditoria.foto(mov), usuario=usuario)
    return mov


@transaction.atomic
def transferir(origem, destino, data, valor, descricao='', usuario=None, request=None):
    if origem.pk == destino.pk:
        raise RegraFinanceira('Escolha contas diferentes.')
    if valor <= ZERO:
        raise RegraFinanceira('O valor da transferência precisa ser maior que zero.')
    par = uuid.uuid4()
    texto = (descricao or '').strip() or f'Transferência {origem.nome} → {destino.nome}'
    saida = MovimentoBancario.objects.create(conta=origem, data=data, valor=-valor, descricao=texto[:200],
                                             origem='TRANSFERENCIA', transferencia=par, criado_por=usuario)
    MovimentoBancario.objects.create(conta=destino, data=data, valor=valor, descricao=texto[:200],
                                     origem='TRANSFERENCIA', transferencia=par, criado_por=usuario)
    auditoria.registrar(request, 'CRIAR', f'Transferiu de {origem.nome} para {destino.nome}', area='bancos',
                        obj=saida, valor=valor, loja=origem.loja, usuario=usuario)
    return par


@transaction.atomic
def estornar_movimento(movimento, motivo, usuario=None, request=None):
    """Avulso e transferência se estornam aqui; o de baixa, pela própria baixa."""
    if movimento.origem == 'BAIXA':
        raise RegraFinanceira('Este movimento é de uma baixa: estorne a baixa (Contas pagas/recebidas).')
    if len((motivo or '').strip()) < 5:
        raise RegraFinanceira('Diga o motivo do estorno.')
    alvos = [movimento]
    if movimento.transferencia:
        alvos = list(MovimentoBancario.objects.filter(transferencia=movimento.transferencia))
    for mov in alvos:
        _desconciliar(mov, usuario, request, 'movimento estornado')
        mov.estornado = True
        mov.save(update_fields=['estornado'])
    auditoria.registrar(request, 'ESTORNAR', f'Estornou o lançamento "{movimento.descricao}" — {motivo.strip()}',
                        area='bancos', obj=movimento, valor=movimento.valor, loja=movimento.conta.loja,
                        usuario=usuario)
