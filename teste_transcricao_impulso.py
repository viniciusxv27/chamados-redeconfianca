"""Transcrição → "Importar para o Impulso" nas Tarefas Criadas.

Quem participa do Impulso vê o botão em cada tarefa e importa a tarefa como meta
para si, com o gestor que escolher — as mesmas regras de criar meta pela tela do
Impulso (colaborador: gestor do setor dele e escolhe se precisa de aprovação;
gestor: qualquer gestor e já aprovada).

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import json
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

from agenda.models import MeetingTranscription
from communications.models import CommunicationGroup
from core.models import Notification, TaskActivity
from impulso.models import GRUPO_ADM, GRUPO_GESTOR, Meta
from users.models import Sector

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


def janela(html):
    """Só a janela de importar (a página tem outros selects com as mesmas pessoas)."""
    if 'id="impulso-modal"' not in html:
        return ''
    return html.split('id="impulso-modal"', 1)[1].split('</form>', 1)[0]


marcador = transaction.atomic()
marcador.__enter__()
try:
    adm = CommunicationGroup.objects.filter(name__iexact=GRUPO_ADM).first()
    ges = CommunicationGroup.objects.filter(name__iexact=GRUPO_GESTOR).first()
    assert adm and ges, 'grupos do Impulso não encontrados'
    area = Sector.objects.create(name='ZZ Area Importar')
    outra = Sector.objects.create(name='ZZ Outra Area Importar')

    def novo(apelido, nome, setor, grupos=()):
        u = User.objects.create_user(
            username=f'zzimp.{apelido}', email=f'zzimp.{apelido}@exemplo-teste.local',
            password='S3nha!teste', first_name=nome, last_name='Teste', sector=setor)
        for g in grupos:
            u.communication_groups.add(g)
        return u

    dono = novo('dono', 'ZZDono', area, [adm])
    gestor = novo('gestor', 'ZZGestor', area, [adm, ges])
    gestor_fora = novo('gestorfora', 'ZZGestorFora', outra, [adm, ges])
    chefe = novo('chefe', 'ZZChefe', outra, [adm, ges])
    visitante = novo('visita', 'ZZVisita', area)
    estranho = novo('estranho', 'ZZEstranho', area, [adm])

    agora = timezone.now()
    hoje = timezone.localdate()
    reuniao = MeetingTranscription.objects.create(owner=dono, title='ZZ Reunião de alinhamento', status='completed')
    reuniao.shared_with.add(visitante, chefe)

    def tarefa(titulo, **kw):
        nova = TaskActivity.objects.create(
            title=titulo, assigned_to=dono, created_by=dono, status='PENDING',
            description='Tarefa gerada automaticamente da transcrição: ZZ Reunião de alinhamento\n\n'
                        'Responsável mencionado: ZZDono', **kw)
        reuniao.tasks_created.add(nova)
        return nova

    t_relatorio = tarefa('ZZ Enviar o relatório de vendas', priority='HIGH', due_date=agora + timedelta(days=7))
    t_escala = tarefa('ZZ Revisar a escala da loja')
    t_fornecedor = tarefa('ZZ Ligar para o fornecedor', due_date=agora - timedelta(days=3))

    reuniao_alheia = MeetingTranscription.objects.create(owner=estranho, title='ZZ Reunião alheia', status='completed')
    t_alheia = TaskActivity.objects.create(title='ZZ Tarefa alheia', description='x',
                                           assigned_to=estranho, created_by=estranho)
    reuniao_alheia.tasks_created.add(t_alheia)

    c_dono, c_visita, c_chefe, c_estranho = Client(), Client(), Client(), Client()
    c_dono.force_login(dono)
    c_visita.force_login(visitante)
    c_chefe.force_login(chefe)
    c_estranho.force_login(estranho)

    URL_DETALHE = f'/agenda/transcricoes/{reuniao.pk}/'

    def url_importar(tk):
        return f'/agenda/api/transcricoes/{reuniao.pk}/tasks/{tk.pk}/impulso/'

    def importar(cliente, tk, **dados):
        corpo = {'titulo': tk.title, 'descricao': tk.description,
                 'prazo': (hoje + timedelta(days=5)).isoformat(),
                 'gestor': gestor.id, 'precisa_aprovacao': 'sim'}
        corpo.update(dados)
        return cliente.post(url_importar(tk), data=json.dumps(corpo), content_type='application/json')

    print('== A TELA DA TRANSCRIÇÃO ==')
    r = c_dono.get(URL_DETALHE)
    html = r.content.decode()
    modal = janela(html)
    t('abre (200)', r.status_code == 200, r.status_code)
    t('cada tarefa tem o botão de importar', html.count('class="impulso-importar') == 3,
      html.count('class="impulso-importar'))
    t('o botão leva a tarefa, o título e o prazo', f'data-task="{t_relatorio.pk}"' in html
      and 'data-titulo="ZZ Enviar o relatório de vendas"' in html
      and f'data-prazo="{timezone.localtime(t_relatorio.due_date):%Y-%m-%d}"' in html)
    t('tem a janela de importar', bool(modal))
    t('o colaborador escolhe entre os gestores do setor dele', f'<option value="{gestor.id}">' in modal)
    t('gestor de outro setor não aparece para ele', f'value="{gestor_fora.id}"' not in modal)
    t('e decide se precisa da aprovação (padrão: sim)',
      'Precisa da autorização do gestor?' in modal and 'value="sim" checked' in modal)
    t('o prazo não aceita data passada', f'min="{hoje:%Y-%m-%d}"' in modal)
    t('sem comentário de template vazando', '{#' not in html and '{% comment' not in html)
    blocos = [b for b in re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, flags=re.S)
              if 'impulso-modal' in b]
    t('um script cuida da importação', len(blocos) == 1, len(blocos))
    if blocos:
        valido, erro = sintaxe_ok(blocos[0])
        t('o JavaScript da página tem sintaxe válida (node --check)', valido, erro)

    html = c_visita.get(URL_DETALHE).content.decode()
    # O script da página cita a classe para todo mundo; o que não pode existir é o botão.
    t('quem não participa do Impulso não vê o botão',
      'class="impulso-importar' not in html and 'id="impulso-modal"' not in html)
    t('mas continua vendo as tarefas', 'ZZ Enviar o relatório de vendas' in html)

    html = c_chefe.get(URL_DETALHE).content.decode()
    modal_chefe = janela(html)
    t('gestor do Impulso também vê o botão', 'class="impulso-importar' in html)
    t('e escolhe qualquer gestor, começando por ele mesmo',
      f'<option value="{chefe.id}" selected>' in modal_chefe and '(você)' in modal_chefe
      and f'<option value="{gestor.id}">' in modal_chefe)
    t('sem a pergunta da aprovação (a meta dele já vale)',
      'Precisa da autorização do gestor?' not in modal_chefe and 'Como gestor do Impulso' in modal_chefe)

    print('\n== IMPORTAR COM APROVAÇÃO ==')
    r = importar(c_dono, t_relatorio)
    j = r.json() if r.status_code < 500 else {}
    meta = Meta.objects.filter(tarefa_origem=t_relatorio, colaborador=dono).first()
    t('importa (200)', r.status_code == 200 and j.get('ok'), r.content[:300])
    t('vira meta da própria pessoa', meta is not None and meta.colaborador_id == dono.id)
    t('com o gestor escolhido', meta is not None and meta.gestor_id == gestor.id)
    t('aguardando a aprovação dele',
      meta is not None and meta.aprovacao == Meta.Aprovacao.PENDENTE and j.get('aprovada') is False)
    t('pedida e criada pela própria pessoa',
      meta is not None and meta.solicitada_por_id == dono.id and meta.created_by_id == dono.id)
    t('com o título, a descrição e o prazo escolhidos', meta is not None
      and meta.titulo == t_relatorio.title and meta.descricao == t_relatorio.description
      and meta.prazo == hoje + timedelta(days=5))
    t('de única vez', meta is not None and meta.recorrencia == Meta.Recorrencia.UNICA)
    t('a resposta aponta para a meta', meta is not None and j.get('url') == f'/impulso/metas/{meta.id}/')
    t('o gestor é avisado para aprovar',
      Notification.objects.filter(user=gestor, title='Nova solicitação de meta').exists())

    r = importar(c_dono, t_relatorio)
    t('importar de novo a mesma tarefa: recusado (409)', r.status_code == 409
      and meta is not None and r.json().get('url') == f'/impulso/metas/{meta.id}/', r.status_code)
    t('e não cria outra meta', Meta.objects.filter(tarefa_origem=t_relatorio, colaborador=dono).count() == 1)

    html = c_dono.get(URL_DETALHE).content.decode()
    t('a tela troca o botão pelo link da meta', meta is not None
      and f'href="/impulso/metas/{meta.id}/"' in html and 'aguardando o gestor aprovar' in html)
    t('só na tarefa importada', f'data-task="{t_relatorio.pk}"' not in html and f'data-task="{t_escala.pk}"' in html)

    print('\n== IMPORTAR SEM APROVAÇÃO ==')
    r = importar(c_dono, t_escala, precisa_aprovacao='nao')
    meta_escala = Meta.objects.filter(tarefa_origem=t_escala, colaborador=dono).first()
    t('"é uma atividade minha": entra aprovada', r.status_code == 200 and meta_escala is not None
      and meta_escala.aprovacao == Meta.Aprovacao.APROVADA, r.content[:300])
    t('o gestor fica sabendo',
      Notification.objects.filter(user=gestor, title='Nova atividade criada pelo colaborador').exists())
    from impulso.views import _metas_do_usuario
    t('e a meta está no Kanban da pessoa', meta_escala is not None
      and meta_escala.id in set(_metas_do_usuario(dono).values_list('id', flat=True)))
    html = c_dono.get(URL_DETALHE).content.decode()
    t('a tela mostra o link para ver a meta', meta_escala is not None
      and f'href="/impulso/metas/{meta_escala.id}/"' in html and 'No Impulso — ver a meta' in html)

    print('\n== VALIDAÇÕES ==')
    antes = Meta.objects.count()
    r = importar(c_dono, t_fornecedor, gestor=gestor_fora.id)
    t('gestor de outro setor: recusado', r.status_code == 400 and 'gestor do seu setor' in r.json().get('error', ''),
      r.content[:200])
    r = importar(c_dono, t_fornecedor, prazo=(hoje - timedelta(days=1)).isoformat())
    t('prazo no passado: recusado', r.status_code == 400 and 'anterior a hoje' in r.json().get('error', ''))
    r = importar(c_dono, t_fornecedor, prazo='')
    t('sem prazo: recusado', r.status_code == 400 and 'prazo' in r.json().get('error', ''))
    r = importar(c_dono, t_fornecedor, prazo='2026-02-30')
    t('data inexistente: recusada sem erro 500', r.status_code == 400, r.status_code)
    r = importar(c_dono, t_fornecedor, titulo='   ')
    t('sem título: recusado', r.status_code == 400 and 'título' in r.json().get('error', ''))
    r = c_dono.post(url_importar(t_fornecedor), data='não é json', content_type='application/json')
    t('JSON inválido: recusado', r.status_code == 400, r.status_code)
    t('nada foi criado nas recusas', Meta.objects.count() == antes)

    print('\n== QUEM PODE ==')
    r = importar(c_visita, t_fornecedor)
    t('quem não participa do Impulso não importa (403)', r.status_code == 403, r.status_code)
    r = importar(c_estranho, t_fornecedor)
    t('quem não enxerga a transcrição não importa (404)', r.status_code == 404, r.status_code)
    r = importar(c_dono, t_alheia)
    t('tarefa de outra transcrição: 404', r.status_code == 404, r.status_code)
    r = importar(Client(), t_fornecedor)
    t('sem login: 401 em JSON', r.status_code == 401, r.status_code)
    t('GET não importa (405)', c_dono.get(url_importar(t_fornecedor)).status_code == 405)
    t('nada foi criado', Meta.objects.count() == antes)

    print('\n== GESTOR DO IMPULSO IMPORTANDO PARA SI ==')
    r = importar(c_chefe, t_relatorio, gestor=gestor_fora.id)
    meta_chefe = Meta.objects.filter(tarefa_origem=t_relatorio, colaborador=chefe).first()
    t('a meta é dele, com o gestor que ele escolheu', r.status_code == 200 and meta_chefe is not None
      and meta_chefe.gestor_id == gestor_fora.id, r.content[:300])
    t('já entra aprovada, sem pedido', meta_chefe is not None
      and meta_chefe.aprovacao == Meta.Aprovacao.APROVADA and meta_chefe.solicitada_por_id is None)
    t('o gestor escolhido é avisado',
      Notification.objects.filter(user=gestor_fora, title='Meta criada no seu nome').exists())
    t('cada pessoa importa a sua: a do colaborador continua lá',
      Meta.objects.filter(tarefa_origem=t_relatorio).count() == 2)
    avisos_antes = Notification.objects.filter(user=chefe).count()
    r = importar(c_chefe, t_fornecedor, gestor=chefe.id)
    t('gestor de si mesmo: aprovada e sem aviso para ele mesmo', r.status_code == 200
      and Meta.objects.filter(tarefa_origem=t_fornecedor, colaborador=chefe, gestor=chefe,
                              aprovacao=Meta.Aprovacao.APROVADA).exists()
      and Notification.objects.filter(user=chefe).count() == avisos_antes, r.content[:300])

    print('\n== RECUSADA, PODE IMPORTAR DE NOVO ==')
    Meta.objects.filter(pk=meta.pk).update(aprovacao=Meta.Aprovacao.RECUSADA)
    html = c_dono.get(URL_DETALHE).content.decode()
    t('com a meta recusada, o botão volta', f'data-task="{t_relatorio.pk}"' in html)
    r = importar(c_dono, t_relatorio)
    t('e dá para importar de novo', r.status_code == 200
      and Meta.objects.filter(tarefa_origem=t_relatorio, colaborador=dono).count() == 2, r.content[:300])

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
