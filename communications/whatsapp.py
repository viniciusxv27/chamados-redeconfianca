"""Envio de WhatsApp (Z-API) para comunicados — fire-and-forget em thread.

Reaproveita core.zapi.send_whatsapp_message (mesmo padrão de documentos/users).
O envio roda em uma thread daemon para não travar a publicação do comunicado
(quando é send_to_all, a audiência pode ser grande).
"""
import logging
import threading

logger = logging.getLogger(__name__)


def _mensagem(nome, titulo, link):
    return (
        f"Olá, {nome}! 👋\n\n"
        f"Você recebeu um novo comunicado no portal: *{titulo}*.\n\n"
        f"Acesse diretamente por aqui:\n{link}\n\n"
        f"Após ler, registre o seu \"de acordo\" (Estou Ciente) para confirmar. ✅"
    )


def build_payloads(titulo, usuarios, link):
    """Monta [(phone, mensagem), ...] para usuários com telefone. Função pura (testável)."""
    payloads = []
    for u in usuarios:
        phone = (getattr(u, 'phone', '') or '').strip()
        if not phone:
            continue
        nome = getattr(u, 'first_name', '') or getattr(u, 'username', '') or 'colaborador'
        payloads.append((phone, _mensagem(nome, titulo, link)))
    return payloads


def enviar_whatsapp_comunicado(titulo, usuarios, link):
    """Dispara (em background) o WhatsApp com o link do comunicado. Nunca levanta.

    Retorna a quantidade de mensagens enfileiradas (usuários com telefone).
    """
    try:
        payloads = build_payloads(titulo, list(usuarios), link)
    except Exception:
        logger.exception('Erro montando payloads de WhatsApp do comunicado')
        return 0
    if not payloads:
        return 0

    def _run():
        from core.zapi import send_whatsapp_message
        for phone, message in payloads:
            try:
                send_whatsapp_message(phone, message)
            except Exception:
                logger.exception('Falha ao enviar WhatsApp de comunicado para %s', phone)

    threading.Thread(target=_run, daemon=True).start()
    return len(payloads)
