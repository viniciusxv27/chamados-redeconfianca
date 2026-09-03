"""Escolher o gestor responsável ao criar a meta em /impulso/metas/nova/.

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
from core.models import Notification
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

    minha = Sector.objects.create(name='ZZ Area Minha')
    outra = Sector.objects.create(name='ZZ Area Outra')

    def novo(username, setor, grupos=()):
        u = User.objects.create_user(
            username=username, email=f'{username}@exemplo-teste.local',
            password='S3nha!teste', sector=setor,
            first_name=username.split('.')[1].title(), last_name='Teste')
        for g in grupos:
            u.communication_groups.add(g)
        return u

    criador = novo('mg.criador', minha, [adm, ges])
    colega = novo('mg.colega', minha, [adm, ges])      # outro gestor da MESMA área
    distante = novo('mg.distante', outra, [adm, ges])  # gestor de OUTRA área
    colab = novo('mg.colab', minha, [adm])
    colab_de_fora = novo('mg.forinha', outra, [adm])

    hoje = timezone.localdate()
    prazo = (hoje + timedelta(days=7)).isoformat()

    c = Client()
    c.force_login(criador)

    print('== A TELA ==')
    html = c.get('/impulso/metas/nova/').content.decode()
    t('existe o seletor de gestor', 'name="gestor"' in html and 'id="impGestor"' in html)
    t('com o rótulo certo', 'Gestor responsável' in html)
    t('quem cria vem marcado por padrão', '(você)' in html)
    t('o colega aparece na lista', colega.get_full_name() in html)
    t('explica o que o gestor faz', 'avalia a meta no fim' in html)
    t('some quando o colaborador é de outra área',
      "dono.classList.toggle('hidden', !!info)" in html)

    def criar(**extra):
        dados = {'colaborador': colab.id, 'titulo': f'ZZ {extra.get("titulo", "meta")}',
                 'descricao': 'descrição', 'prazo': prazo,
                 'recorrencia': Meta.Recorrencia.UNICA}
        dados.update(extra)
        r = c.post('/impulso/metas/nova/', dados, follow=True)
        return Meta.objects.filter(titulo=dados['titulo']).first(), r

    print('\n== ESCOLHENDO OUTRO GESTOR ==')
    meta, r = criar(titulo='ZZ com colega', gestor=colega.id)
    t('a meta é criada', meta is not None)
    t('e fica no nome do gestor escolhido', meta and meta.gestor_id == colega.id,
      meta.gestor_id if meta else None)
    t('o colaborador continua o escolhido', meta and meta.colaborador_id == colab.id)
    t('nasce aprovada (mesma área)', meta and meta.aprovacao == Meta.Aprovacao.APROVADA)
    t('registra quem criou', meta and meta.created_by_id == criador.id)
    t('a tela diz quem ficou responsável',
      colega.get_full_name() in r.content.decode())
    t('o gestor escolhido é avisado',
      Notification.objects.filter(user=colega, title='Meta criada no seu nome').exists())

    print('\n== SEM ESCOLHER, CONTINUA COM QUEM CRIOU ==')
    meta, r = criar(titulo='ZZ sem escolha')
    t('fica com o criador', meta and meta.gestor_id == criador.id)
    t('e não manda aviso à toa',
      not Notification.objects.filter(user=criador, title='Meta criada no seu nome').exists())

    print('\n== ESCOLHA INVÁLIDA NÃO PASSA ==')
    naogestor = colab.id
    meta, r = criar(titulo='ZZ nao gestor', gestor=naogestor)
    t('quem não é gestor do Impulso não vira responsável',
      meta and meta.gestor_id == criador.id, meta.gestor_id if meta else None)

    meta, r = criar(titulo='ZZ id lixo', gestor='abc')
    t('id ilegível cai no criador', meta and meta.gestor_id == criador.id)

    meta, r = criar(titulo='ZZ inexistente', gestor=99999999)
    t('id inexistente cai no criador', meta and meta.gestor_id == criador.id)

    print('\n== OUTRA ÁREA: A TRAVA CONTINUA VALENDO ==')
    meta, r = criar(titulo='ZZ fora da area', colaborador=colab_de_fora.id,
                    gestor=colega.id, gestor_aprovador=distante.id)
    t('a meta é criada', meta is not None)
    t('quem fica com ela é o gestor da área do colaborador',
      meta and meta.gestor_id == distante.id, meta.gestor_id if meta else None)
    t('a escolha livre não fura a trava', meta and meta.gestor_id != colega.id)
    t('e vai para aprovação', meta and meta.aprovacao == Meta.Aprovacao.PENDENTE,
      meta.aprovacao if meta else None)

    print('\n== O COLABORADOR NÃO GANHOU PODER NOVO ==')
    cc = Client(); cc.force_login(colab)
    html = cc.get('/impulso/metas/nova/').content.decode()
    t('a tela dele não tem o seletor de gestor responsável',
      'Gestor responsável' not in html)
    t('mas continua escolhendo para quem pedir', 'name="gestor"' in html)

    antes = Meta.objects.count()
    r = cc.post('/impulso/metas/nova/', {
        'colaborador': colab_de_fora.id,          # tentando criar para outro
        'gestor': distante.id,
        'titulo': 'ZZ do colaborador', 'descricao': 'x', 'prazo': prazo,
        'recorrencia': Meta.Recorrencia.UNICA}, follow=True)
    criada = Meta.objects.filter(titulo='ZZ do colaborador').first()
    t('a meta do colaborador é sempre para ele mesmo',
      criada is None or criada.colaborador_id == colab.id,
      criada.colaborador_id if criada else None)
    t('e só para gestor do setor dele',
      criada is None or criada.gestor_id != distante.id)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
