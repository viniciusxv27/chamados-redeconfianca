"""Token de entrada na sala (JaaS / Jitsi com autenticação).

Por que existe: o servidor público `meet.jit.si` exige que a reunião seja
iniciada por alguém autenticado — sem isso todo mundo fica na tela "a
conferência ainda não começou porque não chegou nenhum moderador". Nenhuma
opção de configuração do lado do navegador contorna isso, porque a regra é do
servidor.

Com as credenciais do 8x8 (JaaS) preenchidas, o portal assina um token dizendo
quem é a pessoa — nome vindo do cadastro — e ela entra direto, sem tela
intermediária e sem poder trocar o nome, porque aí o nome vem no token e não do
navegador.

O JWT é montado à mão com `cryptography` (que já está no projeto) em vez de
puxar mais uma dependência para o deploy por causa de trinta linhas.
"""
import base64
import json
import logging
import time

logger = logging.getLogger(__name__)

VALIDADE_SEGUNDOS = 6 * 60 * 60          # 6h: reunião longa não pode expirar no meio


def _b64(dados):
    return base64.urlsafe_b64encode(dados).rstrip(b'=')


def _assinar(mensagem, pem):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    chave = serialization.load_pem_private_key(pem.encode('utf-8'), password=None)
    return chave.sign(mensagem, padding.PKCS1v15(), hashes.SHA256())


def gerar_token(cfg, user, sala, moderador=True):
    """Devolve o JWT, ou None quando a conta JaaS não está configurada.

    Nunca levanta exceção: sem token a sala ainda abre (no modo sem
    autenticação), e derrubar a tela da reunião por causa de uma credencial
    errada seria pior do que entrar sem token.
    """
    app_id = (cfg.jaas_app_id or '').strip()
    key_id = (cfg.jaas_api_key_id or '').strip()
    pem = (cfg.jaas_chave_privada or '').strip()
    if not (app_id and key_id and pem):
        return None

    agora = int(time.time())
    cabecalho = {'alg': 'RS256', 'typ': 'JWT', 'kid': key_id}
    corpo = {
        'aud': 'jitsi',
        'iss': 'chat',
        'sub': app_id,
        'room': '*',
        'exp': agora + VALIDADE_SEGUNDOS,
        'nbf': agora - 10,
        'context': {
            'user': {
                'id': str(user.id),
                'name': user.get_full_name() or user.email,
                'email': user.email or '',
                'moderator': 'true' if moderador else 'false',
            },
            'features': {
                'livestreaming': 'false',
                'outbound-call': 'false',
                'transcription': 'false',
                # A ata do portal grava pelo navegador; gravação no servidor
                # do 8x8 é cobrada à parte e não é o que usamos.
                'recording': 'false',
            },
        },
    }

    try:
        partes = _b64(json.dumps(cabecalho, separators=(',', ':')).encode()) + b'.' + \
                 _b64(json.dumps(corpo, separators=(',', ':')).encode())
        assinatura = _assinar(partes, pem)
        return (partes + b'.' + _b64(assinatura)).decode('ascii')
    except Exception as exc:                                    # noqa: BLE001
        logger.error('Token da sala não pôde ser assinado: %s', exc)
        return None


# O servidor público do Jitsi. Ele não conhece as chaves do 8x8, então mandar
# um token JaaS para cá devolve "você não está autorizado a entrar nesta
# chamada" — que é o mesmo erro de não ter token nenhum, e por isso confunde.
PUBLICO = 'meet.jit.si'
SERVIDOR_JAAS = '8x8.vc'


def servidor_para(cfg):
    """Onde a sala abre, considerando as credenciais.

    Com credenciais do 8x8 preenchidas, o destino tem de ser o servidor do 8x8:
    o token só vale lá. Um endereço próprio (Jitsi da empresa com JWT) é
    respeitado; o que não pode é continuar apontando para o servidor público,
    que rejeita o token e deixa todo mundo de fora.
    """
    escolhido = (cfg.servidor_jitsi or '').strip()
    tem_credenciais = bool((cfg.jaas_app_id or '').strip()
                           and (cfg.jaas_api_key_id or '').strip()
                           and (cfg.jaas_chave_privada or '').strip())
    if tem_credenciais and (not escolhido or escolhido == PUBLICO):
        return SERVIDOR_JAAS
    return escolhido or PUBLICO


def dados_da_sala(cfg, user, sala):
    """O que a tela precisa para abrir a sala: servidor, nome e token."""
    token = gerar_token(cfg, user, sala)
    servidor = servidor_para(cfg)
    if token:
        app_id = (cfg.jaas_app_id or '').strip()
        # No 8x8 a sala é sempre "AppID/nome". Num Jitsi próprio com JWT o nome
        # é só o nome — prefixar lá criaria uma sala com barra no meio.
        nome = f'{app_id}/{sala}' if servidor == SERVIDOR_JAAS else sala
        return {
            'servidor': servidor,
            'sala': nome,
            'token': token,
            'autenticado': True,
        }
    return {
        'servidor': servidor,
        'sala': sala,
        'token': '',
        'autenticado': False,
    }
