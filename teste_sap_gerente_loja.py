"""Visão SAP para os gerentes (PADRÃO do grupo GERENTES) e no tema escuro.

Pedido (07/10/2026): "para os gerentes — usuários padrão no grupo GERENTES —
deve aparecer o /sap 'Visão SAP' em 'Gestão Administrativa'" e "adapte /sap ao
tema escuro".

A auditoria traz nome e documento de cliente: o gerente entra, mas vê (e
marca) só a loja do cadastro dele. A administração continua vendo a rede e
é a única que manda reler o SAP.

Tudo numa transação desfeita; a leitura do MySQL do SAP fica com dublê.
"""
import os
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-sap'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-sap2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import timezone

from auditoria_sap import permissions as perm
from auditoria_sap import views as sap_views
from auditoria_sap.models import LinhaAuditoria
from communications.models import CommunicationGroup
from users.module_access import tem_acesso
from users.models import Sector
from users.templatetags.acessos import sap_na_gestao

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
    loja = Sector.objects.create(name='Loja Zz Pôrto Teste')
    outra = Sector.objects.create(name='Loja Zz Outra Teste')
    chefe = User.objects.create_user(
        username='zzsap.chefe', email='zzsap.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN', is_superuser=True)
    gerentes, _ = CommunicationGroup.objects.get_or_create(name='GERENTES', defaults={'created_by': chefe})

    def pessoa(nome, hierarquia='PADRAO', setor=loja, gerente=True, **kw):
        u = User.objects.create_user(
            username=f'zzsap.{nome}', email=f'zzsap.{nome}@exemplo-teste.local', password='S3nha!teste',
            first_name=nome.capitalize(), last_name='Sap', hierarchy=hierarquia, sector=setor, **kw)
        if gerente:
            u.communication_groups.add(gerentes)
        return u

    gerente = pessoa('gerente')
    gerente_escuro = pessoa('escuro', theme='dark')
    vendedor = pessoa('vendedor', gerente=False)
    sem_loja = pessoa('semloja', setor=None)
    admin = pessoa('admin', hierarquia='ADMIN', setor=outra, gerente=False)
    # Gerente que é do escritório: o M2M de setores tem a rede inteira, e não pode abrir nada.
    gerente.sectors.add(outra)

    def linha(n, pdv, cliente):
        return LinhaAuditoria.objects.create(
            chave=f'zzsap{n:035d}', tipo_erro='SEM NF', pdv=pdv, id_venda=f'ZZ{n}',
            nome_cliente=cliente, data_venda=timezone.localdate(), dados={'CLIENTE': cliente})

    minha1 = linha(1, 'ZZ PORTO TESTE', 'ZZSAP Cliente da minha loja 1')
    minha2 = linha(2, 'ZZ PÔRTO TESTE', 'ZZSAP Cliente da minha loja 2')     # o SAP escreve com acento
    dela = linha(3, 'ZZ OUTRA TESTE', 'ZZSAP Cliente da outra loja')

    print('== QUEM ENTRA ==')
    t('gerente PADRÃO do GERENTES entra', perm.pode_ver(gerente) and perm.e_gerente_de_loja(gerente))
    t('mas não vê a rede', not perm.ve_a_rede(gerente))
    t('o menu do portal (tem_acesso sap) reconhece', tem_acesso(gerente, 'sap'))
    t('PADRÃO fora do GERENTES continua de fora', not perm.pode_ver(vendedor))
    t('gerente sem loja no cadastro não entra (não há o que mostrar)', not perm.pode_ver(sem_loja))
    t('a administração continua vendo a rede', perm.ve_a_rede(admin) and perm.lojas_visiveis(admin) is None)
    t('a loja do gerente é o setor principal, não o M2M',
      perm.lojas_visiveis(gerente) == {'ZZ PORTO TESTE'}, perm.lojas_visiveis(gerente))
    t('a comparação ignora "Loja", acento e caixa',
      perm.normalizar_loja('Loja Glória') == perm.normalizar_loja('GLÓRIA') == 'GLORIA')

    print('\n== O MENU ==')
    t('o gerente acha a Visão SAP em "Gestão Administrativa"', sap_na_gestao(gerente))
    t('a administração continua com ela no ADMINISTRATIVO, sem link repetido', not sap_na_gestao(admin))
    t('quem não entra não vê link nenhum', not sap_na_gestao(vendedor) and not tem_acesso(vendedor, 'sap'))
    cg = Client()
    cg.force_login(gerente)
    r = cg.get('/sap/')
    html = r.content.decode()
    t('a tela abre para o gerente', r.status_code == 200, r.status_code)
    menu = html.split('id="gestao-menu"')[1].split('</ul>')[0] if 'id="gestao-menu"' in html else ''
    t('com o link dentro de Gestão Administrativa', 'href="/sap/"' in menu)
    # A própria tela também aponta para /sap/ (aba e "Limpar"); o menu é quem tem data-rota.
    t('e só uma vez no menu', html.count('data-rota="/sap/"') == 1, html.count('data-rota="/sap/"'))
    ca = Client()
    ca.force_login(admin)
    html_admin = ca.get('/sap/').content.decode()
    menu_admin = html_admin.split('id="gestao-menu"')[1].split('</ul>')[0]
    t('para a administração o link não vai para Gestão Administrativa', 'href="/sap/"' not in menu_admin)

    print('\n== SÓ A LOJA DELE ==')
    t('a tela diz que é só a loja dele', 'Só a sua loja: Loja Zz Pôrto Teste' in html)
    t('as linhas da loja dele aparecem (com e sem acento no SAP)',
      'ZZSAP Cliente da minha loja 1' in html and 'ZZSAP Cliente da minha loja 2' in html)
    t('a da outra loja não', 'ZZSAP Cliente da outra loja' not in html)
    t('o resumo conta só a loja dele', r.context['resumo']['total'] == 2, r.context['resumo'])
    t('o filtro de loja vira o nome da loja (sem seletor)', 'name="loja"' not in html)
    r = cg.get('/sap/', {'loja': 'ZZ OUTRA TESTE', 'q': 'ZZSAP'})
    t('pedir a outra loja pela URL não abre nada', r.context['resumo']['total'] == 0
      and 'ZZSAP Cliente da outra loja' not in r.content.decode())
    r = cg.get('/sap/painel/')
    t('o painel por loja tem só a loja dele', [l['pdv'] for l in r.context['por_loja']] == sorted(['ZZ PORTO TESTE', 'ZZ PÔRTO TESTE'])
      or {l['pdv'] for l in r.context['por_loja']} == {'ZZ PORTO TESTE', 'ZZ PÔRTO TESTE'},
      [l['pdv'] for l in r.context['por_loja']])

    print('\n== DETALHE E MARCAR ==')
    t('abre a ficha de uma linha dela', cg.get(f'/sap/linha/{minha1.id}/').status_code == 200)
    t('a ficha de outra loja é 404', cg.get(f'/sap/linha/{dela.id}/').status_code == 404)
    r = cg.post(f'/sap/linha/{dela.id}/marcar/', {'resolvida': '1'})
    dela.refresh_from_db()
    t('marcar linha de outra loja é 404 e não muda nada', r.status_code == 404 and not dela.resolvida)
    r = cg.post(f'/sap/linha/{minha1.id}/marcar/', {'resolvida': '1', 'observacao': 'ZZ NF emitida'})
    minha1.refresh_from_db()
    t('marca a da loja dele, com quem marcou', r.status_code == 200 and minha1.resolvida
      and minha1.resolvida_por_id == gerente.id)

    print('\n== RELER O SAP CONTINUA COM A ADMINISTRAÇÃO ==')
    with mock.patch.object(sap_views, 'sincronizar') as reler:
        r = cg.post('/sap/atualizar/', {'voltar': '/sap/'})
    t('o gerente não dispara a leitura do SAP', not reler.called and r.status_code == 302)

    print('\n== QUEM NÃO ENTRA ==')
    cv = Client()
    cv.force_login(vendedor)
    t('PADRÃO fora do GERENTES volta para a home', cv.get('/sap/').status_code == 302)
    t('nem pela ficha', cv.get(f'/sap/linha/{minha2.id}/', HTTP_X_REQUESTED_WITH='XMLHttpRequest').status_code == 403)

    print('\n== A ADMINISTRAÇÃO VÊ A REDE ==')
    r = ca.get('/sap/', {'q': 'ZZSAP', 'situacao': 'todas'})
    html_admin = r.content.decode()
    t('as três lojas', all(n in html_admin for n in ('loja 1', 'loja 2', 'outra loja')))
    t('sem o aviso de loja', 'Só a sua loja' not in html_admin)

    print('\n== TEMA ESCURO ==')
    ce = Client()
    ce.force_login(gerente_escuro)
    html = ce.get('/sap/').content.decode()
    t('a página sai com a classe dark', 'class="dark"' in html[:400])
    t('e com o desenho próprio do SAP no escuro',
      all(s in html for s in ('html.dark .sap-card', 'html.dark .sap-hero', 'html.dark .sap-tab-active',
                              'html.dark .sap-aberta', 'html.dark .sap-barra')))
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)

print(f'\n{ok} OK / {fail} falhas — rollback: nada deste teste ficou no banco.')
sys.exit(1 if fail else 0)
