"""Consulta de CEP: o endereço vem de fora, não da digitação.

No pré-cadastro o colaborador preenche só o CEP e o número; rua, bairro,
cidade e UF chegam prontos daqui. É o que evita "Rua das Flores" escrito de
seis jeitos diferentes na ficha de 183 pessoas — e um CEP que não existe passa
a ser erro na hora, não descoberta no dia do envio da documentação.

Duas fontes, na ordem: ViaCEP (a de sempre) e BrasilAPI como reserva. O
resultado fica em cache por semanas: CEP não muda, e a mesma rua se repete
entre colegas.

Três respostas possíveis, e o chamador precisa distinguir as três:

- ``{'cep': ..., 'logradouro': ...}``  → achou;
- ``None``                            → CEP não existe (é erro de quem digitou);
- ``CepIndisponivel``                 → ninguém respondeu (não é culpa de quem
  digitou: a tela deixa seguir com o que já estava preenchido).
"""
import logging
import re

import requests
from django.core.cache import caches

logger = logging.getLogger(__name__)

TIMEOUT = (3, 5)                  # (conexão, leitura) — a tela espera por isto
CACHE_SEGUNDOS = 60 * 60 * 24 * 30


class CepIndisponivel(Exception):
    """Nenhuma fonte respondeu — diferente de "CEP não existe"."""


def so_digitos(valor):
    return re.sub(r'\D', '', str(valor or ''))[:8]


def formatar(valor):
    """00000000 -> 00000-000 (o que não tem 8 dígitos volta como veio)."""
    digitos = so_digitos(valor)
    return f'{digitos[:5]}-{digitos[5:]}' if len(digitos) == 8 else str(valor or '')


def valido(valor):
    """Tem cara de CEP? (8 dígitos e não é tudo o mesmo número)"""
    digitos = so_digitos(valor)
    return len(digitos) == 8 and len(set(digitos)) > 1


def _cache():
    # O cache local não sai deste processo, mas o CEP é imutável: o Redis
    # compartilhado é o padrão, e o LocMem dos testes também serve.
    return caches['default']


def _do_viacep(digitos):
    resposta = requests.get(f'https://viacep.com.br/ws/{digitos}/json/', timeout=TIMEOUT)
    resposta.raise_for_status()
    dados = resposta.json()
    if dados.get('erro') in (True, 'true'):
        return None
    if not (dados.get('localidade') and dados.get('uf')):
        return None
    return {
        'cep': digitos,
        'logradouro': (dados.get('logradouro') or '').strip(),
        'bairro': (dados.get('bairro') or '').strip(),
        'cidade': (dados.get('localidade') or '').strip(),
        'uf': (dados.get('uf') or '').strip().upper(),
    }


def _do_brasilapi(digitos):
    resposta = requests.get(f'https://brasilapi.com.br/api/cep/v1/{digitos}', timeout=TIMEOUT)
    if resposta.status_code in (400, 404):
        return None                      # o serviço responde 404 para CEP que não existe
    resposta.raise_for_status()
    dados = resposta.json()
    if not (dados.get('city') and dados.get('state')):
        return None
    return {
        'cep': digitos,
        'logradouro': (dados.get('street') or '').strip(),
        'bairro': (dados.get('neighborhood') or '').strip(),
        'cidade': (dados.get('city') or '').strip(),
        'uf': (dados.get('state') or '').strip().upper(),
    }


FONTES = (('ViaCEP', _do_viacep), ('BrasilAPI', _do_brasilapi))


def buscar(valor):
    """O endereço do CEP. ``None`` se não existe; ``CepIndisponivel`` se ninguém respondeu."""
    digitos = so_digitos(valor)
    if not valido(digitos):
        return None

    # Teste não fala com a internet: quem quiser exercitar a consulta troca
    # `buscar` (ou as fontes) por um dublê. Sem isto, qualquer teste que
    # preenchesse o pré-cadastro bateria no ViaCEP de verdade.
    from core.utils import processo_de_teste
    if processo_de_teste():
        raise CepIndisponivel('processo de teste: consulta de CEP não sai para a internet')

    chave = f'cep:{digitos}'
    guardado = _cache().get(chave)
    if guardado is not None:
        return guardado or None          # '' em cache = CEP que não existe

    falhas = []
    for nome, fonte in FONTES:
        try:
            achado = fonte(digitos)
        except Exception as exc:         # noqa: BLE001 — a próxima fonte tenta
            falhas.append(f'{nome}: {exc}')
            continue
        _cache().set(chave, achado or '', CACHE_SEGUNDOS)
        return achado

    logger.warning('Nenhuma fonte de CEP respondeu para %s: %s', digitos, '; '.join(falhas))
    raise CepIndisponivel('; '.join(falhas))
