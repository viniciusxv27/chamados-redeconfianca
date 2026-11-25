"""Visão SAP para os gerentes (PADRÃO do grupo GERENTES) e no tema escuro.

26/11/2026: a regra mudou — todo mundo entra pelo Estoque; auditoria, painel e Renova só com a
liberação (Acessos e gestores). O gerente liberado vê a rede inteira; o Estoque LP é do SUPERADMIN.

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
            chave=f'zzsap{n:035d}', tipo_erro='SERIAL ERRADO NO VIVO GO',   # a aba em que a lista abre pdv=pdv, id_venda=f'ZZ{n}',
            nome_cliente=cliente, data_venda=timezone.localdate(), dados={'CLIENTE': cliente})

    minha1 = linha(1, 'ZZ PORTO TESTE', 'ZZSAP Cliente da minha loja 1')
    minha2 = linha(2, 'ZZ PÔRTO TESTE', 'ZZSAP Cliente da minha loja 2')     # o SAP escreve com acento
    dela = linha(3, 'ZZ OUTRA TESTE', 'ZZSAP Cliente da outra loja')

    # 26/11/2026: "A visão SAP, todo mundo deveria ter acesso, porém, de início, só liberado o
    # ESTOQUE, os demais, só naquele campo de liberação dentro de acessos e gestores." Acabou a
    # Visão SAP recortada pela loja do gerente: sem a liberação ele fica no Estoque; liberado,
    # vê a rede inteira, como qualquer liberado.
    from users.models import UserModuleAccess
    print('== QUEM ENTRA (todos pelo Estoque; o resto pela liberação) ==')
    t('gerente não entra mais sozinho na auditoria, mas tem o Estoque',
      not perm.pode_ver(gerente) and perm.pode_ver_estoque(gerente) and perm.so_estoque(gerente))
    t('ADMIN também não entra pela hierarquia', not perm.pode_ver(admin) and not perm.e_gestor(admin))
    t('PADRÃO comum entra no Estoque', perm.pode_ver_estoque(vendedor) and perm.so_estoque(vendedor))
    t('o menu (tem_acesso sap) não abre a auditoria para eles', not tem_acesso(gerente, 'sap') and not tem_acesso(admin, 'sap'))
    t('SUPERADMIN vê tudo e o Estoque LP', perm.ve_a_rede(chefe) and perm.e_gestor(chefe) and perm.pode_ver_estoque_lp(chefe))
    t('o Estoque LP é só do SUPERADMIN', not perm.pode_ver_estoque_lp(admin) and not perm.pode_ver_estoque_lp(gerente))
    t('não há mais link recortado por loja no menu', not sap_na_gestao(gerente) and not sap_na_gestao(admin))

    cg = Client(); cg.force_login(gerente)
    r = cg.get('/sap/')
    t('sem a liberação, /sap/ leva ao Estoque', r.status_code == 302 and r['Location'] == '/sap/estoque/', r.get('Location'))
    t('o painel e a Renova também', cg.get('/sap/painel/')['Location'] == '/sap/estoque/'
      and cg.get('/sap/renova/')['Location'] == '/sap/estoque/')
    t('a ficha por AJAX dá 403', cg.get(f'/sap/linha/{minha1.id}/', HTTP_X_REQUESTED_WITH='XMLHttpRequest').status_code == 403)
    cg.post(f'/sap/linha/{minha1.id}/marcar/', {'resolvida': '1'})
    minha1.refresh_from_db()
    t('e não marca linha nenhuma', not minha1.resolvida)
    r = cg.get('/sap/estoque-lp/')
    t('nem abre o Estoque LP (volta para o Estoque)', r.status_code == 302 and r['Location'] == '/sap/estoque/', r.get('Location'))
    r = cg.get('/sap/estoque-lp/unidades/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    t('nem a gaveta do LP', r.status_code == 403, r.status_code)

    print('\n== LIBERADO EM "ACESSOS E GESTORES" ==')
    UserModuleAccess.objects.create(user=gerente, module_key='sap')
    gerente = User.objects.get(pk=gerente.pk)          # a liberação fica guardada na instância
    t('entra e vê a rede inteira', perm.pode_ver(gerente) and perm.lojas_visiveis(gerente) is None
      and tem_acesso(gerente, 'sap'))
    r = cg.get('/sap/', {'q': 'ZZSAP', 'situacao': 'todas'})
    html = r.content.decode()
    t('a tela abre', r.status_code == 200, r.status_code)
    t('com as três lojas, sem o aviso "só a sua loja"',
      all(n in html for n in ('loja 1', 'loja 2', 'outra loja')) and 'Só a sua loja' not in html)
    menu = html.split('id="gestao-menu"')[1].split('</ul>')[0] if 'id="gestao-menu"' in html else ''
    t('o menu leva à auditoria, em Gestão Operacional', 'href="/sap/"' in menu)
    t('e só uma vez no menu', html.count('data-rota="/sap/"') == 1, html.count('data-rota="/sap/"'))
    t('as abas da auditoria aparecem, a do Estoque LP não', '>Auditoria' in html.replace('\n', '').replace('  ', '')
      or 'fa-list-check' in html)
    t('a aba Estoque LP não aparece para ele', '/sap/estoque-lp/' not in html)
    r = cg.post(f'/sap/linha/{dela.id}/marcar/', {'resolvida': '1', 'observacao': 'ZZ NF emitida'})
    dela.refresh_from_db()
    t('marca linha de qualquer loja, com quem marcou', r.status_code == 200 and dela.resolvida
      and dela.resolvida_por_id == gerente.id)
    with mock.patch.object(sap_views, 'sincronizar') as reler:
        r = cg.post('/sap/atualizar/', {'voltar': '/sap/'})
    t('liberado não é gestor: não dispara a leitura do SAP', not reler.called and r.status_code == 302)

    print('\n== O SUPERADMIN ==')
    cs = Client(); cs.force_login(chefe)
    html = cs.get('/sap/').content.decode()
    t('vê a aba Estoque LP', '/sap/estoque-lp/' in html and 'Estoque LP' in html)
    UserModuleAccess.objects.create(user=gerente_escuro, module_key='sap')

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
