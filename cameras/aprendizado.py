"""O que a análise aprende com cada decisão do SUPERADMIN (furto confirmado ou falso alarme).

Três frentes, todas a partir das mesmas decisões:

1. **Chance real** de um alerta novo: a taxa de confirmados de alertas parecidos, do geral para o
   particular (tipo → loja → câmera). Com poucas decisões numa câmera, vale mais o que se sabe
   da loja e da rede; com muitas, vale o histórico da própria câmera (média bayesiana).
2. **Rigor** mandado ao agente da loja: câmera/tipo que só dá falso alarme fica mais exigente
   (espera mais, pede detecção mais firme); a que acerta muito fica mais sensível.
3. **Memória da IA de visão**: os casos decididos mais recentes da mesma câmera (com o motivo do
   falso alarme e a observação de quem conferiu) entram como exemplo na próxima análise.
"""
from collections import Counter, defaultdict

from django.core.cache import cache
from django.db.models import Count

from .models import AlertaCamera

CHAVE_CACHE = 'cameras:aprendizado:v1'
VALIDADE_CACHE = 600
PESO_DO_GERAL = 4          # quantas decisões "vale" o conhecimento do nível de cima
MINIMO_PARA_AJUSTAR = 5    # decisões na câmera (ou loja) antes de mudar o rigor do agente
# Antes de qualquer decisão: o quanto cada tipo costuma ser de verdade.
TAXA_INICIAL = {'ITEM_SUMIU': .30, 'FORA_HORARIO': .50, 'PERMANENCIA': .15}
EXEMPLOS_NA_MEMORIA = 6

RIGOR_MAIS_SENSIVEL, RIGOR_NORMAL, RIGOR_EXIGENTE, RIGOR_MUITO_EXIGENTE = -1, 0, 1, 2
ROTULO_RIGOR = {-1: 'Mais sensível', 0: 'Normal', 1: 'Mais exigente', 2: 'Muito exigente'}

C, F = AlertaCamera.Status.CONFIRMADO, AlertaCamera.Status.FALSO


def _contagens():
    """{nível: {chave: Counter(CONFIRMADO=n, FALSO=n)}}, com cache: muda só quando alguém decide."""
    dados = cache.get(CHAVE_CACHE)
    if dados is not None:
        return dados
    tipo, loja, canal = defaultdict(Counter), defaultdict(Counter), defaultdict(Counter)
    motivos = defaultdict(Counter)
    linhas = (AlertaCamera.objects.filter(status__in=[C, F])
              .values_list('tipo', 'gravador_id', 'canal_id', 'status', 'motivo_falso').annotate(n=Count('id')))
    for t, g, c, status, motivo, n in linhas:
        tipo[t][status] += n
        loja[(t, g)][status] += n
        if c:
            canal[(t, c)][status] += n
        if status == F and motivo:
            motivos[(t, c)][motivo] += n
    dados = {'tipo': dict(tipo), 'loja': dict(loja), 'canal': dict(canal), 'motivos': dict(motivos)}
    cache.set(CHAVE_CACHE, dados, VALIDADE_CACHE)
    return dados


def esquecer():
    """Chamado a cada decisão: a próxima conta já usa o que acabou de ser decidido."""
    cache.delete(CHAVE_CACHE)


def _media(contagem, anterior):
    confirmados, falsos = contagem.get(C, 0), contagem.get(F, 0)
    return (confirmados + PESO_DO_GERAL * anterior) / (confirmados + falsos + PESO_DO_GERAL)


def avaliar(tipo, gravador_id, canal_id):
    """{'chance': 0-100, 'decisoes_canal', 'decisoes_loja', 'decisoes_tipo'} para um alerta assim."""
    d = _contagens()
    geral = d['tipo'].get(tipo, Counter())
    n_tipo = sum(geral.values())
    taxa = TAXA_INICIAL.get(tipo, .3)
    if n_tipo:
        taxa = (geral.get(C, 0) + 2 * taxa) / (n_tipo + 2)
    loja = d['loja'].get((tipo, gravador_id), Counter())
    taxa = _media(loja, taxa)
    canal = d['canal'].get((tipo, canal_id), Counter()) if canal_id else Counter()
    taxa = _media(canal, taxa)
    return {'chance': round(taxa * 100), 'decisoes_canal': sum(canal.values()), 'decisoes_loja': sum(loja.values()),
            'decisoes_tipo': n_tipo}


def rigor(tipo, gravador_id, canal_id):
    """O rigor que o agente deve usar neste tipo de alerta nesta câmera."""
    a = avaliar(tipo, gravador_id, canal_id)
    if max(a['decisoes_canal'], a['decisoes_loja']) < MINIMO_PARA_AJUSTAR:
        return RIGOR_NORMAL
    if a['chance'] < 15:
        return RIGOR_MUITO_EXIGENTE
    if a['chance'] < 30:
        return RIGOR_EXIGENTE
    if a['chance'] >= 70:
        return RIGOR_MAIS_SENSIVEL
    return RIGOR_NORMAL


def ajustes_do_gravador(gravador, canais):
    """{numero_do_canal: {tipo: rigor}} só com o que foge do normal (vai no sinal do agente)."""
    saida = {}
    for canal in canais:
        por_tipo = {t: r for t in AlertaCamera.Tipo.values if (r := rigor(t, gravador.id, canal.id)) != RIGOR_NORMAL}
        if por_tipo:
            saida[str(canal.numero)] = por_tipo
    return saida


def motivos_de_falso(tipo, canal_id):
    """[(rótulo, n)] dos falsos alarmes desta câmera, do mais comum para o menos."""
    rotulos = dict(AlertaCamera.MotivoFalso.choices)
    contagem = _contagens()['motivos'].get((tipo, canal_id), Counter())
    return [(rotulos.get(m, m), n) for m, n in contagem.most_common()]


def exemplos(alerta, quantos=EXEMPLOS_NA_MEMORIA):
    """Casos já decididos parecidos com este (mesma câmera; senão mesma loja), do mais recente."""
    base = (AlertaCamera.objects.filter(status__in=[C, F], tipo=alerta.tipo).exclude(pk=alerta.pk)
            .order_by('-tratado_em', '-criado_em'))
    casos = list(base.filter(canal_id=alerta.canal_id)[:quantos]) if alerta.canal_id else []
    if len(casos) < quantos:
        casos += list(base.filter(gravador_id=alerta.gravador_id).exclude(pk__in=[c.pk for c in casos])[:quantos - len(casos)])
    return casos


def prioridade(alerta, decisoes=None):
    """0-100: junta a nota do detector, o histórico e (quando houver) a IA de visão."""
    partes = [(alerta.confianca or 0, 1.0)]
    if alerta.chance_real is not None:
        if decisoes is None:
            a = avaliar(alerta.tipo, alerta.gravador_id, alerta.canal_id)
            decisoes = a['decisoes_canal'] + a['decisoes_loja']
        partes.append((alerta.chance_real, 1.5 if decisoes >= MINIMO_PARA_AJUSTAR else .75))
    if alerta.ia_risco is not None:
        partes.append((alerta.ia_risco, 2.0))
    return round(sum(v * p for v, p in partes) / sum(p for _v, p in partes))


def aplicar(alerta):
    """Preenche chance_real e prioridade de um alerta (sem salvar)."""
    a = avaliar(alerta.tipo, alerta.gravador_id, alerta.canal_id)
    alerta.chance_real = a['chance']
    alerta.prioridade = prioridade(alerta, a['decisoes_canal'] + a['decisoes_loja'])
    return a


def quadro_geral(gravadores):
    """Linhas para a tela "Aprendizado": por loja, câmera e tipo, o que já foi decidido e o rigor atual."""
    d = _contagens()
    canais = {}
    from .models import Canal
    for c in Canal.objects.filter(gravador__in=gravadores).select_related('gravador'):
        canais[c.id] = c
    linhas = []
    rotulos_tipo = dict(AlertaCamera.Tipo.choices)
    for (tipo, canal_id), contagem in d['canal'].items():
        canal = canais.get(canal_id)
        if canal is None:
            continue
        a = avaliar(tipo, canal.gravador_id, canal_id)
        r = rigor(tipo, canal.gravador_id, canal_id)
        linhas.append({'loja': canal.gravador.loja, 'gravador_id': canal.gravador_id, 'canal': canal.titulo, 'tipo': rotulos_tipo.get(tipo, tipo),
                       'confirmados': contagem.get(C, 0), 'falsos': contagem.get(F, 0), 'chance': a['chance'],
                       'rigor': r, 'rigor_rotulo': ROTULO_RIGOR[r], 'motivos': motivos_de_falso(tipo, canal_id)[:3]})
    linhas.sort(key=lambda x: (x['loja'], x['canal'], x['tipo']))
    totais = Counter()
    for contagem in d['tipo'].values():
        totais.update(contagem)
    return linhas, {'confirmados': totais.get(C, 0), 'falsos': totais.get(F, 0)}
