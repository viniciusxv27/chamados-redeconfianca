"""Transcrição → "Enviar para o Impulso de <pessoa>" nas Tarefas Criadas (16/11/2026).

Depois de escolher o responsável da tarefa, quem participa do Impulso manda a
tarefa para o Impulso daquela pessoa: o formulário de meta do Impulso abre
preenchido com a tarefa (título, descrição, prazo, pessoa) e a meta guarda de
qual tarefa veio. Valem as regras da tela de meta do Impulso: o gestor cria para
qualquer colaborador; os demais, só para quem é de ADM's LOJAS.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import timedelta
from html import unescape

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
# Cache em memória: o `default` é o Redis remoto do dev e limpar ele seria um FLUSHDB do ambiente todo.
import redeconfianca.settings as _cfg  # noqa: E402
_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'teste-{n}'} for n in _cfg.CACHES}
django.setup()

from django.conf import settings
from django.core.cache import caches

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import timezone

from agenda.models import MeetingTranscription
from communications.models import CommunicationGroup
from core.models import TaskActivity
from impulso.models import GRUPO_ADM, GRUPO_ADM_LOJAS, GRUPO_GESTOR, Meta
from impulso.utils import _q_grupo
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


def cartao(html, tarefa):
    """Só o cartão da tarefa nas Tarefas Criadas."""
    marca = f'id="task-card-{tarefa.pk}"'
    if marca not in html:
        return ''
    return html.split(marca, 1)[1].split('id="task-card-', 1)[0]


for nome in settings.CACHES:
    caches[nome].clear()

marcador = transaction.atomic()
marcador.__enter__()
try:
    adm = CommunicationGroup.objects.filter(name__iexact=GRUPO_ADM).first()
    ges = CommunicationGroup.objects.filter(name__iexact=GRUPO_GESTOR).first()
    # Cadastrado como "ADM's LOJAS " (espaço no fim): o Impulso compara sem as pontas.
    lojas = CommunicationGroup.objects.filter(_q_grupo('name', GRUPO_ADM_LOJAS)).first()
    assert adm and ges and lojas, 'grupos do Impulso não encontrados'
    area = Sector.objects.create(name='ZZ Area Enviar')
    loja = Sector.objects.create(name='ZZ Loja Enviar')

    def novo(apelido, nome, setor, grupos=()):
        u = User.objects.create_user(
            username=f'zzenv.{apelido}', email=f'zzenv.{apelido}@exemplo-teste.local',
            password='S3nha!teste', first_name=nome, last_name='Teste', sector=setor)
        for g in grupos:
            u.communication_groups.add(g)
        return u

    gestor = novo('gestor', 'ZZGestor', area, [adm, ges])
    colab = novo('colab', 'ZZColab', area, [adm])
    colega = novo('colega', 'ZZColega', area, [adm])
    adm_loja = novo('admloja', 'ZZAdmLoja', loja, [lojas])
    estranho = novo('estranho', 'ZZEstranho', area, [adm])
    visitante = novo('visita', 'ZZVisita', area)

    agora = timezone.now()
    hoje = timezone.localdate()
    reuniao = MeetingTranscription.objects.create(owner=gestor, title='ZZ Reunião de vendas', status='completed')
    reuniao.shared_with.add(colab, visitante)

    def tarefa(titulo, responsavel, **kw):
        nova = TaskActivity.objects.create(
            title=titulo, assigned_to=responsavel, created_by=gestor, status='PENDING',
            description=f'Contexto: {titulo}.\n\nResponsável mencionado: alguém', **kw)
        reuniao.tasks_created.add(nova)
        return nova

    t_colab = tarefa('ZZ Enviar o relatório de vendas', colab, due_date=agora + timedelta(days=7))
    t_minha = tarefa('ZZ Revisar a escala', gestor)
    t_loja = tarefa('ZZ Conferir o caixa da loja', adm_loja, due_date=agora - timedelta(days=2))
    t_colega = tarefa('ZZ Ligar para o fornecedor', colega)

    c_gestor, c_colab, c_estranho, c_visita = Client(), Client(), Client(), Client()
    c_gestor.force_login(gestor)
    c_colab.force_login(colab)
    c_estranho.force_login(estranho)
    c_visita.force_login(visitante)
    URL = f'/agenda/transcricoes/{reuniao.pk}/'

    def link(tk, pessoa):
        return f'/impulso/metas/nova/?tarefa={tk.pk}&amp;colaborador={pessoa.pk}'

    print('== GESTOR NA TELA DA TRANSCRIÇÃO ==')
    r = c_gestor.get(URL)
    html = r.content.decode()
    t('abre (200)', r.status_code == 200, r.status_code)
    t('tarefa de um colaborador: "Enviar para o Impulso de ZZColab"',
      'Enviar para o Impulso de ZZColab' in cartao(html, t_colab) and link(t_colab, colab) in cartao(html, t_colab))
    t('tarefa de ADM de loja: também aparece para o gestor',
      link(t_loja, adm_loja) in cartao(html, t_loja))
    t('tarefa dele mesmo: sem "Enviar" (o "Importar" cuida)',
      'Enviar para o Impulso' not in cartao(html, t_minha) and 'impulso-importar' in cartao(html, t_minha))
    t('a lista de quem pode receber vai para o script', 'id="impulsoParaIds"' in html)
    t('sem comentário de template vazando', '{#' not in html and '{% comment' not in html)
    blocos = [b for b in re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, flags=re.S)
              if 'impulsoDoResponsavel' in b]
    t('um script monta o botão depois de atribuir', len(blocos) == 1, len(blocos))
    if blocos:
        valido, erro = sintaxe_ok(blocos[0])
        t('o JavaScript da página tem sintaxe válida (node --check)', valido, erro)

    print('\n== FORMULÁRIO DO IMPULSO PREENCHIDO ==')
    r = c_gestor.get(f'/impulso/metas/nova/?tarefa={t_colab.pk}&colaborador={colab.pk}')
    form = r.content.decode()
    t('abre (200)', r.status_code == 200, r.status_code)
    t('leva a tarefa escondida', f'name="tarefa" value="{t_colab.pk}"' in form)
    t('avisa de qual reunião veio', 'ZZ Reunião de vendas' in form)
    t('título preenchido', 'value="ZZ Enviar o relatório de vendas"' in form)
    t('descrição preenchida', 'Contexto: ZZ Enviar o relatório de vendas.' in unescape(form))
    prazo = timezone.localtime(t_colab.due_date).date()
    t('prazo da tarefa', f'value="{prazo:%Y-%m-%d}"' in form)
    t('a pessoa já vem marcada', f'<option value="{colab.pk}" selected>' in form)
    t('o rascunho é só desta tarefa', f':tarefa{t_colab.pk}' in form)

    r = c_gestor.get(f'/impulso/metas/nova/?tarefa={t_loja.pk}&colaborador={adm_loja.pk}')
    form = r.content.decode()
    t('prazo vencido da tarefa não vem preenchido',
      re.search(r'name="prazo"[^>]*value=', form) is None)

    print('\n== CRIAR A META ==')
    corpo = {'colaborador': colab.pk, 'gestor': gestor.pk, 'titulo': t_colab.title,
             'descricao': 'Mandar o relatório', 'prazo': (hoje + timedelta(days=5)).isoformat(),
             'recorrencia': 'UNICA', 'tarefa': t_colab.pk}
    r = c_gestor.post('/impulso/metas/nova/', corpo)
    meta = Meta.objects.filter(tarefa_origem=t_colab, colaborador=colab).first()
    t('cria a meta do colaborador ligada à tarefa', r.status_code == 302 and meta is not None,
      (r.status_code, r.get('Location')))
    t('do jeito da tela do Impulso (gestor da área: aprovada)',
      meta is not None and meta.aprovacao == Meta.Aprovacao.APROVADA and meta.gestor_id == gestor.pk)

    html = c_gestor.get(URL).content.decode()
    t('a transcrição mostra "No Impulso de ZZColab"',
      meta is not None and 'No Impulso de ZZColab' in cartao(html, t_colab)
      and f'/impulso/metas/{meta.pk}/' in cartao(html, t_colab))

    r = c_gestor.get(f'/impulso/metas/nova/?tarefa={t_colab.pk}&colaborador={colab.pk}')
    t('abrir de novo leva à meta que já existe', meta is not None and r.status_code == 302
      and r['Location'] == f'/impulso/metas/{meta.pk}/', (r.status_code, r.get('Location')))
    r = c_gestor.post('/impulso/metas/nova/', corpo)
    t('enviar de novo não duplica', Meta.objects.filter(tarefa_origem=t_colab, colaborador=colab).count() == 1)

    Meta.objects.filter(pk=meta.pk).update(aprovacao=Meta.Aprovacao.RECUSADA)
    html = c_gestor.get(URL).content.decode()
    t('meta recusada: o botão de enviar volta', link(t_colab, colab) in cartao(html, t_colab))

    print('\n== ERRO NO FORMULÁRIO NÃO PERDE A TAREFA ==')
    ruim = dict(corpo, prazo=(hoje - timedelta(days=1)).isoformat())
    r = c_gestor.post('/impulso/metas/nova/', ruim)
    t('volta ao formulário com a tarefa', r.status_code == 302
      and f'tarefa={t_colab.pk}' in r['Location'] and f'colaborador={colab.pk}' in r['Location'],
      r.get('Location'))

    print('\n== COLABORADOR (NÃO GESTOR) ==')
    html = c_colab.get(URL).content.decode()
    t('para ADM de loja: aparece', link(t_loja, adm_loja) in cartao(html, t_loja))
    t('para colega do setor: não aparece (o Impulso não deixa)',
      'Enviar para o Impulso' not in cartao(html, t_colega))
    r = c_colab.get(f'/impulso/metas/nova/?tarefa={t_loja.pk}&colaborador={adm_loja.pk}')
    form = r.content.decode()
    t('o formulário marca o ADM de loja', re.search(
        rf'<option value="{adm_loja.pk}"[^>]*selected', form) is not None)
    corpo_loja = {'colaborador': adm_loja.pk, 'gestor_aprovador': gestor.pk, 'titulo': t_loja.title,
                  'descricao': 'Conferir', 'prazo': (hoje + timedelta(days=3)).isoformat(),
                  'recorrencia': 'UNICA', 'tarefa': t_loja.pk}
    r = c_colab.post('/impulso/metas/nova/', corpo_loja)
    m_loja = Meta.objects.filter(tarefa_origem=t_loja, colaborador=adm_loja).first()
    t('cria para o ADM de loja, esperando o gestor aprovar',
      m_loja is not None and m_loja.aprovacao == Meta.Aprovacao.PENDENTE, (r.status_code, r.get('Location')))

    print('\n== QUEM NÃO VÊ A REUNIÃO ==')
    r = c_estranho.get(f'/impulso/metas/nova/?tarefa={t_colega.pk}&colaborador={colega.pk}')
    form = r.content.decode()
    t('formulário abre vazio, sem a tarefa', r.status_code == 200
      and 'name="tarefa"' not in form and 'ZZ Ligar para o fornecedor' not in form)
    r = c_estranho.post('/impulso/metas/nova/', {
        'titulo': 'ZZ meta do estranho', 'descricao': 'x', 'prazo': (hoje + timedelta(days=2)).isoformat(),
        'recorrencia': 'UNICA', 'gestor': gestor.pk, 'precisa_aprovacao': 'sim', 'tarefa': t_colega.pk})
    m_est = Meta.objects.filter(titulo='ZZ meta do estranho').first()
    t('tarefa de reunião alheia não fica ligada', m_est is None or m_est.tarefa_origem_id is None,
      m_est and m_est.tarefa_origem_id)

    html = c_visita.get(URL).content.decode()
    # O script da página cita o texto para todo mundo; o que não pode existir é o botão.
    t('quem não é do Impulso não vê o botão', 'data-impulso-pessoa="' not in html
      and 'id="impulsoParaIds"' not in html and re.search(r'/impulso/metas/nova/\?tarefa=\d', html) is None)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
