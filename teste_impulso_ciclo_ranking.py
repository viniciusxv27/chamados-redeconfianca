"""Impulso: encerrar o mês e o ranking que se abre em seguida.

Pedidos: "não estou conseguindo encerrar o mês, devo encerrar e ter um botão
'Ver ranking', deve ser algo animado, moderno, intuitivo e dinâmico quando abre
a página e compartilhável para mandar".

O que impedia de encerrar: `mes_fechar` exigia **gestor do Impulso**, e vários
SUPERADMIN do portal não estão no grupo GESTORES (IMPULSO) — para eles o botão
nem aparecia na tela do ciclo, e o POST direto era recusado.

O que este teste cobre:

- SUPERADMIN encerra e reabre mês, encerra ciclo e vê os botões na tela;
- quem não é nem gestor nem SUPERADMIN continua de fora;
- fechar o mês leva direto para o ranking;
- o ranking: pódio, ordem com o mesmo desempate do ranking ao vivo, botão de
  compartilhar e de baixar a imagem;
- quem pediu menos movimento recebe a tela parada.

Roda dentro de uma transação desfeita.
"""
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from datetime import date
from decimal import Decimal

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-imp-ciclo'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-imp-ciclo2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client

from communications.models import CommunicationGroup
from impulso.models import Ciclo, CicloMes, PontuacaoMensal
from impulso.utils import is_impulso_manager, pode_gerir_ciclos
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
    setor = Sector.objects.create(name='ZZ Setor Ciclo')
    # De propósito sem is_superuser: é o SUPERADMIN por hierarquia que estava
    # barrado, e é ele que o teste precisa representar.
    chefe = User.objects.create_user(
        username='zzcr.chefe', email='zzcr.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN', sector=setor)
    comum = User.objects.create_user(
        username='zzcr.comum', email='zzcr.comum@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Comum', hierarchy='PADRAO', sector=setor)
    adm, _ = CommunicationGroup.objects.get_or_create(name='ESCRITÓRIO (ADM)',
                                                      defaults={'created_by': chefe})
    for u in (chefe, comum):
        u.communication_groups.add(adm)

    t('o caso do pedido existe: SUPERADMIN que não é gestor do Impulso',
      not is_impulso_manager(chefe) and pode_gerir_ciclos(chefe))
    t('e quem não é nem um nem outro continua de fora', not pode_gerir_ciclos(comum))

    ciclo = Ciclo.objects.create(nome='ZZ Ciclo do teste', inicio=date(2026, 9, 1),
                                 fim=date(2026, 9, 30), criado_por=chefe)
    mes = CicloMes.objects.create(ciclo=ciclo, referencia=date(2026, 9, 1))

    c = Client(); c.force_login(chefe)
    cc = Client(); cc.force_login(comum)

    print('== ENCERRAR O MÊS ==')
    html = c.get(f'/impulso/ciclos/{ciclo.id}/').content.decode()
    t('o SUPERADMIN vê o botão de finalizar na tela do ciclo',
      f'/impulso/ciclos/mes/{mes.id}/fechar/' in html)

    r = c.post(f'/impulso/ciclos/mes/{mes.id}/fechar/')
    mes.refresh_from_db()
    t('e consegue finalizar', mes.is_fechado, (r.status_code, r.get('Location')))
    t('indo direto para o ranking', r.status_code == 302
      and r['Location'] == f'/impulso/ciclos/mes/{mes.id}/ranking/', r.get('Location'))
    t('com a pontuação congelada de quem é do Impulso',
      PontuacaoMensal.objects.filter(mes=mes).count() >= 2,
      PontuacaoMensal.objects.filter(mes=mes).count())

    t('quem não pode continua sem conseguir',
      cc.post(f'/impulso/ciclos/mes/{mes.id}/reabrir/').status_code == 302
      and CicloMes.objects.get(pk=mes.pk).is_fechado)

    r = c.post(f'/impulso/ciclos/mes/{mes.id}/reabrir/')
    mes.refresh_from_db()
    t('o SUPERADMIN também reabre', not mes.is_fechado)
    c.post(f'/impulso/ciclos/mes/{mes.id}/fechar/')
    mes.refresh_from_db()

    r = c.post(f'/impulso/ciclos/{ciclo.id}/encerrar/')
    ciclo.refresh_from_db()
    t('e encerra o ciclo', not ciclo.is_aberto, r.status_code)

    print('\n== O RANKING ==')
    r = c.get(f'/impulso/ciclos/mes/{mes.id}/ranking/')
    t('a tela abre', r.status_code == 200)
    linhas = r.context['linhas']
    t('com todo mundo que pontuou', len(linhas) == PontuacaoMensal.objects.filter(mes=mes).count())
    t('o pódio são os três primeiros', r.context['podio'] == linhas[:3])
    t('e cada linha sabe a própria posição',
      [l['posicao'] for l in linhas] == list(range(1, len(linhas) + 1)))
    t('a ordem usa o mesmo desempate do ranking ao vivo',
      all('desempate' in l['dados'] for l in linhas))

    html = r.content.decode()
    t('a tela mostra o mês e o ciclo',
      'Setembro de 2026' in html and 'ZZ Ciclo do teste' in html)
    t('tem pódio animado', 'rk-coluna' in html and 'rk-medalha' in html)
    t('os números sobem na frente de quem olha', 'data-valor' in html and 'requestAnimationFrame' in html)
    t('dá para compartilhar', 'rkCompartilhar' in html and 'navigator.share' in html)
    t('e para baixar a imagem pronta', 'rkImagem' in html and 'toBlob' in html)
    t('a imagem leva só o pódio',
      len(r.context['dados_json']) == min(3, len(linhas))
      and all({'nome', 'iniciais', 'pct', 'faixa'} == set(d) for d in r.context['dados_json']))
    t('quem pediu menos movimento recebe a tela parada',
      'prefers-reduced-motion' in html and 'matchMedia' in html)
    t('o colaborador comum também vê o ranking (é para mostrar, não esconder)',
      cc.get(f'/impulso/ciclos/mes/{mes.id}/ranking/').status_code == 200)

    t('a tela do mês leva ao ranking',
      f'/impulso/ciclos/mes/{mes.id}/ranking/' in c.get(f'/impulso/ciclos/mes/{mes.id}/').content.decode())
    t('e a do ciclo também',
      f'/impulso/ciclos/mes/{mes.id}/ranking/' in c.get(f'/impulso/ciclos/{ciclo.id}/').content.decode())

    limpo = re.sub(r'\{%.*?%\}', 'tag', html, flags=re.S)
    limpo = re.sub(r'\{\{.*?\}\}', 'var', limpo, flags=re.S)
    blocos = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', limpo, flags=re.S)
    erros = []
    for bloco in blocos:
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as tmp:
            tmp.write(bloco)
            caminho = tmp.name
        saida = subprocess.run(['node', '--check', caminho], capture_output=True, text=True)
        if saida.returncode != 0:
            erros.append(saida.stderr[:300])
        os.unlink(caminho)
    t('o JavaScript da tela passa no node --check', not erros, erros[:1])
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
