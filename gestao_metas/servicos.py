"""Regras do Gerenciamento de metas — as fórmulas da planilha, sem arrastar nada.

Para a competência M (o mês da meta):

- últimos 4 meses = M-4 … M-1; ``MEDIA QUAD`` = média dos que têm valor
  (o AVERAGE do Excel ignora célula vazia);
- ``REAL ANO ANTERIOR`` = realizado em M-12;
- ``META MÊS ANTERIOR`` = meta final da competência M-1 (Fixa: a nova meta, em
  quantidade); sem M-1 no módulo, o valor trazido da planilha antiga;
- comparações (todas IFERROR → 0, como na planilha):
  M×M = meta / realizado de M-1 − 1; Y×Y = meta / real ano anterior − 1;
  Meta×Quad = meta / média quad − 1; Meta×Meta ant. = meta / meta mês anterior − 1.

Fixa (quantidade): meta em R$ → QTD = ARREDONDAR.PARA.CIMA(meta / ticket
médio); por consultor, para cima e para baixo; a nova meta é o valor por
consultor escolhido (Cima/Baixo) × consultores. As comparações da Fixa usam a
nova meta contra quantidades (TV + Fixa).

O ticket médio (o XLOOKUP da planilha estava com #REF!) é o R$ da Fixa ÷ a
quantidade (TV + Fixa) somados nos 4 meses mais recentes antes de M em que a
loja tem os dois; sem isso, o ticket da rede no mesmo critério.
"""
import math
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from .models import (PILARES, Competencia, HistoricoMeta, LojaMeta, MetaLoja, Realizado)

MESES_CURTOS = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']
MESES_LONGOS = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro',
                'outubro', 'novembro', 'dezembro']
ORDEM_REGIOES = ['RJ', 'ES', 'SP']


# ---------------------------------------------------------------------------
# Datas e números
# ---------------------------------------------------------------------------
def somar_meses(d, n):
    total = d.year * 12 + (d.month - 1) + n
    return date(total // 12, total % 12 + 1, 1)


def primeiro_dia(d):
    return date(d.year, d.month, 1)


def rotulo_mes(d, longo=False):
    if longo:
        return '{} de {}'.format(MESES_LONGOS[d.month - 1], d.year)
    return '{}/{}'.format(MESES_CURTOS[d.month - 1], str(d.year)[2:])


def mes_do_codigo(codigo):
    """'2026-11' → date(2026, 11, 1); ValueError se não for um mês."""
    ano, mes = str(codigo or '').split('-')
    return date(int(ano), int(mes), 1)


def numero(valor):
    """Texto digitado ("15.700,50", "15700.5", "R$ 1.000") → Decimal; vazio → None."""
    if valor is None:
        return None
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, (int, float)):
        if isinstance(valor, float) and (math.isnan(valor) or math.isinf(valor)):
            return None
        return Decimal(str(valor))
    texto = str(valor).strip().replace('R$', '').replace(' ', '').replace('\xa0', '')
    if not texto or texto in ('-', '—'):
        return None
    if ',' in texto and '.' in texto:
        if texto.rfind(',') > texto.rfind('.'):
            texto = texto.replace('.', '').replace(',', '.')
        else:
            texto = texto.replace(',', '')
    elif ',' in texto:
        texto = texto.replace('.', '').replace(',', '.')
    elif re.fullmatch(r'-?[1-9]\d{0,2}(\.\d{3})+', texto):
        # "17.000" digitado é dezessete mil (ponto de milhar), não 17.
        texto = texto.replace('.', '')
    try:
        return Decimal(texto)
    except (InvalidOperation, ValueError):
        raise ValueError('Número inválido: {}'.format(valor))


def _f(valor):
    return None if valor is None else float(valor)


def variacao(meta, base):
    """IFERROR(meta / base − 1, 0). Sem meta digitada não há comparação (None)."""
    if meta is None:
        return None
    if not base:
        return 0.0
    return float(meta) / float(base) - 1


def media(valores):
    """AVERAGE do Excel: ignora o que está vazio; nada preenchido → None."""
    cheios = [float(v) for v in valores if v is not None]
    if not cheios:
        return None
    return sum(cheios) / len(cheios)


def _arred_cima(x):
    return int(math.ceil(x - 1e-9))


def _arred_baixo(x):
    return int(math.floor(x + 1e-9))


def calcular_fixa(meta, ticket, qtd_cns, decisao):
    """QTD, por consultor (cima/baixo) e a nova meta — as colunas E, F, G e I da aba FIXA."""
    saida = {'qtd': None, 'cima': None, 'baixo': None, 'nova_meta': None}
    if meta is None or not ticket:
        return saida
    qtd = _arred_cima(float(meta) / float(ticket))
    saida['qtd'] = qtd
    if qtd_cns:
        cima = _arred_cima(qtd / qtd_cns)
        baixo = _arred_baixo(qtd / qtd_cns)
        saida.update(cima=cima, baixo=baixo,
                     nova_meta=(baixo if decisao == MetaLoja.BAIXO else cima) * int(qtd_cns))
    else:
        # Sem consultor informado não há o que dividir: vale a quantidade inteira.
        saida['nova_meta'] = qtd
    return saida


# ---------------------------------------------------------------------------
# Lojas
# ---------------------------------------------------------------------------
def normalizar(nome):
    """Mesma chave da Visão SAP: sem "Loja", sem acento, maiúsculas ("L220" vira ITAIM PAULISTA)."""
    from auditoria_sap.permissions import normalizar_loja
    return ' '.join(normalizar_loja(nome).split())


def regiao_do_setor(setor):
    if not setor:
        return ''
    adabas = (setor.adabas or '').strip().upper()
    if adabas[:2] in ('RJ', 'ES', 'SP'):
        return adabas[:2]
    vivo = (setor.nome_loja_vivo or '').strip().upper()
    if vivo[-3:] in (' RJ', ' ES', ' SP'):
        return vivo[-2:]
    return ''


def garantir_lojas():
    """Na primeira vez, cria as lojas a partir dos setores "Loja …" do portal."""
    if LojaMeta.objects.exists():
        return
    from users.models import Sector
    for i, setor in enumerate(Sector.objects.filter(name__istartswith='loja').order_by('name')):
        chave = normalizar(setor.name)
        if not chave:
            continue
        LojaMeta.objects.get_or_create(chave=chave, defaults={
            'nome': chave, 'setor': setor, 'regiao': regiao_do_setor(setor), 'ordem': (i + 1) * 10,
            'pdv_vivo': setor.nome_loja_vivo or ''})


class MapaLojas:
    """Acha a loja por qualquer nome conhecido: PDV rede, PDV Vivo, outros nomes, setor do portal."""

    def __init__(self):
        self.por_chave = {}
        self.lojas = list(LojaMeta.objects.select_related('setor'))
        for loja in self.lojas:
            nomes = [loja.chave, loja.nome, loja.pdv_vivo] + (loja.apelidos or '').split(',')
            if loja.setor_id:
                nomes += [loja.setor.name, loja.setor.nome_loja_vivo or '']
            for nome in nomes:
                chave = normalizar(nome)
                if chave:
                    self.por_chave.setdefault(chave, loja)

    def achar(self, nome):
        return self.por_chave.get(normalizar(nome))

    def criar(self, nome, pdv_vivo='', ordem=0):
        """Loja nova a partir de uma planilha; liga ao setor do portal de mesmo nome, se houver."""
        from users.models import Sector
        chave = normalizar(nome)
        setor = None
        for s in Sector.objects.filter(name__istartswith='loja'):
            if normalizar(s.name) == chave:
                setor = s
                break
        loja, _ = LojaMeta.objects.get_or_create(chave=chave, defaults={
            'nome': ' '.join(str(nome).split()).upper(), 'pdv_vivo': pdv_vivo or '', 'setor': setor,
            'regiao': regiao_do_setor(setor), 'ordem': ordem})
        self.lojas.append(loja)
        self.por_chave[chave] = loja
        return loja


def lojas_da_competencia(competencia):
    """Lojas ativas mais as que já têm meta nesta competência (mesmo desativadas depois)."""
    com_meta = set(MetaLoja.objects.filter(competencia=competencia).values_list('loja_id', flat=True))
    lojas = [l for l in LojaMeta.objects.select_related('setor').order_by('ordem', 'nome')
             if l.ativa or l.id in com_meta]
    return lojas


# ---------------------------------------------------------------------------
# Competências
# ---------------------------------------------------------------------------
def competencia_anterior(competencia):
    return Competencia.objects.filter(mes=somar_meses(competencia.mes, -1)).first()


@transaction.atomic
def abrir_competencia(mes, user):
    """Cria a competência. A Fixa herda consultores e decisão do mês anterior."""
    mes = primeiro_dia(mes)
    competencia, criada = Competencia.objects.get_or_create(mes=mes, defaults={'criada_por': user})
    if criada:
        anterior = competencia_anterior(competencia)
        if anterior:
            for m in MetaLoja.objects.filter(competencia=anterior, pilar='fixa'):
                MetaLoja.objects.get_or_create(competencia=competencia, loja_id=m.loja_id, pilar='fixa',
                                               defaults={'qtd_cns': m.qtd_cns, 'decisao': m.decisao})
    return competencia, criada


def metas_anteriores(competencia):
    """{(loja_id, pilar): meta final de M-1} — e se M-1 está fechada."""
    anterior = competencia_anterior(competencia)
    if not anterior:
        return {}, None
    mapa = {}
    for m in MetaLoja.objects.filter(competencia=anterior):
        if m.meta_final is not None:
            mapa[(m.loja_id, m.pilar)] = Decimal(m.meta_final)
    return mapa, anterior


# ---------------------------------------------------------------------------
# Montagem da tabela de um pilar
# ---------------------------------------------------------------------------
def _realizados(lojas_ids, meses, pilares=None):
    qs = Realizado.objects.filter(loja_id__in=lojas_ids, mes__in=meses)
    if pilares:
        qs = qs.filter(pilar__in=pilares)
    return {(r.loja_id, r.mes, r.pilar): r for r in qs}


def _ticket(valores_qtds):
    """[(valor, qtd), …] do mais recente para o mais antigo → R$ ÷ quantidade nos 4 primeiros com os dois."""
    usados = [(v, q) for v, q in valores_qtds if v and q][:4]
    if not usados:
        return None
    soma_q = sum(q for _, q in usados)
    return float(sum(float(v) for v, _ in usados)) / soma_q if soma_q else None


def montar(competencia, pilares=None):
    """Tudo o que a tela (e o Excel) precisam: {pilar: {'linhas': [...], 'grupos': [...], 'rede': {...}}}."""
    pilares = pilares or [p for p, _ in PILARES]
    M = competencia.mes
    meses_quad = [somar_meses(M, -i) for i in (4, 3, 2, 1)]
    mes_ano = somar_meses(M, -12)
    meses_ticket = [somar_meses(M, -i) for i in range(1, 13)]
    lojas = lojas_da_competencia(competencia)
    ids = [l.id for l in lojas]
    reais = _realizados(ids, set(meses_quad + [mes_ano] + meses_ticket), pilares)
    metas = {(m.loja_id, m.pilar): m for m in MetaLoja.objects.filter(competencia=competencia, loja_id__in=ids)}
    anteriores, comp_anterior = metas_anteriores(competencia)

    # Ticket da rede (reserva para loja sem histórico de Fixa).
    rede_pares = []
    for mes in meses_ticket:
        v = sum(float(r.valor or 0) for (lid, m, p), r in reais.items() if m == mes and p == 'fixa')
        q = sum((r.quantidade or 0) for (lid, m, p), r in reais.items() if m == mes and p == 'fixa')
        rede_pares.append((v, q))
    ticket_rede = _ticket(rede_pares)

    saida = {}
    for pilar in pilares:
        fixa = pilar == 'fixa'
        linhas = []
        for loja in lojas:
            m = metas.get((loja.id, pilar))

            def real(mes, campo='valor'):
                r = reais.get((loja.id, mes, pilar))
                if r is None:
                    return None
                if campo == 'qtd':
                    return r.quantidade
                return _f(r.valor)

            campo = 'qtd' if fixa else 'valor'
            meses = [real(mes, campo) for mes in meses_quad]
            anterior = anteriores.get((loja.id, pilar))
            if anterior is None and m is not None and m.meta_anterior_manual is not None:
                anterior = m.meta_anterior_manual
            linha = {
                'loja': loja, 'loja_id': loja.id, 'regiao': loja.regiao or '',
                'meses': meses, 'ultimo': meses[-1], 'media_quad': media(meses),
                'real_ano_anterior': real(mes_ano, campo),
                'meta_vivo': _f(m.meta_vivo) if m else None,
                'meta_anterior': _f(anterior),
                'meta': _f(m.meta) if m else None,
            }
            if fixa:
                rs = reais
                linha['meses_valor'] = [real(mes) for mes in meses_quad]
                linha['media_quad_valor'] = media(linha['meses_valor'])
                linha['meses_tv'] = [getattr(rs.get((loja.id, mes, 'fixa')), 'qtd_tv', None) for mes in meses_quad]
                linha['meses_fixa'] = [getattr(rs.get((loja.id, mes, 'fixa')), 'qtd_fixa', None) for mes in meses_quad]
                linha['real_ano_anterior_valor'] = real(mes_ano)
                pares = [(real(mes), real(mes, 'qtd')) for mes in meses_ticket]
                ticket = _ticket(pares)
                linha['ticket_da_rede'] = ticket is None and ticket_rede is not None
                linha['ticket'] = ticket if ticket is not None else ticket_rede
                linha['qtd_cns'] = m.qtd_cns if m else None
                linha['decisao'] = m.decisao if m else MetaLoja.CIMA
                calc = calcular_fixa(linha['meta'], linha['ticket'], linha['qtd_cns'], linha['decisao'])
                linha.update(calc)
                alvo = calc['nova_meta']
            else:
                alvo = linha['meta']
            linha['alvo'] = alvo
            linha.update(_comparacoes(alvo, linha))
            linhas.append(linha)

        grupos = []
        for regiao in ORDEM_REGIOES + ['']:
            do_grupo = [l for l in linhas if l['regiao'] == regiao]
            if do_grupo and (regiao or len(do_grupo) != len(linhas)):
                grupos.append(_somar(do_grupo, regiao or 'Sem região', fixa))
        saida[pilar] = {
            'linhas': linhas, 'grupos': grupos, 'rede': _somar(linhas, 'REDE', fixa), 'fixa': fixa,
        }
    contexto = {
        'meses_quad': meses_quad, 'mes_ano': mes_ano, 'mes_anterior': somar_meses(M, -1),
        'competencia_anterior': comp_anterior, 'ticket_rede': ticket_rede,
    }
    return saida, contexto


def _comparacoes(alvo, linha):
    return {
        'mxm': variacao(alvo, linha['ultimo']),
        'yxy': variacao(alvo, linha['real_ano_anterior']),
        'mxquad': variacao(alvo, linha['media_quad']),
        'mxmeta': variacao(alvo, linha['meta_anterior']),
    }


def _soma(valores):
    cheios = [v for v in valores if v is not None]
    return sum(cheios) if cheios else None


def _somar(linhas, titulo, fixa):
    """Subtotal (RJ, ES, SP) ou REDE: soma coluna a coluna, como o SUM da planilha; comparações refeitas."""
    t = {'titulo': titulo, 'qtd_lojas': len(linhas)}
    t['meses'] = [_soma(l['meses'][i] for l in linhas) for i in range(4)]
    t['ultimo'] = t['meses'][-1]
    for campo in ('media_quad', 'real_ano_anterior', 'meta_vivo', 'meta_anterior', 'meta'):
        t[campo] = _soma(l[campo] for l in linhas)
    if fixa:
        t['meses_valor'] = [_soma(l['meses_valor'][i] for l in linhas) for i in range(4)]
        t['meses_tv'] = [_soma(l['meses_tv'][i] for l in linhas) for i in range(4)]
        t['meses_fixa'] = [_soma(l['meses_fixa'][i] for l in linhas) for i in range(4)]
        t['media_quad_valor'] = _soma(l['media_quad_valor'] for l in linhas)
        for campo in ('qtd_cns', 'qtd', 'cima', 'baixo', 'nova_meta'):
            t[campo] = _soma(l[campo] for l in linhas)
        t['alvo'] = t['nova_meta']
    else:
        t['alvo'] = t['meta']
    t.update(_comparacoes(t['alvo'], t))
    return t


def linha_da_loja(competencia, loja, pilar):
    """A linha recalculada de uma loja (a resposta do salvamento automático)."""
    dados, _ = montar(competencia, [pilar])
    for linha in dados[pilar]['linhas']:
        if linha['loja_id'] == loja.id:
            return linha
    return None


# ---------------------------------------------------------------------------
# Gravação (com histórico)
# ---------------------------------------------------------------------------
def _txt(valor):
    if valor is None:
        return ''
    if isinstance(valor, Decimal):
        valor = valor.normalize()
        return '{:f}'.format(valor)
    return str(valor)


def registrar(competencia, loja, pilar, campo, antes, depois, user, origem):
    if _txt(antes) == _txt(depois):
        return
    HistoricoMeta.objects.create(
        competencia=competencia, loja=loja, loja_nome=loja.nome if loja else '', pilar=pilar or '', campo=campo,
        antes=_txt(antes)[:60], depois=_txt(depois)[:60], origem=origem[:60],
        por=user if getattr(user, 'is_authenticated', False) else None,
        por_nome=((user.get_full_name() or user.username) if getattr(user, 'is_authenticated', False) else '')[:200])


CAMPOS_EDITAVEIS = ('meta', 'qtd_cns', 'decisao')


@transaction.atomic
def salvar_campo(competencia, loja, pilar, campo, valor, user, origem='Digitado'):
    """Grava um campo de uma linha. Fixa: a nova meta é recalculada e guardada junto."""
    if competencia.fechada:
        raise PermissionError('A competência está fechada.')
    if campo not in CAMPOS_EDITAVEIS:
        raise ValueError('Campo desconhecido.')
    if pilar not in dict(PILARES):
        raise ValueError('Pilar desconhecido.')
    if campo in ('qtd_cns', 'decisao') and pilar != 'fixa':
        raise ValueError('Esse campo só existe na Fixa.')
    m, _ = MetaLoja.objects.select_for_update().get_or_create(competencia=competencia, loja=loja, pilar=pilar)
    if campo == 'meta':
        novo = numero(valor)
        if novo is not None:
            if novo < 0:
                raise ValueError('A meta não pode ser negativa.')
            if novo >= Decimal('1e12'):
                raise ValueError('Valor grande demais.')
            novo = novo.quantize(Decimal('0.01'))
    elif campo == 'qtd_cns':
        novo = numero(valor)
        if novo is not None:
            if novo < 0 or novo != novo.to_integral_value() or novo > 999:
                raise ValueError('Consultores: um número inteiro de 0 a 999.')
            novo = int(novo)
    else:
        novo = str(valor or '').upper()
        if novo not in (MetaLoja.CIMA, MetaLoja.BAIXO):
            raise ValueError('Decisão: Cima ou Baixo.')
    antes = getattr(m, campo)
    setattr(m, campo, novo)
    m.atualizado_por = user if getattr(user, 'is_authenticated', False) else None
    m.save()
    registrar(competencia, loja, pilar, campo, antes, novo, user, origem)
    if pilar == 'fixa':
        atualizar_nova_meta(competencia, loja, m, user, origem)
    return m


def atualizar_nova_meta(competencia, loja, m, user, origem):
    linha = linha_da_loja(competencia, loja, 'fixa')
    nova = linha['nova_meta'] if linha else None
    if m.nova_meta != nova:
        antes = m.nova_meta
        MetaLoja.objects.filter(pk=m.pk).update(nova_meta=nova)
        m.nova_meta = nova
        registrar(competencia, loja, 'fixa', 'nova_meta', antes, nova, user, origem)


ORIGENS = {
    'meta_vivo': 'Meta Vivo',
    'media_quad': 'Média do quadrimestre',
    'meta_anterior': 'Meta do mês anterior',
    'real_ano_anterior': 'Real do ano anterior',
    'ultimo': 'Realizado do último mês',
}


@transaction.atomic
def preencher(competencia, pilar, origem, percentual=0, arredondar=0, somente_vazias=False, user=None):
    """Preenchimento rápido: a meta de todas as lojas = referência × (1 + x%), arredondada.

    Na Fixa a meta é em R$: média, último mês e ano anterior usam o R$ da Fixa;
    meta Vivo e meta anterior (quantidades) viram R$ pelo ticket médio da loja.
    """
    if competencia.fechada:
        raise PermissionError('A competência está fechada.')
    if origem not in ORIGENS:
        raise ValueError('Origem desconhecida.')
    dados, _ = montar(competencia, [pilar])
    fator = 1 + float(percentual or 0) / 100
    alteradas = 0
    rotulo = 'Preenchimento: {}{}'.format(ORIGENS[origem], ' {:+g}%'.format(float(percentual)) if percentual else '')
    for linha in dados[pilar]['linhas']:
        if somente_vazias and linha['meta'] is not None:
            continue
        if pilar == 'fixa':
            if origem in ('meta_vivo', 'meta_anterior'):
                base = linha[origem] * linha['ticket'] if (linha[origem] is not None and linha['ticket']) else None
            elif origem == 'media_quad':
                base = linha['media_quad_valor']
            elif origem == 'ultimo':
                base = linha['meses_valor'][-1]
            else:
                base = linha['real_ano_anterior_valor']
        else:
            base = linha[origem]
        if base is None:
            continue
        valor = base * fator
        if arredondar:
            valor = round(valor / arredondar) * arredondar
        valor = Decimal(str(round(valor, 2)))
        salvar_campo(competencia, linha['loja'], pilar, 'meta', valor, user, origem=rotulo[:60])
        alteradas += 1
    return alteradas


def fechar(competencia, user):
    competencia.status = Competencia.FECHADA
    competencia.fechada_por = user
    competencia.fechada_em = timezone.now()
    competencia.save(update_fields=['status', 'fechada_por', 'fechada_em'])
    registrar(competencia, None, '', 'status', 'Aberta', 'Fechada', user, 'Fechar competência')


def reabrir(competencia, user):
    competencia.status = Competencia.ABERTA
    competencia.save(update_fields=['status'])
    registrar(competencia, None, '', 'status', 'Fechada', 'Aberta', user, 'Reabrir competência')


def recalcular_fixa(competencia, user=None, origem='Recalculado'):
    """Depois de importar realizado (o ticket muda), a nova meta da Fixa é refeita."""
    if competencia.fechada:
        return
    dados, _ = montar(competencia, ['fixa'])
    metas = {m.loja_id: m for m in MetaLoja.objects.filter(competencia=competencia, pilar='fixa')}
    for linha in dados['fixa']['linhas']:
        m = metas.get(linha['loja_id'])
        if m and m.nova_meta != linha['nova_meta']:
            antes = m.nova_meta
            MetaLoja.objects.filter(pk=m.pk).update(nova_meta=linha['nova_meta'])
            registrar(competencia, linha['loja'], 'fixa', 'nova_meta', antes, linha['nova_meta'], user, origem)


def sem_acento(texto):
    texto = unicodedata.normalize('NFKD', str(texto or ''))
    return ''.join(c for c in texto if not unicodedata.combining(c))
