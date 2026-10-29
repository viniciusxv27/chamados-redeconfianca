"""SUPERADMIN nas metas do Impulso (/impulso/metas/).

1. Filtro "Criado por" no Kanban — só o SUPERADMIN vê e só para ele vale.
2. Ao criar, o SUPERADMIN escolhe "Enviar para aprovação do Gestor". Desmarcado
   (padrão), a meta nasce aprovada por ele; marcado, segue a aprovação de hoje.
   Quem não é SUPERADMIN continua igual, mesmo mandando o campo no POST.

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

from unittest import mock

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


# Nada sai do processo: o fluxo de aprovação só cria notificação no portal, mas
# se algum dia mandar WhatsApp/e-mail, o envio fica preso aqui também.
patches = [mock.patch('django.core.mail.send_mail', return_value=1),
           mock.patch('django.core.mail.EmailMessage.send', return_value=1)]
for p in patches:
    p.start()

marcador = transaction.atomic()
marcador.__enter__()
try:
    adm = CommunicationGroup.objects.filter(name__iexact=GRUPO_ADM).first()
    ges = CommunicationGroup.objects.filter(name__iexact=GRUPO_GESTOR).first()
    assert adm and ges, 'grupos do Impulso não encontrados'

    minha = Sector.objects.create(name='ZZ SA Area')

    def novo(username, setor, grupos=(), **extra):
        u = User.objects.create_user(
            username=username, email=f'{username}@exemplo-teste.local',
            password='S3nha!teste', sector=setor,
            first_name=username.split('.')[1].title(), last_name='Teste', **extra)
        for g in grupos:
            u.communication_groups.add(g)
        return u

    sa = novo('zsa.chefe', minha, [adm, ges], hierarchy='SUPERADMIN')
    sa_sem_gestor = novo('zsa.solo', minha, [adm], hierarchy='SUPERADMIN')
    criador = novo('zsa.criador', minha, [adm, ges])     # gestor comum
    colega = novo('zsa.colega', minha, [adm, ges])       # outro gestor da área
    colab = novo('zsa.colab', minha, [adm])

    hoje = timezone.localdate()
    prazo = (hoje + timedelta(days=7)).isoformat()

    def criar(cliente, titulo, **extra):
        dados = {'colaborador': colab.id, 'titulo': titulo, 'descricao': 'descrição',
                 'prazo': prazo, 'recorrencia': Meta.Recorrencia.UNICA}
        dados.update(extra)
        r = cliente.post('/impulso/metas/nova/', dados, follow=True)
        return Meta.objects.filter(titulo=titulo).first(), r

    c_sa = Client(); c_sa.force_login(sa)
    c_cri = Client(); c_cri.force_login(criador)
    c_col = Client(); c_col.force_login(colab)
    c_colega = Client(); c_colega.force_login(colega)

    print('== CRIAR: A TELA ==')
    html = c_sa.get('/impulso/metas/nova/').content.decode()
    t('SUPERADMIN vê "Enviar para aprovação do Gestor"',
      'name="enviar_aprovacao"' in html and 'Enviar para aprovação do Gestor' in html)
    t('a caixa vem desmarcada', 'id="impEnviarAprovacao"' in html
      and 'id="impEnviarAprovacao" checked' not in html)
    for nome, cli in (('gestor comum', c_cri), ('colaborador', c_col)):
        t(f'{nome} não vê a caixa',
          'name="enviar_aprovacao"' not in cli.get('/impulso/metas/nova/').content.decode())

    print('\n== CRIAR: SUPERADMIN DIRETO (padrão) ==')
    Notification.objects.filter(user__in=[colab, colega, criador, sa]).delete()
    antes = timezone.now()
    meta, r = criar(c_sa, 'ZZSA direta')
    t('a meta é criada', meta is not None)
    t('nasce APROVADA', meta and meta.aprovacao == Meta.Aprovacao.APROVADA,
      meta and meta.aprovacao)
    t('aprovada pelo SUPERADMIN', meta and meta.decidida_por_id == sa.id)
    t('com data da aprovação', meta and meta.decidida_em and meta.decidida_em >= antes)
    t('não é "solicitação" de ninguém', meta and meta.solicitada_por_id is None)
    t('gestor responsável é o escolhido (ele mesmo)', meta and meta.gestor_id == sa.id)
    t('nada pendente de aprovação',
      not Meta.objects.filter(titulo='ZZSA direta', aprovacao=Meta.Aprovacao.PENDENTE).exists())
    t('nenhum gestor é chamado para aprovar',
      not Notification.objects.filter(title__in=['Nova atividade para aprovar',
                                                 'Demanda de outra área para aprovar',
                                                 'Nova solicitação de meta']).filter(
          user__in=[colega, criador]).exists())
    t('o colaborador é avisado da meta',
      Notification.objects.filter(user=colab, title='Nova meta atribuída').exists())

    meta, r = criar(c_sa, 'ZZSA direta colega', gestor=colega.id)
    t('direto com outro gestor responsável: aprovada', meta and meta.vale_pontos)
    t('e o gestor escolhido só é informado',
      Notification.objects.filter(user=colega, title='Meta criada no seu nome').exists()
      and not Notification.objects.filter(user=colega, title='Nova atividade para aprovar').exists())

    meta, r = criar(c_sa, 'ZZSA campo vazio', enviar_aprovacao='')
    t('campo vazio = direto', meta and meta.vale_pontos)

    print('\n== CRIAR: SUPERADMIN PEDINDO APROVAÇÃO ==')
    meta, r = criar(c_sa, 'ZZSA aprovar colega', gestor=colega.id, enviar_aprovacao='on')
    t('fica PENDENTE', meta and meta.aprovacao == Meta.Aprovacao.PENDENTE,
      meta and meta.aprovacao)
    t('com o gestor escolhido para aprovar', meta and meta.gestor_id == colega.id)
    t('registrada como pedida pelo SUPERADMIN', meta and meta.solicitada_por_id == sa.id)
    t('ainda sem decisão', meta and meta.decidida_por_id is None and meta.decidida_em is None)
    t('o gestor é avisado para aprovar',
      Notification.objects.filter(user=colega, title='Nova atividade para aprovar').exists())
    t('o gestor pode decidir', meta and meta.pode_decidir(colega))
    t('entra na fila de solicitações do gestor',
      'ZZSA aprovar colega' in c_colega.get('/impulso/metas/solicitacoes/').content.decode())
    t('fora do Kanban até aprovar',
      'ZZSA aprovar colega' not in c_colega.get('/impulso/metas/?mes=').content.decode())
    c_colega.post(f'/impulso/metas/{meta.id}/decidir/', {'decisao': 'aprovar'})
    meta.refresh_from_db()
    t('o gestor aprova como hoje', meta.aprovacao == Meta.Aprovacao.APROVADA
      and meta.decidida_por_id == colega.id)
    t('quem pediu (SUPERADMIN) é avisado',
      Notification.objects.filter(user=sa, title='Pedido aprovado').exists())

    meta, r = criar(c_sa, 'ZZSA aprovar area', enviar_aprovacao='on',
                    gestor_aprovador=criador.id)
    t('sendo ele o gestor: vai para o gestor da área do colaborador',
      meta and meta.aprovacao == Meta.Aprovacao.PENDENTE and meta.gestor_id == criador.id,
      meta and (meta.aprovacao, meta.gestor_id))
    t('e quem é avisado é esse gestor',
      Notification.objects.filter(user=criador, title='Nova atividade para aprovar').exists())

    meta, r = criar(c_sa, 'ZZSA aprovar area lixo', enviar_aprovacao='on',
                    gestor_aprovador=colab.id)
    t('aprovador inválido cai num gestor da área (nunca nele mesmo)',
      meta and meta.pendente_aprovacao and meta.gestor_id in (criador.id, colega.id),
      meta and meta.gestor_id)

    print('\n== CRIAR: SUPERADMIN QUE NÃO É GESTOR ==')
    c_solo = Client(); c_solo.force_login(sa_sem_gestor)
    html = c_solo.get('/impulso/metas/nova/').content.decode()
    t('também vê a caixa', 'name="enviar_aprovacao"' in html)
    meta, r = criar(c_solo, 'ZZSA solo direto', colaborador='', gestor=colega.id,
                    precisa_aprovacao='sim')
    t('direto: aprovada por ele (o rádio é ignorado)',
      meta and meta.vale_pontos and meta.decidida_por_id == sa_sem_gestor.id,
      meta and meta.aprovacao)
    meta, r = criar(c_solo, 'ZZSA solo aprovar', colaborador='', gestor=colega.id,
                    precisa_aprovacao='nao', enviar_aprovacao='on')
    t('marcado: vai para o gestor (o rádio é ignorado)',
      meta and meta.pendente_aprovacao and meta.gestor_id == colega.id,
      meta and meta.aprovacao)

    print('\n== CRIAR: QUEM NÃO É SUPERADMIN NÃO MUDA ==')
    meta, r = criar(c_cri, 'ZZSA gestor comum', enviar_aprovacao='on')
    t('gestor comum, mesma área: aprovada como hoje (campo ignorado)',
      meta and meta.vale_pontos)
    t('sem "decidida por" (como hoje)', meta and meta.decidida_por_id is None)
    meta, r = criar(c_col, 'ZZSA colab pede', colaborador='', gestor=criador.id,
                    enviar_aprovacao='')
    t('colaborador sem escolher: pendente como hoje', meta and meta.pendente_aprovacao)
    meta, r = criar(c_col, 'ZZSA colab direto', colaborador='', gestor=criador.id,
                    precisa_aprovacao='nao', enviar_aprovacao='on')
    t('colaborador escolhendo "não": aprovada, campo ignorado',
      meta and meta.vale_pontos and meta.decidida_por_id is None)
    meta, r = criar(c_col, 'ZZSA colab sim', colaborador='', gestor=criador.id,
                    precisa_aprovacao='sim')
    t('colaborador escolhendo "sim": pendente', meta and meta.pendente_aprovacao)

    print('\n== FILTRO "CRIADO POR" NO KANBAN ==')
    Meta.objects.create(gestor=criador, colaborador=colab, titulo='ZZSA do criador',
                        descricao='x', prazo=hoje + timedelta(days=3), created_by=criador)
    Meta.objects.create(gestor=sa, colaborador=colab, titulo='ZZSA do chefe',
                        descricao='x', prazo=hoje + timedelta(days=3), created_by=sa)

    html = c_sa.get('/impulso/metas/?mes=').content.decode()
    t('SUPERADMIN vê o seletor', 'name="criado_por"' in html and 'Criado por' in html)
    t('com quem criou metas na lista', f'value="{criador.id}"' in html)
    t('sem filtro vê as duas', 'ZZSA do criador' in html and 'ZZSA do chefe' in html)

    html = c_sa.get(f'/impulso/metas/?mes=&criado_por={criador.id}').content.decode()
    t('filtrando, só as do criador', 'ZZSA do criador' in html and 'ZZSA do chefe' not in html)
    t('o seletor fica marcado', f'value="{criador.id}" selected' in html)
    t('os outros formulários carregam o filtro',
      f'name="criado_por" value="{criador.id}"' in html)
    t('o "Mostrar mais" carrega o filtro', f'criado_por={criador.id}' in html)

    html = c_sa.get(f'/impulso/metas/?mes=&criado_por={criador.id}&colaborador={colab.id}'
                    f'&q=zsa').content.decode()
    t('combina com colaborador e busca', 'ZZSA do criador' in html and 'ZZSA do chefe' not in html)

    html = c_sa.get(f'/impulso/metas/?mes=&criado_por={sa.id}&criadas_por_mim=1').content.decode()
    t('combina com "Criadas por mim"', 'ZZSA do chefe' in html and 'ZZSA do criador' not in html)

    html = c_sa.get('/impulso/metas/?mes=&criado_por=abc').content.decode()
    t('valor ilegível é ignorado', 'ZZSA do criador' in html and 'ZZSA do chefe' in html)

    r = c_sa.get(f'/impulso/metas/concluidas/?mes=&criado_por={criador.id}')
    t('"Mostrar mais" responde com o filtro', r.status_code == 200 and r.json().get('ok'))

    # A volta pelo "Voltar" da meta lembra o filtro.
    c_sa.get(f'/impulso/metas/?mes=&criado_por={criador.id}')
    from impulso.views import CHAVE_FILTROS_KANBAN
    t('o filtro fica guardado para o Voltar',
      c_sa.session.get(CHAVE_FILTROS_KANBAN, {}).get('criado_por') == str(criador.id))

    print('\n== QUEM NÃO É SUPERADMIN NÃO USA O FILTRO ==')
    html = c_cri.get('/impulso/metas/?mes=').content.decode()
    t('gestor comum não vê o seletor', 'name="criado_por"' not in html)
    t('e vê as duas', 'ZZSA do criador' in html and 'ZZSA do chefe' in html)
    html = c_cri.get(f'/impulso/metas/?mes=&criado_por={sa.id}').content.decode()
    t('o parâmetro na URL é ignorado para ele',
      'ZZSA do criador' in html and 'ZZSA do chefe' in html)
    html = c_col.get(f'/impulso/metas/?mes=&criado_por={criador.id}').content.decode()
    t('colaborador: parâmetro ignorado',
      'name="criado_por"' not in html and 'ZZSA do criador' in html and 'ZZSA do chefe' in html)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    for p in patches:
        p.stop()
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
