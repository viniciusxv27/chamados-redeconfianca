"""Impulso: exclusão de card pelo gestor e demanda para outra área.

1. O gestor apaga qualquer card que ele enxerga (a mesma régua do Kanban).
2. Demanda para colaborador de OUTRA área só entra na fila depois que o gestor
   daquela área aprovar.

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
from impulso.models import GRUPO_ADM, GRUPO_GESTOR, Meta
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

    area_a = Sector.objects.create(name='ZZ Area A')
    area_b = Sector.objects.create(name='ZZ Area B')
    area_orfa = Sector.objects.create(name='ZZ Area Sem Gestor')

    def novo(username, setor, grupos):
        u = User.objects.create_user(
            username=username, email=f'{username}@exemplo-teste.local',
            password='S3nha!teste', first_name=username.split('.')[1].title(),
            last_name='Teste', sector=setor)
        for g in grupos:
            u.communication_groups.add(g)
        return u

    gestor_a = novo('ma.gestora', area_a, [adm, ges])
    gestor_b = novo('ma.gestorb', area_b, [adm, ges])
    gestor_b2 = novo('ma.gestorb2', area_b, [adm, ges])
    colab_a = novo('ma.colaba', area_a, [adm])
    colab_b = novo('ma.colabb', area_b, [adm])
    colab_orfao = novo('ma.colaborfao', area_orfa, [adm])

    hoje = timezone.localdate()
    prazo = (hoje + timedelta(days=7)).isoformat()

    ca = Client()
    ca.force_login(gestor_a)
    cb = Client()
    cb.force_login(gestor_b)

    print('== DEMANDA PARA A PRÓPRIA ÁREA ==')
    ca.post('/impulso/metas/nova/', {
        'colaborador': colab_a.id, 'titulo': 'ZZ Meta da minha area',
        'descricao': 'Descrição', 'prazo': prazo, 'recorrencia': 'UNICA'}, follow=True)
    minha = Meta.objects.filter(titulo='ZZ Meta da minha area').first()
    t('meta criada', minha is not None)
    t('entra no Kanban direto', minha and minha.aprovacao == Meta.Aprovacao.APROVADA,
      minha.aprovacao if minha else '')
    t('o gestor que criou é o gestor da meta', minha and minha.gestor_id == gestor_a.id)
    t('não vira solicitação de ninguém', minha and minha.solicitada_por_id is None)

    print('\n== DEMANDA PARA OUTRA ÁREA ==')
    r = ca.post('/impulso/metas/nova/', {
        'colaborador': colab_b.id, 'titulo': 'ZZ Demanda para outra area',
        'descricao': 'Preciso disso da área B.', 'prazo': prazo,
        'recorrencia': 'UNICA', 'gestor_aprovador': gestor_b.id}, follow=True)
    cruzada = Meta.objects.filter(titulo='ZZ Demanda para outra area').first()
    t('meta criada', cruzada is not None)
    t('NÃO entra no Kanban: fica aguardando aprovação',
      cruzada and cruzada.aprovacao == Meta.Aprovacao.PENDENTE,
      cruzada.aprovacao if cruzada else '')
    t('quem pediu fica registrado', cruzada and cruzada.solicitada_por_id == gestor_a.id)
    t('quem decide é o gestor da área do colaborador',
      cruzada and cruzada.gestor_id == gestor_b.id, cruzada.gestor_id if cruzada else '')
    t('a tela avisa que foi para aprovação',
      'enviada para o gestor da área' in r.content.decode())

    t('o gestor da área pode decidir', cruzada.pode_decidir(gestor_b))
    t('outro gestor da mesma área também decide', cruzada.pode_decidir(gestor_b2))
    t('quem pediu NÃO aprova o próprio pedido', not cruzada.pode_decidir(gestor_a))
    t('o colaborador não decide', not cruzada.pode_decidir(colab_b))

    from impulso.views import _metas_do_usuario
    t('a meta pendente não aparece no Kanban de ninguém',
      cruzada.id not in set(_metas_do_usuario(colab_b).values_list('id', flat=True)))

    print('\n== APROVAÇÃO PELA ÁREA DE DESTINO ==')
    cb.post(f'/impulso/metas/{cruzada.id}/decidir/', {'decisao': 'aprovar'}, follow=True)
    cruzada.refresh_from_db()
    t('aprovada pelo gestor da área', cruzada.aprovacao == Meta.Aprovacao.APROVADA,
      cruzada.aprovacao)
    t('registra quem decidiu', cruzada.decidida_por_id == gestor_b.id)
    t('agora entra no Kanban do colaborador',
      cruzada.id in set(_metas_do_usuario(colab_b).values_list('id', flat=True)))

    print('\n== ÁREA SEM GESTOR ==')
    r = ca.post('/impulso/metas/nova/', {
        'colaborador': colab_orfao.id, 'titulo': 'ZZ Demanda sem destino',
        'descricao': 'x', 'prazo': prazo, 'recorrencia': 'UNICA'}, follow=True)
    t('não cria demanda sem ter quem aprove',
      not Meta.objects.filter(titulo='ZZ Demanda sem destino').exists())
    t('explica o que fazer', 'não há gestor do Impulso cadastrado' in r.content.decode())

    print('\n== O FORMULÁRIO AVISA ANTES DE SALVAR ==')
    html = ca.get('/impulso/metas/nova/').content.decode()
    t('manda o mapa de quem é de outra área', 'impForaJson' in html)
    t('o colaborador da outra área está no mapa', f'"{colab_b.id}"' in html)
    t('o mapa traz o nome da área', 'ZZ Area B' in html)
    t('o mapa traz quem aprova', gestor_b.get_full_name() in html)
    t('o formulário tem o seletor de aprovador', 'gestor_aprovador' in html)

    print('\n== EXCLUSÃO DE CARD PELO GESTOR ==')
    # Meta criada por OUTRO gestor, para colaborador da área do gestor A.
    de_outro = Meta.objects.create(
        titulo='ZZ Card de outro gestor', colaborador=colab_a, gestor=gestor_b,
        prazo=hoje + timedelta(days=5), aprovacao=Meta.Aprovacao.APROVADA,
        created_by=gestor_b)
    t('gestor apaga card de gente da área dele, mesmo criado por outro',
      de_outro.pode_excluir(gestor_a))
    t('o gestor da meta também apaga', de_outro.pode_excluir(gestor_b))

    fora = Meta.objects.create(
        titulo='ZZ Card de outra area', colaborador=colab_b, gestor=gestor_b,
        prazo=hoje + timedelta(days=5), aprovacao=Meta.Aprovacao.APROVADA,
        created_by=gestor_b)
    t('gestor não apaga card de área que não é dele', not fora.pode_excluir(gestor_a))
    t('colaborador nunca apaga', not de_outro.pode_excluir(colab_a))

    pendente = Meta.objects.create(
        titulo='ZZ Solicitacao pendente', colaborador=colab_a, gestor=gestor_a,
        prazo=hoje + timedelta(days=5), aprovacao=Meta.Aprovacao.PENDENTE,
        solicitada_por=colab_a, created_by=colab_a)
    t('solicitação pendente não se apaga (para isso existe o recusar)',
      not pendente.pode_excluir(gestor_a))

    r = ca.post(f'/impulso/metas/{de_outro.id}/excluir/', follow=True)
    t('exclusão funciona pela tela', not Meta.objects.filter(id=de_outro.id).exists(),
      r.status_code)

    r = ca.post(f'/impulso/metas/{fora.id}/excluir/', follow=True)
    t('a tela recusa card de outra área', Meta.objects.filter(id=fora.id).exists())

    cc = Client()
    cc.force_login(colab_a)
    r = cc.post(f'/impulso/metas/{fora.id}/excluir/', follow=True)
    t('colaborador não apaga pela tela', Meta.objects.filter(id=fora.id).exists())

    print('\n== O BOTÃO APARECE NO CARD ==')
    minha_visivel = Meta.objects.create(
        titulo='ZZ Card visivel', colaborador=colab_a, gestor=gestor_b,
        prazo=hoje + timedelta(days=5), aprovacao=Meta.Aprovacao.APROVADA,
        created_by=gestor_b)
    html = ca.get('/impulso/metas/').content.decode()
    t('o card do gestor traz a lixeira',
      f'data-id="{minha_visivel.id}"' in html and 'imp-excluir' in html)
    html_colab = cc.get('/impulso/metas/').content.decode()
    # O seletor `.imp-excluir` aparece no script da página de qualquer forma;
    # o que não pode existir é o botão.
    t('o colaborador não vê lixeira nenhuma', 'class="imp-excluir' not in html_colab)
    t('e o card dele confirma que não pode apagar',
      not minha_visivel.pode_excluir(colab_a))

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
