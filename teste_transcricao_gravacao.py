"""Transcrições: gravação e processamento que não se perdem e não param até terminar.

Pedido: "faça de tudo para não perder a gravação e conseguir processar sempre da
forma correta e não pare até sempre finalizar todos os processamentos".

Cobre o servidor: a sessão de gravação existe desde a primeira parte (com dono),
as partes são guardadas uma por índice, o finalizar é idempotente, o job
reivindica a transcrição no banco, salva o progresso trecho a trecho, agenda
nova tentativa quando algo falha e o varredor retoma o que parou e fecha
gravações cujo navegador sumiu.

Nada sai daqui: a OpenAI e o ffmpeg são simulados, as partes e o áudio vão para
uma pasta temporária (não para o MinIO) e tudo roda numa transação desfeita no
fim. Nenhum job roda em thread — só na hora, dentro da transação.
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile
from datetime import timedelta
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import transaction
from django.test import Client
from django.test.utils import override_settings
from django.utils import timezone

from agenda import processamento as proc
from agenda import views as av
from agenda.models import CalendarEvent, MeetingTranscription
from reunioes.models import ParticipanteReuniao, Reuniao

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


ANALISE = {
    'formatted_transcription': 'Texto formatado ZZ', 'summary': 'Resumo ZZ', 'sections': [],
    'key_decisions': [], 'action_items': [{'task': 'ZZ tarefa gerada pela ata', 'priority': 'high'}],
    'participants_identified': [], 'sentiment': 'neutral', 'meeting_type_detected': 'general',
    'tags': [], 'suggested_events': [], 'risks': [],
}


class Whisper:
    """Transcreve trechos de mentira, contando e falhando quando mandado."""

    def __init__(self):
        self.chamadas = []
        self.falhar = set()

    def trecho(self, client, audio_path, indice, inicio, duracao, pasta):
        self.chamadas.append(indice)
        if indice in self.falhar:
            raise RuntimeError(f'OpenAI fora no trecho {indice}')
        return f'texto do trecho {indice}'


pasta = tempfile.mkdtemp(prefix='zz_transcricao_')
partes_storage = FileSystemStorage(location=os.path.join(pasta, 'partes'))
audio_storage = FileSystemStorage(location=os.path.join(pasta, 'audio'))
campo_audio = MeetingTranscription._meta.get_field('audio_file')
storage_original = campo_audio.storage
campo_audio.storage = audio_storage


def por_parte(upload_id, indice, dados=b'webm', sufixo='.webm'):
    partes_storage.save(f'transcriptions/parts/{upload_id}/part_{indice:06d}{sufixo}', ContentFile(dados))


def rodar(pk, modo='upload', **mocks):
    """Roda o job de verdade, na hora, com a OpenAI e o ffmpeg simulados."""
    with contextlib.ExitStack() as pilha:
        pilha.enter_context(mock.patch.object(proc, 'PERMITIR_JOBS_NOS_TESTES', True))
        pilha.enter_context(mock.patch('openai.OpenAI'))
        if mocks:
            pilha.enter_context(mock.patch.multiple(av, **mocks))
        return proc.iniciar_job(pk, modo=modo, sincrono=True)


marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(av, '_get_transcription_parts_storage', return_value=partes_storage), \
         override_settings(OPENAI_API_KEY='sk-teste-falsa-zz'):

        dono = User.objects.create_user(username='zztg.dono', email='zztg.dono@exemplo-teste.local',
                                        password='S3nha!teste', first_name='ZZ', last_name='Dono')
        outro = User.objects.create_user(username='zztg.outro', email='zztg.outro@exemplo-teste.local',
                                         password='S3nha!teste', first_name='ZZ', last_name='Outro')
        chefe = User.objects.create_user(username='zztg.chefe', email='zztg.chefe@exemplo-teste.local',
                                         password='S3nha!teste', first_name='ZZ', last_name='Chefe',
                                         hierarchy='SUPERADMIN')
        c = Client()
        c.force_login(dono)
        co = Client()
        co.force_login(outro)
        cc = Client()
        cc.force_login(chefe)

        print('== REGRAS BÁSICAS ==')
        t('espera cresce: 1, 2, 4, 8, 16 min e para em 30',
          [proc.backoff(n).total_seconds() / 60 for n in (1, 2, 3, 4, 5, 6, 12)] == [1, 2, 4, 8, 16, 30, 30])
        t('em teste a varredura automática nunca liga', not proc.deve_rodar_varredura())
        with mock.patch.dict(os.environ, {'RC_VARREDURA_TRANSCRICOES': '1'}):
            t('nem forçando pela variável de ambiente', not proc.deve_rodar_varredura())
        t('garantir_varredura não sobe thread em teste', proc.garantir_varredura() is False)
        t('em teste nenhum job roda sozinho (nem pelo nome antigo)',
          av._start_transcription_background_job(999999999, 'x', mode='reprocess') is False)
        t('e a retomada também não', av._prioritize_processing_transcriptions('x') == 0)

        print('\n== SESSÃO EXPIRADA NÃO VIRA "PARTE SALVA" ==')
        anon = Client()
        r = anon.post('/agenda/api/transcricoes/upload/chunk/', {'upload_id': 'zzsessao0001'})
        t('parte sem login: 401 em JSON (e não o redirecionamento para o login)',
          r.status_code == 401 and r.json().get('sessao_expirada') is True, r.status_code)
        for url in ('/agenda/api/transcricoes/gravacao/iniciar/', '/agenda/api/transcricoes/upload/finalize/'):
            t(f'{url} sem login: 401', anon.post(url, {}).status_code == 401)
        t('pendentes sem login: 401', anon.get('/agenda/api/transcricoes/pendentes/').status_code == 401)

        print('\n== A SESSÃO DE GRAVAÇÃO NASCE COM DONO ==')
        r = c.post('/agenda/api/transcricoes/gravacao/iniciar/',
                   {'upload_id': 'zzgrav-0001-aaaa', 'title': 'ZZ Reunião de pauta', 'origem': 'gravador'})
        dados = r.json()
        t('iniciar: 200 com a sessão', r.status_code == 200 and dados.get('status') == 'recording', r.content[:200])
        sessao = MeetingTranscription.objects.get(upload_id='zzgrav-0001-aaaa')
        t('a transcrição existe desde já, gravando, do dono', sessao.owner_id == dono.id
          and sessao.title == 'ZZ Reunião de pauta' and sessao.origem == 'gravador')
        r = c.post('/agenda/api/transcricoes/gravacao/iniciar/', {'upload_id': 'zzgrav-0001-aaaa'})
        t('iniciar de novo devolve a mesma sessão', r.json().get('id') == sessao.pk)
        r = co.post('/agenda/api/transcricoes/gravacao/iniciar/', {'upload_id': 'zzgrav-0001-aaaa'})
        t('outra pessoa não entra na sessão de ninguém (403)', r.status_code == 403, r.status_code)
        t('upload_id inválido: 400',
          c.post('/agenda/api/transcricoes/gravacao/iniciar/', {'upload_id': 'a/../b'}).status_code == 400)

        agora = timezone.now()
        evento = CalendarEvent.objects.create(owner=outro, title='ZZ evento da reunião', start=agora,
                                              end=agora + timedelta(hours=1))
        reuniao = Reuniao.objects.create(titulo='ZZ reunião com ata', organizador=outro, inicio=agora, evento=evento)
        ParticipanteReuniao.objects.create(reuniao=reuniao, user=dono)
        r = c.post('/agenda/api/transcricoes/gravacao/iniciar/',
                   {'upload_id': 'zzgrav-reuniao-01', 'origem': 'reuniao', 'event_id': evento.pk})
        ata = MeetingTranscription.objects.get(upload_id='zzgrav-reuniao-01')
        t('quem está na reunião liga a ata ao evento dela (antes só o dono do evento)', ata.event_id == evento.pk)
        evento_alheio = CalendarEvent.objects.create(owner=outro, title='ZZ evento privado', start=agora,
                                                     end=agora + timedelta(hours=1))
        c.post('/agenda/api/transcricoes/gravacao/iniciar/',
               {'upload_id': 'zzgrav-privado-01', 'event_id': evento_alheio.pk})
        t('evento de outra pessoa sem vínculo não é ligado',
          MeetingTranscription.objects.get(upload_id='zzgrav-privado-01').event_id is None)

        print('\n== AS PARTES ==')
        def parte(cliente, upload_id, indice, dados=b'webm-dados', nome='parte.webm', **extra):
            return cliente.post('/agenda/api/transcricoes/upload/chunk/', {
                'upload_id': upload_id, 'chunk_index': indice, 'total_chunks': 0,
                'audio': SimpleUploadedFile(nome, dados, content_type='audio/webm'), **extra})

        r = parte(c, 'zzgrav-0002-bbbb', 2, title='ZZ Sem iniciar')
        t('primeira parte sem "iniciar" cria a sessão (cliente antigo segue funcionando)',
          r.status_code == 200 and r.json().get('received') is True and r.json().get('transcription_id'), r.content[:200])
        nova = MeetingTranscription.objects.get(upload_id='zzgrav-0002-bbbb')
        t('com o título mandado junto', nova.title == 'ZZ Sem iniciar' and nova.status == 'recording')
        parte(c, 'zzgrav-0002-bbbb', 0)
        nova.refresh_from_db()
        t('partes_recebidas = maior índice + 1 (chegou a 2 antes da 0)', nova.partes_recebidas == 3, nova.partes_recebidas)
        t('e marca quando chegou a última', nova.ultima_parte_em is not None)
        parte(c, 'zzgrav-0002-bbbb', 0, dados=b'reenviada')
        arquivos = partes_storage.listdir('transcriptions/parts/zzgrav-0002-bbbb')[1]
        t('parte reenviada substitui (um arquivo por índice)', sorted(arquivos) == ['part_000000.webm', 'part_000002.webm'], arquivos)
        with partes_storage.open('transcriptions/parts/zzgrav-0002-bbbb/part_000000.webm') as fh:
            t('com o conteúdo novo', fh.read() == b'reenviada')
        t('outra pessoa não escreve na gravação de ninguém (403)', parte(co, 'zzgrav-0002-bbbb', 1).status_code == 403)
        t('índice absurdo: 400', parte(c, 'zzgrav-0002-bbbb', 999999).status_code == 400)
        parte(c, 'zzgrav-0002-bbbb', 1, nome='../../x.p%hp')
        t('nome de arquivo estranho vira .webm, sem sair da pasta',
          'part_000001.webm' in partes_storage.listdir('transcriptions/parts/zzgrav-0002-bbbb')[1])
        parte(c, 'zzgrav-safari-01', 0, nome='gravacao.mp4')
        t('o formato do Safari (mp4) é mantido',
          partes_storage.listdir('transcriptions/parts/zzgrav-safari-01')[1] == ['part_000000.mp4'])

        print('\n== A LISTA DE PARTES NÃO DUPLICA TRECHO ==')
        for nome in ('part_000000.webm', 'part_000001.webm', 'part_000001_AbCdEf.webm', 'lixo.txt'):
            partes_storage.save(f'transcriptions/parts/zzlista-0001/{nome}', ContentFile(b'x'))
        lista = av._list_transcription_parts(partes_storage, 'zzlista-0001')
        t('uma por índice, em ordem, ignorando cópia com sufixo e lixo',
          [n.rsplit('/', 1)[-1] for n in lista] == ['part_000000.webm', 'part_000001.webm'], lista)
        quebrado = mock.Mock()
        quebrado.listdir.side_effect = OSError('MinIO fora')
        t('storage fora: sem levantar, lista vazia (como antes)', av._list_transcription_parts(quebrado, 'zzlista-0001') == [])
        try:
            av._list_transcription_parts(quebrado, 'zzlista-0001', levantar=True)
            t('no job, storage fora sobe como erro (vira nova tentativa)', False)
        except OSError:
            t('no job, storage fora sobe como erro (vira nova tentativa)', True)

        print('\n== FINALIZAR É IDEMPOTENTE ==')
        def finalizar(cliente, upload_id, **extra):
            return cliente.post('/agenda/api/transcricoes/upload/finalize/', {'upload_id': upload_id, **extra})

        r = finalizar(c, 'zzgrav-0002-bbbb', total_chunks=4, title='ZZ Pauta final', duration_seconds=95)
        dados = r.json()
        nova.refresh_from_db()
        t('finalizar: 202 e vai para processamento', r.status_code == 202 and nova.status == 'processing', r.content[:200])
        t('sem criar outra transcrição', dados.get('id') == nova.pk
          and MeetingTranscription.objects.filter(upload_id='zzgrav-0002-bbbb').count() == 1)
        t('conta o buraco de verdade (4 geradas, 3 no storage)', dados.get('missing_chunks') == 1 and dados.get('parts_received') == 3, dados)
        t('título e duração atualizados', nova.title == 'ZZ Pauta final' and nova.duration_seconds == 95)
        t('em teste não iniciou job em thread', dados.get('iniciado') is False)
        r = finalizar(c, 'zzgrav-0002-bbbb')
        t('finalizar de novo: mesma transcrição', r.json().get('id') == nova.pk
          and MeetingTranscription.objects.filter(upload_id='zzgrav-0002-bbbb').count() == 1)
        t('outra pessoa não finaliza (403)', finalizar(co, 'zzgrav-0002-bbbb').status_code == 403)
        t('o SUPERADMIN finaliza (202)', finalizar(cc, 'zzgrav-0002-bbbb').status_code == 202)
        t('sem parte nenhuma: 400', finalizar(c, 'zzgrav-vazia-0001').status_code == 400)

        MeetingTranscription.objects.filter(pk=nova.pk).update(batimento_em=timezone.now(), tentativas=3)
        finalizar(c, 'zzgrav-0002-bbbb')
        nova.refresh_from_db()
        t('com job vivo, finalizar não reinicia a contagem (não duplica processamento)', nova.tentativas == 3)

        MeetingTranscription.objects.filter(pk=nova.pk).update(
            status='completed', batimento_em=None, progresso={'partes_processadas': 3})
        r = finalizar(c, 'zzgrav-0002-bbbb')
        nova.refresh_from_db()
        t('já concluída e sem partes novas: não processa de novo', r.status_code == 200
          and r.json().get('status') == 'completed' and nova.status == 'completed')
        por_parte('zzgrav-0002-bbbb', 3)
        MeetingTranscription.objects.filter(pk=nova.pk).update(partes_recebidas=4)
        r = finalizar(c, 'zzgrav-0002-bbbb')
        nova.refresh_from_db()
        t('concluída mas chegou parte depois: processa de novo com tudo', r.status_code == 202 and nova.status == 'processing')

        print('\n== PENDÊNCIAS E DESCARTE ==')
        dados = c.get('/agenda/api/transcricoes/pendentes/').json()
        ids = {p['id'] for p in dados['pendentes']}
        t('pendências do dono: gravando e processando', {sessao.pk, nova.pk} <= ids, ids)
        t('com o que a tela precisa', all(k in dados['pendentes'][0] for k in
                                           ('upload_id', 'status', 'partes_recebidas', 'etapa_rotulo', 'redirect')))
        t('não mostra a de outra pessoa', not ({p['id'] for p in co.get('/agenda/api/transcricoes/pendentes/').json()['pendentes']} & ids))

        por_parte('zzgrav-0001-aaaa', 0)
        r = co.post(f'/agenda/api/transcricoes/{sessao.pk}/descartar/')
        t('outra pessoa não descarta (404)', r.status_code == 404, r.status_code)
        r = c.post(f'/agenda/api/transcricoes/{nova.pk}/descartar/')
        t('não descarta o que está processando (409)', r.status_code == 409, r.status_code)
        r = c.post(f'/agenda/api/transcricoes/{sessao.pk}/descartar/')
        t('descarta a gravação em andamento', r.status_code == 200 and not MeetingTranscription.objects.filter(pk=sessao.pk).exists())
        t('e apaga as partes dela', partes_storage.listdir('transcriptions/parts/zzgrav-0001-aaaa')[1] == [])

        print('\n== STATUS: "TRAVADO" É JOB SEM SINAL, NÃO ESPERA MARCADA ==')
        MeetingTranscription.objects.filter(pk=nova.pk).update(
            status='processing', batimento_em=timezone.now() - timedelta(minutes=10), proxima_tentativa_em=None,
            etapa='transcricao', progresso={'partes_processadas': 4, 'total_segmentos': 4, 'segmentos': {'0': 'a', '1': 'b'}})
        dados = c.get(f'/agenda/api/transcricoes/{nova.pk}/status/').json()
        t('sem batimento há 10 min: travado', dados['is_stale'] is True, dados)
        t('com a etapa e o progresso', dados['etapa'] == 'transcricao' and dados['etapa_rotulo'] == 'Transcrevendo'
          and dados['progresso_pct'] == 45, dados.get('progresso_pct'))
        MeetingTranscription.objects.filter(pk=nova.pk).update(proxima_tentativa_em=timezone.now() + timedelta(minutes=5))
        dados = c.get(f'/agenda/api/transcricoes/{nova.pk}/status/').json()
        t('com nova tentativa marcada: não é travado', dados['is_stale'] is False and dados['aguardando_nova_tentativa'] is True)

        print('\n== REIVINDICAÇÃO NO BANCO ==')
        MeetingTranscription.objects.filter(pk=nova.pk).update(batimento_em=None, proxima_tentativa_em=None, processando_por='')
        t('o primeiro pega', proc.reivindicar(nova.pk, 'worker-a'))
        t('o segundo, logo depois, não', not proc.reivindicar(nova.pk, 'worker-b'))
        MeetingTranscription.objects.filter(pk=nova.pk).update(batimento_em=timezone.now() - timedelta(minutes=5))
        t('sem batimento há 5 min, outro retoma', proc.reivindicar(nova.pk, 'worker-b'))
        try:
            proc.conferir_dono(nova.pk, 'worker-a')
            t('o job antigo percebe que perdeu a transcrição', False)
        except proc.PerdeuAReivindicacao:
            t('o job antigo percebe que perdeu a transcrição', True)
        MeetingTranscription.objects.filter(pk=nova.pk).update(
            batimento_em=None, proxima_tentativa_em=timezone.now() + timedelta(minutes=3))
        t('com espera marcada para depois, ninguém pega antes da hora', not proc.reivindicar(nova.pk, 'worker-c'))

        print('\n== FALHA VIRA NOVA TENTATIVA, SEM DESISTIR CEDO ==')
        MeetingTranscription.objects.filter(pk=nova.pk).update(
            proxima_tentativa_em=None, processando_por='worker-x', tentativas=0, raw_transcription='')
        antes = timezone.now()
        proc.registrar_falha(nova.pk, 'worker-x', RuntimeError('Request timed out'))
        nova.refresh_from_db()
        espera = (nova.proxima_tentativa_em - antes).total_seconds()
        t('segue em processamento, com tentativa marcada para daqui a 1 min',
          nova.status == 'processing' and 50 <= espera <= 75 and nova.tentativas == 1, (nova.status, espera))
        t('a mensagem diz que tenta sozinho', 'tenta de novo sozinho' in nova.error_message, nova.error_message)
        t('e larga a transcrição para quem vier', nova.processando_por == '' and nova.batimento_em is None)
        MeetingTranscription.objects.filter(pk=nova.pk).update(processando_por='worker-y')
        antes = timezone.now()
        proc.registrar_falha(nova.pk, 'worker-y', RuntimeError('rate limit'))
        nova.refresh_from_db()
        t('a segunda espera é maior (2 min)', 110 <= (nova.proxima_tentativa_em - antes).total_seconds() <= 135)
        MeetingTranscription.objects.filter(pk=nova.pk).update(processando_por='worker-z')
        proc.registrar_falha(nova.pk, 'outro-worker', RuntimeError('x'))
        nova.refresh_from_db()
        t('falha de um job que já perdeu a transcrição é ignorada', nova.tentativas == 2)
        proc.registrar_falha(nova.pk, 'worker-z', proc.ErroPermanente('Nenhuma parte do áudio foi encontrada.'))
        nova.refresh_from_db()
        t('erro permanente vira erro de verdade', nova.status == 'error' and nova.proxima_tentativa_em is None)
        MeetingTranscription.objects.filter(pk=nova.pk).update(status='processing', processando_por='w',
                                                               tentativas=proc.MAX_TENTATIVAS - 1)
        proc.registrar_falha(nova.pk, 'w', RuntimeError('x'))
        nova.refresh_from_db()
        t(f'depois de {proc.MAX_TENTATIVAS} tentativas, desiste com aviso', nova.status == 'error'
          and 'tentou' in nova.error_message)

        print('\n== O PIPELINE, DO COMEÇO AO FIM ==')
        def sessao_com_partes(upload_id, quantas, titulo='ZZ Gravação longa'):
            transcricao = MeetingTranscription.objects.create(
                owner=dono, title=titulo, upload_id=upload_id, origem='gravador', status='processing',
                etapa='montagem', partes_recebidas=quantas, ultima_parte_em=timezone.now())
            for i in range(quantas):
                por_parte(upload_id, i, dados=f'parte-{i}|'.encode())
            return transcricao

        longa = sessao_com_partes('zzpipe-longa-01', 3)
        whisper = Whisper()
        analise = mock.Mock(return_value=dict(ANALISE))
        iniciou = rodar(longa.pk, _probe_audio_duration_seconds=mock.Mock(return_value=3000.0),
                        _transcrever_trecho=whisper.trecho, _generate_transcription_analysis=analise)
        longa.refresh_from_db()
        t('o job roda e conclui', iniciou and longa.status == 'completed', (longa.status, longa.error_message))
        t('juntou as 3 partes e guardou o áudio', longa.progresso.get('partes_processadas') == 3 and bool(longa.audio_file))
        with longa.audio_file.open('rb') as fh:
            t('na ordem', fh.read() == b'parte-0|parte-1|parte-2|')
        t('3000 s em trechos de 12 min: 5 trechos', sorted(whisper.chamadas) == [0, 1, 2, 3, 4], whisper.chamadas)
        t('o texto sai na ordem, mesmo com trechos em paralelo',
          longa.raw_transcription == '\n\n'.join(f'texto do trecho {i}' for i in range(5)))
        t('com a análise, a tarefa e o evento na agenda', longa.summary == 'Resumo ZZ'
          and longa.tasks_created.count() == 1 and longa.calendar_event_created_id is not None)
        t('e sem sobra de processamento', longa.etapa == '' and longa.processando_por == '' and longa.batimento_em is None)

        print('\n== RETOMADA: SÓ O QUE FALTOU ==')
        falha = sessao_com_partes('zzpipe-falha-01', 2, titulo='ZZ Com trecho que falha')
        whisper = Whisper()
        whisper.falhar = {3}
        analise = mock.Mock(return_value=dict(ANALISE))
        comuns = dict(_probe_audio_duration_seconds=mock.Mock(return_value=3000.0), _transcrever_trecho=whisper.trecho,
                      _generate_transcription_analysis=analise)
        rodar(falha.pk, **comuns)
        falha.refresh_from_db()
        t('um trecho falhou: não conclui com buraco, agenda nova tentativa',
          falha.status == 'processing' and falha.proxima_tentativa_em is not None and falha.tentativas == 1,
          (falha.status, falha.error_message))
        t('os outros trechos ficaram salvos', sorted(falha.progresso.get('segmentos', {})) == ['0', '1', '2', '4'])
        t('o texto bruto ainda não foi gravado e a análise não rodou', falha.raw_transcription == '' and analise.call_count == 0)
        t('o aviso fala dos trechos', 'trechos' in falha.error_message, falha.error_message)
        MeetingTranscription.objects.filter(pk=falha.pk).update(proxima_tentativa_em=None)
        whisper.chamadas.clear()
        whisper.falhar = set()
        rodar(falha.pk, modo='retomar', **comuns)
        falha.refresh_from_db()
        t('na retomada, só o trecho que faltou vai para o Whisper', whisper.chamadas == [3], whisper.chamadas)
        t('e conclui com o texto inteiro, em ordem', falha.status == 'completed'
          and falha.raw_transcription.count('texto do trecho') == 5)

        print('\n== ANÁLISE QUE FALHA NÃO PAGA O WHISPER DE NOVO ==')
        so_analise = sessao_com_partes('zzpipe-analise-01', 1, titulo='ZZ Análise falha')
        whisper = Whisper()
        analise = mock.Mock(side_effect=RuntimeError('insufficient_quota'))
        comuns = dict(_probe_audio_duration_seconds=mock.Mock(return_value=3000.0), _transcrever_trecho=whisper.trecho,
                      _generate_transcription_analysis=analise)
        rodar(so_analise.pk, **comuns)
        so_analise.refresh_from_db()
        t('a análise falhou: nova tentativa marcada', so_analise.status == 'processing' and so_analise.proxima_tentativa_em)
        t('mas o texto bruto já está salvo', so_analise.raw_transcription.count('texto do trecho') == 5)
        MeetingTranscription.objects.filter(pk=so_analise.pk).update(proxima_tentativa_em=None)
        whisper.chamadas.clear()
        comuns['_generate_transcription_analysis'] = mock.Mock(return_value=dict(ANALISE))
        rodar(so_analise.pk, modo='retomar', **comuns)
        so_analise.refresh_from_db()
        t('na retomada, nenhum trecho vai para o Whisper', whisper.chamadas == [], whisper.chamadas)
        t('e conclui', so_analise.status == 'completed')
        rodar(so_analise.pk, modo='reprocess', **comuns)
        t('reprocessar de novo não duplica a tarefa (mesma lista de ações)', so_analise.tasks_created.count() == 1)

        print('\n== DEPOIS DE VÁRIAS TENTATIVAS, ACEITA LACUNA MARCADA ==')
        teimosa = sessao_com_partes('zzpipe-teimosa-01', 1, titulo='ZZ Trecho teimoso')
        MeetingTranscription.objects.filter(pk=teimosa.pk).update(tentativas=av.TENTATIVAS_ANTES_DE_ACEITAR_LACUNAS)
        whisper = Whisper()
        whisper.falhar = {1}
        rodar(teimosa.pk, _probe_audio_duration_seconds=mock.Mock(return_value=3000.0), _transcrever_trecho=whisper.trecho,
              _generate_transcription_analysis=mock.Mock(return_value=dict(ANALISE)))
        teimosa.refresh_from_db()
        t('conclui com o trecho marcado no texto', teimosa.status == 'completed'
          and '[Trecho 00:12–00:24 não transcrito]' in teimosa.raw_transcription, teimosa.raw_transcription[:200])

        print('\n== PARTE QUE CHEGA DURANTE O PROCESSAMENTO ==')
        atrasada = sessao_com_partes('zzpipe-atraso-01', 3, titulo='ZZ Parte atrasada')
        duracoes = {'n': 0}

        def duracao_e_parte_nova(caminho):
            duracoes['n'] += 1
            if duracoes['n'] == 1:
                por_parte('zzpipe-atraso-01', 3, dados=b'parte-3|')
                MeetingTranscription.objects.filter(pk=atrasada.pk).update(partes_recebidas=4)
            return 60.0

        rodar(atrasada.pk, _probe_audio_duration_seconds=duracao_e_parte_nova,
              _try_transcribe_file=mock.Mock(return_value=('texto curto', 60)),
              _generate_transcription_analysis=mock.Mock(return_value=dict(ANALISE)))
        atrasada.refresh_from_db()
        with atrasada.audio_file.open('rb') as fh:
            audio = fh.read()
        t('refaz com a parte nova e conclui', atrasada.status == 'completed'
          and atrasada.progresso.get('partes_processadas') == 4 and audio.endswith(b'parte-3|'), audio)

        print('\n== JOB QUE PERDEU A TRANSCRIÇÃO PARA QUIETO ==')
        disputada = sessao_com_partes('zzpipe-disputa-01', 1, titulo='ZZ Disputada')

        def analise_e_outro_assume(*args, **kwargs):
            MeetingTranscription.objects.filter(pk=disputada.pk).update(processando_por='outro-worker')
            return dict(ANALISE)

        rodar(disputada.pk, _probe_audio_duration_seconds=mock.Mock(return_value=60.0),
              _try_transcribe_file=mock.Mock(return_value=('texto', 60)),
              _generate_transcription_analysis=analise_e_outro_assume)
        disputada.refresh_from_db()
        t('não conclui por cima do outro nem conta como falha', disputada.status == 'processing'
          and disputada.tentativas == 0 and disputada.processando_por == 'outro-worker')

        print('\n== SEM CHAVE DA OPENAI ==')
        sem_chave = sessao_com_partes('zzpipe-semchave-1', 1, titulo='ZZ Sem chave')
        with override_settings(OPENAI_API_KEY=''):
            rodar(sem_chave.pk)
        sem_chave.refresh_from_db()
        t('gravação nova sem chave: erro claro (não fica tentando)', sem_chave.status == 'error'
          and 'OPENAI_API_KEY' in sem_chave.error_message, sem_chave.error_message)
        com_texto = MeetingTranscription.objects.create(owner=dono, title='ZZ Texto salvo', status='processing',
                                                        raw_transcription='Texto já transcrito')
        with override_settings(OPENAI_API_KEY=''):
            rodar(com_texto.pk, modo='reprocess')
        com_texto.refresh_from_db()
        t('reprocessar com texto salvo e sem chave conclui com o texto (como antes)',
          com_texto.status == 'completed' and com_texto.formatted_transcription == 'Texto já transcrito')

        print('\n== O VARREDOR ==')
        orfa = MeetingTranscription.objects.create(owner=dono, title='ZZ Órfã', upload_id='zzvarre-orfa-01',
                                                   status='recording', partes_recebidas=2,
                                                   ultima_parte_em=timezone.now() - timedelta(minutes=30))
        por_parte('zzvarre-orfa-01', 0)
        por_parte('zzvarre-orfa-01', 1)
        viva = MeetingTranscription.objects.create(owner=dono, title='ZZ Viva', upload_id='zzvarre-viva-01',
                                                   status='recording', partes_recebidas=5,
                                                   ultima_parte_em=timezone.now() - timedelta(minutes=2))
        vazia = MeetingTranscription.objects.create(owner=dono, title='ZZ Vazia', upload_id='zzvarre-vazia-1',
                                                    status='recording', partes_recebidas=0)
        MeetingTranscription.objects.filter(pk=vazia.pk).update(created_at=timezone.now() - timedelta(hours=3))
        ids = [orfa.pk, viva.pk, vazia.pk]
        resumo = proc.varrer(somente_ids=ids)
        orfa.refresh_from_db()
        viva.refresh_from_db()
        t('gravação sem parte há 30 min é fechada para processar', orfa.status == 'processing'
          and orfa.finalizada_automaticamente and resumo['orfas_finalizadas'] == 1, resumo)
        t('gravação com parte há 2 min continua gravando', viva.status == 'recording')
        t('sessão sem áudio há 3 h é apagada', not MeetingTranscription.objects.filter(pk=vazia.pk).exists()
          and resumo['vazias_apagadas'] == 1)
        t('em teste, sem permissão, o varredor não processa nada', resumo['retomadas'] == 0)

        r = parte(c, 'zzvarre-orfa-01', 2)
        orfa.refresh_from_db()
        t('o navegador voltou com mais áudio: a gravação reabre', r.status_code == 200
          and orfa.status == 'recording' and not orfa.finalizada_automaticamente and orfa.partes_recebidas == 3)
        MeetingTranscription.objects.filter(pk=orfa.pk).update(
            status='processing', finalizada_automaticamente=True, ultima_parte_em=timezone.now() - timedelta(minutes=30))

        whisper = Whisper()
        with mock.patch.object(proc, 'PERMITIR_JOBS_NOS_TESTES', True), mock.patch('openai.OpenAI'), \
             mock.patch.multiple(av, _probe_audio_duration_seconds=mock.Mock(return_value=60.0),
                                 _try_transcribe_file=mock.Mock(return_value=('texto da órfã', 60)),
                                 _generate_transcription_analysis=mock.Mock(return_value=dict(ANALISE))):
            resumo = proc.varrer(sincrono=True, somente_ids=[orfa.pk])
        orfa.refresh_from_db()
        t('o varredor retoma e conclui a órfã', resumo['retomadas'] == 1 and orfa.status == 'completed', (resumo, orfa.status))
        t('com as 3 partes', orfa.progresso.get('partes_processadas') == 3)

        antiga = MeetingTranscription.objects.create(owner=dono, title='ZZ Antiga', upload_id='zzvarre-antiga-1',
                                                     status='completed')
        antiga.audio_file.save('antiga.webm', ContentFile(b'audio'), save=True)
        por_parte('zzvarre-antiga-1', 0)
        MeetingTranscription.objects.filter(pk=antiga.pk).update(updated_at=timezone.now() - timedelta(days=8))
        resumo = proc.varrer(somente_ids=[antiga.pk])
        antiga.refresh_from_db()
        t('partes de gravação concluída há 8 dias são apagadas (o áudio montado fica)',
          resumo['partes_limpas'] == 1 and partes_storage.listdir('transcriptions/parts/zzvarre-antiga-1')[1] == []
          and audio_storage.exists(antiga.audio_file.name) and antiga.progresso.get('partes_apagadas'))

        print('\n== COMANDO DE MANUTENÇÃO ==')
        parada = sessao_com_partes('zzcmd-parada-01', 1, titulo='ZZ Parada no deploy')
        MeetingTranscription.objects.filter(pk=parada.pk).update(batimento_em=timezone.now() - timedelta(minutes=10),
                                                                 processando_por='worker-que-morreu')
        saida = io.StringIO()
        with mock.patch.object(proc, 'PERMITIR_JOBS_NOS_TESTES', True), mock.patch('openai.OpenAI'), \
             mock.patch.multiple(av, _probe_audio_duration_seconds=mock.Mock(return_value=60.0),
                                 _try_transcribe_file=mock.Mock(return_value=('texto', 60)),
                                 _generate_transcription_analysis=mock.Mock(return_value=dict(ANALISE))):
            call_command('processar_transcricoes', '--id', str(parada.pk), stdout=saida)
        parada.refresh_from_db()
        t('o comando retoma o job que morreu no deploy', 'retomadas: 1' in saida.getvalue()
          and parada.status == 'completed', (saida.getvalue(), parada.status))

        print('\n== REPROCESSAR ==')
        aberta = MeetingTranscription.objects.create(owner=dono, title='ZZ Aberta', upload_id='zzrepro-aberta-1',
                                                     status='recording', partes_recebidas=2)
        r = c.post(f'/agenda/api/transcricoes/{aberta.pk}/reprocess/', data='{}', content_type='application/json')
        aberta.refresh_from_db()
        t('numa gravação aberta, serve de "encerrar agora"', r.status_code == 202 and aberta.status == 'processing')
        sem_partes = MeetingTranscription.objects.create(owner=dono, title='ZZ Sem partes', upload_id='zzrepro-vazia-1',
                                                         status='recording')
        r = c.post(f'/agenda/api/transcricoes/{sem_partes.pk}/reprocess/', data='{}', content_type='application/json')
        t('gravação sem parte nenhuma: 400', r.status_code == 400, r.status_code)
        with mock.patch.object(av, '_start_transcription_background_job', return_value=True) as inicio:
            c.post(f'/agenda/api/transcricoes/{aberta.pk}/reprocess/',
                   data='{"raw_text": "texto editado", "force_raw": true, "lixo": "x"}', content_type='application/json')
        t('só as opções que o pipeline entende vão adiante',
          inicio.call_args.kwargs.get('options') == {'force_raw': True, 'raw_text': 'texto editado'}, inicio.call_args)

        print('\n== ARQUIVO PEQUENO VAI PARA O STORAGE ANTES DA RESPOSTA ==')
        r = c.post('/agenda/api/transcricoes/upload/', {
            'title': 'ZZ Arquivo pequeno', 'audio': SimpleUploadedFile('reuniao.mp3', b'ID3-audio', content_type='audio/mpeg')})
        dados = r.json()
        arquivo = MeetingTranscription.objects.get(pk=dados['id'])
        t('202, processando, origem arquivo', r.status_code == 202 and arquivo.status == 'processing' and arquivo.origem == 'arquivo')
        t('o áudio já está guardado', bool(arquivo.audio_file) and audio_storage.exists(arquivo.audio_file.name))

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    campo_audio.storage = storage_original
    shutil.rmtree(pasta, ignore_errors=True)
    print('\nrollback: nada deste teste foi gravado no banco; arquivos temporários apagados.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
