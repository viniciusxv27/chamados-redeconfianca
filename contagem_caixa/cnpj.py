"""Consulta de CNPJ na base pública da Receita Federal.

O fornecedor do PIS/Cofins entra pelo CNPJ: razão social, situação cadastral,
atividade e opção pelo Simples chegam da Receita, não da digitação. É o que
mantém o mesmo fornecedor com um nome só na planilha do mês e mostra na hora o
CNPJ baixado ou inapto (que a contabilidade precisa saber antes de tomar
crédito).

A Receita não tem API aberta: o site oficial pede captcha. As duas fontes
abaixo republicam o cadastro da Receita (dados abertos do CNPJ) e respondem
sem chave — BrasilAPI primeiro, CNPJ.ws de reserva. O resultado fica em cache
por uma semana.

Três respostas possíveis, como na busca de CEP (users/cep.py):

- ``{'cnpj': ..., 'razao_social': ...}`` → achou;
- ``None``                              → CNPJ não existe na Receita;
- ``CnpjIndisponivel``                  → ninguém respondeu (a tela deixa
  seguir com a razão social digitada).
"""
import logging
import re

import requests
from django.core.cache import caches

logger = logging.getLogger(__name__)

TIMEOUT = (3, 8)
CACHE_SEGUNDOS = 60 * 60 * 24 * 7
# Comprovante oficial (com captcha): o link vai na tela e na planilha.
LINK_RECEITA = 'https://solucoes.receita.fazenda.gov.br/Servicos/cnpjreva/Cnpjreva_Solicitacao.asp?cnpj={cnpj}'


class CnpjIndisponivel(Exception):
    """Nenhuma fonte respondeu — diferente de "CNPJ não existe"."""


def so_digitos(valor):
    return re.sub(r'\D', '', str(valor or ''))


def formatar(valor):
    d = so_digitos(valor)
    if len(d) != 14:
        return str(valor or '')
    return f'{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}'


def valido(valor):
    """14 dígitos com os dois verificadores certos."""
    d = so_digitos(valor)
    if len(d) != 14 or d == d[0] * 14:
        return False
    for tamanho in (12, 13):
        pesos = list(range(tamanho - 7, 1, -1)) + list(range(9, 1, -1))
        resto = sum(int(d[i]) * pesos[i] for i in range(tamanho)) % 11
        if (0 if resto < 2 else 11 - resto) != int(d[tamanho]):
            return False
    return True


def _da_brasilapi(digitos):
    r = requests.get(f'https://brasilapi.com.br/api/cnpj/v1/{digitos}', timeout=TIMEOUT)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    d = r.json()
    return {
        'cnpj': digitos,
        'razao_social': (d.get('razao_social') or '').strip(),
        'nome_fantasia': (d.get('nome_fantasia') or '').strip(),
        'situacao': (d.get('descricao_situacao_cadastral') or '').strip().upper(),
        'atividade': (d.get('cnae_fiscal_descricao') or '').strip(),
        'simples': d.get('opcao_pelo_simples') if isinstance(d.get('opcao_pelo_simples'), bool) else None,
        'municipio': (d.get('municipio') or '').strip().title(),
        'uf': (d.get('uf') or '').strip().upper(),
    }


def _do_cnpjws(digitos):
    r = requests.get(f'https://publica.cnpj.ws/cnpj/{digitos}', timeout=TIMEOUT)
    if r.status_code in (400, 404):
        return None
    r.raise_for_status()
    d = r.json()
    e = d.get('estabelecimento') or {}
    simples = (d.get('simples') or {}).get('simples')
    return {
        'cnpj': digitos,
        'razao_social': (d.get('razao_social') or '').strip(),
        'nome_fantasia': (e.get('nome_fantasia') or '').strip(),
        'situacao': (e.get('situacao_cadastral') or '').strip().upper(),
        'atividade': ((e.get('atividade_principal') or {}).get('descricao') or '').strip(),
        'simples': {'Sim': True, 'Não': False}.get(simples),
        'municipio': ((e.get('cidade') or {}).get('nome') or '').strip(),
        'uf': ((e.get('estado') or {}).get('sigla') or '').strip().upper(),
    }


FONTES = (('BrasilAPI', _da_brasilapi), ('CNPJ.ws', _do_cnpjws))


def buscar(valor):
    """O cadastro do CNPJ. ``None`` se não existe; ``CnpjIndisponivel`` se ninguém respondeu."""
    digitos = so_digitos(valor)
    if not valido(digitos):
        return None

    # Teste não fala com a internet: quem quiser exercitar troca `buscar`.
    from core.utils import processo_de_teste
    if processo_de_teste():
        raise CnpjIndisponivel('processo de teste: consulta de CNPJ não sai para a internet')

    chave = f'cnpj:{digitos}'
    guardado = caches['default'].get(chave)
    if guardado is not None:
        return guardado or None

    falhas = []
    for nome, fonte in FONTES:
        try:
            achado = fonte(digitos)
        except Exception as exc:  # noqa: BLE001 — a próxima fonte tenta
            falhas.append(f'{nome}: {exc}')
            continue
        if achado is not None and not achado['razao_social']:
            falhas.append(f'{nome}: resposta sem razão social')
            continue
        caches['default'].set(chave, achado or '', CACHE_SEGUNDOS)
        return achado

    logger.warning('Nenhuma fonte de CNPJ respondeu para %s: %s', digitos, '; '.join(falhas))
    raise CnpjIndisponivel('; '.join(falhas))
