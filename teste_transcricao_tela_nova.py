"""As telas do gravador: nova transcrição, janela do gravador e o detalhe.

Confere que as páginas carregam o módulo rc-gravador com as URLs certas, que o
JavaScript embutido nos templates tem sintaxe válida (rodando `node --check`
em cada <script> da página renderizada), que a janela do gravador não aceita
nada estranho pela URL e que o detalhe mostra "gravando", a etapa, o progresso
e a nova tentativa.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import timedelta

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import timezone

from agenda.models import CalendarEvent, MeetingTranscription
from reunioes.models import ParticipanteReuniao, Reuniao

User = get_user_model()
NODE = shutil.which('node')
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def scripts_embutidos(html, marcador):
    """Os <script> sem src da página que contêm `marcador`."""
    blocos = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, flags=re.S)
    return [b for b in blocos if marcador in b]


def sintaxe_ok(codigo):
    if not NODE:
        return True, 'node ausente'
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as fh:
        fh.write(codigo)
        caminho = fh.name
    try:
        r = subprocess.run([NODE, '--check', caminho], capture_output=True, text=True)
        return r.returncode == 0, r.stderr[-800:]
    finally:
        os.unlink(caminho)


marcador = transaction.atomic()
marcador.__enter__()
try:
    dono = User.objects.create_user(username='zztn.dono', email='zztn.dono@exemplo-teste.local', password='S3nha!teste',
                                    first_name='ZZ', last_name='Dono', theme='dark')
    outro = User.objects.create_user(username='zztn.outro', email='zztn.outro@exemplo-teste.local', password='S3nha!teste',
                                     first_name='ZZ', last_name='Outro')
    c = Client()
    c.force_login(dono)

    print('== NOVA TRANSCRIÇÃO ==')
    r = c.get('/agenda/transcricoes/nova/')
    html = r.content.decode()
    t('abre (200)', r.status_code == 200, r.status_code)
    t('carrega o módulo do gravador', 'js/rc-gravador.js' in html)
    for url in ('/agenda/api/transcricoes/gravacao/iniciar/', '/agenda/api/transcricoes/upload/chunk/',
                '/agenda/api/transcricoes/upload/finalize/', '/agenda/api/transcricoes/pendentes/',
                '/agenda/transcricoes/gravador/', '/agenda/api/transcricoes/0/descartar/',
                '/agenda/api/transcricoes/0/reprocess/', '/agenda/api/transcricoes/upload/'):
        t(f'usa {url}', url in html)
    for pedaco, porque in (('id="pendencias"', 'banner de pendências'), ('id="janela-gravacao"', 'painel da janela do gravador'),
                           ('id="gravar-nesta-aba"', 'opção de gravar na própria aba'), ('id="mini-painel"', 'mini-painel flutuante'),
                           ('name="audio-source"', 'escolha da fonte de áudio'), ('id="roles-container"', 'papéis dos participantes'),
                           ('id="audio-file-input"', 'envio de arquivo')):
        t(f'mantém/tem {porque}', pedaco in html)
    t('as três fontes de áudio continuam', html.count('type="radio" name="audio-source"') == 3)
    t('o gravador antigo (tudo em memória) saiu', 'chunkRegistry' not in html and 'LIVE_RECORD_CHUNK_MS' not in html)
    t('sem comentário de template vazando', '{#' not in html and '{% comment' not in html)
    blocos = scripts_embutidos(html, 'URLS_GRAVACAO')
    t('um script do gravador na página', len(blocos) == 1, len(blocos))
    if blocos:
        valido, erro = sintaxe_ok(blocos[0])
        t('o JavaScript da tela tem sintaxe válida (node --check)', valido, erro)
        t('sem evento vinculado, EVENTO_ID vazio', "const EVENTO_ID = '';" in blocos[0])

    agora = timezone.now()
    evento = CalendarEvent.objects.create(owner=outro, title='ZZ Evento da reunião', start=agora, end=agora + timedelta(hours=1))
    reuniao = Reuniao.objects.create(titulo='ZZ Reunião', organizador=outro, inicio=agora, evento=evento)
    ParticipanteReuniao.objects.create(reuniao=reuniao, user=dono)
    html = c.get(f'/agenda/transcricoes/nova/?event_id={evento.pk}').content.decode()
    t('evento da reunião em que a pessoa está é aceito', f"const EVENTO_ID = '{evento.pk}';" in html and 'ZZ Evento da reunião' in html)
    privado = CalendarEvent.objects.create(owner=outro, title='ZZ Evento privado', start=agora, end=agora + timedelta(hours=1))
    html = c.get(f'/agenda/transcricoes/nova/?event_id={privado.pk}').content.decode()
    t('evento alheio sem vínculo não aparece', 'ZZ Evento privado' not in html and "const EVENTO_ID = '';" in html)

    print('\n== JANELA DO GRAVADOR ==')
    r = c.get('/agenda/transcricoes/gravador/', {'fonte': 'both', 'titulo': 'ZZ Pauta', 'upload_id': 'zz-grav-janela-01'})
    html = r.content.decode()
    t('abre (200)', r.status_code == 200, r.status_code)
    t('página própria, sem o menu do portal', 'id="sidebar"' not in html)
    t('carrega o módulo e o tema', 'js/rc-gravador.js' in html and 'css/tema-escuro.css' in html)
    t('respeita o tema escuro de quem abre', '<html lang="pt-br" class="dark">' in html)
    t('recebe a fonte', "const FONTE = 'both';" in html)
    t('recebe o título', 'value="ZZ Pauta"' in html)
    # escapejs troca o hífen por \u002D: no JavaScript é a mesma string.
    t('recebe a sessão', "'zz\\u002Dgrav\\u002Djanela\\u002D01' || RCGravador.novoUploadId()" in html)
    t('tem o token do CSRF', 'csrfmiddlewaretoken' in html or "csrf: '" in html)
    t('sem comentário de template vazando', '{#' not in html and '{% comment' not in html)
    blocos = scripts_embutidos(html, 'new RCGravador')
    t('um script do gravador na janela', len(blocos) == 1, len(blocos))
    if blocos:
        valido, erro = sintaxe_ok(blocos[0])
        t('o JavaScript da janela tem sintaxe válida (node --check)', valido, erro)

    html = c.get('/agenda/transcricoes/gravador/', {
        'fonte': 'hack', 'titulo': '"><script>alert(1)</script>', 'upload_id': 'a/../b'}).content.decode()
    t('fonte desconhecida vira microfone', "const FONTE = 'mic';" in html)
    t('título com HTML é escapado', '<script>alert(1)</script>' not in html and '&lt;script&gt;' in html)
    t('sessão inválida é descartada (gera uma nova)', "'' || RCGravador.novoUploadId()" in html)
    t('sem login: vai para o login', Client().get('/agenda/transcricoes/gravador/').status_code == 302)

    print('\n== DETALHE: GRAVANDO, PROCESSANDO, TENTANDO DE NOVO ==')
    gravando = MeetingTranscription.objects.create(owner=dono, title='ZZ Gravando', status='recording',
                                                   upload_id='zz-tela-grav-01', partes_recebidas=4,
                                                   ultima_parte_em=timezone.now())
    html = c.get(f'/agenda/transcricoes/{gravando.pk}/').content.decode()
    t('etiqueta "Gravando"', 'Gravando' in html)
    t('cartão da gravação em andamento com as partes', 'Gravação em andamento' in html and '4 partes no servidor' in html)
    t('dono pode encerrar agora e processar', 'Encerrar agora e processar' in html)
    blocos = scripts_embutidos(html, 'pollProcessingStatus')
    t('o polling acompanha a gravação também', blocos and "initialStatus !== 'recording'" in blocos[0])
    if blocos:
        valido, erro = sintaxe_ok(blocos[0])
        t('o JavaScript do detalhe tem sintaxe válida (node --check)', valido, erro)

    processando = MeetingTranscription.objects.create(
        owner=dono, title='ZZ Processando', status='processing', etapa='transcricao', finalizada_automaticamente=True,
        progresso={'total_segmentos': 4, 'segmentos': {'0': 'a'}})
    html = c.get(f'/agenda/transcricoes/{processando.pk}/').content.decode()
    t('mostra a etapa', 'Transcrevendo' in html)
    t('mostra que a gravação foi fechada automaticamente', 'gravação fechada automaticamente' in html)
    t('tem a barra de progresso', 'id="processing-bar"' in html)
    t('sem espera marcada, o aviso de nova tentativa fica escondido',
      'id="processing-retry-banner" class="hidden' in html)
    MeetingTranscription.objects.filter(pk=processando.pk).update(
        proxima_tentativa_em=timezone.now() + timedelta(minutes=4),
        error_message='A tentativa 1 não deu certo: OpenAI fora. O portal tenta de novo sozinho às 10:00.')
    html = c.get(f'/agenda/transcricoes/{processando.pk}/').content.decode()
    t('com espera marcada, o aviso aparece com a mensagem',
      'id="processing-retry-banner" class="mb-4' in html and 'tenta de novo sozinho' in html)
    t('e diz que dá para fechar a página', 'Pode fechar esta página' in html)

    print('\n== SALA DE REUNIÃO: A ATA GRAVA COM O MÓDULO ==')
    from reunioes.models import ConfiguracaoReunioes

    cfg = ConfiguracaoReunioes.get()
    ConfiguracaoReunioes.objects.filter(pk=cfg.pk).update(gerar_ata=True)
    Reuniao.objects.filter(pk=reuniao.pk).update(gravar_ata=True)
    r = c.get(f'/reunioes/{reuniao.pk}/sala/')
    html = r.content.decode()
    t('a sala abre (200)', r.status_code == 200, r.status_code)
    t('carrega o módulo do gravador', 'js/rc-gravador.js' in html)
    t('abre a sessão de gravação no servidor', '/agenda/api/transcricoes/gravacao/iniciar/' in html)
    t('grava o áudio da aba junto com o microfone', "fonte: 'aba-mic'" in html)
    t('desligar a chamada encerra e envia a ata antes de sair', 'antesDeSair().finally' in html)
    t('oferece a gravação guardada ao voltar para a sala', 'oferecerGravacaoGuardada()' in html)
    blocos = scripts_embutidos(html, 'JitsiMeetExternalAPI(servidor')
    t('um script da sala', len(blocos) == 1, len(blocos))
    if blocos:
        valido, erro = sintaxe_ok(blocos[0])
        t('o JavaScript da sala tem sintaxe válida (node --check)', valido, erro)
    t('sem comentário de template vazando', '{#' not in html and '{% comment' not in html)
    ConfiguracaoReunioes.objects.filter(pk=cfg.pk).update(gerar_ata=False)
    html = c.get(f'/reunioes/{reuniao.pk}/sala/').content.decode()
    t('sem ata ligada, a sala nem carrega o gravador', 'rc-gravador.js' not in html and 'gravacao/iniciar' not in html)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
