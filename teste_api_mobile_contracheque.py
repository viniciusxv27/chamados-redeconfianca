"""App mobile — módulo Contracheque (/api/mobile/v1/m/contracheque/).

Roda dentro de uma transação desfeita no fim. Os PDFs vão para um storage em
memória (o campo é trocado só durante o teste): nada sobe para o MinIO.
"""
import base64
import io
import os
import sys
from unittest import mock

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

import pypdfium2 as pdfium
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import InMemoryStorage
from django.db import transaction
from django.test import Client

from contracheque.models import IncomeReport, Payslip
from users.models import Sector

User = get_user_model()
ok = fail = 0
BASE = '/api/mobile/v1'
M = f'{BASE}/m/contracheque'


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def pdf_de_teste(paginas=1):
    doc = pdfium.PdfDocument.new()
    for _ in range(paginas):
        doc.new_page(200, 200)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# PNG 1x1 transparente (assinatura mínima válida).
PNG = 'data:image/png;base64,' + base64.b64encode(bytes.fromhex(
    '89504e470d0a1a0a0000000d4948445200000001000000010806000000'
    '1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082')).decode()

for nome in settings.CACHES:
    caches[nome].clear()

memoria = InMemoryStorage()
marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(Payslip._meta.get_field('pdf_file'), 'storage', memoria), \
            mock.patch.object(IncomeReport._meta.get_field('pdf_file'), 'storage', memoria):
        setor = Sector.objects.create(name='ZZ Setor Contracheque App')

        def novo(apelido, hierarquia='PADRAO', **kw):
            return User.objects.create_user(
                username=f'zzcc.{apelido}', email=f'zzcc.{apelido}@exemplo-teste.local', password='S3nha!teste',
                first_name=f'ZZ{apelido.title()}', last_name='Teste', sector=setor, hierarchy=hierarquia, **kw)

        dono = novo('dono')
        outro = novo('outro')
        admin = novo('admin', 'ADMIN')            # ADMINISTRAÇÃO: can_manage_rh

        def holerite(user, mes, ano, **kw):
            p = Payslip(user=user, month=mes, year=ano, net_pay='2345.67', total_earnings='3000.00',
                        total_deductions='654.33', base_salary='2800.00', fgts_base='3000.00',
                        earnings_detail=[{'description': 'Salário', 'value': '2800.00'}],
                        deductions_detail=[{'description': 'INSS', 'value': '300.00'}], **kw)
            p.pdf_file.save(f'zz-cc-{user.pk}-{ano}-{mes}.pdf', ContentFile(pdf_de_teste()), save=False)
            p.save()
            return p

        h1 = holerite(dono, 1, 2026)
        h2 = holerite(dono, 12, 2025)
        h_outro = holerite(outro, 1, 2026)
        inf = IncomeReport(user=dono, base_year=2025, exercise_year=2026, total_rendimentos='36000.00', irrf='1200.00')
        inf.pdf_file.save('zz-ir.pdf', ContentFile(pdf_de_teste()), save=False)
        inf.save()
        inf_outro = IncomeReport(user=outro, base_year=2025, exercise_year=2026)
        inf_outro.pdf_file.save('zz-ir-o.pdf', ContentFile(pdf_de_teste()), save=False)
        inf_outro.save()

        def entrar(user):
            r = Client().post(f'{BASE}/auth/login/', {'email': user.email, 'senha': 'S3nha!teste',
                                                      'plataforma': 'android', 'dispositivo': 'ZZ'},
                              content_type='application/json')
            return Client(HTTP_AUTHORIZATION=f'Bearer {r.json()["token"]}')

        cd, co, ca = entrar(dono), entrar(outro), entrar(admin)

        print('== ENTRADA ==')
        chaves = [m['chave'] for m in cd.get(f'{BASE}/modulos/').json()]
        t('aparece em /modulos/ para todo logado (como o menu da web)', 'contracheque' in chaves, chaves)
        t('sem token: 401/403', Client().get(f'{M}/').status_code in (401, 403))
        # Quem não tem acesso: com o gate fechado o módulo responde 403 e some da lista.
        from api_mobile.modulos import contracheque as mod
        with mock.patch.dict(mod.MODULO, {'pode_entrar': lambda u: False}):
            r = cd.get(f'{M}/')
            t('gate fechado: 403 no módulo', r.status_code == 403, r.status_code)
            t('gate fechado: some de /modulos/', 'contracheque' not in [m['chave'] for m in cd.get(f'{BASE}/modulos/').json()])
        inativo = novo('inativo', is_active=False)
        t('pode_entrar: inativo não entra', mod.pode_entrar(inativo) is False and mod.pode_entrar(dono) is True)

        print('\n== LISTA ==')
        r = cd.get(f'{M}/')
        js = r.json()
        ids = [c['id'] for c in js['contracheques']]
        t('lista responde 200', r.status_code == 200, r.content[:200])
        t('só os meus, do mais novo para o mais velho', ids == [h1.pk, h2.pk], ids)
        t('anos para agrupar', js['anos'] == [2026, 2025], js['anos'])
        t('pendentes de assinatura', js['pendentes'] == 2)
        t('valores como na web', js['contracheques'][0]['liquido'] == '2345.67'
          and js['contracheques'][0]['competencia'] == 'Janeiro/2026')
        t('informes: só os meus', [i['id'] for i in js['informes']] == [inf.pk])
        t('colaborador não administra', js['administra'] is False)

        print('\n== DETALHE E RECORTE ==')
        r = cd.get(f'{M}/{h1.pk}/')
        d = r.json()
        t('detalhe do meu: 200', r.status_code == 200, r.content[:200])
        t('itens e bases', d['itens_proventos'][0]['descricao'] == 'Salário'
          and d['bases'][0]['rotulo'] == 'Base FGTS' and d['salario_base'] == '2800.00')
        t('pode assinar o meu', d['pode_assinar'] is True and d['assinatura'] is None)
        t('link do PDF assinado (5 min)', '/api/mobile/v1/arquivo/' in (d['pdf'] or ''))
        t('sem PDF assinado antes de assinar', d['pdf_assinado'] is None)
        t('holerite de outra pessoa: 404', cd.get(f'{M}/{h_outro.pk}/').status_code == 404)
        t('id que não existe: 404', cd.get(f'{M}/999999999/').status_code == 404)
        t('informe de outra pessoa: 404', cd.get(f'{M}/informes/{inf_outro.pk}/').status_code == 404)
        r = cd.get(f'{M}/informes/{inf.pk}/')
        t('informe meu: seções da web', r.status_code == 200 and len(r.json()['secoes']) == 3
          and r.json()['secoes'][0]['linhas'][0]['valor'] == '36000.00', r.content[:200])
        r = ca.get(f'{M}/{h_outro.pk}/')
        t('ADMINISTRAÇÃO vê o de outra pessoa (como na web)', r.status_code == 200
          and r.json()['de_outra_pessoa']['id'] == outro.pk and r.json()['pode_assinar'] is False)
        t('lista do admin continua só a dele', ca.get(f'{M}/').json()['contracheques'] == [])

        print('\n== PDF POR LINK ASSINADO ==')
        anon = Client()
        r = anon.get(d['pdf'])
        corpo = b''.join(r.streaming_content) if r.status_code == 200 else b''
        t('link abre o PDF sem token', r.status_code == 200 and corpo.startswith(b'%PDF'), r.status_code)
        link_outro = co.get(f'{M}/{h_outro.pk}/').json()['pdf']
        t('o link do outro abre só o do outro', anon.get(link_outro).status_code == 200)
        # Link forjado: assinatura do dono apontando para o holerite do outro não vale.
        from django.core import signing
        forjado = signing.dumps({'u': dono.pk, 't': 'contracheque', 'i': str(h_outro.pk)},
                                salt='api_mobile.arquivo', compress=True)
        t('link do dono para o holerite do outro: 404', anon.get(f'{BASE}/arquivo/{forjado}/').status_code == 404)
        adulterado = d['pdf'].rstrip('/').rsplit('/', 1)[-1][:-2] + 'xx'
        t('link adulterado: 404', anon.get(f'{BASE}/arquivo/{adulterado}/').status_code == 404)
        r = anon.get(cd.get(f'{M}/informes/{inf.pk}/').json()['pdf'])
        t('PDF do informe abre', r.status_code == 200)

        print('\n== ASSINAR ==')
        r = cd.post(f'{M}/{h_outro.pk}/assinar/', {'signature': PNG}, content_type='application/json')
        t('assinar o de outra pessoa: 404', r.status_code == 404, r.status_code)
        r = ca.post(f'{M}/{h_outro.pk}/assinar/', {'signature': PNG}, content_type='application/json')
        t('admin não assina pelo outro: 403 (regra da web)', r.status_code == 403, r.status_code)
        r = cd.post(f'{M}/{h1.pk}/assinar/', {'signature': 'nada'}, content_type='application/json')
        t('assinatura em formato errado: 400', r.status_code == 400 and 'inválido' in r.json()['erro'], r.content[:200])
        r = cd.post(f'{M}/{h1.pk}/assinar/', {}, content_type='application/json')
        t('sem assinatura: 400', r.status_code == 400)
        r = cd.post(f'{M}/{h1.pk}/assinar/', {'signature': PNG}, content_type='application/json',
                    HTTP_USER_AGENT='ZZ App')
        d = r.json()
        h1.refresh_from_db()
        t('assina: 200 e devolve o detalhe', r.status_code == 200 and d['assinado'] is True
          and d['pode_assinar'] is False, r.content[:200])
        t('gravou como a web (hora, hash, imagem, agente)', h1.signed_at is not None and len(h1.signature_hash) == 64
          and h1.signature_image == PNG and h1.signature_user_agent == 'ZZ App')
        t('assinatura aparece no detalhe', d['assinatura'] and d['assinatura']['imagem'] == PNG)
        r = cd.post(f'{M}/{h1.pk}/assinar/', {'signature': PNG}, content_type='application/json')
        t('assinar de novo: 409', r.status_code == 409, r.status_code)
        t('pendentes caiu para 1', cd.get(f'{M}/').json()['pendentes'] == 1)
        r = anon.get(d['pdf_assinado'])
        corpo = b''.join(r.streaming_content) if r.status_code == 200 else b''
        t('PDF assinado (com certificado) abre', r.status_code == 200 and corpo.startswith(b'%PDF'), r.status_code)
        t('holerite não assinado não tem link de assinado', cd.get(f'{M}/{h2.pk}/').json()['pdf_assinado'] is None)
        forjado = signing.dumps({'u': dono.pk, 't': 'contracheque_assinado', 'i': str(h2.pk)},
                                salt='api_mobile.arquivo', compress=True)
        t('PDF assinado de holerite sem assinatura: 404', anon.get(f'{BASE}/arquivo/{forjado}/').status_code == 404)

        print('\n== BLOQUEIO ==')
        with mock.patch('api_mobile.permissoes.bloqueio_atual',
                        return_value={'tipo': 'ferias', 'titulo': 'x', 'mensagem': 'x', 'caminho_web': '/'}):
            t('bloqueado no portal: 423', cd.get(f'{M}/').status_code == 423)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco (PDFs só em memória).')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
