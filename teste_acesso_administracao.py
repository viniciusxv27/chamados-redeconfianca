"""Hierarquia ADMINISTRAÇÃO gerindo os módulos de pessoal.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import sys

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

from tangerino.models import ConfiguracaoTangerino
from users.models import Sector

User = get_user_model()
ok = fail = 0

# Telas de gestão das áreas pedidas. A escala fica de fora desta lista: ela
# abre para todo mundo de propósito (o colaborador entra para ver a própria),
# e o que a ADMINISTRAÇÃO ganha ali é gerenciar — testado à parte.
TELAS = [
    ('/folha-ponto/admin/', 'folha de ponto — painel'),
    ('/folha-ponto/admin/importar/', 'folha de ponto — importar'),
    ('/documentos/admin/', 'documentos — painel'),
    ('/documentos/admin/novo/', 'documentos — novo documento'),
    ('/contracheque/admin/', 'contracheque — painel'),
    ('/contracheque/admin/importar/', 'contracheque — importar'),
    ('/contracheque/admin/informes/', 'contracheque — informes'),
    ('/ponto/equipe/', 'ponto da equipe'),
    ('/ferias/equipe/', 'férias da equipe'),
]


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def abriu(cliente, url):
    """Chegou na tela pedida — sem ter sido desviada para outra.

    Com follow=True o status final é sempre 200 (a página de destino do
    desvio), então quem responde é o redirect_chain: cadeia vazia significa
    que a tela abriu de fato.
    """
    r = cliente.get(url, follow=True)
    corpo = r.content.decode(errors='replace')
    negou = any(m in corpo for m in (
        'não tem permissão', 'Apenas superusuários', 'Apenas o SUPERADMIN',
        'Apenas administradores', 'não tem acesso'))
    return (r.status_code == 200 and not r.redirect_chain and not negou,
            r.status_code, r.redirect_chain)


marcador = transaction.atomic()
marcador.__enter__()
try:
    area = Sector.objects.create(name='ZZ Area Acesso')

    def novo(username, **kw):
        return User.objects.create_user(
            username=username, email=f'{username}@exemplo-teste.local',
            password='S3nha!teste', sector=area,
            first_name=username.split('.')[1].title(), last_name='Teste', **kw)

    administracao = novo('ac.admin', hierarchy='ADMIN')
    padrao = novo('ac.padrao', hierarchy='PADRAO')
    administrativo = novo('ac.administrativo', hierarchy='ADMINISTRATIVO')
    supervisor = novo('ac.supervisor', hierarchy='SUPERVISOR')
    chefe = novo('ac.chefe', hierarchy='SUPERADMIN', is_superuser=True, is_staff=True)

    print('== A REGRA CENTRAL ==')
    t('ADMINISTRAÇÃO administra os módulos de pessoal', administracao.can_manage_rh())
    t('SUPERADMIN continua administrando', chefe.can_manage_rh())
    t('PADRÃO não', not padrao.can_manage_rh())
    t('ADMINISTRATIVO não (é outra hierarquia)', not administrativo.can_manage_rh())
    t('SUPERVISOR não', not supervisor.can_manage_rh())

    print('\n== AS TELAS DE GESTÃO ==')
    ca = Client(); ca.force_login(administracao)
    for url, rotulo in TELAS:
        passou, status, chain = abriu(ca, url)
        t(f'ADMINISTRAÇÃO abre {rotulo}', passou, f'{status} {chain}')

    print('\n== E QUEM NÃO É CONTINUA FORA ==')
    cp = Client(); cp.force_login(padrao)
    for url, rotulo in TELAS:
        passou, status, chain = abriu(cp, url)
        t(f'PADRÃO não abre {rotulo}', not passou, f'{status} {chain}')

    print('\n== ESCALA: ABRE PARA TODOS, GERE SÓ QUEM PODE ==')
    from tangerino import escala as escala_svc

    passou, status, chain = abriu(ca, '/ponto/escala/')
    t('ADMINISTRAÇÃO abre a escala', passou, f'{status} {chain}')
    passou, status, chain = abriu(cp, '/ponto/escala/')
    t('PADRÃO também abre (vê a dele)', passou, f'{status} {chain}')

    t('ADMINISTRAÇÃO gere a escala', escala_svc.pode_gerenciar(administracao))
    t('e enxerga todos os setores', escala_svc.e_gestor_global(administracao))
    t('PADRÃO não gere', not escala_svc.pode_gerenciar(padrao))
    t('e não enxerga todos os setores', not escala_svc.e_gestor_global(padrao))

    # O template resolve a rota, então o que aparece no HTML é o caminho.
    html = ca.get('/ponto/escala/').content.decode()
    t('a tela da ADMINISTRAÇÃO traz o formulário de salvar',
      '/ponto/escala/salvar/' in html)
    html = cp.get('/ponto/escala/').content.decode()
    t('a do PADRÃO não', '/ponto/escala/salvar/' not in html)

    r = cp.post('/ponto/escala/salvar/', {})
    t('e o servidor recusa o PADRÃO salvando', r.status_code in (302, 403), r.status_code)

    print('\n== O QUE NÃO FOI PEDIDO CONTINUA FECHADO ==')
    r = ca.get('/ponto/configuracao/', follow=True)
    t('configuração do módulo de ponto segue só com superusuário',
      'Apenas superusuários' in r.content.decode())
    # A edição de cadastro foi reaberta para a ADMINISTRAÇÃO depois, com a
    # trava de hierarquia — ver teste_agenda_tarefa.py.
    t('ADMINISTRAÇÃO edita cadastro, menos de quem está acima',
      administracao.can_edit_users() and not administracao.pode_editar_usuario(chefe))
    t('e o SUPERADMIN edita qualquer um',
      chefe.can_edit_users() and chefe.pode_editar_usuario(administracao))

    print('\n== O PORTÃO DO MÓDULO DE PONTO ==')
    cfg = ConfiguracaoTangerino.get()
    cfg.ativo = True
    cfg.restrito_ao_grupo = True
    cfg.grupo = None                       # ninguém entraria pelo grupo
    t('quem administra entra mesmo sem estar no grupo liberado',
      cfg.libera(administracao))
    t('e quem não administra continua barrado', not cfg.libera(padrao))

    cfg.ativo = False
    t('módulo desligado: só o superusuário reabre',
      not cfg.libera(administracao) and cfg.libera(chefe))
    cfg.ativo = True

    print('\n== NOME DA FUNÇÃO NÃO PODE MENTIR ==')
    for arq in ('folhaponto/views.py', 'documentos/views.py', 'contracheque/views.py'):
        with open(arq, encoding='utf-8') as f:
            texto = f.read()
        curto = arq.split('/')[0]
        t(f'{curto}: não sobrou is_superadmin liberando quem não é',
          'is_superadmin' not in texto)
        t(f'{curto}: o gate virou pode_administrar', 'def pode_administrar' in texto)
        t(f'{curto}: e usa a regra central', 'can_manage_rh' in texto)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
