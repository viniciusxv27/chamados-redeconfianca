"""Impulso: o gestor enxerga quem é de QUALQUER setor atrelado a ele.

Antes o gestor só via meta/feedback em que ele próprio era o gestor do
registro. Quem estava num setor secundário aparecia no seletor e sumia ao ser
escolhido, porque a meta tinha outro gestor.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import sys
from datetime import timedelta

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
from django.utils import timezone

from communications.models import CommunicationGroup
from impulso.models import GRUPO_ADM, GRUPO_GESTOR, ImpulsoFeedback, Meta
from impulso.utils import get_colaboradores_do_gestor, setores_do_usuario
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
    adm = CommunicationGroup.objects.filter(name__iexact=GRUPO_ADM).first()
    ges = CommunicationGroup.objects.filter(name__iexact=GRUPO_GESTOR).first()
    assert adm and ges, 'grupos do Impulso não encontrados'

    principal = Sector.objects.create(name='ZZ Setor Principal')
    secundario = Sector.objects.create(name='ZZ Setor Secundario')
    alheio = Sector.objects.create(name='ZZ Setor de Outro')

    def novo(username, setor=None, extras=(), grupos=()):
        u = User.objects.create_user(
            username=username, email=f'{username}@exemplo-teste.local',
            password='S3nha!teste', first_name=username.split('.')[1].title(),
            last_name='Teste', sector=setor)
        for g in grupos:
            u.communication_groups.add(g)
        for s in extras:
            u.sectors.add(s)
        return u

    gestor = novo('gs.gestor', principal, extras=[principal, secundario],
                  grupos=[adm, ges])
    outro_gestor = novo('gs.outrogestor', alheio, extras=[alheio], grupos=[adm, ges])

    do_principal = novo('gs.principal', principal, grupos=[adm])
    do_secundario = novo('gs.secundario', secundario, grupos=[adm])
    # Pessoa cujo setor PRINCIPAL é outro, mas que tem o setor do gestor como
    # secundário — o caso espelhado, que também tem de aparecer.
    cruzado = novo('gs.cruzado', alheio, extras=[secundario], grupos=[adm])
    de_fora = novo('gs.defora', alheio, grupos=[adm])

    print('== SETORES DO GESTOR ==')
    ids = setores_do_usuario(gestor)
    t('gestor responde pelo principal e pelo secundário',
      {principal.id, secundario.id} <= ids, ids)

    equipe = set(get_colaboradores_do_gestor(gestor).values_list('id', flat=True))
    t('equipe traz quem é do setor principal', do_principal.id in equipe)
    t('equipe traz quem é do setor secundário', do_secundario.id in equipe)
    t('equipe traz quem tem o setor do gestor como secundário', cruzado.id in equipe)
    t('equipe não traz quem é de outro setor', de_fora.id not in equipe)

    print('\n== KANBAN DE METAS ==')
    hoje = timezone.localdate()

    def nova_meta(colab, quem_gerencia, titulo):
        return Meta.objects.create(
            titulo=titulo, colaborador=colab, gestor=quem_gerencia,
            prazo=hoje + timedelta(days=7),
            aprovacao=Meta.Aprovacao.APROVADA)

    m_principal = nova_meta(do_principal, gestor, 'ZZ Meta do principal')
    # As duas seguintes têm OUTRO gestor: é exatamente o caso que sumia.
    m_secundario = nova_meta(do_secundario, outro_gestor, 'ZZ Meta do secundario')
    m_cruzado = nova_meta(cruzado, outro_gestor, 'ZZ Meta do cruzado')
    m_fora = nova_meta(de_fora, outro_gestor, 'ZZ Meta de fora')

    from impulso.views import _metas_do_usuario, _pode_ver_meta

    visiveis = set(_metas_do_usuario(gestor).values_list('id', flat=True))
    t('vê a meta de quem é do setor principal', m_principal.id in visiveis)
    t('vê a meta do setor secundário mesmo com outro gestor',
      m_secundario.id in visiveis)
    t('vê a meta de quem tem o setor dele como secundário', m_cruzado.id in visiveis)
    t('não vê meta de gente de outro setor', m_fora.id not in visiveis)

    t('consegue abrir a meta do setor secundário',
      _pode_ver_meta(gestor, m_secundario))
    t('não abre meta de fora do escopo dele', not _pode_ver_meta(gestor, m_fora))

    c = Client()
    c.force_login(gestor)
    html = c.get(f'/impulso/metas/?colaborador={do_secundario.id}').content.decode()
    t('kanban filtrado pelo colaborador do setor secundário mostra a meta',
      'ZZ Meta do secundario' in html)
    r = c.get(f'/impulso/metas/{m_secundario.id}/', follow=True)
    t('detalhe da meta abre para o gestor', r.status_code == 200
      and 'ZZ Meta do secundario' in r.content.decode(), r.status_code)

    html = c.get(f'/impulso/metas/?colaborador={de_fora.id}').content.decode()
    t('meta de fora do escopo continua invisível', 'ZZ Meta de fora' not in html)

    print('\n== FEEDBACKS ==')
    ref = hoje.replace(day=1)
    fb_sec = ImpulsoFeedback.objects.create(
        colaborador=do_secundario, gestor=outro_gestor, referencia_mes=ref,
        pontos_fortes='ZZ Forte do secundario', pontos_melhoria='ZZ Melhorar secundario')
    fb_fora = ImpulsoFeedback.objects.create(
        colaborador=de_fora, gestor=outro_gestor, referencia_mes=ref,
        pontos_fortes='ZZ Forte de fora', pontos_melhoria='ZZ Melhorar fora')

    html = c.get('/impulso/feedbacks/').content.decode()
    from impulso.models import ImpulsoFeedback as IF
    from impulso.views import feedback_list  # noqa: F401  (import válido)
    t('vê feedback de quem é do setor secundário',
      do_secundario.get_full_name() in html, html.count('gs.secundario'))
    t('não vê feedback de gente de outro setor',
      de_fora.get_full_name() not in html)
    t('feedback do setor secundário está no conjunto do gestor',
      fb_sec.id in set(IF.objects.filter(
          colaborador_id__in=get_colaboradores_do_gestor(gestor)
          .values_list('id', flat=True)).values_list('id', flat=True)))

    print('\n== O COLABORADOR NÃO GANHOU PODER NENHUM ==')
    cc = Client()
    cc.force_login(do_principal)
    t('colaborador continua vendo só as metas dele',
      set(_metas_do_usuario(do_principal).values_list('id', flat=True)) == {m_principal.id})
    t('colaborador não abre meta de colega',
      not _pode_ver_meta(do_principal, m_secundario))
    r = cc.get(f'/impulso/metas/{m_secundario.id}/', follow=True)
    t('tela recusa o colaborador', 'Sem permissão' in r.content.decode()
      or 'ZZ Meta do secundario' not in r.content.decode())

    print('\n== GESTOR SEM SETOR CADASTRADO ==')
    sem_setor = novo('gs.semsetor', None, grupos=[adm, ges])
    t('gestor sem setor continua vendo a rede inteira (não nasce tela vazia)',
      get_colaboradores_do_gestor(sem_setor).count() > 0)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
