"""Ponte com o sistema de perfil (IA do RH).

O sistema de perfil já faz o trabalho difícil: grava a entrevista, transcreve,
analisa o perfil e casa o candidato com a loja. Duplicar isso aqui seria manter
duas IAs divergindo. O portal entra com o que ele tem de melhor — o banco de
currículos e a agenda — e usa o outro pelo que ele é.

São dois caminhos, do mais barato para o mais caro:

* **link** — o portal monta o endereço público da entrevista (``/e/<token>``).
  Não depende de credencial nenhuma e é o que sempre funciona.
* **leitura do resultado** — quando as credenciais do MySQL do sistema de
  perfil estiverem no ambiente, o portal lê a análise pronta e mostra dentro do
  currículo. Sem credencial, o portal só não mostra o resumo; nada quebra.
"""
import logging
import os

logger = logging.getLogger(__name__)


def base_do_sistema():
    """Endereço do sistema de perfil, sem barra no fim."""
    from .models import ConfiguracaoCurriculos
    try:
        url = (ConfiguracaoCurriculos.get().url_sistema_perfil or '').strip()
    except Exception:                                        # banco indisponível
        url = ''
    if not url:
        url = os.environ.get('SISTEMA_PERFIL_URL', '').strip()
    return url.rstrip('/')


def url_da_entrevista(token):
    """Link público da entrevista. Vazio se não houver token ou endereço."""
    base = base_do_sistema()
    if not base or not token:
        return ''
    return f'{base}/e/{token}'


# ── Leitura do resultado (opcional) ─────────────────────────────────────────

def _conexao():
    """Conexão de leitura com o MySQL do sistema de perfil, ou None.

    A URL vem do ambiente, nunca do código: é credencial de outro sistema e não
    pode viver no repositório.
    """
    url = os.environ.get('SISTEMA_PERFIL_MYSQL_URL', '').strip()
    if not url:
        return None
    try:
        import pymysql
        from urllib.parse import urlparse

        p = urlparse(url)
        return pymysql.connect(
            host=p.hostname, port=p.port or 3306, user=p.username,
            password=p.password, database=(p.path or '/').lstrip('/'),
            charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5, read_timeout=8)
    except Exception as exc:                                 # noqa: BLE001
        logger.warning('Sistema de perfil indisponível: %s', exc)
        return None


def resultado_da_entrevista(token):
    """Resumo da análise daquele token, ou None.

    Somente leitura, e falha em silêncio: a tela do currículo funciona sem o
    resumo, e derrubar a página do RH por causa de um sistema externo fora do ar
    seria trocar um problema pequeno por um grande.
    """
    if not token:
        return None
    conexao = _conexao()
    if conexao is None:
        return None
    try:
        with conexao:
            with conexao.cursor() as cur:
                cur.execute(
                    'SELECT id, criado_em, status FROM analises '
                    'WHERE token_publico=%s LIMIT 1', (token,))
                analise = cur.fetchone()
                if not analise:
                    return None
                cur.execute(
                    'SELECT nome, resumo, competencia_tecnica, tolerancia_pressao, '
                    '       ritmo_aprendizado, status, loja_top, score_top '
                    'FROM candidatos WHERE analise_id=%s ORDER BY id', (analise['id'],))
                candidatos = cur.fetchall() or []
        return {'analise': analise, 'candidatos': candidatos}
    except Exception as exc:                                 # noqa: BLE001
        logger.warning('Não foi possível ler a entrevista %s: %s', token, exc)
        return None


def extrair_token(valor):
    """Aceita o token puro ou o link inteiro colado da barra do navegador."""
    valor = (valor or '').strip()
    if not valor:
        return ''
    if '/e/' in valor:
        valor = valor.split('/e/', 1)[1]
    return valor.strip('/ ').split('?')[0].split('#')[0][:80]
