"""Impulso: quem está em "ADM's LOJAS" participa como o pessoal do ESCRITÓRIO (ADM).

Pedido: o GRUPO_ADM deve ter o grupo "ADM's LOJAS" também. No banco o grupo
está cadastrado como "ADM's LOJAS " (espaço no fim) — a comparação precisa
tolerar isso, senão os seis ADMs continuam fora sem ninguém entender por quê.

Também cobre a regressão do meio do caminho: com GRUPO_ADM virando tupla, o
menu do Impulso quebrava TODA página de quem não é superusuário
("function upper(record) does not exist").

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import sys
from unittest import mock

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

from communications.models import CommunicationGroup
from impulso import utils
from impulso.models import GRUPO_ADM, GRUPO_GESTOR, GRUPOS_ADM
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


print('== AS CONSTANTES ==')
t('GRUPO_ADM continua sendo o nome principal (string)', GRUPO_ADM == 'ESCRITÓRIO (ADM)', GRUPO_ADM)
t('GRUPOS_ADM tem os dois grupos', GRUPOS_ADM == ('ESCRITÓRIO (ADM)', "ADM's LOJAS"), GRUPOS_ADM)
t('GESTORES não mudou', GRUPO_GESTOR == 'GESTORES (IMPULSO)')

marcador = transaction.atomic()
marcador.__enter__()
try:
    escritorio = CommunicationGroup.objects.filter(name__iexact=GRUPO_ADM).first()
    gestores = CommunicationGroup.objects.filter(name__iexact=GRUPO_GESTOR).first()
    t('o grupo ESCRITÓRIO (ADM) existe no banco', escritorio is not None)
    t('o grupo GESTORES (IMPULSO) existe no banco', gestores is not None)
    # O grupo real tem espaço no fim. Para provar a tolerância sem depender de
    # como ele estiver cadastrado no dia, o teste cria grupos próprios com o
    # prefixo ZZ e, nas checagens de tolerância, inclui "ZZ ADM's LOJAS" (sem
    # espaço, em maiúsculas) na lista de nomes aceitos.
    loja = Sector.objects.create(name='ZZ Loja Grupos')
    criador = User.objects.create_user(
        username='zz.criador.grupos', email='zz.criador.grupos@exemplo-teste.local',
        password='S3nha!teste', first_name='ZZ', last_name='Criador', sector=loja)
    lojas_espaco = CommunicationGroup.objects.create(name="ZZ ADM's LOJAS ", description='teste', created_by=criador)
    lojas_caixa = CommunicationGroup.objects.create(name="zz adm's lojas", description='teste', created_by=criador)
    outro = CommunicationGroup.objects.create(name='ZZ Outro grupo', description='teste', created_by=criador)

    def novo(u, nome, grupos=(), **kw):
        x = User.objects.create_user(
            username=u, email=f'{u}@exemplo-teste.local', password='S3nha!teste',
            first_name=nome, last_name='Teste', sector=loja, **kw)
        for g in grupos:
            x.communication_groups.add(g)
        return x

    real_lojas = CommunicationGroup.objects.filter(
        name__iregex=r"^\s*ADM's LOJAS\s*$").first()
    escritorio_u = novo('zz.escritorio', 'ZZEscritorio', [escritorio])
    adm_loja = novo('zz.admloja', 'ZZAdmLoja', [real_lojas] if real_lojas else [lojas_espaco])
    adm_loja_espaco = novo('zz.admespaco', 'ZZAdmEspaco', [lojas_espaco])
    adm_loja_caixa = novo('zz.admcaixa', 'ZZAdmCaixa', [lojas_caixa])
    gestor = novo('zz.gestorg', 'ZZGestor', [gestores])
    ninguem = novo('zz.ninguem', 'ZZNinguem', [outro])
    inativo = novo('zz.inativo', 'ZZInativo', [escritorio], is_active=False)

    TOLERANTE = mock.patch.object(utils, 'GRUPOS_ADM', GRUPOS_ADM + ("ZZ ADM's LOJAS",))

    print('\n== QUEM ENTRA NO MÓDULO ==')
    t('ESCRITÓRIO (ADM) entra', utils.is_impulso_member(escritorio_u))
    t("ADM's LOJAS (grupo real do banco%s) entra" % ('' if real_lojas else ' — ausente, usa variante'),
      utils.is_impulso_member(adm_loja))
    t('variante com espaço no fim NÃO entra pelo nome exato', not utils.is_impulso_member(adm_loja_espaco))
    with TOLERANTE:
        t('grupo com espaço no fim do nome entra quando o nome está na lista', utils.is_impulso_member(adm_loja_espaco))
        t('e sem diferenciar maiúsculas', utils.is_impulso_member(adm_loja_caixa))
    t('gestor entra', utils.is_impulso_member(gestor))
    t('quem está em outro grupo não entra', not utils.is_impulso_member(ninguem))
    t('ADM de loja não vira gestor por isso', not utils.is_impulso_manager(adm_loja))
    t('_in_group aceita um nome só', utils._in_group(escritorio_u, GRUPO_ADM))
    t('_in_group aceita a tupla', utils._in_group(adm_loja_espaco, ("ZZ ADM's LOJAS", 'nada')))
    t('_in_group com tupla não casa por pedaço do nome', not utils._in_group(ninguem, ('Outro',)))

    print('\n== A LISTA DE COLABORADORES (alvos de metas, ciclos, filtros) ==')
    ids = set(utils.get_colaboradores().values_list('id', flat=True))
    t('inclui o escritório', escritorio_u.id in ids)
    t("inclui ADM's LOJAS", adm_loja.id in ids)
    t('não inclui a variante ZZ (nome diferente)', adm_loja_espaco.id not in ids)
    with TOLERANTE:
        ids_tol = set(utils.get_colaboradores().values_list('id', flat=True))
        t('inclui a variante com espaço no fim', adm_loja_espaco.id in ids_tol)
        t('inclui a variante em minúsculas', adm_loja_caixa.id in ids_tol)
    t('não inclui quem é só gestor', gestor.id not in ids)
    t('não inclui outros grupos', ninguem.id not in ids)
    t('não inclui inativos', inativo.id not in ids)
    t('sem duplicar quem está nos dois grupos', True)
    adm_loja.communication_groups.add(escritorio)
    t('… de fato', utils.get_colaboradores().filter(id=adm_loja.id).count() == 1)
    t('o gestor atende os ADMs de loja do setor dele',
      adm_loja.id in set(utils.get_colaboradores_do_gestor(gestor).values_list('id', flat=True)))
    t('gestores continuam vindo do grupo certo',
      gestor.id in set(utils.get_gestores().values_list('id', flat=True))
      and escritorio_u.id not in set(utils.get_gestores().values_list('id', flat=True)))

    print('\n== AS TELAS ==')
    c = Client()
    c.force_login(adm_loja)
    r = c.get('/impulso/')
    t('ADM de loja abre o Impulso (200, sem redirecionar)', r.status_code == 200, r.status_code)
    r = c.get('/')
    t('a home renderiza para quem não é superusuário (regressão do upper(record))',
      r.status_code == 200, r.status_code)
    t('o menu mostra o Impulso', '/impulso/' in r.content.decode())
    c2 = Client()
    c2.force_login(ninguem)
    r = c2.get('/')
    t('quem não participa também vê a home normal', r.status_code == 200, r.status_code)
    r = c2.get('/impulso/', follow=False)
    t('e não entra no Impulso', r.status_code == 302, r.status_code)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
