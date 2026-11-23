"""Push no celular para tudo o que cai no sino do portal.

O portal tem dois sinos (ver api_mobile.views, "Notificações"): o
notifications.UserNotification e o antigo core.Notification. Toda linha nova num
dos dois — criada por .create()/.save() (post_save) ou em lote (bulk_create, pelo
manager AvisaOCelularManager desses dois models) — vira push nos aparelhos em que a
pessoa entrou pelo app (PushMobile), pelo Expo Push Service.

Regras:
- Só sai depois do commit (transaction.on_commit): notificação desfeita não avisa.
- Nunca na requisição: uma fila e uma thread só por processo, em lotes de 100.
- Nunca derruba o portal: qualquer erro (Expo fora, tabela ainda sem migrate) vira log.
- Respeita NotificationPreference: push ou sino (in_app) desligado, horário
  silencioso e o tipo desligado (comunicado, chamado) não recebem.
- Aparelho que o Expo diz não existir mais (DeviceNotRegistered) é apagado.
- Script de teste (teste_*.py) não envia — a mesma trava do WhatsApp e do e-mail.
"""
import logging
import os
import queue
import re
import threading
from collections import defaultdict
from datetime import timedelta

from django.db import DatabaseError, close_old_connections, models, transaction
from django.db.models import Q
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

logger = logging.getLogger(__name__)

URL_EXPO = 'https://exp.host/--/api/v2/push/send'
LOTE = 100  # o Expo aceita até 100 mensagens por requisição
CANAL_ANDROID = 'default'  # o app cria este canal (mobile/src/services/push.ts)
FORMATO_TOKEN = re.compile(r'^Expo(nent)?PushToken\[[^\]\s]{10,}\]$')

# Os testes trocam para False: envia na hora, na mesma conexão (e na mesma transação).
EM_SEGUNDO_PLANO = True

# Tipo da notificação → campo de NotificationPreference (o que a pessoa pode desligar).
PREFERENCIA_DO_TIPO = {
    'c': {'COMMUNICATION': 'communication_new', 'TICKET': 'ticket_created',
          'TICKET_UPDATE': 'ticket_status_changed'},
    # No sino novo, chamado já chega filtrado pelo subtipo (notifications.services).
    'p': {'COMMUNICATION': 'communication_new'},
}


def token_valido(token):
    return bool(token) and len(token) <= 200 and bool(FORMATO_TOKEN.match(token))


# ── Ganchos: onde as notificações nascem ──────────────────────────────────────

class AvisaOCelularQuerySet(models.QuerySet):
    """bulk_create não dispara post_save: o aviso ao celular sai daqui."""

    def bulk_create(self, objs, *args, **kwargs):
        criados = super().bulk_create(objs, *args, **kwargs)
        avisar_criadas(criados)
        return criados


AvisaOCelularManager = models.Manager.from_queryset(AvisaOCelularQuerySet)


@receiver(post_save, sender='core.Notification', dispatch_uid='api_mobile_push_core')
@receiver(post_save, sender='notifications.UserNotification', dispatch_uid='api_mobile_push_sino')
def _ao_salvar(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        avisar_criadas([instance])


def _item(obj):
    """Notificação (de qualquer dos dois sinos) → o que o push precisa. Sem consulta extra."""
    label = obj._meta.label
    if label == 'core.Notification':
        return {'user_id': obj.user_id, 'titulo': obj.title, 'mensagem': obj.message,
                'link': obj.related_url or None, 'id': f'c-{obj.pk}' if obj.pk else None,
                'pref': PREFERENCIA_DO_TIPO['c'].get(obj.notification_type)}
    if label == 'notifications.UserNotification':
        n = obj.notification
        return {'user_id': obj.user_id, 'titulo': n.title, 'mensagem': n.message,
                'link': n.action_url or None, 'id': f'p-{obj.pk}' if obj.pk else None,
                'aviso_id': n.pk, 'pref': PREFERENCIA_DO_TIPO['p'].get(n.notification_type)}
    return None


def avisar_criadas(objs):
    """Põe na fila o push das notificações recém-criadas. Nunca levanta erro."""
    try:
        itens = []
        for obj in objs or []:
            try:
                item = _item(obj)
            except Exception:
                item = None
            if item and item['user_id']:
                itens.append(item)
        if itens:
            transaction.on_commit(lambda: _enfileirar(itens))
    except Exception:
        logger.exception('push mobile: não deu para preparar o aviso')


# ── Fila: uma thread por processo ─────────────────────────────────────────────

_fila = queue.Queue()
_trava = threading.Lock()
_trabalhador = {'thread': None, 'pid': None}


def _enfileirar(itens):
    if not EM_SEGUNDO_PLANO:
        _enviar_sem_levantar(itens, savepoint=True)
        return
    try:
        _fila.put(itens)
        with _trava:
            viva = _trabalhador['thread']
            # Depois de um fork (gunicorn) a thread do pai não existe no filho.
            if viva is None or not viva.is_alive() or _trabalhador['pid'] != os.getpid():
                viva = threading.Thread(target=_trabalhar, name='push-mobile', daemon=True)
                _trabalhador.update(thread=viva, pid=os.getpid())
                viva.start()
    except Exception:
        logger.exception('push mobile: não deu para pôr na fila')


def _trabalhar():
    while True:
        lotes = [_fila.get()]
        while True:
            try:
                lotes.append(_fila.get_nowait())
            except queue.Empty:
                break
        try:
            close_old_connections()
            _enviar_sem_levantar([i for lote in lotes for i in lote])
        finally:
            try:
                from django.db import connection
                connection.close()
            except Exception:
                pass
            for _ in lotes:
                _fila.task_done()


# ── Envio ─────────────────────────────────────────────────────────────────────

def _enviar_sem_levantar(itens, savepoint=False):
    try:
        if savepoint:  # na conexão da requisição: um erro de banco não pode estragar a transação dela
            with transaction.atomic():
                return enviar_agora(itens)
        return enviar_agora(itens)
    except DatabaseError as erro:
        # Típico antes do `migrate api_mobile` (tabela api_mobile_pushmobile ainda não existe).
        logger.warning('push mobile: banco recusou (%s)', str(erro).splitlines()[0][:200])
    except Exception:
        logger.exception('push mobile: falha no envio')
    return None


def _bloqueado_pela_preferencia(pref, campo_tipo):
    if pref is None:
        return False  # sem preferência gravada = aceita tudo (como notifications.services)
    if not pref.push_enabled or not pref.in_app_enabled:
        return True
    if campo_tipo and not pref.is_type_enabled(campo_tipo):
        return True
    return pref.is_quiet_hours()


def _script_de_teste():
    from core.utils import processo_de_teste
    return processo_de_teste()


def _texto(conteudo, limite):
    from .dados import texto_do_html
    return texto_do_html(conteudo or '', limite)


def enviar_agora(itens):
    """Monta as mensagens (aparelhos ativos × preferências) e manda ao Expo. Devolve um resumo."""
    from notifications.models import NotificationPreference

    from .autenticacao import DIAS_SEM_USO
    from .models import PushMobile

    resumo = {'mensagens': 0, 'enviadas': 0, 'falhas': 0, 'removidos': 0}
    ids = {i['user_id'] for i in itens}
    if not ids:
        return resumo
    limite = timezone.now() - timedelta(days=DIAS_SEM_USO)
    aparelhos = defaultdict(list)
    for user_id, token in (PushMobile.objects
                           .filter(sessao__user_id__in=ids, sessao__revogado_em__isnull=True,
                                   sessao__user__is_active=True)
                           .filter(Q(sessao__ultimo_uso__gte=limite) | Q(sessao__ultimo_uso__isnull=True))
                           .values_list('sessao__user_id', 'token_expo')):
        aparelhos[user_id].append(token)
    if not aparelhos:
        return resumo  # ninguém aqui entrou pelo app
    prefs = {p.user_id: p for p in NotificationPreference.objects.filter(user_id__in=list(aparelhos))}
    _completar_ids(itens, aparelhos)

    mensagens = []
    for item in itens:
        tokens = aparelhos.get(item['user_id'])
        if not tokens or _bloqueado_pela_preferencia(prefs.get(item['user_id']), item.get('pref')):
            continue
        titulo = _texto(item['titulo'], 120) or 'Rede Confiança'
        corpo = _texto(item['mensagem'], 240)
        dados = {'url': item.get('link'), 'id': item.get('id')}
        for token in tokens:
            mensagens.append({'to': token, 'title': titulo, 'body': corpo, 'data': dados,
                              'sound': 'default', 'priority': 'high', 'channelId': CANAL_ANDROID})
    resumo['mensagens'] = len(mensagens)
    if not mensagens:
        return resumo

    if _script_de_teste():
        logger.info('push mobile: script de teste, %s mensagens não enviadas', len(mensagens))
        return resumo

    invalidos = set()
    for inicio in range(0, len(mensagens), LOTE):
        lote = mensagens[inicio:inicio + LOTE]
        enviadas, falhas, mortos = _post_expo(lote)
        resumo['enviadas'] += enviadas
        resumo['falhas'] += falhas
        invalidos |= mortos
    if invalidos:
        resumo['removidos'], _ = PushMobile.objects.filter(token_expo__in=invalidos).delete()
    return resumo


def _completar_ids(itens, aparelhos):
    """bulk_create com ignore_conflicts (notifications.services) não devolve o pk: busca aqui,
    para o app marcar como lida ao tocar. Uma consulta só, e só de quem tem aparelho."""
    from notifications.models import UserNotification

    faltam = [i for i in itens if not i.get('id') and i.get('aviso_id') and i['user_id'] in aparelhos]
    if not faltam:
        return
    pks = {(a, u): pk for a, u, pk in UserNotification.objects.filter(
        notification_id__in={i['aviso_id'] for i in faltam}, user_id__in={i['user_id'] for i in faltam},
    ).values_list('notification_id', 'user_id', 'pk')}
    for i in faltam:
        pk = pks.get((i['aviso_id'], i['user_id']))
        if pk:
            i['id'] = f'p-{pk}'


def _post_expo(lote):
    """Um POST ao Expo. Devolve (enviadas, falhas, tokens que não existem mais)."""
    import requests
    from django.conf import settings

    cabecalhos = {'Accept': 'application/json', 'Accept-Encoding': 'gzip, deflate',
                  'Content-Type': 'application/json'}
    # Só se o "Enhanced security for push notifications" for ligado no painel do Expo.
    chave = getattr(settings, 'EXPO_ACCESS_TOKEN', '')
    if not chave:
        try:
            from decouple import config
            chave = config('EXPO_ACCESS_TOKEN', default='')
        except Exception:
            chave = ''
    if chave:
        cabecalhos['Authorization'] = f'Bearer {chave}'
    try:
        resposta = requests.post(URL_EXPO, json=lote, headers=cabecalhos, timeout=15)
        corpo = resposta.json()
    except Exception as erro:
        logger.warning('push mobile: Expo não respondeu (%s)', erro)
        return 0, len(lote), set()
    tickets = corpo.get('data') if isinstance(corpo, dict) else None
    if isinstance(tickets, dict):
        tickets = [tickets]
    if not isinstance(tickets, list):
        logger.warning('push mobile: resposta inesperada do Expo (%s): %s',
                       getattr(resposta, 'status_code', '?'), str(corpo)[:300])
        return 0, len(lote), set()
    enviadas = falhas = 0
    mortos = set()
    for mensagem, ticket in zip(lote, tickets):
        if isinstance(ticket, dict) and ticket.get('status') == 'ok':
            enviadas += 1
            continue
        falhas += 1
        ticket = ticket if isinstance(ticket, dict) else {}
        detalhes = ticket.get('details') or {}
        if detalhes.get('error') == 'DeviceNotRegistered':
            mortos.add(detalhes.get('expoPushToken') or mensagem['to'])
        else:
            logger.info('push mobile: Expo recusou (%s)', ticket.get('message'))
    return enviadas, falhas, mortos
