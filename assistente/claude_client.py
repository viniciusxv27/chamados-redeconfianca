"""Cliente do Claude por usuário + laço de ferramentas (tool use).

A chave é do usuário (billing dele). O laço deixa o Claude pedir ferramentas
— que só leem dados do próprio usuário — até formar a resposta. Degrada com
elegância: sem chave, levanta ``NaoConectado`` e a tela mostra o cartão de
conexão em vez de quebrar.
"""
import logging

from . import ferramentas
from .models import AssistenteConexao, MODELOS_VALIDOS

logger = logging.getLogger(__name__)

MAX_RODADAS_FERRAMENTA = 6
MAX_TOKENS = 1500

SYSTEM = (
    "Você é o assistente do portal interno da Rede Confiança, falando com um "
    "colaborador específico. Responda em português do Brasil, de forma objetiva e cordial.\n"
    "Você só tem acesso aos dados DO PRÓPRIO usuário com quem está falando — perfil, "
    "chamados, ponto do dia, férias, folhas de ponto, contracheques, cursos, documentos, "
    "metas do Impulso e pastas do Drive dele. Use as ferramentas para buscar esses dados "
    "antes de responder quando a pergunta for sobre eles; nunca invente números, datas ou "
    "status. Se algo estiver fora do que você consegue ver, diga isso com clareza e oriente "
    "onde a pessoa encontra no portal."
)


class NaoConectado(Exception):
    """O usuário ainda não conectou uma chave do Claude."""


def conexao(user):
    obj, _ = AssistenteConexao.objects.get_or_create(user=user)
    return obj


def _erro_amigavel(exc):
    nome = type(exc).__name__
    if 'Authentication' in nome or 'PermissionDenied' in nome:
        return 'Chave inválida ou sem permissão. Confira a chave da Anthropic.'
    if 'RateLimit' in nome:
        return 'Limite de uso atingido na sua conta Anthropic. Tente mais tarde.'
    if 'NotFound' in nome:
        return 'Modelo indisponível para a sua conta. Escolha outro modelo.'
    return f'Falha ao falar com o Claude: {exc}'


def testar_chave(chave, modelo='claude-sonnet-5'):
    """(ok, mensagem) — valida uma chave com uma chamada mínima."""
    if not (chave or '').strip():
        return False, 'Informe a chave.'
    if modelo not in MODELOS_VALIDOS:
        modelo = 'claude-sonnet-5'
    try:
        from anthropic import Anthropic
        Anthropic(api_key=chave.strip()).messages.create(
            model=modelo, max_tokens=8, messages=[{'role': 'user', 'content': 'ping'}])
        return True, 'Conexão com o Claude OK.'
    except Exception as exc:  # noqa: BLE001
        return False, _erro_amigavel(exc)


def _cliente(user):
    conx = conexao(user)
    chave = conx.get_api_key()
    if not chave:
        raise NaoConectado('Conecte o seu Claude em Configurações para usar o assistente.')
    from anthropic import Anthropic
    modelo = conx.modelo if conx.modelo in MODELOS_VALIDOS else 'claude-sonnet-5'
    return Anthropic(api_key=chave), modelo


def responder(user, mensagem, historico=None):
    """Roda o laço de ferramentas e devolve o texto final da resposta."""
    client, modelo = _cliente(user)
    tools = ferramentas.tools_schema()

    messages = list(historico or [])
    messages.append({'role': 'user', 'content': mensagem})

    for _ in range(MAX_RODADAS_FERRAMENTA):
        try:
            resp = client.messages.create(
                model=modelo, max_tokens=MAX_TOKENS, system=SYSTEM, tools=tools, messages=messages)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(_erro_amigavel(exc))

        if resp.stop_reason == 'tool_use':
            # Guarda o turno do assistente (com os pedidos de ferramenta) e
            # responde cada pedido só com dados do próprio usuário.
            messages.append({'role': 'assistant', 'content': resp.content})
            resultados = []
            for bloco in resp.content:
                if getattr(bloco, 'type', None) == 'tool_use':
                    saida = ferramentas.executar(bloco.name, bloco.input, user)
                    resultados.append({'type': 'tool_result', 'tool_use_id': bloco.id, 'content': saida})
            messages.append({'role': 'user', 'content': resultados})
            continue

        # Sem mais ferramentas: junta o texto final.
        texto = ''.join(b.text for b in resp.content if getattr(b, 'type', None) == 'text')
        return texto.strip() or 'Não consegui formular uma resposta.'

    return ('Precisei de muitas consultas para responder e parei por segurança. '
            'Tente reformular a pergunta de forma mais específica.')
