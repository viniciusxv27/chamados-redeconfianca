"""Dados da empresa pelo CNPJ, em APIs públicas (sem chave) — para cadastrar cliente PJ na venda.

Tenta uma de cada vez: BrasilAPI → Minha Receita → CNPJ.ws → ReceitaWS. Todas são
gratuitas e limitam pedidos por minuto (a BrasilAPI responde 429 com frequência),
então a que falhar ou recusar passa para a próxima, e o que achou fica 1 dia no cache.
"CNPJ não existe" (404/400) para na hora: as outras diriam o mesmo.
"""
import logging

import requests
from django.core.cache import cache

from . import slv

logger = logging.getLogger(__name__)

TEMPO_LIMITE = 8          # segundos por API
CACHE_SEGUNDOS = 24 * 3600
CABECALHOS = {'User-Agent': 'portal-redeconfianca/1.0', 'Accept': 'application/json'}


class NaoEncontrado(Exception):
    pass


def _texto(v):
    return str(v or '').strip()


def _logradouro(tipo, nome):
    tipo, nome = _texto(tipo), _texto(nome)
    return f'{tipo} {nome}'.strip() if tipo and not nome.upper().startswith(tipo.upper()) else nome


def _do_padrao_receita(d):
    """BrasilAPI e Minha Receita devolvem o mesmo formato (o da base aberta da Receita)."""
    return {'razao_social': _texto(d.get('razao_social')), 'nome_fantasia': _texto(d.get('nome_fantasia')),
            'situacao': _texto(d.get('descricao_situacao_cadastral')), 'telefone': slv.so_digitos(d.get('ddd_telefone_1')),
            'email': _texto(d.get('email')).lower(), 'cep': slv.so_digitos(d.get('cep')),
            'logradouro': _logradouro(d.get('descricao_tipo_de_logradouro'), d.get('logradouro')),
            'numero': _texto(d.get('numero')), 'complemento': _texto(d.get('complemento')),
            'bairro': _texto(d.get('bairro')), 'cidade': _texto(d.get('municipio')), 'uf': _texto(d.get('uf')).upper()}


def _do_cnpj_ws(d):
    e = d.get('estabelecimento') or {}
    return {'razao_social': _texto(d.get('razao_social')), 'nome_fantasia': _texto(e.get('nome_fantasia')),
            'situacao': _texto(e.get('situacao_cadastral')).upper(),
            'telefone': slv.so_digitos(f"{e.get('ddd1') or ''}{e.get('telefone1') or ''}"),
            'email': _texto(e.get('email')).lower(), 'cep': slv.so_digitos(e.get('cep')),
            'logradouro': _logradouro(e.get('tipo_logradouro'), e.get('logradouro')),
            'numero': _texto(e.get('numero')), 'complemento': _texto(e.get('complemento')),
            'bairro': _texto(e.get('bairro')), 'cidade': _texto((e.get('cidade') or {}).get('nome')),
            'uf': _texto((e.get('estado') or {}).get('sigla')).upper()}


def _do_receitaws(d):
    if _texto(d.get('status')).upper() == 'ERROR':
        raise NaoEncontrado(_texto(d.get('message')))
    telefone = slv.so_digitos(_texto(d.get('telefone')).split('/')[0])
    return {'razao_social': _texto(d.get('nome')), 'nome_fantasia': _texto(d.get('fantasia')),
            'situacao': _texto(d.get('situacao')).upper(), 'telefone': telefone,
            'email': _texto(d.get('email')).lower(), 'cep': slv.so_digitos(d.get('cep')),
            'logradouro': _texto(d.get('logradouro')), 'numero': _texto(d.get('numero')),
            'complemento': _texto(d.get('complemento')), 'bairro': _texto(d.get('bairro')),
            'cidade': _texto(d.get('municipio')), 'uf': _texto(d.get('uf')).upper()}


FONTES = [
    ('BrasilAPI', 'https://brasilapi.com.br/api/cnpj/v1/{}', _do_padrao_receita),
    ('Minha Receita', 'https://minhareceita.org/{}', _do_padrao_receita),
    ('CNPJ.ws', 'https://publica.cnpj.ws/cnpj/{}', _do_cnpj_ws),
    ('ReceitaWS', 'https://receitaws.com.br/v1/cnpj/{}', _do_receitaws),
]


def consultar(cnpj):
    """``{'ok': True, 'empresa': {...}, 'fonte': ...}`` ou ``{'ok': False, 'erro': ...}`` (nunca levanta)."""
    cnpj = slv.so_digitos(cnpj)
    if not slv.cnpj_valido(cnpj):
        return {'ok': False, 'erro': 'CNPJ inválido.'}
    chave = f'vendas:cnpj:{cnpj}'
    guardado = cache.get(chave)
    if guardado:
        return guardado
    for nome, url, converter in FONTES:
        try:
            r = requests.get(url.format(cnpj), headers=CABECALHOS, timeout=TEMPO_LIMITE)
            if r.status_code in (400, 404):
                return {'ok': False, 'nao_encontrado': True, 'erro': 'CNPJ não encontrado na Receita.'}
            if r.status_code != 200:
                logger.info('CNPJ %s: %s respondeu %s', cnpj, nome, r.status_code)
                continue
            empresa = converter(r.json())
        except NaoEncontrado:
            return {'ok': False, 'nao_encontrado': True, 'erro': 'CNPJ não encontrado na Receita.'}
        except (requests.RequestException, ValueError, AttributeError, TypeError) as exc:
            logger.info('CNPJ %s: %s falhou (%s)', cnpj, nome, exc)
            continue
        if not empresa['razao_social']:
            continue
        resposta = {'ok': True, 'empresa': empresa, 'fonte': nome}
        cache.set(chave, resposta, CACHE_SEGUNDOS)
        return resposta
    return {'ok': False, 'erro': 'As consultas públicas de CNPJ não responderam agora. Preencha os dados à mão.'}
