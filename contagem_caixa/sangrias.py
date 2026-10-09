"""Gestão de sangrias: o dinheiro que sai do caixa da loja, com o motivo.

Cada sangria registrada entra sozinha no dia de caixa da loja — a soma do dia
vai para ``ContagemCaixaDia.sangria_registrada``, que sai da Entrada e, por ela,
do saldo. Acabam as planilhas paralelas: o mesmo registro serve para conciliar
o caixa e para saber quanto cada loja gasta, com o quê e com que frequência.
"""
import re
import statistics
import unicodedata
from decimal import Decimal

from django.db.models import Count, Sum

from .models import ContagemCaixaDia, Sangria
from .permissions import e_gestor, lojas_do_usuario

ZERO = Decimal('0.00')

# Uma sangria "fora do padrão" custa bem mais que o normal da categoria: acima
# de FATOR_FORA_DO_PADRAO vezes a mediana, com amostra mínima para a mediana
# querer dizer alguma coisa.
FATOR_FORA_DO_PADRAO = 3
AMOSTRA_MINIMA = 5
# Mesmo favorecido (ou mesma descrição) pelo menos estas vezes no período.
MINIMO_RECORRENTE = 3


# ── Permissões ──────────────────────────────────────────────────────────────
def pode_registrar(user, loja):
    """Registra sangria na loja: quem tem a loja no caixa (o gestor, em todas)."""
    return lojas_do_usuario(user).filter(id=loja.id).exists()


def pode_alterar(user, sangria):
    """Altera ou apaga: o gestor sempre; quem registrou, enquanto não foi conferida."""
    if e_gestor(user):
        return True
    return (not sangria.conferida and sangria.registrada_por_id == getattr(user, 'id', None)
            and pode_registrar(user, sangria.loja))


def pode_conferir(user):
    return e_gestor(user)


# ── O caixa ─────────────────────────────────────────────────────────────────
def sincronizar_dia(loja_id, data):
    """Regrava a soma das sangrias do dia no caixa e refaz o saldo dali em diante."""
    from .servicos import recalcular_saldos

    soma = (Sangria.objects.filter(loja_id=loja_id, data=data)
            .aggregate(s=Sum('valor'))['s']) or ZERO
    dia = ContagemCaixaDia.objects.filter(loja_id=loja_id, data=data).first()
    if dia is None:
        if not soma:
            return None
        # Sangria em dia sem importação ainda: o dia nasce com ela, e a
        # importação do SAP depois só preenche o Valor SAP.
        dia = ContagemCaixaDia.objects.create(loja_id=loja_id, data=data)
    if dia.sangria_registrada != soma:
        dia.sangria_registrada = soma
        dia.save(update_fields=['sangria_registrada', 'atualizado_em'])
    recalcular_saldos(loja_id, desde=data)
    return dia


# ── Relatórios ──────────────────────────────────────────────────────────────
def _chave_texto(texto):
    texto = unicodedata.normalize('NFKD', str(texto or '')).encode('ascii', 'ignore').decode().lower()
    palavras = re.findall(r'[a-z0-9]+', texto)
    return ' '.join(palavras[:4])


def resumo(qs):
    dados = qs.aggregate(total=Sum('valor'), quantidade=Count('id'))
    total, quantidade = dados['total'] or ZERO, dados['quantidade'] or 0
    return {
        'total': total,
        'quantidade': quantidade,
        'media': (total / quantidade) if quantidade else ZERO,
        'a_conferir': qs.filter(conferida=False).count(),
        'valor_a_conferir': qs.filter(conferida=False).aggregate(s=Sum('valor'))['s'] or ZERO,
        'sem_comprovante': qs.filter(comprovante='').count() + qs.filter(comprovante__isnull=True).count(),
    }


def _com_fatia(linhas, total):
    maior = max((l['total'] for l in linhas), default=ZERO)
    for l in linhas:
        l['percentual'] = round(float(l['total'] * 100 / total), 1) if total else 0
        l['barra'] = round(float(l['total'] * 100 / maior)) if maior else 0
    return linhas


def por_categoria(qs, total):
    linhas = list(qs.values('categoria__nome').annotate(total=Sum('valor'), n=Count('id'))
                  .order_by('-total'))
    return _com_fatia([{'nome': l['categoria__nome'], 'total': l['total'], 'n': l['n']} for l in linhas], total)


def por_loja(qs, total):
    linhas = list(qs.values('loja_id', 'loja__name').annotate(total=Sum('valor'), n=Count('id'))
                  .order_by('-total'))
    return _com_fatia([{'id': l['loja_id'], 'nome': l['loja__name'], 'total': l['total'], 'n': l['n']}
                       for l in linhas], total)


def por_mes(qs):
    meses = {}
    for data, valor in qs.values_list('data', 'valor'):
        chave = data.replace(day=1)
        meses[chave] = meses.get(chave, ZERO) + valor
    maior = max(meses.values(), default=ZERO)
    return [{'mes': m, 'total': v, 'altura': round(float(v * 100 / maior)) if maior else 0}
            for m, v in sorted(meses.items())][-12:]


def recorrentes(qs):
    """O mesmo gasto se repetindo: mesmo favorecido (ou descrição) na mesma categoria."""
    grupos = {}
    for s in qs.select_related('categoria', 'loja'):
        chave = (_chave_texto(s.favorecido) or _chave_texto(s.descricao), s.categoria_id)
        if not chave[0]:
            continue
        g = grupos.setdefault(chave, {'rotulo': s.favorecido or s.descricao[:60], 'categoria': s.categoria.nome,
                                      'n': 0, 'total': ZERO, 'lojas': set(), 'ultima': s.data})
        g['n'] += 1
        g['total'] += s.valor
        g['lojas'].add(s.loja.name)
        g['ultima'] = max(g['ultima'], s.data)
    linhas = [dict(g, lojas=sorted(g['lojas'])) for g in grupos.values() if g['n'] >= MINIMO_RECORRENTE]
    return sorted(linhas, key=lambda g: (-g['total'], -g['n']))[:15]


def fora_do_padrao(qs):
    """Sangrias acima de 3× a mediana da categoria no período (com amostra mínima)."""
    valores = {}
    for categoria_id, valor in qs.values_list('categoria_id', 'valor'):
        valores.setdefault(categoria_id, []).append(valor)
    medianas = {c: statistics.median(v) for c, v in valores.items() if len(v) >= AMOSTRA_MINIMA}
    acima = []
    for s in qs.select_related('categoria', 'loja').order_by('-valor'):
        mediana = medianas.get(s.categoria_id)
        if mediana and s.valor > mediana * FATOR_FORA_DO_PADRAO:
            s.mediana_categoria = mediana
            s.vezes_mediana = round(float(s.valor / mediana), 1)
            acima.append(s)
    return acima[:15]
