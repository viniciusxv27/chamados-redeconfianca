"""Contracheque: a ADMINISTRAÇÃO tem o mesmo acesso que o SUPERADMIN.

Pedido: "em /contracheque/ — Administração deve ter o mesmo acesso que SUPERADMIN".

No servidor isso já valia (tudo passa por ``pode_administrar`` → ``can_manage_rh``),
mas os dois botões "Administrar" — a única porta para as telas de gestão —
apareciam só para ``hierarchy == 'SUPERADMIN'``. Aqui se prova, página por
página, que ADMINISTRAÇÃO e SUPERADMIN recebem a mesma resposta, e que o PADRÃO
continua de fora. O SUPERADMIN de teste NÃO é superuser: senão o teste não
distinguiria a regra da hierarquia do atalho do superuser.

Roda numa transação desfeita no fim e com os PDFs em memória (nada vai ao MinIO).
"""
import os
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import InMemoryStorage
from django.db import transaction
from django.test import Client

from contracheque.models import IncomeReport, Payslip
from users.models import UserModuleAccess

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


PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'
memoria = InMemoryStorage()
marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(Payslip._meta.get_field('pdf_file'), 'storage', memoria), \
            mock.patch.object(IncomeReport._meta.get_field('pdf_file'), 'storage', memoria):

        def novo(username, hierarquia):
            return User.objects.create_user(
                username=username, email=f'{username}@exemplo-teste.local', password='S3nha!teste',
                first_name=username.split('.')[1].title(), last_name='Teste', hierarchy=hierarquia)

        superadmin = novo('zzcc.superadmin', 'SUPERADMIN')          # sem is_superuser, de propósito
        administracao = novo('zzcc.administracao', 'ADMIN')
        padrao = novo('zzcc.padrao', 'PADRAO')
        liberado = novo('zzcc.liberado', 'PADRAO')
        UserModuleAccess.objects.create(user=liberado, module_key='rh.gestao')
        t('o SUPERADMIN de teste não é superuser (a comparação vale pela hierarquia)',
          not superadmin.is_superuser and not administracao.is_superuser)

        contracheque = Payslip(user=padrao, month=1, year=2026, employee_name='ZZ Padrao')
        contracheque.pdf_file.save('zz-cc.pdf', ContentFile(PDF), save=False)
        contracheque.save()
        informe = IncomeReport(user=padrao, base_year=2025, exercise_year=2026, employee_name='ZZ Padrao')
        informe.pdf_file.save('zz-ir.pdf', ContentFile(PDF), save=False)
        informe.save()

        clientes = {}
        for u in (superadmin, administracao, padrao, liberado):
            c = Client()
            c.force_login(u)
            clientes[u.username] = c

        def pedir(u, url, metodo='get', **dados):
            r = getattr(clientes[u.username], metodo)(url, dados)
            return r.status_code, r.get('Location', '') or r.get('Content-Type', '').split(';')[0]

        print('== O BOTÃO "ADMINISTRAR" ==')
        for url, alvo in (('/contracheque/', '/contracheque/admin/'),
                          ('/contracheque/informes/', '/contracheque/admin/informes/')):
            html = {u.username: clientes[u.username].get(url).content.decode()
                    for u in (superadmin, administracao, padrao, liberado)}
            t(f'{url}: ADMINISTRAÇÃO vê o botão, como o SUPERADMIN',
              f'href="{alvo}"' in html['zzcc.superadmin'] and f'href="{alvo}"' in html['zzcc.administracao'])
            t(f'{url}: quem tem a liberação individual de RH também', f'href="{alvo}"' in html['zzcc.liberado'])
            t(f'{url}: o PADRÃO não', f'href="{alvo}"' not in html['zzcc.padrao'])

        print('\n== AS TELAS DE GESTÃO: MESMA RESPOSTA PARA OS DOIS ==')
        telas = ['/contracheque/admin/', '/contracheque/admin/importar/', '/contracheque/admin/informes/',
                 '/contracheque/admin/informes/importar/', '/contracheque/admin/relatorio-assinaturas/?month=1&year=2026',
                 f'/contracheque/{contracheque.pk}/', f'/contracheque/{contracheque.pk}/pdf/',
                 f'/contracheque/informes/{informe.pk}/', f'/contracheque/informes/{informe.pk}/pdf/']
        for url in telas:
            do_super, do_admin = pedir(superadmin, url), pedir(administracao, url)
            t(f'GET {url}: ADMINISTRAÇÃO = SUPERADMIN ({do_admin[0]} {do_admin[1]})', do_super == do_admin
              and do_admin[0] == 200, (do_super, do_admin))
        t('o relatório de assinaturas sai em CSV para a ADMINISTRAÇÃO',
          pedir(administracao, '/contracheque/admin/relatorio-assinaturas/?month=1&year=2026') == (200, 'text/csv'))

        acoes = [('/contracheque/api/importar/', {}), ('/contracheque/api/importar-lote/', {}),
                 ('/contracheque/api/reimportar-mes/', {}), ('/contracheque/api/excluir-lote/', {}),
                 ('/contracheque/api/informes/importar-lote/', {}),
                 ('/contracheque/api/informes/excluir-lote/', {}),
                 ('/contracheque/api/download-nao-encontrados/', {})]
        for url, dados in acoes:
            do_super, do_admin = pedir(superadmin, url, 'post', **dados), pedir(administracao, url, 'post', **dados)
            t(f'POST {url} (sem arquivo): ADMINISTRAÇÃO recebe o mesmo que o SUPERADMIN ({do_admin[0]})',
              do_super == do_admin and do_admin[0] != 403, (do_super, do_admin))

        print('\n== O PADRÃO CONTINUA DE FORA ==')
        for url in telas[:5]:
            r = clientes['zzcc.padrao'].get(url, follow=True)
            t(f'GET {url}: PADRÃO barrado (volta para os próprios contracheques/informes, sem CSV)',
              r.redirect_chain and r.request['PATH_INFO'] in ('/contracheque/', '/contracheque/informes/')
              and not r.get('Content-Type', '').startswith('text/csv'), (r.redirect_chain, r.request['PATH_INFO']))
        codigo, _ = pedir(padrao, '/contracheque/api/excluir-lote/', 'post')
        t('POST de exclusão em lote: PADRÃO barrado', codigo in (302, 403), codigo)
        t('o PADRÃO segue vendo o próprio contracheque', pedir(padrao, f'/contracheque/{contracheque.pk}/')[0] == 200)
        outro = novo('zzcc.outro', 'PADRAO')
        c_outro = Client()
        c_outro.force_login(outro)
        r = c_outro.get(f'/contracheque/{contracheque.pk}/')
        t('e ninguém de fora vê o contracheque dele', r.status_code in (302, 403, 404), r.status_code)
        t('o contracheque de teste continua lá (nenhuma ação apagou nada)',
          Payslip.objects.filter(pk=contracheque.pk).exists() and IncomeReport.objects.filter(pk=informe.pk).exists())
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco (e nada subiu para o MinIO).')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
