"""Contracheque: parcelas do 13º no campo "Mês" da importação.

Pedido: "em /contracheque/admin/importar/ * No campo mes mesmo, adicione
"Primeira Parcela 13*" e "Segunda Parcela 13*", tanto em lote quanto individual".

As parcelas são os valores 13 e 14 do mesmo campo ``month``: a unicidade
(user, month, year) separa a parcela do contracheque de novembro/dezembro.
Roda numa transação desfeita no fim, cache LocMem e PDFs em memória (nada vai
ao MinIO nem fica no banco).
"""
import os
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')
# Cache em memória: o `default` é o Redis remoto compartilhado.
import redeconfianca.settings as _cfg  # noqa: E402
_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'teste-{n}'} for n in _cfg.CACHES}
django.setup()

from django.conf import settings  # noqa: E402

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model  # noqa: E402
from django.core.files.base import ContentFile  # noqa: E402
from django.core.files.storage import InMemoryStorage  # noqa: E402
from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.db import transaction  # noqa: E402
from django.test import Client  # noqa: E402

from contracheque.models import Payslip  # noqa: E402

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
            mock.patch('contracheque.views.extract_payslip_data', return_value={'net_pay': 1000}), \
            mock.patch('contracheque.views.extract_single_page_pdf', return_value=None):

        admin = User.objects.create_user(
            username='zz13.admin', email='zz13.admin@exemplo-teste.local', password='S3nha!teste',
            first_name='Admin', last_name='Treze', hierarchy='SUPERADMIN')
        pessoa = User.objects.create_user(
            username='zz13.pessoa', email='zz13.pessoa@exemplo-teste.local', password='S3nha!teste',
            first_name='Pessoa', last_name='Treze', hierarchy='PADRAO', cpf='99988877766')
        c = Client()
        c.force_login(admin)

        print('== AS OPÇÕES NA TELA DE IMPORTAÇÃO ==')
        html = c.get('/contracheque/admin/importar/').content.decode()
        for sel in ('single-month', 'bulk-month'):
            trecho = html.split(f'id="{sel}"', 1)[1].split('</select>', 1)[0]
            t(f'{sel}: tem "Primeira Parcela 13º" (13)', '<option value="13">Primeira Parcela 13º</option>' in trecho)
            t(f'{sel}: tem "Segunda Parcela 13º" (14)', '<option value="14">Segunda Parcela 13º</option>' in trecho)
            t(f'{sel}: os 12 meses continuam', '<option value="12">Dezembro</option>' in trecho
              and '<option value="1">Janeiro</option>' in trecho)

        print('\n== IMPORTAÇÃO INDIVIDUAL ==')

        def individual(mes):
            return c.post('/contracheque/api/importar/', {
                'user_id': pessoa.pk, 'month': mes, 'year': 2026,
                'pdf_file': SimpleUploadedFile('h.pdf', PDF, content_type='application/pdf')})

        for mes in (11, 12, 13, 14):
            r = individual(mes)
            t(f'mês {mes} importa (200)', r.status_code == 200, r.content[:200])
        t('as quatro convivem: novembro, dezembro e as duas parcelas',
          Payslip.objects.filter(user=pessoa, year=2026).count() == 4)
        r = individual(13).json()
        t('a mesma parcela de novo é recusada, com o rótulo certo',
          'Primeira Parcela 13º/2026' in r.get('error', ''), r)
        r = individual(15)
        t('mês fora das opções é recusado (400)', r.status_code == 400, r.status_code)
        r = individual('x')
        t('mês não numérico é recusado (400)', r.status_code == 400, r.status_code)

        p13 = Payslip.objects.get(user=pessoa, year=2026, month=13)
        p14 = Payslip.objects.get(user=pessoa, year=2026, month=14)
        t('rótulos: period_display', p13.period_display == 'Primeira Parcela 13º/2026'
          and p14.period_display == 'Segunda Parcela 13º/2026', (p13.period_display, p14.period_display))
        t('is_decimo_terceiro só nas parcelas', p13.is_decimo_terceiro and p14.is_decimo_terceiro
          and not Payslip.objects.get(user=pessoa, year=2026, month=12).is_decimo_terceiro)

        print('\n== IMPORTAÇÃO EM LOTE ==')
        Payslip.objects.filter(user=pessoa).delete()
        lido = [{'employee_name': 'Pessoa Treze', 'cpf': '999.888.777-66', 'net_pay': 500}]
        with mock.patch('contracheque.views.extract_all_payslips', side_effect=lambda *_: [dict(x) for x in lido]):
            def lote(mes):
                return c.post('/contracheque/api/importar-lote/', {
                    'month': mes, 'year': 2026,
                    'pdf_files': [SimpleUploadedFile('lote.pdf', PDF, content_type='application/pdf')]})

            for mes in (12, 13, 14):
                r = lote(mes).json()
                t(f'lote no mês {mes} importa', r.get('success_count') == 1, r)
            r = lote(14).json()
            t('lote repetido na 2ª parcela pula', r.get('success_count') == 0
              and r['results'][0]['status'] == 'skip', r)
            t('lote com mês inválido é recusado (400)', lote(0).status_code == 400)
        t('dezembro e as duas parcelas separados no banco',
          sorted(Payslip.objects.filter(user=pessoa, year=2026).values_list('month', flat=True)) == [12, 13, 14])

        print('\n== ONDE O CONTRACHEQUE APARECE ==')
        cp = Client()
        cp.force_login(pessoa)
        html = cp.get('/contracheque/').content.decode()
        t('lista do colaborador mostra as duas parcelas',
          'Primeira Parcela 13º/2026' in html and 'Segunda Parcela 13º/2026' in html)
        html = c.get('/contracheque/admin/?month=13&year=2026').content.decode()
        t('lista do admin filtrada pela 1ª parcela mostra só ela',
          'Primeira Parcela 13º/2026' in html and 'Segunda Parcela 13º/2026' not in html)
        t('filtro do admin oferece as parcelas', '>Segunda Parcela 13º</option>' in html)
        p14 = Payslip.objects.get(user=pessoa, year=2026, month=14)
        r = cp.get(f'/contracheque/{p14.pk}/')
        t('detalhe abre com o rótulo', r.status_code == 200 and 'Segunda Parcela 13º/2026' in r.content.decode())
        r = cp.get(f'/contracheque/{p14.pk}/pdf/')
        t('PDF abre e o nome do arquivo leva 14', r.status_code == 200
          and '_2026_14.pdf' in r.get('Content-Disposition', ''), (r.status_code, r.get('Content-Disposition')))
        r = c.get('/contracheque/admin/relatorio-assinaturas/?month=13&year=2026')
        t('relatório de assinaturas da 1ª parcela sai com o rótulo',
          r.status_code == 200 and 'Primeira Parcela 13º/2026' in r.content.decode('utf-8-sig'))

        from api_mobile.modulos.contracheque import _holerite_resumo
        resumo = _holerite_resumo(p14)
        t('API mobile: competência e mes_nome com o rótulo', resumo['competencia'] == 'Segunda Parcela 13º/2026'
          and resumo['mes_nome'] == 'Segunda Parcela 13º', resumo)
        from assistente.ferramentas import _meus_contracheques
        texto = _meus_contracheques(pessoa, {'ano': 2026})
        t('assistente lista com o rótulo (não "14/2026")', 'Segunda Parcela 13º/2026' in texto
          and '14/2026' not in texto, texto)

        print('\n== EXCLUSÃO EM LOTE NÃO PEGA DEZEMBRO ==')
        r = c.post('/contracheque/api/excluir-lote/', {'month': 14, 'year': 2026})
        restam = sorted(Payslip.objects.filter(user=pessoa, year=2026).values_list('month', flat=True))
        t('excluir a 2ª parcela deixa dezembro e a 1ª', 12 in restam and 13 in restam and 14 not in restam,
          (r.status_code, restam))
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
