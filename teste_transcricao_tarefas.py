"""Agenda › transcrição: etapa dedicada de tarefas (16/11/2026).

Pedaços com sobreposição, deduplicação de quase-iguais, mais tarefas que a
análise geral, fallback quando a etapa falha, descrição da TaskActivity e
reprocessar sem duplicar. IA SIMULADA (nada sai); banco SQLite descartável.
"""
import os
import pathlib
import sys
import tempfile
import threading
from types import SimpleNamespace
from unittest import mock

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-transc-tarefas-'))
(PASTA / 'zz_transc_tarefas_settings.py').write_text(f"""
from redeconfianca.settings import *              # noqa: F401,F403

DATABASES = {{'default': {{'ENGINE': 'django.db.backends.sqlite3',
                          'NAME': r'{PASTA}/banco.sqlite3'}}}}


class SemMigrations:
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


MIGRATION_MODULES = SemMigrations()

USE_S3 = False
MEDIA_ROOT = r'{PASTA}/media'
MEDIA_URL = '/media/'
STORAGES = {{
    'default': {{'BACKEND': 'django.core.files.storage.FileSystemStorage'}},
    'staticfiles': {{'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
}}
DEBUG = False
OPENAI_API_KEY = ''
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']
CACHES = {{
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-transc-tf'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-transc-tf-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_transc_tarefas_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.core.management import call_command  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import override_settings, setup_test_environment  # noqa: E402

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import json  # noqa: E402
import re  # noqa: E402

from django.contrib.auth import get_user_model  # noqa: E402

from agenda import tarefas as tf  # noqa: E402
from agenda import views as av  # noqa: E402
from agenda.models import MeetingTranscription  # noqa: E402
from core.models import TaskActivity  # noqa: E402

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


class IAFalsa:
    """Imita client.chat.completions.create; `responder(trecho, total, mensagens)` devolve o texto."""

    def __init__(self, responder):
        self.responder = responder
        self.chamadas = []
        self.trava = threading.Lock()
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kw):
        usuario = kw['messages'][-1]['content']
        achado = re.search(r'Trecho (\d+) de (\d+)', usuario)
        trecho, total = (int(achado.group(1)), int(achado.group(2))) if achado else (1, 1)
        with self.trava:
            self.chamadas.append((trecho, kw))
        conteudo = self.responder(trecho, total, kw['messages'])
        if isinstance(conteudo, Exception):
            raise conteudo
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=conteudo),
                                                        finish_reason='stop')])


def js(*tarefas):
    return json.dumps({'tasks': list(tarefas)}, ensure_ascii=False)


def item(task, **kw):
    base = {'task': task, 'description': f'Contexto de {task}.', 'responsible': 'A definir', 'deadline': None,
            'priority': 'medium', 'success_criteria': 'Feito.', 'topic': 'Pauta', 'evidence': ''}
    base.update(kw)
    return base


TEXTO_LONGO = ' '.join(f'Frase {n} da reunião, o pessoal comentou um ponto qualquer.' for n in range(3300))

print('== PEDAÇOS ==')
p30 = tf.pedacos_para_tarefas(TEXTO_LONGO[:30600])
p180 = tf.pedacos_para_tarefas(TEXTO_LONGO[:182000])
t('30 mil caracteres → 2 pedaços', len(p30) == 2, len(p30))
t('180 mil caracteres → 10 pedaços', len(p180) == 10, len(p180))
t('texto curto → 1 pedaço só', len(tf.pedacos_para_tarefas('Fulano vai mandar a planilha.')) == 1)
t('texto vazio → nenhum pedaço (nenhuma chamada)', tf.pedacos_para_tarefas('  ') == [])
t('nenhum pedaço passa de ~20 mil', max(len(p) for p in p180) <= 20000 * 1.15)
emendas = all(p180[i][-200:].split('. ')[-2] in p180[i + 1] for i in range(len(p180) - 1))
t('pedaços vizinhos se sobrepõem (a frase da emenda está nos dois)', emendas)
t('corte em fim de frase, não no meio da palavra',
  all(p.endswith('.') for p in p180[:-1]) and all(p.startswith('Frase') for p in p180))
frases = re.findall(r'Frase \d+ ', TEXTO_LONGO[:182000])
t('a transcrição inteira é coberta', all(any(f in p for p in p180) for f in frases[::97]))
sem_quebra = 'palavra ' * 6000
t('sem pontuação nenhuma ainda corta em espaço',
  all(not p.startswith('alavra') for p in tf.pedacos_para_tarefas(sem_quebra)))

print('\n== DEDUPLICAÇÃO ==')
N = tf.normalizar_item
t('quase-iguais (acento/pontuação/plural) viram uma',
  tf.mesma_tarefa(N('Enviar relatório de vendas de outubro ao João'), N('Enviar relatorios de venda de outubro ao João.')))
t('fornecedor Alfa ≠ fornecedor Beta',
  not tf.mesma_tarefa(N('Ligar para o fornecedor Alfa'), N('Ligar para o fornecedor Beta')))
t('loja L220 ≠ loja L222', not tf.mesma_tarefa(N('Conferir estoque da loja L220'), N('Conferir estoque da loja L222')))
mesma_fala = 'eu fico de mandar a planilha de metas pro Bruno até amanhã'
a = N({'task': 'Enviar planilha de metas ao Bruno', 'evidence': mesma_fala, 'description': 'Curta.'})
b = N({'task': 'Mandar a planilha de metas para o Bruno', 'evidence': mesma_fala, 'responsible': 'Carlos',
       'deadline': '2026-11-17', 'priority': 'high', 'description': 'Planilha de metas de dezembro com 12 lojas.'})
t('mesma fala com texto parecido (pedaços vizinhos) é a mesma tarefa', tf.mesma_tarefa(a, b))
c = N({'task': 'Ligar para o fornecedor Alfa', 'evidence': 'vou ligar pro fornecedor Alfa e pro Beta hoje'})
d = N({'task': 'Ligar para o fornecedor Beta', 'evidence': 'vou ligar pro fornecedor Alfa e pro Beta hoje'})
t('uma fala com duas ações continua em duas tarefas', not tf.mesma_tarefa(c, d))
m = tf.deduplicar_tarefas([a, b, c, d])
t('dedup: 4 → 3', len(m) == 3, [x['task'] for x in m])
t('mescla guarda responsável e prazo citados, prioridade maior e as duas descrições',
  m[0]['responsible'] == 'Carlos' and m[0]['deadline'] == '2026-11-17' and m[0]['priority'] == 'high'
  and 'Curta.' in m[0]['description'] and '12 lojas' in m[0]['description'], m[0])
t('normaliza prioridade em português, prazo inválido e responsável vazio',
  N({'task': 'Fazer X', 'priority': 'Alta', 'deadline': 'sexta', 'responsible': 'N/A'})
  == {**N({'task': 'Fazer X'}), 'priority': 'high'})
t('item só com texto (formato antigo) também vale', N('Revisar contrato')['task'] == 'Revisar contrato')
t('lixo não vira tarefa', N({'task': ''}) is None and N(None) is None and N({'x': 1}) is None)
ordem = tf.organizar_tarefas([N(item('Arquivar as notas antigas')) | {'priority': 'low'},
                              N(item('Revisar o contrato do aluguel', priority='high')),
                              N(item('Pagar o boleto da energia', priority='high', deadline='2026-11-20'))])
t('ordena por prioridade e depois prazo',
  [x['task'] for x in ordem] == ['Pagar o boleto da energia', 'Revisar o contrato do aluguel',
                                 'Arquivar as notas antigas'], [x['task'] for x in ordem])
t('"com prazo" ≠ "sem prazo" (negação não é palavra vazia)',
  not tf.mesma_tarefa(N('Entregar o pedido com nota'), N('Entregar o pedido sem nota')))
muitas = [N(item(f'Conferir a loja L{n:03d}')) for n in range(75)]
with mock.patch.object(tf.logger, 'info') as log:
    cortadas = tf.organizar_tarefas(muitas, rotulo='ZZ')
t('teto de 60, com log do que ficou de fora', len(cortadas) == 60 and log.called
  and '15' in str(log.call_args) and 'L074' in str(log.call_args))

print('\n== JSON DA IA ==')
t('lê {"tasks": [...]}', len(tf._ler_json_tarefas(js(item('Fazer A'), item('Fazer B')))) == 2)
t('lê com ```json em volta', len(tf._ler_json_tarefas('```json\n' + js(item('Fazer A')) + '\n```')) == 1)
cortada = js(item('Fazer A'), item('Fazer B'), item('Fazer C'))[:-60]
t('resposta cortada no limite: aproveita as tarefas completas',
  [x['task'] for x in tf._ler_json_tarefas(cortada)] == ['Fazer A', 'Fazer B'])
try:
    tf._ler_json_tarefas('não sei')
    t('texto sem JSON é erro', False)
except ValueError:
    t('texto sem JSON é erro', True)

print('\n== EXTRAÇÃO (IA simulada) ==')


def resp_por_trecho(trecho, total, _msgs):
    tarefas = [item(f'Verificar o item {trecho}{k} citado no trecho', responsible=f'Pessoa {trecho}')
               for k in range(1, 6)]
    if trecho > 1:   # a emenda: o mesmo compromisso visto pelo pedaço anterior, escrito quase igual
        tarefas.append(item(f'Verificar o item {trecho - 1}5 citado no trecho.'))
    return js(*tarefas)


ia = IAFalsa(resp_por_trecho)
itens, pedacos, falhas = tf.extrair_tarefas(ia, 'ZZ Reunião', TEXTO_LONGO[:30600], tf.date(2026, 11, 11))
t('30 mil: 2 chamadas', len(ia.chamadas) == 2 and pedacos == 2 and falhas == 0, len(ia.chamadas))
prompt = ia.chamadas[0][1]['messages'][0]['content']
t('prompt focado em compromissos, com a data da reunião e evidence',
  'compromissos' in prompt and '2026-11-11' in prompt and '"evidence"' in prompt and 'infinitivo' in prompt)
t('mesmo modelo da análise geral (gpt-4o, JSON)',
  ia.chamadas[0][1]['model'] == 'gpt-4o' and ia.chamadas[0][1]['response_format'] == {'type': 'json_object'})
ia180 = IAFalsa(resp_por_trecho)
itens180, pedacos180, _f = tf.extrair_tarefas(ia180, 'ZZ', TEXTO_LONGO[:182000])
t('180 mil: 10 chamadas', len(ia180.chamadas) == 10 and pedacos180 == 10, len(ia180.chamadas))
t('resultado na ordem do texto mesmo em paralelo',
  [x['responsible'] for x in itens180[::6]] == [f'Pessoa {n}' for n in range(1, 11)],
  [x['responsible'] for x in itens180[::6]])

ANALISE_ACOES = [{'task': 'Acompanhar os pontos da reunião', 'responsible': 'A definir', 'priority': 'medium'},
                 {'task': 'Definir próximos passos', 'priority': 'low'}]


def extracao(ia_, texto=TEXTO_LONGO[:30600]):
    return tf.ExtracaoEmParalelo(ia_, 'ZZ Reunião', texto, tf.date(2026, 11, 11))


final = tf.tarefas_da_reuniao(ANALISE_ACOES, extracao(IAFalsa(resp_por_trecho)), rotulo='ZZ')
t('mais tarefas que a análise geral (2 → 10) e a da emenda não duplica', len(final) == 10, len(final))
t('todas da etapa dedicada, com chaves antigas e novas',
  all(x['origem'] == 'extracao' for x in final)
  and all({'task', 'responsible', 'deadline', 'priority', 'success_criteria',
           'description', 'topic', 'evidence'} <= set(x) for x in final))

print('\n== FALLBACK ==')
fora = tf.tarefas_da_reuniao(ANALISE_ACOES, extracao(IAFalsa(lambda *a: RuntimeError('OpenAI fora'))))
t('IA fora → ficam as da análise geral', [x['task'] for x in fora] == [a_['task'] for a_ in ANALISE_ACOES]
  and all(x['origem'] == 'analise' for x in fora), fora)
lixo = tf.tarefas_da_reuniao(ANALISE_ACOES, extracao(IAFalsa(lambda *a: 'isto não é JSON')))
t('JSON inválido → ficam as da análise geral', len(lixo) == 2)
vazia = tf.tarefas_da_reuniao(ANALISE_ACOES, extracao(IAFalsa(lambda *a: js())))
t('etapa não achou nada → ficam as da análise geral', len(vazia) == 2)
t('etapa desligada (client None) → análise geral', len(tf.tarefas_da_reuniao(ANALISE_ACOES, extracao(None))) == 2)
with override_settings(AGENDA_EXTRACAO_TAREFAS=False):
    ia_off = IAFalsa(resp_por_trecho)
    off = tf.tarefas_da_reuniao(ANALISE_ACOES, extracao(ia_off))
t('AGENDA_EXTRACAO_TAREFAS=False → nenhuma chamada a mais', len(off) == 2 and not ia_off.chamadas)


def metade_falha(trecho, total, msgs):
    return RuntimeError('caiu') if trecho == 2 else resp_por_trecho(trecho, total, msgs)


parcial = tf.tarefas_da_reuniao(ANALISE_ACOES, extracao(IAFalsa(metade_falha)))
t('um pedaço falhou → o que veio + as da análise completam', len(parcial) == 5 + 2, len(parcial))
tentativas = []


def falha_uma_vez(trecho, total, msgs):
    tentativas.append(trecho)
    return RuntimeError('soluço') if tentativas.count(trecho) == 1 else resp_por_trecho(trecho, total, msgs)


t('pedaço que falha uma vez é tentado de novo',
  len(tf.tarefas_da_reuniao([], extracao(IAFalsa(falha_uma_vez)))) == 10 and len(tentativas) == 4)
liberar = threading.Event()


def lenta(trecho, total, msgs):
    liberar.wait(5)
    return resp_por_trecho(trecho, total, msgs)


ia_lenta = IAFalsa(lenta)
ext = tf.ExtracaoEmParalelo(ia_lenta, 'ZZ', TEXTO_LONGO[:182000])
ext.abandonar()
liberar.set()
try:
    ext._futuro.result(timeout=10)
except Exception:                                               # noqa: BLE001
    pass
t('análise falhou → pedaços que não começaram não gastam chamada', len(ia_lenta.chamadas) <= 4, len(ia_lenta.chamadas))

print('\n== NO PIPELINE: TAREFAS CRIADAS ==')
dono = User.objects.create_user(username='zz.tf.dono', email='zz.tf.dono@exemplo-teste.local',
                                password='S3nha!teste', first_name='Dora', last_name='Dona')
outro = User.objects.create_user(username='zz.tf.outro', email='zz.tf.outro@exemplo-teste.local',
                                 password='S3nha!teste', first_name='Otto', last_name='Outro')
ANALISE = {'formatted_transcription': 'Texto ZZ', 'summary': 'Resumo ZZ', 'sections': [], 'key_decisions': [],
           'action_items': ANALISE_ACOES, 'participants_identified': [], 'sentiment': 'neutral',
           'meeting_type_detected': 'general', 'tags': [], 'suggested_events': [], 'risks': []}
reuniao = MeetingTranscription.objects.create(owner=dono, title='ZZ Reunião de metas', status='processing',
                                              raw_transcription=TEXTO_LONGO[:30600])


def salvar_de(obj):
    def salvar(*campos):
        obj.save(update_fields=list(dict.fromkeys(list(campos) + ['updated_at'])))
    return salvar


def processar(obj, ia_, analise=ANALISE):
    with mock.patch.object(av, '_generate_transcription_analysis', return_value=dict(analise)):
        av._analisar_e_concluir(obj, ia_, obj.raw_transcription, salvar_de(obj))
    obj.refresh_from_db()


def com_prazo(trecho, total, msgs):
    return js(item('Enviar a planilha de metas de dezembro ao Bruno', responsible='Carlos', priority='high',
                   deadline='2026-11-14', description='Bruno precisa das metas das 12 lojas para fechar o mês.',
                   success_criteria='Bruno confirma o recebimento.', topic='Metas de dezembro',
                   evidence='Carlos, me manda a planilha até sexta'),
              *[item(f'Conferir a loja L{trecho}{k:02d} no relatório') for k in range(4)])


processar(reuniao, IAFalsa(com_prazo))
tarefas = list(reuniao.tasks_created.order_by('pk'))
t('pipeline: 9 tarefas (análise geral tinha 2)', len(tarefas) == 9 and len(reuniao.action_items) == 9, len(tarefas))
principal = next(x for x in tarefas if 'planilha' in x.title)
desc = principal.description
t('descrição útil: contexto, critério, responsável citado, prazo, assunto e fala',
  '12 lojas' in desc and 'Critério de sucesso: Bruno confirma' in desc and 'Responsável mencionado: Carlos' in desc
  and 'Prazo combinado: 14/11/2026' in desc and 'Assunto: Metas de dezembro' in desc
  and 'me manda a planilha até sexta' in desc, desc)
t('descrição diz de qual reunião veio (com link)',
  'Tarefa gerada automaticamente da transcrição: ZZ Reunião de metas' in desc
  and f'/agenda/transcricoes/{reuniao.pk}/' in desc)
t('atribuída ao dono, alta e com prazo (data com fuso)',
  principal.assigned_to == dono and principal.created_by == dono and principal.priority == 'HIGH'
  and principal.due_date is not None and principal.due_date.tzinfo is not None
  and av.timezone.localtime(principal.due_date).date().isoformat() == '2026-11-14')
t('concluiu', reuniao.status == 'completed' and reuniao.etapa == '')

print('\n== REPROCESSAR NÃO DUPLICA ==')
total_antes = TaskActivity.objects.count()
processar(reuniao, IAFalsa(com_prazo))
t('mesma lista → nada muda', TaskActivity.objects.count() == total_antes and reuniao.tasks_created.count() == 9)
mexida_status = next(x for x in tarefas if 'L100' in x.title)
mexida_status.status = 'DONE'
mexida_status.save()
mexida_pessoa = next(x for x in tarefas if 'L101' in x.title)
c = Client()
c.force_login(dono)
r = c.post(f'/agenda/api/transcricoes/{reuniao.pk}/tasks/{mexida_pessoa.pk}/assign/',
           json.dumps({'user_id': outro.pk}), content_type='application/json')
t('atribuir pela tela segue funcionando', r.status_code == 200, r.status_code)


def segunda_rodada(trecho, total, msgs):   # a IA escreve diferente na segunda vez
    return js(item('Enviar a planilha de metas de dezembro ao Bruno.', responsible='Carlos', priority='high'),
              *[item(f'Conferir a loja L{trecho}{k:02d} no relatório') for k in range(4)],
              item(f'Avisar o time sobre a mudança {trecho}'))


processar(reuniao, IAFalsa(segunda_rodada))
vinculadas = list(reuniao.tasks_created.all())
titulos = [x.title for x in vinculadas]
t('reprocessar com lista nova: sem título repetido', len(titulos) == len(set(t_.lower().rstrip('.') for t_ in titulos)),
  titulos)
t('tarefas que alguém mexeu ficam (status e responsável)',
  TaskActivity.objects.filter(pk=mexida_status.pk, status='DONE').exists()
  and TaskActivity.objects.filter(pk=mexida_pessoa.pk, assigned_to=outro).exists()
  and {mexida_status.pk, mexida_pessoa.pk} <= {x.pk for x in vinculadas})
t('as intocadas antigas foram trocadas (sem órfã na lista do dono)',
  not TaskActivity.objects.filter(pk__in=[x.pk for x in tarefas if x.pk not in (mexida_status.pk, mexida_pessoa.pk)])
  .exclude(source_transcription=reuniao).exists())
t('lista nova inteira representada (11 itens = 11 tarefas vinculadas)',
  len(reuniao.action_items) == 11 and len(vinculadas) == 11, (len(reuniao.action_items), len(vinculadas)))

print('\n== FALLBACK NO PIPELINE ==')
falha = MeetingTranscription.objects.create(owner=dono, title='ZZ IA fora', status='processing',
                                            raw_transcription='Fulano vai mandar a planilha.')
processar(falha, IAFalsa(lambda *a: RuntimeError('insufficient_quota')))
t('etapa de tarefas falhou → as 2 da análise viram tarefa e a transcrição conclui',
  falha.tasks_created.count() == 2 and falha.status == 'completed')
t('descrição no formato novo mesmo no fallback',
  all('Responsável mencionado:' in x.description and 'ZZ IA fora' in x.description
      for x in falha.tasks_created.all()))
antiga = MeetingTranscription.objects.create(owner=dono, title='ZZ Antiga', status='completed',
                                             action_items=[{'task': 'Fazer Y', 'responsible': 'Bruno', 'priority': 'high',
                                                            'deadline': '2026-11-20'}, 'Fazer Z'])
av._create_tasks_from_transcription(antiga, dono)
t('action_items antigos (sem as chaves novas, até string) ainda criam tarefas',
  sorted(antiga.tasks_created.values_list('title', flat=True)) == ['Fazer Y', 'Fazer Z'])

print('\n== TELA ==')
r = c.get(f'/agenda/transcricoes/{reuniao.pk}/')
html = r.content.decode()
t('tela da transcrição abre com os itens novos', r.status_code == 200 and 'Avisar o time sobre a mudan' in html,
  r.status_code)
r = c.get('/agenda/transcricoes/')
t('lista de transcrições abre', r.status_code == 200, r.status_code)

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
