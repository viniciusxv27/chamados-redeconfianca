"""Base de clientes do Vivo GO espelhada no Postgres.

As views ``vendas_produtos_2026`` e ``vendas_servicos_2026`` (MySQL do Vivo GO,
``VIVOGO_MYSQL_URL``) não têm chave nem índice: contar leva 5 s e buscar um
CPF varre a view inteira. Por isso a base vem para cá:

- **De 5 em 5 minutos**: só as linhas inseridas desde a última leitura
  (``DATA_INSERCAO_VENDA``, com 2 h de folga para trás).
- **Uma vez por dia**: a leitura completa, que reconcilia — apaga daqui o que
  saiu de lá (cancelamento, correção de linha).

Cada linha vira uma ``CompraVivoGo`` com chave = hash da linha inteira + a
ordem dela entre linhas idênticas. Depois, o resumo de cada cliente tocado
(nome, última linha, último PDV, plano, primeira/última compra, receita) vai
para ``vendas.Cliente`` — sem sobrescrever o que foi cadastrado no portal.

Sem cron: o middleware chama ``disparar_se_esta_na_hora`` (mesmo arranjo do
agendador do SAP); a leitura roda numa thread e só um processo ganha a vez.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from contextlib import nullcontext
from datetime import timedelta
from decimal import Decimal
from urllib.parse import unquote, urlparse

from django.conf import settings
from django.db import close_old_connections, transaction
from django.db.models import Count, Max, Min, Q, Sum
from django.utils import timezone

logger = logging.getLogger(__name__)

VIEWS = (('P', 'vendas_produtos_2026'), ('S', 'vendas_servicos_2026'))
INTERVALO = timedelta(minutes=5)
INTERVALO_COMPLETA = timedelta(hours=24)
FOLGA = timedelta(hours=2)            # relê as últimas 2 h: linha inserida com atraso não escapa
TRAVA_MAXIMA = timedelta(minutes=20)  # leitura que morreu no meio não prende a fila para sempre


class VivoGoIndisponivel(Exception):
    pass


# ── Conexão e leitura ───────────────────────────────────────────────────────
def _url():
    from decouple import config
    return getattr(settings, 'VIVOGO_MYSQL_URL', '') or config('VIVOGO_MYSQL_URL', default='')


def configurado():
    return bool(_url())


def conectar():
    import pymysql
    url = urlparse(_url())
    if not url.hostname:
        raise VivoGoIndisponivel('VIVOGO_MYSQL_URL não configurada.')
    try:
        return pymysql.connect(host=url.hostname, port=url.port or 3306, user=unquote(url.username or ''),
                               password=unquote(url.password or ''), database=url.path.lstrip('/'),
                               charset='utf8mb4', connect_timeout=15, read_timeout=300,
                               cursorclass=pymysql.cursors.DictCursor)
    except Exception as exc:  # noqa: BLE001
        raise VivoGoIndisponivel(f'MySQL do Vivo GO fora do ar: {exc}') from exc


def ler(desde=None, conexao=None):
    """As linhas das duas views (todas, ou inseridas a partir de ``desde``)."""
    proprio = conexao is None
    conexao = conexao or conectar()
    linhas = []
    try:
        with conexao.cursor() as cursor:
            for tipo, view in VIEWS:
                if desde:
                    cursor.execute(f'SELECT * FROM `{view}` WHERE DATA_INSERCAO_VENDA >= %s',
                                   (timezone.localtime(desde).replace(tzinfo=None),))
                else:
                    cursor.execute(f'SELECT * FROM `{view}`')
                linhas.extend((tipo, linha) for linha in cursor.fetchall())
    except VivoGoIndisponivel:
        raise
    except Exception as exc:  # noqa: BLE001
        raise VivoGoIndisponivel(f'Leitura do Vivo GO falhou: {exc}') from exc
    finally:
        if proprio:
            conexao.close()
    return linhas


# ── Normalização ────────────────────────────────────────────────────────────
def so_digitos(valor):
    return re.sub(r'\D', '', str(valor or ''))


def _texto(valor, limite):
    return ' '.join(str(valor or '').split())[:limite]


def _aware(valor):
    if valor is None:
        return None
    if hasattr(valor, 'hour'):
        return timezone.make_aware(valor) if timezone.is_naive(valor) else valor
    return valor


def _serial(valor):
    return valor.isoformat() if hasattr(valor, 'isoformat') else (str(valor) if isinstance(valor, Decimal) else valor)


def normalizar(linhas):
    """[(tipo, linha do MySQL)] → [dict para CompraVivoGo], com a chave estável."""
    vistos, saida = {}, []
    for tipo, r in linhas:
        base = hashlib.sha1(json.dumps({'t': tipo, **{k: _serial(v) for k, v in r.items()}},
                                       sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        n = vistos.get(base, 0)
        vistos[base] = n + 1
        documento = so_digitos(r.get('CPF_CLIENTE') or r.get('DOCUMENTO_CLIENTE'))
        if tipo == 'P':
            item = r.get('NOME_COMERCIAL_PRODUTO') or r.get('NOME_PRODUTO')
            receita = r.get('RECEITA')
        else:
            item = r.get('SERVICO')
            receita = r.get('NOVA RECEITA') if r.get('NOVA RECEITA') is not None else r.get('RECEITA')
        saida.append({
            'chave': f'{base}:{n}', 'tipo': tipo, 'id_venda': _texto(r.get('ID_VENDA'), 30),
            'data_venda': r.get('DATA_VENDA'), 'data_insercao': _aware(r.get('DATA_INSERCAO_VENDA')),
            'cpf': documento[:14], 'nome_cliente': _texto(r.get('NOME_CLIENTE'), 200),
            'numero_acesso': so_digitos(r.get('NUMERO_ACESSO'))[:20], 'pdv': _texto(r.get('PDV'), 80),
            'coordenacao': _texto(r.get('COORDENACAO'), 80), 'vendedor': _texto(r.get('VENDEDOR'), 200),
            'item': _texto(item, 255), 'plano': _texto(r.get('PLANO'), 200), 'sku': _texto(r.get('SKU'), 60),
            'subcategoria': _texto(r.get('SUBCATEGORIA'), 120), 'tipo_produto': _texto(r.get('TIPO_PRODUTO'), 120),
            'qtd': int(r.get('QTD_VENDIDA') or 1), 'receita': receita, 'pilar': _texto(r.get('PILAR'), 60),
            'status_servico': _texto(r.get('STATUS_SERVICO'), 60),
            'data_cancelamento': r.get('DATA_CANCELAMENTO') if not hasattr(r.get('DATA_CANCELAMENTO'), 'hour')
            else r.get('DATA_CANCELAMENTO').date(),
        })
    return saida


def segmentacao_do_plano(nome):
    """Pós, Controle, Pré ou Vivo Empresas pelo nome do plano no Vivo GO (aproximação)."""
    texto = (nome or '').upper()
    if 'CONTROLE' in texto:
        return 'CONTROLE'
    if 'PRÉ' in texto or 'PRE ' in texto or texto.startswith('PRE') or 'PRÉ-PAGO' in texto:
        return 'PRE'
    if 'EMPRESA' in texto or 'PJ' in texto.split():
        return 'EMPRESAS'
    if texto:
        return 'POS'
    return ''


# ── Gravar ──────────────────────────────────────────────────────────────────
def _gravar_linhas(dados, completa, escopo=None):
    """Insere as linhas novas; na completa, apaga as que não existem mais lá. Devolve (novas, removidas, cpfs)."""
    from .models import CompraVivoGo

    chaves = [d['chave'] for d in dados]
    existentes = set()
    for i in range(0, len(chaves), 5000):
        existentes.update(CompraVivoGo.objects.filter(chave__in=chaves[i:i + 5000]).values_list('chave', flat=True))
    novas = [CompraVivoGo(**d) for d in dados if d['chave'] not in existentes]
    tocados = {d['cpf'] for d in dados if d['chave'] not in existentes}
    CompraVivoGo.objects.bulk_create(novas, batch_size=2000, ignore_conflicts=True)
    removidas = 0
    if completa:
        conjunto = set(chaves)
        base = escopo if escopo is not None else CompraVivoGo.objects.all()
        sobrando = [c for c in base.values_list('chave', flat=True).iterator() if c not in conjunto]
        for i in range(0, len(sobrando), 5000):
            parte = sobrando[i:i + 5000]
            tocados.update(CompraVivoGo.objects.filter(chave__in=parte).values_list('cpf', flat=True))
            removidas += CompraVivoGo.objects.filter(chave__in=parte).delete()[0]
    return len(novas), removidas, {c for c in tocados if c}


def atualizar_clientes(cpfs):
    """Resumo de compras de cada CPF → vendas.Cliente (cria quem ainda não existe).

    O cadastro do portal manda: nome e telefone só são preenchidos quando
    estão vazios (ou o cliente veio do próprio Vivo GO).
    """
    from .models import Cliente, CompraVivoGo

    cpfs = [c for c in cpfs if len(c) == 11]
    agora = timezone.now()
    criados = atualizados = 0
    for i in range(0, len(cpfs), 2000):
        lote = cpfs[i:i + 2000]
        resumo = {r['cpf']: r for r in CompraVivoGo.objects.filter(cpf__in=lote).values('cpf').annotate(
            primeira=Min('data_venda'), ultima=Max('data_venda'), n=Count('id_venda', distinct=True),
            total=Sum('receita', filter=Q(data_cancelamento__isnull=True)))}
        ultimas = {}
        for c in (CompraVivoGo.objects.filter(cpf__in=lote).order_by('cpf', '-data_venda', '-data_insercao')
                  .values('cpf', 'nome_cliente', 'numero_acesso', 'pdv', 'vendedor').iterator()):
            ultimas.setdefault(c['cpf'], c)
        planos = {}
        for c in (CompraVivoGo.objects.filter(cpf__in=lote, tipo='S').exclude(plano='')
                  .order_by('cpf', '-data_venda', '-data_insercao').values('cpf', 'plano', 'receita').iterator()):
            planos.setdefault(c['cpf'], c)
        existentes = {c.cpf: c for c in Cliente.objects.filter(cpf__in=lote)}
        novos, mudados = [], []
        for cpf in lote:
            r, u = resumo.get(cpf), ultimas.get(cpf)
            if not r or not u:
                continue
            p = planos.get(cpf) or {}
            dados = dict(linha=u['numero_acesso'], pdv_ultimo=u['pdv'], vendedor_ultimo=u['vendedor'],
                         plano_vivogo=p.get('plano', ''), primeira_compra=r['primeira'], ultima_compra=r['ultima'],
                         qtd_vendas=r['n'], total_gasto=r['total'] or Decimal('0'), sincronizado_em=agora)
            cliente = existentes.get(cpf)
            if cliente is None:
                novos.append(Cliente(cpf=cpf, nome=(u['nome_cliente'] or 'Cliente sem nome')[:200], origem='VIVOGO',
                                     telefone=u['numero_acesso'][:13], segmentacao=segmentacao_do_plano(p.get('plano')),
                                     plano_nome=p.get('plano', '')[:200],
                                     valor_pago=p.get('receita') if p.get('receita') else None, **dados))
                continue
            for campo, valor in dados.items():
                setattr(cliente, campo, valor)
            if cliente.origem == 'VIVOGO' or not cliente.nome:
                cliente.nome = (u['nome_cliente'] or cliente.nome)[:200]
            if not cliente.telefone and u['numero_acesso']:
                cliente.telefone = u['numero_acesso'][:13]
            if cliente.origem == 'VIVOGO' and not cliente.plano_id and p.get('plano'):
                cliente.plano_nome, cliente.segmentacao = p['plano'][:200], segmentacao_do_plano(p['plano'])
                cliente.valor_pago = p.get('receita') or cliente.valor_pago
            mudados.append(cliente)
        Cliente.objects.bulk_create(novos, batch_size=1000, ignore_conflicts=True)
        Cliente.objects.bulk_update(mudados, ['linha', 'pdv_ultimo', 'vendedor_ultimo', 'plano_vivogo', 'primeira_compra',
                                              'ultima_compra', 'qtd_vendas', 'total_gasto', 'sincronizado_em', 'nome',
                                              'telefone', 'plano_nome', 'segmentacao', 'valor_pago'], batch_size=1000)
        criados += len(novos)
        atualizados += len(mudados)
    return criados, atualizados


def sincronizar(completa=False, leitor=None, agora=None, escopo=None):
    """Lê o Vivo GO e atualiza o espelho. Devolve o resumo (str).

    ``leitor`` troca a leitura e ``escopo`` limita o que a completa pode apagar (testes).
    """
    from .models import SincronizacaoVivoGo

    agora = agora or timezone.now()
    sinc = SincronizacaoVivoGo.get()
    completa = completa or sinc.marca is None or not sinc.ultima_completa \
        or agora - sinc.ultima_completa >= INTERVALO_COMPLETA
    desde = None if completa else (sinc.marca - FOLGA)
    try:
        linhas = (leitor or ler)(desde)
    except VivoGoIndisponivel as exc:
        SincronizacaoVivoGo.objects.filter(pk=sinc.pk).update(
            ultimo_erro=str(exc)[:500], ultimo_erro_em=agora, em_andamento_desde=None)
        raise
    dados = normalizar(linhas)
    # A completa grava em lotes (não segura o banco compartilhado minutos numa transação só);
    # se cair no meio, a próxima leitura completa termina — as chaves não duplicam.
    with (nullcontext() if completa else transaction.atomic()):
        novas, removidas, cpfs = _gravar_linhas(dados, completa, escopo)
        criados, atualizados = atualizar_clientes(cpfs)
        marca = max((d['data_insercao'] for d in dados if d['data_insercao']), default=sinc.marca)
        resumo = (f"{'completa' if completa else 'incremental'}: {len(dados)} linha(s) lida(s), {novas} nova(s), "
                  f"{removidas} removida(s); {criados} cliente(s) novo(s), {atualizados} atualizado(s)")
        campos = dict(marca=max(filter(None, [marca, sinc.marca])) if (marca or sinc.marca) else None,
                      ultima_incremental=agora, ultimo_resumo=resumo[:255], ultimo_erro='', em_andamento_desde=None)
        if completa:
            campos['ultima_completa'] = agora
        SincronizacaoVivoGo.objects.filter(pk=sinc.pk).update(**campos)
    logger.info('Vivo GO %s', resumo)
    return resumo


# ── Agendador (sem cron) ────────────────────────────────────────────────────
_ultima_checagem = None
_trava_local = threading.Lock()


def _rodar():
    try:
        sincronizar()
    except VivoGoIndisponivel as exc:
        logger.warning('Sincronização do Vivo GO falhou: %s', exc)
    except Exception as exc:  # noqa: BLE001 — nunca derruba a thread
        logger.exception('Sincronização do Vivo GO quebrou: %s', exc)
        from .models import SincronizacaoVivoGo
        SincronizacaoVivoGo.objects.update(ultimo_erro=str(exc)[:500], ultimo_erro_em=timezone.now(),
                                           em_andamento_desde=None)
    finally:
        close_old_connections()


def disparar_se_esta_na_hora():
    """Chamado pelo middleware. Devolve True se ESTA chamada disparou a leitura."""
    global _ultima_checagem
    agora = timezone.now()
    with _trava_local:
        if _ultima_checagem is not None and (agora - _ultima_checagem).total_seconds() < 60:
            return False
        _ultima_checagem = agora
    try:
        from core.utils import processo_de_teste

        from .models import SincronizacaoVivoGo
        if processo_de_teste() or not configurado():
            return False
        sinc = SincronizacaoVivoGo.get()
        if sinc.ultima_incremental and agora - sinc.ultima_incremental < INTERVALO:
            return False
        livre = Q(em_andamento_desde__isnull=True) | Q(em_andamento_desde__lt=agora - TRAVA_MAXIMA)
        ganhou = SincronizacaoVivoGo.objects.filter(pk=sinc.pk).filter(livre).update(em_andamento_desde=agora)
        if not ganhou:
            return False
        threading.Thread(target=_rodar, name='vivogo-clientes', daemon=True).start()
        return True
    except Exception as exc:  # noqa: BLE001 — jamais quebra a página
        logger.warning('Agendador do Vivo GO ignorado por erro: %s', exc)
        return False
