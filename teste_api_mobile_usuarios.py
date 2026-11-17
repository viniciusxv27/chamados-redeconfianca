"""API do app — módulo Usuários (/api/mobile/v1/m/usuarios/).

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import sys

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

from communications.models import CommunicationGroup
from users.models import Sector, UserModuleAccess

User = get_user_model()
ok = fail = 0
BASE = '/api/mobile/v1'
M = f'{BASE}/m/usuarios'


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


for nome in settings.CACHES:
    caches[nome].clear()

marcador = transaction.atomic()
marcador.__enter__()
try:
    setor = Sector.objects.create(name='ZZ Setor Usuarios App')
    outro = Sector.objects.create(name='ZZ Outro Setor Usuarios App')

    def novo(apelido, hierarquia='PADRAO', setor_=setor, **kw):
        return User.objects.create_user(
            username=f'zzusr.{apelido}', email=f'zzusr.{apelido}@exemplo-teste.local', password='S3nha!teste',
            first_name=f'ZZUsr{apelido.title()}', last_name='Teste', sector=setor_, hierarchy=hierarquia, **kw)

    chefe = novo('chefe', 'SUPERADMIN', outro)
    supervisor = novo('supervisor', 'SUPERVISOR')
    padrao = novo('padrao')
    liberado = novo('liberado')
    UserModuleAccess.objects.create(user=liberado, module_key='usuarios.gerenciar', granted_by=chefe)
    alvo = novo('alvo', setor_=outro, phone='(11) 98765-4321', job_title='Vendedor ZZ',
                status='FERIAS', is_active=True)
    alvo.sectors.add(setor)
    grupo = CommunicationGroup.objects.create(name='ZZ Grupo Usuarios App', created_by=chefe)
    grupo.members.add(alvo)
    desligado = novo('desligado', status='INATIVO', is_active=False, phone='12345')
    alvo.refresh_from_db()

    def entrar(user):
        r = Client().post(f'{BASE}/auth/login/', {'email': user.email, 'senha': 'S3nha!teste', 'plataforma': 'android',
                                                   'dispositivo': 'ZZ Pixel'}, content_type='application/json')
        assert r.status_code == 200, r.content[:300]
        return Client(HTTP_AUTHORIZATION=f'Bearer {r.json()["token"]}')

    cc, cs, cp, cl = entrar(chefe), entrar(supervisor), entrar(padrao), entrar(liberado)

    print('== ACESSO ==')
    for nome, user, cli, esperado in [('SUPERADMIN', chefe, cc, True), ('SUPERVISOR', supervisor, cs, True),
                                      ('PADRÃO', padrao, cp, False), ('PADRÃO com liberação', liberado, cl, True)]:
        t(f'{nome}: regra do portal (can_manage_users) = {esperado}', user.can_manage_users() is esperado)
        chaves = {m['chave'] for m in cli.get(f'{BASE}/modulos/').json()}
        t(f'{nome}: módulo na lista = {esperado}', ('usuarios' in chaves) is esperado, chaves)
        r = cli.get(f'{M}/')
        t(f'{nome}: lista {"200" if esperado else "403"}', r.status_code == (200 if esperado else 403), r.status_code)
        r = cli.get(f'{M}/{alvo.pk}/')
        t(f'{nome}: detalhe {"200" if esperado else "403"}', r.status_code == (200 if esperado else 403), r.status_code)
        t(f'{nome}: filtros {"200" if esperado else "403"}',
          cli.get(f'{M}/filtros/').status_code == (200 if esperado else 403))
    t('sem token: 401/403', Client().get(f'{M}/').status_code in (401, 403))

    print('\n== RECORTE ==')
    # A web lista todos para quem passa no gate (users/views.py:264): o supervisor vê gente de outro setor.
    r = cs.get(f'{M}/', {'busca': 'ZZUsr'})
    ids = {u['id'] for u in r.json()['resultados']}
    t('supervisor vê todos (como a web), inclusive outro setor e inativos',
      {chefe.pk, supervisor.pk, padrao.pk, liberado.pk, alvo.pk, desligado.pk} <= ids, ids)
    t('liberação individual vê o mesmo recorte',
      {u['id'] for u in cl.get(f'{M}/', {'busca': 'ZZUsr'}).json()['resultados']} == ids)
    t('id inexistente: 404', cs.get(f'{M}/999999999/').status_code == 404)
    t('PADRÃO sem gate não abre detalhe de ninguém (403)', cp.get(f'{M}/{padrao.pk}/').status_code == 403)

    print('\n== BUSCA E FILTROS ==')
    def ids_de(cli, **params):
        return {u['id'] for u in cli.get(f'{M}/', params).json()['resultados']}

    t('busca por login', ids_de(cs, busca='zzusr.alvo') == {alvo.pk})
    t('busca por e-mail', ids_de(cs, busca='zzusr.desligado@exemplo') == {desligado.pk})
    t('busca por nome completo', ids_de(cs, busca='ZZUsrAlvo Teste') == {alvo.pk})
    t('busca pelo nome do setor', alvo.pk in ids_de(cs, busca='ZZ Outro Setor Usuarios'))
    t('filtro setor (principal)', ids_de(cs, busca='ZZUsr', setor=outro.pk) == {chefe.pk, alvo.pk})
    t('filtro hierarquia', ids_de(cs, busca='ZZUsr', hierarquia='SUPERVISOR') == {supervisor.pk})
    t('filtro situação férias', ids_de(cs, busca='ZZUsr', status='ferias') == {alvo.pk})
    t('filtro situação inativo', ids_de(cs, busca='ZZUsr', status='INATIVO') == {desligado.pk})
    t('filtro inválido é ignorado', ids_de(cs, busca='ZZUsr', status='xyz', hierarquia='abc', setor='a') == ids)
    r = cs.get(f'{M}/', {'busca': 'ZZUsr'}).json()
    t('paginação no formato do app', {'resultados', 'pagina', 'total', 'tem_mais'} <= set(r) and r['total'] == 6)
    f = cs.get(f'{M}/filtros/').json()
    t('filtros trazem setores, hierarquias e situações',
      any(s['id'] == outro.pk for s in f['setores']) and len(f['hierarquias']) == 5 and len(f['situacoes']) == 4)
    t('totais do topo', f['totais']['usuarios'] == User.objects.count()
      and f['totais']['ativos'] == User.objects.filter(is_active=True).count())

    print('\n== DETALHE ==')
    d = cs.get(f'{M}/{alvo.pk}/').json()
    t('identificação e lotação', d['nome'] == 'ZZUsrAlvo Teste' and d['login'] == 'zzusr.alvo'
      and d['email'] == alvo.email and d['cargo'] == 'Vendedor ZZ' and d['setor_principal'] == outro.name
      and set(d['setores']) == {setor.name, outro.name}, d)
    t('grupos de comunicação', d['grupos'] == ['ZZ Grupo Usuarios App'], d.get('grupos'))
    t('situação e férias', d['status'] == 'FERIAS' and d['status_nome'] == 'Férias' and 'ferias_inicio' in d)
    t('WhatsApp com 55 + DDD', d['whatsapp'] == '5511987654321', d.get('whatsapp'))
    t('telefone estranho não vira WhatsApp', cs.get(f'{M}/{desligado.pk}/').json()['whatsapp'] is None)
    t('acesso_ativo reflete is_active', cs.get(f'{M}/{desligado.pk}/').json()['acesso_ativo'] is False)
    proibidos = {'password', 'senha', 'login_code', 'cpf', 'pre_registration_token', 'token', 'salary', 'pix_key'}
    texto = cc.get(f'{M}/{alvo.pk}/').content.decode()
    t('nada de senha/hash/código de login/CPF', not (proibidos & set(d)) and alvo.password not in texto)

    print('\n== SALDO C$ (como a coluna da web) ==')
    t('SUPERVISOR não vê saldo (templates/admin/users.html:247)', 'saldo_cs' not in d)
    t('PADRÃO com liberação não vê saldo', 'saldo_cs' not in cl.get(f'{M}/{alvo.pk}/').json())
    t('SUPERADMIN vê saldo', 'saldo_cs' in cc.get(f'{M}/{alvo.pk}/').json())

    print('\n== SÓ LEITURA ==')
    t('POST na lista não é aceito', cc.post(f'{M}/', {}, content_type='application/json').status_code == 405)
    t('DELETE no detalhe não é aceito', cc.delete(f'{M}/{alvo.pk}/').status_code == 405)
    alvo.refresh_from_db()
    t('nada mudou no usuário', alvo.is_active and alvo.status == 'FERIAS')
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)

print(f'\n{ok} OK, {fail} falhas')
sys.exit(1 if fail else 0)
