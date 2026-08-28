"""Ponte entre a agenda e o módulo de reuniões.

Quando alguém marca uma **Chamada** na agenda, o esperado é que o portal já
resolva a sala — não que a pessoa vá a outro lugar criar a reunião e volte para
colar o link. Aqui a agenda pede a sala e recebe o endereço pronto.
"""
import logging

logger = logging.getLogger(__name__)

# Tipo de evento da agenda que ganha sala de vídeo automaticamente.
TIPOS_COM_SALA = ('call',)


def precisa_de_sala(evento):
    return getattr(evento, 'event_type', '') in TIPOS_COM_SALA


def sala_para_evento(evento, convidados=(), autor=None):
    """Garante a reunião do evento e devolve o caminho da sala.

    Idempotente: chamar de novo no mesmo evento não cria uma segunda sala, só
    atualiza tema, pauta e horários — evento remarcado não pode virar duas
    salas com o mesmo nome.

    Devolve o caminho (``/reunioes/12/sala/``) ou None se algo falhar. Falhar
    aqui não pode derrubar a criação do evento: a agenda funciona sem sala, e
    perder o compromisso por causa do vídeo seria o pior dos dois mundos.
    """
    from .models import ParticipanteReuniao, Reuniao

    try:
        reuniao = Reuniao.objects.filter(evento=evento).first()
        dados = {
            'titulo': (evento.title or 'Chamada')[:200],
            'pauta': evento.description or '',
            'inicio': evento.start,
            'fim': evento.end,
        }
        if reuniao is None:
            reuniao = Reuniao.objects.create(
                organizador=evento.owner, evento=evento, **dados)
        else:
            for campo, valor in dados.items():
                setattr(reuniao, campo, valor)
            reuniao.save(update_fields=[*dados.keys(), 'atualizado_em'])

        atuais = set(reuniao.participantes.values_list('user_id', flat=True))
        for u in convidados:
            if u.id not in atuais and u.id != reuniao.organizador_id:
                ParticipanteReuniao.objects.get_or_create(
                    reuniao=reuniao, user=u,
                    defaults={'origem': ParticipanteReuniao.MANUAL,
                              'rotulo_origem': 'convidado pela agenda'})

        caminho = f'/reunioes/{reuniao.id}/sala/'
        if evento.link != caminho:
            evento.link = caminho
            evento.save(update_fields=['link'])
        return caminho
    except Exception as exc:                                    # noqa: BLE001
        logger.error('Sala do evento %s não pôde ser criada: %s',
                     getattr(evento, 'id', '?'), exc)
        return None
