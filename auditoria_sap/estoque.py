"""Visão de estoque (23/11/2026): o estoque do SAP por loja, unidade e reposição.

Pedido: "criar uma visão de estoque, use como referência de funcionalidade o
Power BI (Consulta de Estoque), porém o layout precisa ser intuitivo ao máximo
[...] interprete a distância entre lojas".

**De onde vem cada coisa** (MySQL ``SAP``, só leitura — ``auditoria_sap.mysql``)

- ``estoque_mb52``: o saldo por centro × depósito × material. Cada carga é uma
  ``execucao_id`` (``mb52_AAAAMMDD_HHMMSS``); vale a mais recente. ``LVUT`` é o
  livre para venda; as linhas **sem depósito** de um centro são o que está em
  trânsito para ele; a linha sem centro é o total da planilha (fica de fora).
- ``estoque_iq09``: uma linha por unidade com serial (IMEI/ICCID), com o
  depósito, o tipo de estoque (01 livre, 06 em trânsito, 07 bloqueado...), o
  status do sistema e a data da última movimentação (``modificado_em``, número
  de série do Excel). Item sem serial (cabo, película) só existe na MB52.
- ``produtos_nome_comercial``: o nome comercial, cor, categoria e fabricante
  do material (``cod_sap``).
- ``vw_vivogo_produtos_historico``: as vendas do Vivo GO (uma linha por item),
  para a reposição: vendas de 30 e 90 dias por loja e SKU (o SKU do Vivo GO é o
  material do SAP).

**Distância entre lojas**: o SAP não tem endereço. ``LOJAS`` guarda a posição
aproximada de cada centro (cidade/bairro/shopping) e a distância é em linha reta
(haversine) — serve para ordenar "quem está mais perto", não para rota. Uma loja
nova no SAP aparece com o nome da MB52 e sem distância até entrar em ``LOJAS``.

A leitura das três tabelas leva alguns segundos: a fotografia fica guardada por
carga (muda 2–3 vezes por dia) e a tela trabalha sempre em cima dela.
"""
import logging
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.core.cache import cache
from django.utils import timezone

from . import mysql
from .permissions import normalizar_loja

logger = logging.getLogger(__name__)

DEPOSITO_VENDA = 'LVUT'
DEPOSITOS = {
    'LVUT': 'Livre para venda',
    'RPAR': 'Reparo',
    'EXPO': 'Exposição (demo)',
    'LOJA': 'Uso da loja',
    'SNTR': 'Sinistro',
    'AKEY': 'Acessórios key',
    '': 'Em trânsito',
}
TIPOS_DE_ESTOQUE = {'01': 'Livre', '02': 'Controle de qualidade', '06': 'Em trânsito', '07': 'Bloqueado'}
TIPO_TRANSITO = '06'
VIZINHAS = 6                 # como no Power BI: a loja escolhida e as seis mais perto
CRITICO_VENDAS_30 = 5        # zerado com 5+ vendas em 30 dias = crítico
TEMPO_FOTO = 6 * 3600        # a fotografia de uma carga não muda; some sozinha depois
TEMPO_ULTIMA_CARGA = 300     # de 5 em 5 min pergunta se chegou carga nova
# Transferência sugerida só entre lojas próximas: de Anchieta para Carapicuíba (705 km) é pedido, não troca.
LIMITE_TRANSFERENCIA_KM = 200


@dataclass(frozen=True)
class Loja:
    centro: str
    nome: str
    pdv: str                 # como o Vivo GO escreve a loja (vendas e cadastro do portal)
    cidade: str
    uf: str
    lat: float
    lng: float

    @property
    def chave(self):
        return normalizar_loja(self.pdv)


# Posição aproximada (centro do bairro/shopping). Conferir/ajustar aqui se a loja mudar.
LOJAS = (
    Loja('038E', 'Serra Sede', 'SERRA SEDE', 'Serra', 'ES', -20.1286, -40.3078),
    Loja('039E', 'Porto Canoa', 'PORTO CANOA', 'Serra', 'ES', -20.1979, -40.2346),
    Loja('041E', 'Shopping Laranjeiras', 'LARANJEIRAS', 'Serra', 'ES', -20.1958, -40.2510),
    Loja('043E', 'Centro de Vila Velha', 'CENTRO VILA VELHA', 'Vila Velha', 'ES', -20.3297, -40.2925),
    Loja('044E', 'Centro de Vitória', 'CENTRO VIX', 'Vitória', 'ES', -20.3195, -40.3378),
    Loja('045E', 'Iconha', 'ICONHA', 'Iconha', 'ES', -20.7917, -40.8131),
    Loja('046E', 'Shopping Norte Sul', 'NORTE SUL', 'Vitória', 'ES', -20.2620, -40.2660),
    Loja('047E', 'Shopping Montserrat', 'MONTSERRAT', 'Serra', 'ES', -20.2045, -40.2589),
    Loja('048E', 'Masterplace', 'MASTERPLACE', 'Vitória', 'ES', -20.2955, -40.2917),
    Loja('049E', 'Jacaraípe', 'JACARAÍPE', 'Serra', 'ES', -20.1530, -40.1880),
    Loja('057E', 'Anchieta', 'ANCHIETA', 'Anchieta', 'ES', -20.8055, -40.6455),
    Loja('060E', 'Itacibá', 'ITACIBA', 'Cariacica', 'ES', -20.3301, -40.3720),
    Loja('064E', 'Piúma', 'PIUMA', 'Piúma', 'ES', -20.8370, -40.7290),
    Loja('065E', 'Glória', 'GLÓRIA', 'Vila Velha', 'ES', -20.3385, -40.3045),
    Loja('069E', 'Viana (Marcílio de Noronha)', 'MARCILIO DE NORONHA', 'Viana', 'ES', -20.3735, -40.4300),
    Loja('079J', 'Miracema', 'MIRACEMA', 'Miracema', 'RJ', -21.4148, -42.1966),
    Loja('080J', 'Santo Antônio de Pádua', 'SANTO ANTONIO DE PADUA', 'Santo Antônio de Pádua', 'RJ',
         -21.5390, -42.1800),
    Loja('081J', 'Bom Jesus do Itabapoana', 'BOM JESUS DO ITABAPOANA', 'Bom Jesus do Itabapoana', 'RJ',
         -21.1340, -41.6797),
    Loja('148J', 'São Fidélis', 'SAO FIDELIS', 'São Fidélis', 'RJ', -21.6460, -41.7470),
    Loja('149J', 'Quissamã', 'QUISSAMA', 'Quissamã', 'RJ', -22.1060, -41.4720),
    Loja('220L', 'Itaim Paulista', 'ITAIM PAULISTA', 'São Paulo', 'SP', -23.5010, -46.3990),
    Loja('222L', 'Carapicuíba', 'CARAPICUIBA', 'Carapicuíba', 'SP', -23.5235, -46.8356),
    Loja('223L', 'Embu das Artes', 'EMBU DAS ARTES', 'Embu das Artes', 'SP', -23.6489, -46.8522),
)
POR_CENTRO = {loja.centro: loja for loja in LOJAS}
POR_CHAVE = {loja.chave: loja for loja in LOJAS}
# Outras grafias da mesma loja (cadastro do portal, Vivo GO antigo).
APELIDOS = {
    'VIANA': '069E', 'CENTRO DE VITORIA': '044E', 'VITORIA': '044E', 'VILA VELHA': '043E',
    'GLORIA': '065E', 'JACARAIPE': '049E', 'ITACIBA': '060E', 'ALTO LAGE': '060E',
    'SHOPPING LARANJEIRAS': '041E', 'SH. LARANJEIRAS': '041E', 'SHOPPING MONTSERRAT': '047E',
    'SH. MONTSERRAT': '047E', 'SHOPPING NORTE SUL': '046E', 'SH. NORTE SUL': '046E',
    'PADUA': '080J', 'BOM JESUS': '081J', 'SAO FIDELIS': '148J', 'QUISSAMA': '149J',
}


def centro_da_loja(nome):
    """O centro SAP de um nome de loja ("Loja Glória", "GLÓRIA", "065E"), ou ''."""
    texto = ' '.join(str(nome or '').split()).upper()
    if texto[:4] in POR_CENTRO:
        return texto[:4]
    chave = normalizar_loja(nome)
    if chave in POR_CHAVE:
        return POR_CHAVE[chave].centro
    return APELIDOS.get(chave, '')


def distancia_km(a, b):
    """Distância em linha reta entre duas lojas (km), ou None sem posição."""
    if a is None or b is None:
        return None
    r = 6371.0
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = p2 - p1, math.radians(b.lng - a.lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def vizinhas(centro, quantas=VIZINHAS, centros=None):
    """[(centro, km)] das lojas mais perto de ``centro`` (sem ela), da mais perto para a mais longe."""
    origem = POR_CENTRO.get(centro)
    if origem is None:
        return []
    lista = []
    for outro in (centros if centros is not None else POR_CENTRO):
        if outro == centro or outro not in POR_CENTRO:
            continue
        lista.append((outro, distancia_km(origem, POR_CENTRO[outro])))
    lista.sort(key=lambda par: par[1])
    return lista[:quantas] if quantas else lista


# ---------------------------------------------------------------------------
# Leitura do SAP
# ---------------------------------------------------------------------------

def _texto(valor):
    return '' if valor is None else str(valor).strip()


def _numero(valor):
    try:
        return float(str(valor).replace(',', '.')) if valor not in (None, '') else 0.0
    except ValueError:
        return 0.0


def _data_excel(valor):
    """``modificado_em`` vem como número de série do Excel ("45964"); às vezes como data."""
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = _texto(valor)
    if re.fullmatch(r'\d{5}(\.\d+)?', texto):
        return date(1899, 12, 30) + timedelta(days=int(float(texto)))
    for formato in ('%Y-%m-%d', '%d/%m/%Y', '%d.%m.%Y'):
        try:
            return datetime.strptime(texto[:10], formato).date()
        except ValueError:
            continue
    return None


def _data_da_carga(execucao):
    m = re.search(r'(\d{8})_(\d{6})', execucao or '')
    if not m:
        return None
    return timezone.make_aware(datetime.strptime(m.group(1) + m.group(2), '%Y%m%d%H%M%S'))


def ultimas_cargas():
    """(execução da IQ09, execução da MB52) mais recentes. Guardado 5 min."""
    chave = 'sap_estoque:ultimas_cargas:v1'
    try:
        guardado = cache.get(chave)
    except Exception:                                            # noqa: BLE001 — sem cache, lê de novo
        guardado = None
    if guardado:
        return guardado
    linhas = mysql._consultar(
        "SELECT (SELECT MAX(execucao_id) FROM estoque_iq09) AS iq09, "
        "(SELECT MAX(execucao_id) FROM estoque_mb52) AS mb52")
    par = (_texto(linhas[0]['iq09']), _texto(linhas[0]['mb52'])) if linhas else ('', '')
    try:
        cache.set(chave, par, TEMPO_ULTIMA_CARGA)
    except Exception:                                            # noqa: BLE001
        pass
    return par


def _ler_mb52(execucao):
    saldos, transito, descricoes, nomes_sap = {}, {}, {}, {}
    for r in mysql._consultar(
            "SELECT centro, nome_1, deposito, material, texto_breve_material, utilizacao_livre, "
            "transito_e_te, bloqueado, em_contr_qualidade FROM estoque_mb52 WHERE execucao_id = %s",
            (execucao,)):
        centro, material = _texto(r['centro']), _texto(r['material'])
        if not centro or not material:
            continue                         # a linha de total da planilha
        descricoes.setdefault(material, _texto(r['texto_breve_material']))
        if r.get('nome_1'):
            nomes_sap.setdefault(centro, _texto(r['nome_1']))
        deposito = _texto(r['deposito']).upper()
        if not deposito:
            qtd = _numero(r['transito_e_te'])
            if qtd:
                transito[(centro, material)] = transito.get((centro, material), 0) + qtd
            continue
        livre = _numero(r['utilizacao_livre'])
        bloqueado = _numero(r['bloqueado']) + _numero(r['em_contr_qualidade'])
        if livre or bloqueado:
            por_dep = saldos.setdefault((centro, material), {})
            atual = por_dep.get(deposito, (0, 0))
            por_dep[deposito] = (atual[0] + livre, atual[1] + bloqueado)
    return saldos, transito, descricoes, nomes_sap


def _ler_iq09(execucao):
    unidades, descricoes = [], {}
    for r in mysql._consultar(
            "SELECT material, denominacao, n_de_serie, centro, deposito, tipo_de_estoque, "
            "status_sistema, status_usuario, modificado_em FROM estoque_iq09 WHERE execucao_id = %s",
            (execucao,)):
        material = _texto(r['material'])
        descricoes.setdefault(material, _texto(r['denominacao']))
        unidades.append((
            material,
            _texto(r['n_de_serie']),
            _texto(r['centro']),
            _texto(r['deposito']).upper(),
            _texto(r['tipo_de_estoque']).zfill(2) if _texto(r['tipo_de_estoque']) else '',
            _texto(r['status_sistema']),
            _texto(r['status_usuario']),
            _data_excel(r['modificado_em']),
        ))
    return unidades, descricoes


# Grafias diferentes do mesmo nome no catálogo do SAP que a chave sozinha não junta.
MESMO_NOME = {'bluecastle': 'blucastle'}
CAMPOS_PADRONIZADOS = ('categoria', 'subcategoria', 'fabricante')


def _chave_nome(texto):
    """"Demais Eletrônicos", "Demais eletrônicos" e "TP-Link"/"TP Link" viram a mesma chave."""
    chave = re.sub(r'[^0-9a-z]', '', _sem_acento(texto))
    return MESMO_NOME.get(chave, chave)


def padronizar(valores):
    """{grafia: grafia padrão} — vale a mais usada; no empate, a que não está toda em maiúsculas.

    Pedido de 24/11/2026: "algumas categorias ficaram duplicadas, ajuste e padronize" —
    o catálogo traz "Bem Estar" (189) e "Bem estar" (1), "Áudio e Vídeo" e "Áudio e vídeo",
    "HUAWEI" e "Huawei", "PlayStation" e "Playstation"..."""
    contagem = {}
    for valor in valores:
        if valor:
            contagem[valor] = contagem.get(valor, 0) + 1
    por_chave = {}
    for valor, n in contagem.items():
        por_chave.setdefault(_chave_nome(valor), []).append((n, not valor.isupper(), valor))
    padrao = {}
    for grafias in por_chave.values():
        escolhida = max(grafias)[2]
        for _n, _m, valor in grafias:
            padrao[valor] = escolhida
    return padrao


def _ler_catalogo():
    catalogo = {}
    for r in mysql._consultar(
            "SELECT cod_sap, cor, nome_comercial, fabricante, categoria, subcategoria, gama "
            "FROM produtos_nome_comercial"):
        cod = _texto(r['cod_sap'])
        if cod:
            catalogo[cod] = {campo: _texto(r[campo]) for campo in
                             ('nome_comercial', 'cor', 'fabricante', 'categoria', 'subcategoria', 'gama')}
    for campo in CAMPOS_PADRONIZADOS:
        padrao = padronizar(item[campo] for item in catalogo.values())
        for item in catalogo.values():
            item[campo] = padrao.get(item[campo], item[campo])
    return catalogo


def _ler_renova(hoje):
    """Descontos do Vivo Renova vigentes hoje (descontos_fabricante, a "Tabela Vivo Renova").

    ``{chave do nome: {'desconto', 'fim', 'modelo', 'fabricante'}}`` — pelo nome comercial
    e pelo modelo, que é como a tabela traz o aparelho ("iPhone 16 128GB")."""
    renova = {}
    for r in mysql._consultar(
            "SELECT fabricante, modelo, nome_comercial, desconto, inicio, fim FROM descontos_fabricante "
            "WHERE inicio <= %s AND fim >= %s", (hoje, hoje)):
        desconto = _numero(r['desconto'])
        if desconto <= 0:
            continue
        item = {'desconto': desconto, 'fim': r['fim'], 'modelo': _texto(r['modelo']) or _texto(r['nome_comercial']),
                'fabricante': _texto(r['fabricante'])}
        for nome in (r['nome_comercial'], r['modelo']):
            chave = _chave_nome(nome)
            if chave and (chave not in renova or renova[chave]['desconto'] < desconto):
                renova[chave] = item
    return renova


def _ler_vendas(hoje):
    """({(centro, material): [vendas 30 d, vendas 90 d]}, {material: nome no Vivo GO})."""
    d30, d90 = hoje - timedelta(days=30), hoje - timedelta(days=90)
    vendas, nomes, sem_loja = {}, {}, set()
    for r in mysql._consultar(
            "SELECT PDV, SKU, SUM(DATA_VENDA >= %s) AS d30, COUNT(*) AS d90, MAX(NOME_COMERCIAL_PRODUTO) AS nome "
            "FROM vw_vivogo_produtos_historico WHERE DATA_VENDA >= %s AND SKU <> '' GROUP BY PDV, SKU",
            (d30, d90)):
        centro = centro_da_loja(r['PDV'])
        if not centro:
            sem_loja.add(_texto(r['PDV']))
            continue
        chave = (centro, _texto(r['SKU']).upper())
        if r.get('nome'):
            nomes.setdefault(chave[1], _texto(r['nome']))
        atual = vendas.setdefault(chave, [0, 0])
        atual[0] += int(r['d30'] or 0)
        atual[1] += int(r['d90'] or 0)
    if sem_loja:
        logger.info('Estoque SAP: PDVs do Vivo GO sem centro conhecido: %s', ', '.join(sorted(sem_loja)))
    return vendas, nomes


_MEMORIA = {}


def fotografia(forcar=False):
    """O estoque da última carga, pronto para a tela. Levanta ``mysql.SapIndisponivel``."""
    iq09, mb52 = ultimas_cargas()
    hoje = timezone.localdate()
    chave = f'sap_estoque:foto:v3:{iq09}:{mb52}:{hoje.isoformat()}'
    if not forcar and _MEMORIA.get('chave') == chave:
        return _MEMORIA['foto']
    foto = None
    if not forcar:
        try:
            foto = cache.get(chave)
        except Exception:                                        # noqa: BLE001
            foto = None
    if foto is None:
        saldos, transito, desc_mb52, nomes_sap = _ler_mb52(mb52)
        unidades, desc_iq09 = _ler_iq09(iq09)
        try:
            catalogo = _ler_catalogo()
        except mysql.SapIndisponivel:
            logger.warning('Estoque SAP: catálogo de nomes comerciais não lido', exc_info=True)
            catalogo = {}
        try:
            vendas, nomes_vendas = _ler_vendas(hoje)
        except mysql.SapIndisponivel:
            logger.warning('Estoque SAP: vendas do Vivo GO não lidas (reposição sem vendas)', exc_info=True)
            vendas, nomes_vendas = None, {}
        try:
            renova = _ler_renova(hoje)
        except mysql.SapIndisponivel:
            logger.warning('Estoque SAP: descontos do Renova não lidos', exc_info=True)
            renova = {}
        foto = {
            'iq09': iq09, 'mb52': mb52, 'renova': renova,
            'quando_iq09': _data_da_carga(iq09), 'quando_mb52': _data_da_carga(mb52),
            'saldos': saldos, 'transito': transito, 'unidades': unidades,
            'descricoes': {**desc_iq09, **desc_mb52}, 'nomes_sap': nomes_sap,
            'catalogo': catalogo, 'vendas': vendas, 'nomes_vendas': nomes_vendas, 'hoje': hoje,
        }
        try:
            cache.set(chave, foto, TEMPO_FOTO)
        except Exception:                                        # noqa: BLE001 — grande demais para o cache: fica só na memória
            logger.info('Estoque SAP: fotografia não coube no cache', exc_info=True)
    _MEMORIA.clear()
    _MEMORIA.update(chave=chave, foto=foto)
    return foto


# ---------------------------------------------------------------------------
# O que a tela pergunta
# ---------------------------------------------------------------------------

def _sem_acento(texto):
    texto = unicodedata.normalize('NFKD', str(texto or ''))
    return ''.join(c for c in texto if not unicodedata.combining(c)).lower()


def centros_da_foto(foto):
    """Os centros com estoque na carga, na ordem de ``LOJAS`` (os desconhecidos no fim)."""
    vistos = {c for c, _m in foto['saldos']} | {c for c, _m in foto['transito']} | set(foto['nomes_sap'])
    conhecidos = [loja.centro for loja in LOJAS if loja.centro in vistos]
    return conhecidos + sorted(vistos - set(conhecidos))


def nome_do_centro(foto, centro):
    loja = POR_CENTRO.get(centro)
    if loja:
        return loja.nome
    return (foto['nomes_sap'].get(centro) or centro).title()


def produto(foto, material):
    """O nome que a loja reconhece: o comercial (com cor) ou, sem cadastro, o do SAP."""
    cat = foto['catalogo'].get(material) or {}
    descricao = foto['descricoes'].get(material, '')
    nome = (cat.get('nome_comercial') or (foto.get('nomes_vendas') or {}).get(material)
            or descricao.title() or material)
    return {
        'material': material,
        'nome': nome,
        'cor': cat.get('cor', ''),
        'categoria': cat.get('categoria', '') or 'Sem categoria',
        'subcategoria': cat.get('subcategoria', ''),
        'fabricante': cat.get('fabricante', ''),
        'gama': cat.get('gama', ''),
        'descricao_sap': descricao,
        'tem_cadastro': bool(cat),
        'renova': (foto.get('renova') or {}).get(_chave_nome(nome)),
    }


def bate_busca(foto, material, busca):
    """A busca da tela: nome comercial, descrição do SAP, material, cor ou fabricante."""
    if not busca:
        return True
    p = produto(foto, material)
    alvo = _sem_acento(' '.join((p['nome'], p['descricao_sap'], material, p['cor'], p['fabricante'])))
    return all(parte in alvo for parte in _sem_acento(busca).split())


def livre_para_venda(foto, centro, material):
    return foto['saldos'].get((centro, material), {}).get(DEPOSITO_VENDA, (0, 0))[0]


def em_transito(foto, centro, material):
    return foto['transito'].get((centro, material), 0)


def dias_desde(dia, hoje):
    return (hoje - dia).days if dia else None


def unidades_de(foto, centro=None, material=None, modo='disponivel', serial=''):
    """As unidades (com serial) de um centro/material.

    ``modo``: ``disponivel`` (LVUT livre), ``transito`` (a caminho do centro) ou
    ``todas`` (qualquer depósito, inclusive reparo e exposição).
    """
    hoje = foto['hoje']
    serial = re.sub(r'\s', '', serial or '').upper()
    saida = []
    for (mat, num, cen, dep, tipo, status, status_usuario, quando) in foto['unidades']:
        if centro and cen != centro:
            continue
        if material and mat != material:
            continue
        if serial and serial not in num.upper():
            continue
        if modo == 'disponivel' and not (dep == DEPOSITO_VENDA and tipo == '01'):
            continue
        if modo == 'transito' and tipo != TIPO_TRANSITO:
            continue
        saida.append({
            'material': mat, 'serial': num, 'centro': cen, 'loja': nome_do_centro(foto, cen),
            'deposito': dep or '—', 'deposito_nome': DEPOSITOS.get(dep, dep or 'Em trânsito'),
            'tipo': tipo, 'tipo_nome': TIPOS_DE_ESTOQUE.get(tipo, tipo or '—'),
            'status_sistema': status, 'status_usuario': status_usuario,
            'desde': quando, 'dias': dias_desde(quando, hoje),
        })
    saida.sort(key=lambda u: (-(u['dias'] or 0), u['serial']))
    return saida


def colunas_da_matriz(foto, centro):
    """As lojas da matriz: a escolhida e as seis mais perto (com km), ou a rede toda."""
    todos = centros_da_foto(foto)
    if centro and centro in todos:
        perto = vizinhas(centro, VIZINHAS, centros=todos)
        return [{'centro': centro, 'nome': nome_do_centro(foto, centro), 'km': 0, 'minha': True}] + [
            {'centro': c, 'nome': nome_do_centro(foto, c), 'km': km, 'minha': False} for c, km in perto]
    return [{'centro': c, 'nome': nome_do_centro(foto, c), 'km': None, 'minha': False} for c in todos]


def mais_perto_com_saldo(foto, centro, material, quanto_falta=1, de='disponivel'):
    """A loja mais perto de ``centro`` com o material livre (para buscar/transferir): (centro, km, qtd) ou None."""
    for outro, km in vizinhas(centro, quantas=0, centros=centros_da_foto(foto)):
        qtd = livre_para_venda(foto, outro, material)
        if qtd >= quanto_falta:
            return {'centro': outro, 'nome': nome_do_centro(foto, outro), 'km': km, 'qtd': qtd}
    return None


def matriz(foto, colunas, modo='disponivel', busca='', categoria='', so_com_saldo=True):
    """Linhas produto × loja (as colunas dadas), com o total da rede."""
    centros = [c['centro'] for c in colunas]
    fonte = foto['transito'] if modo == 'transito' else None
    materiais = set()
    if modo == 'transito':
        materiais = {m for (c, m), q in fonte.items() if q}
    else:
        materiais = {m for (c, m), deps in foto['saldos'].items() if deps.get(DEPOSITO_VENDA, (0, 0))[0]}
    linhas = []
    for material in materiais:
        if not bate_busca(foto, material, busca):
            continue
        p = produto(foto, material)
        if categoria and p['categoria'] != categoria:
            continue
        qtds = [(em_transito(foto, c, material) if modo == 'transito' else livre_para_venda(foto, c, material))
                for c in centros]
        if so_com_saldo and not any(qtds):
            continue
        rede = sum((em_transito(foto, c, material) if modo == 'transito' else livre_para_venda(foto, c, material))
                   for c in centros_da_foto(foto))
        linhas.append({'produto': p, 'qtds': qtds, 'total': sum(qtds), 'rede': rede})
    return linhas


def reposicao(foto, centro=None, busca='', categoria='', status=''):
    """Análise de pedidos: por loja e produto, estoque (livre + trânsito) × vendas 30/90 dias.

    Sugestão = vendas de 30 dias − estoque considerado (nunca negativa). Status:
    ``critico`` (sem livre na loja e 5+ vendas em 30 dias), ``ruptura`` (sem livre, com venda),
    ``insuficiente`` (tem, mas não cobre 30 dias) ou ``ok``. Para o que falta,
    ``buscar_em`` é a loja mais perto com sobra (livre acima da própria venda de
    30 dias) — a transferência antes do pedido.
    """
    vendas = foto['vendas'] or {}
    centros = [centro] if centro else centros_da_foto(foto)
    chaves = set()
    for c in centros:
        chaves |= {(c, m) for (cc, m) in foto['saldos'] if cc == c}
        chaves |= {(c, m) for (cc, m) in foto['transito'] if cc == c}
        chaves |= {(c, m) for (cc, m) in vendas if cc == c}
    linhas = []
    sobra_cache = {}
    for c, material in chaves:
        v30, v90 = vendas.get((c, material), (0, 0))
        livre = livre_para_venda(foto, c, material)
        transito = em_transito(foto, c, material)
        estoque = livre + transito
        if not v30 and not v90:
            continue                          # sem venda: não é assunto de reposição
        sugestao = max(0, int(math.ceil(v30 - estoque)))
        # Ruptura é a prateleira vazia (o que está chegando ainda não se vende);
        # a sugestão já desconta o que está a caminho.
        if livre <= 0:
            situacao = 'critico' if v30 >= CRITICO_VENDAS_30 else ('ruptura' if v30 else 'parado')
        elif estoque < v30:
            situacao = 'insuficiente'
        else:
            situacao = 'ok'
        if situacao == 'parado':
            situacao = 'ok'
        if status and situacao != status:
            continue
        if not bate_busca(foto, material, busca):
            continue
        p = produto(foto, material)
        if categoria and p['categoria'] != categoria:
            continue
        buscar = None
        if sugestao:
            for outro, km in vizinhas(c, quantas=0, centros=centros_da_foto(foto)):
                chave = (outro, material)
                if chave not in sobra_cache:
                    sobra_cache[chave] = livre_para_venda(foto, outro, material) - vendas.get(chave, (0, 0))[0]
                if km is not None and km > LIMITE_TRANSFERENCIA_KM:
                    break
                if sobra_cache[chave] >= 1:
                    buscar = {'centro': outro, 'nome': nome_do_centro(foto, outro), 'km': km,
                              'sobra': int(sobra_cache[chave])}
                    break
        linhas.append({
            'centro': c, 'loja': nome_do_centro(foto, c), 'produto': p,
            'livre': livre, 'transito': transito, 'estoque': estoque,
            'v30': v30, 'v90': v90, 'sugestao': sugestao, 'situacao': situacao, 'buscar_em': buscar,
        })
    ordem = {'critico': 0, 'ruptura': 1, 'insuficiente': 2, 'ok': 3}
    linhas.sort(key=lambda l: (ordem[l['situacao']], -l['sugestao'], -l['v30'], l['produto']['nome']))
    return linhas


def catalogo(foto, busca='', categoria='', fabricante=''):
    """Consulta produto: o cadastro e os saldos da rede (livre para venda e em trânsito)."""
    materiais = set(foto['catalogo']) | {m for (_c, m) in foto['saldos']} | {m for (_c, m) in foto['transito']}
    livre_rede, transito_rede = {}, {}
    for (c, m), deps in foto['saldos'].items():
        livre_rede[m] = livre_rede.get(m, 0) + deps.get(DEPOSITO_VENDA, (0, 0))[0]
    for (c, m), q in foto['transito'].items():
        transito_rede[m] = transito_rede.get(m, 0) + q
    linhas = []
    for material in materiais:
        p = produto(foto, material)
        if categoria and p['categoria'] != categoria:
            continue
        if fabricante and p['fabricante'] != fabricante:
            continue
        if not bate_busca(foto, material, busca):
            continue
        linhas.append({'produto': p, 'livre': livre_rede.get(material, 0), 'transito': transito_rede.get(material, 0)})
    linhas.sort(key=lambda l: (-(l['livre'] + l['transito']), l['produto']['nome']))
    return linhas


def categorias(foto):
    """As categorias do filtro, uma de cada (o catálogo já vem padronizado); "Sem categoria" no fim."""
    nomes = {c.get('categoria') for c in foto['catalogo'].values() if c.get('categoria')}
    return sorted(nomes, key=_sem_acento) + ['Sem categoria']


def fabricantes(foto):
    return sorted({c.get('fabricante') for c in foto['catalogo'].values() if c.get('fabricante')}, key=_sem_acento)


def lojas_com_o_produto(foto, material, minha=''):
    """Onde tem o material (livre e em trânsito), da loja mais perto de ``minha`` para a mais longe."""
    origem = POR_CENTRO.get(minha)
    lojas = []
    for centro in centros_da_foto(foto):
        livre, transito = livre_para_venda(foto, centro, material), em_transito(foto, centro, material)
        if not (livre or transito):
            continue
        km = distancia_km(origem, POR_CENTRO.get(centro)) if origem and centro != minha else (0 if centro == minha else None)
        lojas.append({'centro': centro, 'nome': nome_do_centro(foto, centro), 'livre': int(livre),
                      'transito': int(transito), 'km': None if km is None else round(km), 'minha': centro == minha})
    lojas.sort(key=lambda l: (not l['minha'], l['km'] if l['km'] is not None else 99999, -l['livre']))
    return lojas
