"""Liberação de setor na contestação + idioma e logo da sala de reuniões.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import sys
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

from communications.models import CommunicationGroup
from contestacao.models import LiberacaoContestacao
from contestacao.views import _get_sync_window_state
from reunioes.models import Reuniao
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


marcador = transaction.atomic()
marcador.__enter__()
try:
    loja = Sector.objects.create(name='ZZ Loja Liberada')
    outra = Sector.objects.create(name='ZZ Loja Normal')

    def novo(username, setor, **kw):
        return User.objects.create_user(
            username=username, email=f'{username}@exemplo-teste.local',
            password='S3nha!teste', first_name=username.split('.')[1].title(),
            last_name='Teste', sector=setor, **kw)

    liberado = novo('lb.liberado', loja)
    bloqueado = novo('lb.bloqueado', outra)
    # Quem contesta é o gerente da loja: sem o grupo GERENTES o módulo nem abre.
    gerentes = CommunicationGroup.objects.filter(name__iexact='GERENTES').first()
    assert gerentes, 'grupo GERENTES não encontrado'
    liberado.communication_groups.add(gerentes)
    bloqueado.communication_groups.add(gerentes)
    chefe = novo('lb.chefe', outra, hierarchy='SUPERADMIN')
    # Quem responde por duas lojas: a liberação de uma tem de valer para ele.
    duas_lojas = novo('lb.duaslojas', outra)
    duas_lojas.sectors.add(loja)

    print('== A JANELA GERAL ESTÁ FECHADA? ==')
    geral = _get_sync_window_state(None)
    t('estado geral calculado', 'is_blocked' in geral)
    print(f'   (janela geral: bloqueada={geral["is_blocked"]}, '
          f'restante={geral["remaining_label"]})')

    print('\n== LIBERAÇÃO DE UM SETOR ==')
    amanha = timezone.now() + timedelta(days=1)
    lib = LiberacaoContestacao.objects.create(
        setor=loja, prazo=amanha, motivo='ZZ Refazer julho', criado_por=chefe)
    t('liberação nasce vigente', lib.vigente)
    t('alcança quem é do setor', LiberacaoContestacao.para_usuario(liberado) == lib)
    t('não alcança quem é de outro setor',
      LiberacaoContestacao.para_usuario(bloqueado) is None)
    t('alcança quem tem a loja como setor secundário',
      LiberacaoContestacao.para_usuario(duas_lojas) == lib)

    e_lib = _get_sync_window_state(liberado)
    e_blo = _get_sync_window_state(bloqueado)
    t('o setor liberado pode contestar', e_lib['is_blocked'] is False, e_lib)
    t('o prazo mostrado é o da liberação', e_lib['sync_deadline_at'] == amanha)
    t('a tela sabe qual liberação está valendo', e_lib.get('liberacao') == lib)
    t('quem não foi liberado segue como estava',
      e_blo['is_blocked'] == geral['is_blocked'])
    t('e sem liberação nenhuma', e_blo.get('liberacao') is None)

    print('\n== PRAZO VENCIDO E REVOGAÇÃO ==')
    lib.prazo = timezone.now() - timedelta(minutes=1)
    lib.save(update_fields=['prazo'])
    t('liberação vencida não vale mais', not lib.vigente)
    t('e o setor volta a ficar como o resto',
      _get_sync_window_state(liberado)['is_blocked'] == geral['is_blocked'])

    lib.prazo = amanha
    lib.ativa = False
    lib.save(update_fields=['prazo', 'ativa'])
    t('liberação revogada não vale', not lib.vigente
      and LiberacaoContestacao.para_usuario(liberado) is None)

    lib.ativa = True
    lib.save(update_fields=['ativa'])

    print('\n== A TELA DE LIBERAÇÃO ==')
    cc = Client()
    cc.force_login(liberado)
    r = cc.get('/contestacao/liberar-setor/', follow=True)
    t('quem não administra não abre a tela',
      'Só quem administra' in r.content.decode())

    ca = Client()
    ca.force_login(chefe)
    r = ca.get('/contestacao/liberar-setor/')
    t('o administrador abre', r.status_code == 200, r.status_code)
    html = r.content.decode()
    t('a liberação em vigor aparece', 'ZZ Loja Liberada' in html)
    t('o motivo aparece', 'ZZ Refazer julho' in html)

    depois = timezone.localtime(timezone.now() + timedelta(days=2))
    ca.post('/contestacao/liberar-setor/', {
        'setor': outra.id, 'prazo': depois.strftime('%Y-%m-%dT%H:%M'),
        'motivo': 'ZZ Segunda liberação'}, follow=True)
    t('o administrador cria liberação',
      LiberacaoContestacao.objects.filter(setor=outra, ativa=True).exists())

    r = ca.post('/contestacao/liberar-setor/', {
        'setor': outra.id,
        'prazo': (timezone.localtime(timezone.now()) - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
        'motivo': 'ZZ No passado'}, follow=True)
    t('prazo no passado é recusado', 'precisa ser no futuro' in r.content.decode())
    t('e não cria nada',
      not LiberacaoContestacao.objects.filter(motivo='ZZ No passado').exists())

    nova = LiberacaoContestacao.objects.filter(setor=outra, ativa=True).first()
    ca.post('/contestacao/liberar-setor/',
            {'acao': 'revogar', 'liberacao': nova.id}, follow=True)
    nova.refresh_from_db()
    t('o administrador revoga', not nova.ativa)

    r = cc.post('/contestacao/liberar-setor/', {
        'setor': loja.id, 'prazo': depois.strftime('%Y-%m-%dT%H:%M')}, follow=True)
    t('quem não administra não cria liberação',
      not LiberacaoContestacao.objects.filter(setor=loja).exclude(id=lib.id).exists())

    print('\n== A LISTA AVISA QUEM FOI LIBERADO ==')
    html = cc.get('/contestacao/').content.decode()
    t('a lista abre para o liberado', 'Contestação' in html or 'Exclus' in html)
    t('mostra a liberação especial', 'Liberação especial' in html, )
    t('e cita o setor', 'ZZ Loja Liberada' in html)
    html_blo = Client()
    html_blo.force_login(bloqueado)
    t('quem não foi liberado não vê o aviso',
      'Liberação especial' not in html_blo.get('/contestacao/').content.decode())

    print('\n== SALA: IDIOMA E LOGO ==')
    dono = novo('lb.dono', outra, hierarchy='SUPERADMIN')
    reuniao = Reuniao.objects.create(
        titulo='ZZ Sala idioma', inicio=timezone.now(),
        fim=timezone.now() + timedelta(hours=1), organizador=dono)
    cd = Client()
    cd.force_login(dono)
    html = cd.get(f'/reunioes/{reuniao.id}/sala/').content.decode()
    t('a sala pede português do Brasil', "defaultLanguage: 'ptBR'" in html)
    t('não deixa o navegador escolher o idioma', 'LANG_DETECTION: false' in html)
    t('aponta a identidade visual do portal', 'dynamicBrandingUrl' in html
      and '/reunioes/branding.json' in html)
    t('usa a nossa marca d\'água', 'SHOW_JITSI_WATERMARK: true' in html
      and 'logo-t.png' in html)
    t('a tela de carregamento também mostra a logo',
      'alt="Rede Confiança"' in html)

    print('\n== O ARQUIVO DE IDENTIDADE VISUAL ==')
    r = Client().get('/reunioes/branding.json')
    t('é público (quem busca é o Jitsi, sem sessão)', r.status_code == 200, r.status_code)
    t('libera a leitura de outro domínio',
      r.headers.get('Access-Control-Allow-Origin') == '*')
    dados = r.json()
    t('traz a logo', 'logo' in (dados.get('logoImageUrl') or ''))
    t('traz o fundo', 'logo' in (dados.get('backgroundImageUrl') or ''))
    t('as URLs são absolutas',
      (dados.get('logoImageUrl') or '').startswith('http')
      and (dados.get('backgroundImageUrl') or '').startswith('http'))
    t('não vaza nada além das imagens',
      set(dados) <= {'logoImageUrl', 'logoClickUrl', 'backgroundImageUrl',
                     'backgroundColor', 'didPageUrl'}, set(dados))

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
