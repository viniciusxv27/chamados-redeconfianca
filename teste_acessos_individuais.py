"""Acessos de módulo por usuário: todos os módulos e cada visão de detalhe.

A prova principal é mecânica: para CADA chave do catálogo, um PADRÃO sem
grupo nenhum não tem o acesso; depois da liberação, tem. Chave que não
mudasse nada seria uma caixinha decorativa na tela.

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

from users import module_access as ma
from users.models import Sector, UserModuleAccess

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


def fresco(user):
    """Recarrega sem o cache de liberações da instância."""
    return User.objects.get(pk=user.pk)


marcador = transaction.atomic()
marcador.__enter__()
try:
    area = Sector.objects.create(name='ZZ Setor Acessos')

    def novo(u, hierarquia='PADRAO', **kw):
        return User.objects.create_user(
            username=u, email=f'{u}@exemplo-teste.local', password='S3nha!teste',
            sector=area, first_name=u.split('.')[1].title(), last_name='T',
            hierarchy=hierarquia, **kw)

    chefe = novo('ac.chefe', 'SUPERADMIN', is_superuser=True, is_staff=True)
    admin = novo('ac.admin', 'ADMIN')
    padrao = novo('ac.padrao')

    print('== O CATÁLOGO É HONESTO: TODA CHAVE TEM GATE ==')
    for chave in sorted(ma.MODULE_KEYS):
        t(f'{chave}: tem gate registrado', chave in ma.GATES)
    for chave in sorted(ma.GATES):
        t(f'{chave}: está no catálogo', chave in ma.MODULE_KEYS)

    # O módulo de ponto no dev está aberto a todos (não restrito a grupo);
    # restringe aqui — dentro da transação desfeita — para a liberação ter o
    # que provar. Sem isso "ponto" seria True antes e depois.
    from tangerino.models import ConfiguracaoTangerino
    cfg_ponto = ConfiguracaoTangerino.get()
    cfg_ponto.ativo = True
    cfg_ponto.restrito_ao_grupo = True
    cfg_ponto.grupo = None
    cfg_ponto.save()

    print('\n== CADA CHAVE ABRE O QUE PROMETE (PADRÃO sem grupo) ==')
    for chave in sorted(ma.MODULE_KEYS):
        antes = ma.tem_acesso(fresco(padrao), chave)
        ma.set_user_modules(padrao, [chave], granted_by=chefe)
        depois = ma.tem_acesso(fresco(padrao), chave)
        ma.set_user_modules(padrao, [], granted_by=chefe)
        de_volta = ma.tem_acesso(fresco(padrao), chave)
        t(f'{chave}: fechado → liberado → fechado',
          (not antes) and depois and (not de_volta), (antes, depois, de_volta))

    print('\n== DETALHE TRAZ A PORTA JUNTO ==')
    ma.set_user_modules(padrao, ['impulso.gestor'], granted_by=chefe)
    chaves = set(UserModuleAccess.objects.filter(user=padrao).values_list('module_key', flat=True))
    t('liberar "gestor do Impulso" libera a entrada no Impulso', chaves == {'impulso', 'impulso.gestor'}, chaves)
    from impulso.utils import is_impulso_manager, is_impulso_member
    t('o gate do módulo abre', is_impulso_member(fresco(padrao)))
    t('e o de gestor também', is_impulso_manager(fresco(padrao)))
    ma.set_user_modules(padrao, [], granted_by=chefe)
    t('chave desconhecida é ignorada',
      ma.set_user_modules(padrao, ['nao.existe'], granted_by=chefe) == (set(), set()))

    print('\n== O MENU OBEDECE AO GATE ==')
    c = Client(); c.force_login(padrao)
    html = c.get('/').content.decode()
    t('PADRÃO sem liberação não vê Fornecedores', 'suppliers' not in html)
    t('nem Treinamentos (gerenciar)', '/trainings/manage/' not in html and 'trainings_manage' not in html)
    ma.set_user_modules(padrao, ['fornecedores', 'treinamentos.gestao', 'compras', 'projetos.gestao'], granted_by=chefe)
    html = c.get('/').content.decode()
    t('liberado, Fornecedores aparece no menu', 'suppliers' in html)
    t('Compras também', 'purchases' in html)
    t('Projetos – Gerenciar também', 'Projetos - Gerenciar' in html)
    t('e a seção ADMINISTRATIVO abre para ele', 'management-menu' in html)
    ma.set_user_modules(padrao, [], granted_by=chefe)

    print('\n== AS VIEWS OBEDECEM (não só o menu) ==')
    r = c.get('/suppliers/', follow=True)
    t('fornecedores fechado sem liberação', r.status_code in (302, 403) or 'permiss' in r.content.decode().lower())
    ma.set_user_modules(padrao, ['fornecedores'], granted_by=chefe)
    c = Client(); c.force_login(fresco(padrao))
    r = c.get('/suppliers/')
    t('fornecedores abre com a liberação', r.status_code == 200, r.status_code)
    ma.set_user_modules(padrao, [], granted_by=chefe)

    from prizes.models import Prize  # noqa: F401 — módulo existe
    u = fresco(padrao)
    t('mercadinho: PADRÃO não gerencia', not u.can_manage_prizes())
    ma.set_user_modules(padrao, ['mercadinho.gerenciar'], granted_by=chefe)
    t('mercadinho: liberado gerencia', fresco(padrao).can_manage_prizes())
    t('...sem virar gestor de usuários por tabela', not fresco(padrao).can_manage_users())
    ma.set_user_modules(padrao, [], granted_by=chefe)

    from trainings.views import pode_gerenciar_treinamentos
    t('treinamentos: PADRÃO não publica', not pode_gerenciar_treinamentos(fresco(padrao)))
    ma.set_user_modules(padrao, ['treinamentos.gestao'], granted_by=chefe)
    t('treinamentos: liberado publica', pode_gerenciar_treinamentos(fresco(padrao)))
    t('...sem virar gestor de usuários', not fresco(padrao).can_manage_users())
    ma.set_user_modules(padrao, [], granted_by=chefe)

    print('\n== A REGRA NORMAL CONTINUA VALENDO (grant-only) ==')
    t('ADMIN administra pessoal sem liberação', admin.can_manage_rh())
    t('SUPERADMIN é gestor do Impulso sem liberação', is_impulso_manager(chefe))
    ma.set_user_modules(admin, [], granted_by=chefe)
    t('tirar liberação de quem já tinha pela regra não tira nada', fresco(admin).can_manage_rh())

    print('\n== A TELA DE EDIÇÃO ==')
    cs = Client(); cs.force_login(chefe)
    html = cs.get(f'/users/manage/users/{padrao.id}/edit/').content.decode()
    t('o SUPERADMIN vê o catálogo inteiro', 'id="acessosIndividuais"' in html and 'acesso-modulo' in html)
    for chave in sorted(ma.MODULE_KEYS):
        t(f'  caixa de {chave} está na tela', f'value="{chave}"' in html)
    t('tem busca por módulo', 'id="acessoBusca"' in html)
    t('e o botão "tudo" por módulo', 'acesso-tudo' in html)
    t('os grupos aparecem', all(g in html for g in ('Gestão Comercial', 'Pessoal', 'Operação', 'Administrativo', 'Financeiro')))

    r = cs.post(f'/users/manage/users/{padrao.id}/edit/', {
        'email': padrao.email, 'username': padrao.username, 'full_name': 'Ac Padrao',
        'sector': area.id, 'hierarchy': 'PADRAO', 'job_title': 'X',
        'birth_date': '1990-01-01', 'admission_date': '2026-01-05',
        'modulos': ['cursos.gestao', 'caixa'],
    }, follow=True)
    chaves = set(UserModuleAccess.objects.filter(user=padrao).values_list('module_key', flat=True))
    t('salvar grava as marcadas (e a porta do detalhe)',
      {'cursos', 'cursos.gestao', 'caixa'} <= chaves, chaves)
    t('registra quem liberou',
      UserModuleAccess.objects.filter(user=padrao, module_key='caixa', granted_by=chefe).exists())

    print('\n== A ADMINISTRAÇÃO EDITA CADASTRO MAS NÃO DISTRIBUI ACESSO ==')
    ca = Client(); ca.force_login(admin)
    html = ca.get(f'/users/manage/users/{padrao.id}/edit/').content.decode()
    t('a ADMINISTRAÇÃO abre a edição', 'Acessos de módulo' in html)
    t('mas não vê as caixas',
      'class="acesso-modulo' not in html and '<input type="checkbox" name="modulos"' not in html)
    t('vê o que já está liberado', 'Cursos Vivo' in html and 'Contagem de Caixa' in html)
    t('e o aviso de que só o SUPERADMIN mexe', 'Só o SUPERADMIN dá ou tira' in html)
    r = ca.post(f'/users/manage/users/{padrao.id}/edit/', {
        'email': padrao.email, 'username': padrao.username, 'full_name': 'Ac Padrao',
        'sector': area.id, 'hierarchy': 'PADRAO', 'job_title': 'X',
        'birth_date': '1990-01-01', 'admission_date': '2026-01-05',
        'modulos': ['rh.gestao', 'usuarios.gerenciar', 'popups.gerenciar'],
    }, follow=True)
    chaves = set(UserModuleAccess.objects.filter(user=padrao).values_list('module_key', flat=True))
    t('um POST da ADMINISTRAÇÃO com liberações é ignorado (as antigas ficam)',
      chaves == {'cursos', 'cursos.gestao', 'caixa'}, chaves)
    t('e não dá para ela liberar administração de pessoal', not fresco(padrao).can_manage_rh())

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
