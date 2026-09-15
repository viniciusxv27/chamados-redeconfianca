"""Assistente: acesso completo à Agenda (/agenda/) e às Reuniões (/reunioes/).

Pedido: o assistente precisa ter acesso completo também a esses dois módulos.
Aqui se confere que ele lê o que a pessoa vê na tela, age pelas views do
próprio módulo (mesmas permissões, mesmos avisos) e que nenhuma ação roda sem a
confirmação do usuário numa mensagem seguinte.

Nada sai daqui: o Claude é um dublê (nenhuma chamada à Anthropic), o push do
navegador é dublê, o cache é de memória e o reprocessamento de transcrição não
dispara job. Roda dentro de uma transação desfeita no fim.
"""
import os
import sys
from datetime import datetime, time, timedelta
from types import SimpleNamespace
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core.cache.backends.locmem import LocMemCache
from django.db import transaction
from django.utils import timezone

import agenda.views as agenda_views
from agenda.models import CalendarEvent, EventParticipant, MeetingRequest, MeetingTranscription
from assistente import claude_client, ferramentas
from core.models import Notification, TaskActivity
from reunioes.models import ConfiguracaoReunioes, ParticipanteReuniao, Reuniao
from users.models import Sector

User = get_user_model()
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


AMANHA = timezone.localdate() + timedelta(days=1)
DIA = f'{AMANHA:%Y-%m-%d}'


def iso(h, m=0):
    return f'{DIA}T{h:02d}:{m:02d}'


def roda(nome, args, user, turno='turno-1'):
    return ferramentas.executar(nome, args, user, turno=turno)


def prepara_e_confirma(nome, args, user):
    """Como no chat: a ação é preparada numa pergunta e confirmada na seguinte."""
    previa = roda(nome, args, user, 'turno-1')
    return previa, roda('confirmar_acao', {'acao': nome}, user, 'turno-2')


marcador = transaction.atomic()
marcador.__enter__()
try:
    assert not User.objects.filter(username__startswith='zzassag.').exists(), 'usuários do teste já existem'
    push = mock.MagicMock(return_value={})
    with mock.patch.object(ferramentas, 'cache', LocMemCache('zz-assistente-agenda', {})), \
            mock.patch.object(agenda_views, 'send_push_notification_to_user', push), \
            mock.patch.object(agenda_views, '_start_transcription_background_job', return_value=True) as job, \
            mock.patch.object(agenda_views, '_prioritize_processing_transcriptions', return_value=0):

        setor = Sector.objects.create(name='ZZ Loja Assistente Agenda')

        def novo(apelido, setor_do_usuario=setor, **extra):
            u = User.objects.create_user(
                username=f'zzassag.{apelido}', email=f'zzassag.{apelido}@exemplo-teste.local',
                password='S3nha!teste', first_name='ZZAssAg', last_name=apelido.title(),
                sector=setor_do_usuario, **extra)
            if setor_do_usuario:
                u.sectors.add(setor_do_usuario)
            return u

        ana, bruno, carla = novo('ana'), novo('bruno'), novo('carla')
        chefe = novo('chefe', hierarchy='SUPERVISOR')
        estranho = novo('estranho', setor_do_usuario=None)

        print('== AS FERRAMENTAS ==')
        esquema = {x['name']: x for x in ferramentas.tools_schema()}
        novas = ['buscar_pessoas', 'agenda_eventos', 'agenda_evento', 'agenda_convites', 'agenda_disponibilidade',
                 'agenda_solicitacoes', 'agenda_transcricoes', 'agenda_transcricao', 'reunioes_lista',
                 'reunioes_detalhe', 'reunioes_opcoes_convite', 'confirmar_acao', 'descartar_acao']
        acoes = [n for n, d in ferramentas.TOOLS.items() if d.get('acao')]
        t('leituras de agenda e reuniões estão no assistente', all(n in esquema for n in novas),
          [n for n in novas if n not in esquema])
        t('as 18 ações também, cada uma com prévia', len(acoes) == 18
          and all(ferramentas.TOOLS[n].get('previa') and n in esquema for n in acoes), acoes)
        t('as ferramentas antigas continuam', all(n in esquema for n in ('meu_perfil', 'minhas_metas', 'resultados_comerciais')))
        t('todo esquema é válido (required dentro de properties)', all(
            s['input_schema']['type'] == 'object'
            and set(s['input_schema']['required']) <= set(s['input_schema']['properties']) for s in esquema.values()))
        t('o SYSTEM explica agenda, reuniões, confirmação e que conteúdo não é ordem',
          all(p in claude_client.SYSTEM for p in ('AGENDA', 'REUNIÕES', 'confirmar_acao', 'nunca instrução')))

        saida = roda('buscar_pessoas', {'busca': 'ZZAssAg bruno'}, ana)
        t('buscar_pessoas devolve o id do colega', f'id {bruno.pk}' in saida and 'Bruno' in saida, saida)

        print('\n== NENHUMA AÇÃO SEM CONFIRMAÇÃO ==')
        args_evento = {'titulo': 'ZZ Alinhamento', 'inicio': iso(10), 'fim': iso(11), 'tipo': 'chamada',
                       'participantes': [bruno.pk]}
        previa = roda('agenda_criar_evento', args_evento, ana, 'turno-1')
        t('a ferramenta só prepara', previa.startswith('AÇÃO PREPARADA')
          and not CalendarEvent.objects.filter(title='ZZ Alinhamento').exists(), previa)
        t('o resumo diz o que vai acontecer (convidado e sala)', 'Bruno' in previa and 'sala de vídeo' in previa, previa)
        t('na mesma pergunta a confirmação é recusada',
          roda('confirmar_acao', {'acao': 'agenda_criar_evento'}, ana, 'turno-1').startswith('Ainda não')
          and not CalendarEvent.objects.filter(title='ZZ Alinhamento').exists())
        t('confirmar outra ação não executa a preparada',
          'A ação preparada é agenda_criar_evento' in roda('confirmar_acao', {'acao': 'reunioes_cancelar'}, ana, 'turno-2'))
        feito = roda('confirmar_acao', {'acao': 'agenda_criar_evento'}, ana, 'turno-2')
        evento = CalendarEvent.objects.filter(owner=ana, title='ZZ Alinhamento').first()
        t('numa pergunta seguinte executa, pela view da agenda', evento is not None and feito.startswith('Evento criado'), feito)
        t('com o convite pendente do Bruno', EventParticipant.objects.filter(event=evento, user=bruno, status='pending').exists())
        t('e a sala de vídeo que a tela cria para chamada', Reuniao.objects.filter(evento=evento).exists() and '/sala/' in feito)
        t('o Bruno foi avisado como na tela (notificação e push)',
          Notification.objects.filter(user=bruno, title='Convite para evento').exists()
          and any(c.args and c.args[0] == bruno for c in push.call_args_list))
        t('confirmar de novo não cria outro', 'Não há ação' in roda('confirmar_acao', {'acao': 'agenda_criar_evento'}, ana, 'turno-3')
          and CalendarEvent.objects.filter(owner=ana, title='ZZ Alinhamento').count() == 1)
        invalido = roda('agenda_criar_evento', dict(args_evento, fim=iso(9)), ana)
        t('pedido inválido nem fica pendente', invalido.startswith('Não dá para fazer isso')
          and 'Não há ação' in roda('confirmar_acao', {'acao': 'agenda_criar_evento'}, ana, 'turno-2'), invalido)
        roda('agenda_excluir_evento', {'evento_id': evento.pk}, ana)
        t('descartar_acao desiste da ação preparada', 'descartada' in roda('descartar_acao', {}, ana, 'turno-2')
          and CalendarEvent.objects.filter(pk=evento.pk).exists())

        print('\n== A AGENDA COMO NA TELA ==')
        saida = roda('agenda_eventos', {'inicio': DIA, 'fim': DIA}, ana)
        t('a Ana vê o evento, com id e convite pendente', f'#{evento.pk}' in saida and '1 pendente(s)' in saida, saida)
        saida = roda('agenda_eventos', {'pessoa_id': ana.pk, 'inicio': DIA, 'fim': DIA}, bruno)
        t('colega sem hierarquia vê só "ocupado"', 'ocupado' in saida and 'ZZ Alinhamento' not in saida, saida)
        t('o supervisor do setor vê o detalhe',
          'ZZ Alinhamento' in roda('agenda_eventos', {'pessoa_id': ana.pk, 'inicio': DIA, 'fim': DIA}, chefe))
        convite = EventParticipant.objects.get(event=evento, user=bruno)
        t('o Bruno vê o convite com o id', f'convite #{convite.pk}' in roda('agenda_convites', {}, bruno))
        t('quem não foi convidado nem tem hierarquia não abre o evento',
          'permissão' in roda('agenda_evento', {'evento_id': evento.pk}, carla))
        saida = roda('agenda_disponibilidade', {'pessoa_id': ana.pk, 'data': DIA}, bruno)
        t('disponibilidade mostra o buraco das 10h às 11h', '08:00–10:00' in saida and '11:00–18:00' in saida, saida)

        _, feito = prepara_e_confirma('agenda_responder_convite', {'convite_id': convite.pk, 'resposta': 'aceitar'}, bruno)
        convite.refresh_from_db()
        t('o Bruno aceita o convite', convite.status == 'accepted' and evento.participants.filter(pk=bruno.pk).exists(), feito)
        t('e a Ana é avisada', Notification.objects.filter(user=ana, title='Convite aceito').exists())
        t('convidado não remarca evento de outra pessoa',
          'Só quem criou' in roda('agenda_editar_evento', {'evento_id': evento.pk, 'inicio': iso(15)}, bruno))
        _, feito = prepara_e_confirma('agenda_editar_evento', {'evento_id': evento.pk, 'inicio': iso(15)}, ana)
        evento.refresh_from_db()
        t('a dona remarca e a duração se mantém', timezone.localtime(evento.start).hour == 15
          and evento.end - evento.start == timedelta(hours=1), feito)
        t('quem aceitou recebe o aviso de remarcação', Notification.objects.filter(user=bruno, title='Evento remarcado').exists())

        _, feito = prepara_e_confirma('agenda_solicitar_reuniao', {'pessoa_id': carla.pk, 'titulo': 'ZZ Pedido',
                                                                   'inicio': iso(14), 'fim': iso(14, 30)}, ana)
        pedido = MeetingRequest.objects.filter(requester=ana, target=carla, title='ZZ Pedido').first()
        t('pede reunião a um colega', pedido is not None and pedido.status == 'pending', feito)
        t('a Carla vê a solicitação', pedido is not None
          and f'solicitação #{pedido.pk}' in roda('agenda_solicitacoes', {'tipo': 'recebidas'}, carla))
        t('quem pediu não aceita o próprio pedido',
          'Só quem recebeu' in roda('agenda_responder_solicitacao', {'solicitacao_id': pedido.pk, 'acao': 'aceitar'}, ana))
        _, feito = prepara_e_confirma('agenda_responder_solicitacao', {'solicitacao_id': pedido.pk, 'acao': 'aceitar'}, carla)
        pedido.refresh_from_db()
        t('a Carla aceita e o evento entra nas duas agendas', pedido.status == 'accepted'
          and CalendarEvent.objects.filter(owner=carla, title='ZZ Pedido').exists()
          and CalendarEvent.objects.filter(owner=ana, title='ZZ Pedido').exists(), feito)

        print('\n== TRANSCRIÇÕES E ATAS ==')
        ata = MeetingTranscription.objects.create(
            owner=ana, title='ZZ Ata do alinhamento', status='completed', summary='Resumo ZZ da reunião.',
            key_decisions=[{'decision': 'ZZ decidir X', 'context': 'c', 'impact': 'i'}],
            action_items=[{'task': 'ZZ fazer Y', 'responsible': 'Bruno', 'priority': 'high'}],
            formatted_transcription='Fala ZZ. ' * 3000)
        tarefa = TaskActivity.objects.create(title='ZZ fazer Y', description='da ata', assigned_to=ana, created_by=ana)
        ata.tasks_created.add(tarefa)
        t('quem não recebeu a ata não a vê', 'não foi compartilhada' in roda('agenda_transcricao', {'transcricao_id': ata.pk}, bruno))
        saida = roda('agenda_transcricao', {'transcricao_id': ata.pk}, ana)
        t('a dona lê resumo, decisões, ações e tarefas com id',
          all(x in saida for x in ('Resumo ZZ', 'ZZ decidir X', 'ZZ fazer Y', f'tarefa #{tarefa.pk}')), saida[:600])
        t('o texto só vem se pedido, em trechos', 'TEXTO (' not in saida
          and 'a_partir_de=15000' in roda('agenda_transcricao', {'transcricao_id': ata.pk, 'incluir_texto': True}, ana))
        _, feito = prepara_e_confirma('agenda_compartilhar_transcricao', {'transcricao_id': ata.pk, 'adicionar': [bruno.pk]}, ana)
        t('compartilha a ata com o Bruno', ata.shared_with.filter(pk=bruno.pk).exists(), feito)
        t('que lê, mas não recompartilha', 'Resumo ZZ' in roda('agenda_transcricao', {'transcricao_id': ata.pk}, bruno)
          and 'Só quem gravou' in roda('agenda_compartilhar_transcricao', {'transcricao_id': ata.pk, 'adicionar': [carla.pk]}, bruno))
        _, feito = prepara_e_confirma('agenda_atribuir_tarefa', {'transcricao_id': ata.pk, 'tarefa_id': tarefa.pk,
                                                                 'pessoa_id': bruno.pk}, ana)
        tarefa.refresh_from_db()
        t('passa a tarefa da ata para o Bruno', tarefa.assigned_to_id == bruno.pk, feito)
        _, feito = prepara_e_confirma('agenda_reprocessar_transcricao', {'transcricao_id': ata.pk}, ana)
        ata.refresh_from_db()
        t('reprocessa pela view (o job é dublê)', ata.status == 'processing' and job.called, feito)
        gravando = MeetingTranscription.objects.create(owner=ana, title='ZZ Gravação perdida', status='recording')
        _, feito = prepara_e_confirma('agenda_descartar_gravacao', {'transcricao_id': gravando.pk}, ana)
        t('descarta gravação sem texto', not MeetingTranscription.objects.filter(pk=gravando.pk).exists(), feito)

        print('\n== REUNIÕES ==')
        previa = roda('reunioes_criar', {'titulo': 'ZZ Reunião de loja', 'inicio': iso(16), 'fim': iso(17),
                                         'pessoas': [bruno.pk], 'setores': [setor.pk]}, ana, 'turno-1')
        t('a prévia conta quem o setor convida', 'Convidados (3)' in previa and 'setor ZZ Loja Assistente Agenda' in previa, previa)
        feito = roda('confirmar_acao', {'acao': 'reunioes_criar'}, ana, 'turno-2')
        reuniao = Reuniao.objects.filter(organizador=ana, titulo='ZZ Reunião de loja').first()
        t('cria a reunião pela view do módulo', reuniao is not None and feito.startswith('Reunião criada'), feito)
        convidados = set(reuniao.participantes.values_list('user_id', flat=True)) if reuniao else set()
        t('com os convidados e o motivo de cada um', convidados == {bruno.pk, carla.pk, chefe.pk}
          and reuniao.participantes.get(user=carla).origem == ParticipanteReuniao.SETOR, convidados)
        t('na agenda de todos, com aviso', bool(reuniao and reuniao.evento_id)
          and Notification.objects.filter(user=carla, title='Convite para reunião').exists())
        t('a Carla vê a reunião na lista dela', f'#{reuniao.pk}' in roda('reunioes_lista', {}, carla))
        t('quem não está na reunião não vê o detalhe', 'não pode vê-la' in roda('reunioes_detalhe', {'reuniao_id': reuniao.pk}, estranho))
        t('convidado não cancela', 'Só quem organizou' in roda('reunioes_cancelar', {'reuniao_id': reuniao.pk}, bruno))
        _, feito = prepara_e_confirma('reunioes_editar', {'reuniao_id': reuniao.pk, 'remover_pessoas': [chefe.pk],
                                                          'adicionar_pessoas': [estranho.pk]}, ana)
        convidados = set(reuniao.participantes.values_list('user_id', flat=True))
        t('edita os convidados sem perder quem já estava', convidados == {bruno.pk, carla.pk, estranho.pk}, (convidados, feito))
        cfg = ConfiguracaoReunioes.get()
        cfg.permitir_link_publico = True
        cfg.save()
        _, feito = prepara_e_confirma('reunioes_link_publico', {'reuniao_id': reuniao.pk, 'acao': 'abrir'}, ana)
        reuniao.refresh_from_db()
        t('abre o link de visitante', bool(reuniao.token_publico) and reuniao.token_publico in feito, feito)
        _, feito = prepara_e_confirma('reunioes_registrar_ata', {'reuniao_id': reuniao.pk, 'transcricao_id': ata.pk}, ana)
        reuniao.refresh_from_db()
        t('registra a ata: todos passam a ver e a reunião encerra', reuniao.status == Reuniao.ENCERRADA
          and ata.shared_with.filter(pk=carla.pk).exists(), feito)
        t('encerrar de novo é recusado já na prévia', 'já está encerrada' in roda('reunioes_encerrar', {'reuniao_id': reuniao.pk}, ana))
        outra = Reuniao.objects.create(titulo='ZZ Outra', inicio=timezone.make_aware(datetime.combine(AMANHA, time(18))),
                                       organizador=ana)
        ParticipanteReuniao.objects.create(reuniao=outra, user=bruno)
        _, feito = prepara_e_confirma('reunioes_cancelar', {'reuniao_id': outra.pk}, ana)
        outra.refresh_from_db()
        t('cancela e avisa os convidados', outra.status == Reuniao.CANCELADA
          and Notification.objects.filter(user=bruno, title='Reunião cancelada').exists(), feito)
        _, feito = prepara_e_confirma('agenda_excluir_evento', {'evento_id': evento.pk}, ana)
        t('exclui o evento da agenda', not CalendarEvent.objects.filter(pk=evento.pk).exists(), feito)

        print('\n== O LAÇO DO CLAUDE (DUBLÊ, SEM REDE) ==')
        pedidos, roteiro = [], []

        class Mensagens:
            def create(self, **kwargs):
                pedidos.append(kwargs)
                return roteiro.pop(0)

        def resposta(*blocos, parar='end_turn'):
            return SimpleNamespace(stop_reason=parar, content=list(blocos))

        def uso(nome, entrada, ident):
            return SimpleNamespace(type='tool_use', name=nome, input=entrada, id=ident)

        def texto(valor):
            return SimpleNamespace(type='text', text=valor)

        cliente = SimpleNamespace(messages=Mensagens())
        pelo_chat = {'titulo': 'ZZ Pelo chat', 'inicio': iso(9), 'fim': iso(9, 30)}
        with mock.patch.object(claude_client, '_cliente', return_value=(cliente, 'claude-sonnet-5')):
            roteiro[:] = [resposta(uso('agenda_criar_evento', pelo_chat, 'u1'), parar='tool_use'),
                          resposta(uso('confirmar_acao', {'acao': 'agenda_criar_evento'}, 'u2'), parar='tool_use'),
                          resposta(texto('Posso criar?'))]
            r1 = claude_client.responder(ana, 'marca ZZ Pelo chat amanhã 9h', [])
            resultados = [b['content'] for m in pedidos[-1]['messages'] if isinstance(m['content'], list)
                          for b in m['content'] if isinstance(b, dict) and b.get('type') == 'tool_result']
            t('na mesma pergunta o Claude não confirma sozinho', r1 == 'Posso criar?'
              and not CalendarEvent.objects.filter(title='ZZ Pelo chat').exists()
              and any(r.startswith('Ainda não') for r in resultados), resultados)
            primeiro = pedidos[0]
            t('o SYSTEM vai com cache de prompt', primeiro['system'][0].get('cache_control') == {'type': 'ephemeral'})
            # A lista de mensagens é a mesma que o laço vai completando: procura a pergunta nela.
            blocos_da_pergunta = [b for m in primeiro['messages'] if m['role'] == 'user' and isinstance(m['content'], list)
                                  for b in m['content'] if isinstance(b, dict) and b.get('type') == 'text']
            t('a pergunta leva a data e a hora atuais', any(b['text'] == 'marca ZZ Pelo chat amanhã 9h' for b in blocos_da_pergunta)
              and any(b['text'].startswith('[Agora:') for b in blocos_da_pergunta), blocos_da_pergunta)
            t('e as ferramentas novas', any(x['name'] == 'reunioes_criar' for x in primeiro['tools']))
            roteiro[:] = [resposta(uso('confirmar_acao', {'acao': 'agenda_criar_evento'}, 'u3'), parar='tool_use'),
                          resposta(texto('Feito!'))]
            r2 = claude_client.responder(ana, 'sim', [{'role': 'user', 'content': 'marca ZZ Pelo chat amanhã 9h'},
                                                      {'role': 'assistant', 'content': 'Posso criar?'}])
            t('com o "sim" numa nova mensagem, o evento é criado', r2 == 'Feito!'
              and CalendarEvent.objects.filter(owner=ana, title='ZZ Pelo chat').exists())

        print('\n== A TELA DO CHAT ==')
        from django.test import Client
        navegador = Client()
        navegador.force_login(ana)
        r = navegador.get('/assistente/')
        html = r.content.decode()
        t('abre (200) e sugere agenda e reuniões', r.status_code == 200 and 'Minhas reuniões desta semana' in html
          and 'Resuma a última ata de reunião' in html and 'cuidar da sua agenda' in html, r.status_code)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
