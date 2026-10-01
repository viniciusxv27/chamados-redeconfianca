"""Impulso: convidar colega para tocar a meta em conjunto.

Pedido: "Criação da função 'Cooperativo / Parceiro' nas METAS. Eu gerei uma
meta ou fui designado a mesma, no momento da criação da 'Meta', posso solicitar
a participação em conjunto do colega de setor para a realização da tarefa."

Já havia "outros responsáveis", mas só o gestor mexia e a pessoa era **posta**
na meta sem ser perguntada. Parceria é convite: quem foi chamado aceita ou não.

O que este teste cobre:

- convidar na criação da meta e depois, pela tela da meta;
- só colega do mesmo setor aparece para chamar;
- aceitar coloca na meta (vê, acompanha, marca o to-do); recusar não;
- o convite é de quem recebeu — ninguém responde pelo outro;
- convite repetido não vira dois;
- o aviso cai no sino dos dois lados.

Roda dentro de uma transação desfeita.
"""
import os
import sys
from datetime import timedelta

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-mp'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-mp2'},
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

from communications.models import CommunicationGroup
from core.models import Notification
from impulso.models import Meta, ParceriaMeta
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
    loja = Sector.objects.create(name='ZZ Loja Parceria')
    outra = Sector.objects.create(name='ZZ Outra Loja')
    chefe = User.objects.create_user(
        username='zzpa.chefe', email='zzpa.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN', is_superuser=True,
        sector=loja)
    adm, _ = CommunicationGroup.objects.get_or_create(name='ESCRITÓRIO (ADM)',
                                                      defaults={'created_by': chefe})

    def pessoa(nome, setor):
        u = User.objects.create_user(
            username=f'zzpa.{nome}', email=f'zzpa.{nome}@exemplo-teste.local',
            password='S3nha!teste', first_name=nome.capitalize(), last_name='Parceria',
            hierarchy='PADRAO', sector=setor)
        u.communication_groups.add(adm)
        return u

    ana = pessoa('ana', loja)
    bruno = pessoa('bruno', loja)
    distante = pessoa('distante', outra)
    chefe.communication_groups.add(adm)
    # Criar meta para si mesmo exige escolher um gestor do setor.
    gestores, _ = CommunicationGroup.objects.get_or_create(name='GESTORES (IMPULSO)',
                                                           defaults={'created_by': chefe})
    gestor = pessoa('gestor', loja)
    gestor.communication_groups.add(gestores)

    ca = Client(); ca.force_login(ana)
    cb = Client(); cb.force_login(bruno)
    cd = Client(); cd.force_login(distante)
    prazo = (timezone.localdate() + timedelta(days=10)).strftime('%Y-%m-%d')

    print('== CHAMANDO NA CRIAÇÃO DA META ==')
    html = ca.get('/impulso/metas/nova/').content.decode()
    t('a tela de criar oferece chamar colega', 'name="parceiros"' in html)
    t('com o colega do setor', 'Bruno Parceria' in html)
    t('e sem quem é de outro setor', 'Distante Parceria' not in html)

    r = ca.post('/impulso/metas/nova/', {
        'titulo': 'ZZ meta em conjunto', 'descricao': 'ZZ descrição',
        'prazo': prazo, 'colaborador': str(ana.id), 'gestor': str(gestor.id),
        'parceiros': [str(bruno.id)],
    })
    meta = Meta.objects.filter(titulo='ZZ meta em conjunto').first()
    t('a meta é criada', meta is not None, r.status_code)
    convite = ParceriaMeta.objects.filter(meta=meta, convidado=bruno).first()
    t('e o convite sai junto', convite is not None and convite.esta_pendente)
    t('quem convidou fica registrado', convite.convidado_por_id == ana.id)
    t('o Bruno é avisado no sino',
      Notification.objects.filter(user=bruno, title__icontains='parceria').exists())
    t('mas ainda não está na meta', not meta.participantes.filter(id=bruno.id).exists())

    print('\n== RESPONDENDO ==')
    r = cd.post(f'/impulso/metas/parceria/{convite.id}/responder/', {'resposta': 'aceitar'})
    convite.refresh_from_db()
    t('ninguém responde pelo outro', convite.esta_pendente)

    r = cb.post(f'/impulso/metas/parceria/{convite.id}/responder/', {'resposta': 'aceitar'})
    convite.refresh_from_db()
    t('o convidado aceita', convite.status == ParceriaMeta.Status.ACEITA)
    t('e entra na meta', meta.participantes.filter(id=bruno.id).exists())
    t('a meta aparece para ele', cb.get(f'/impulso/metas/{meta.id}/').status_code == 200)
    t('a Ana é avisada da resposta',
      Notification.objects.filter(user=ana, title__icontains='parceria').exists())

    r = cb.post(f'/impulso/metas/parceria/{convite.id}/responder/', {'resposta': 'recusar'})
    convite.refresh_from_db()
    t('responder duas vezes não desfaz', convite.status == ParceriaMeta.Status.ACEITA
      and meta.participantes.filter(id=bruno.id).exists())

    print('\n== CHAMANDO DEPOIS, PELA TELA DA META ==')
    carla = pessoa('carla', loja)
    r = ca.post(f'/impulso/metas/{meta.id}/parceiro/', {'parceiros': [str(carla.id)]})
    c2 = ParceriaMeta.objects.filter(meta=meta, convidado=carla).first()
    t('o convite sai', c2 is not None and c2.esta_pendente)
    ca.post(f'/impulso/metas/{meta.id}/parceiro/', {'parceiros': [str(carla.id)]})
    t('chamar de novo não duplica', ParceriaMeta.objects.filter(meta=meta, convidado=carla).count() == 1)

    cc = Client(); cc.force_login(carla)
    cc.post(f'/impulso/metas/parceria/{c2.id}/responder/', {'resposta': 'recusar'})
    c2.refresh_from_db()
    t('recusar é respeitado', c2.status == ParceriaMeta.Status.RECUSADA
      and not meta.participantes.filter(id=carla.id).exists())

    ca.post(f'/impulso/metas/{meta.id}/parceiro/', {'parceiros': [str(distante.id)]})
    t('não dá para chamar quem é de outro setor',
      not ParceriaMeta.objects.filter(meta=meta, convidado=distante).exists())
    ca.post(f'/impulso/metas/{meta.id}/parceiro/', {'parceiros': [str(ana.id)]})
    t('nem a si mesmo', not ParceriaMeta.objects.filter(meta=meta, convidado=ana).exists())

    print('\n== NA TELA ==')
    html = ca.get(f'/impulso/metas/{meta.id}/').content.decode()
    t('a meta mostra quem está junto', 'Tocando em conjunto' in html and 'Bruno Parceria' in html)
    t('com a situação de cada convite', 'Aceita' in html and 'Recusada' in html)
    carla2 = pessoa('dani', loja)
    ca.post(f'/impulso/metas/{meta.id}/parceiro/', {'parceiros': [str(carla2.id)]})
    pendente = ParceriaMeta.objects.get(meta=meta, convidado=carla2)
    cd2 = Client(); cd2.force_login(carla2)
    html = cd2.get(f'/impulso/metas/{meta.id}/').content.decode()
    t('quem foi chamado vê o convite para responder',
      'Chamaram você para esta meta' in html
      and f'/impulso/metas/parceria/{pendente.id}/responder/' in html)
    t('e quem não foi chamado não vê convite nenhum',
      'Chamaram você para esta meta' not in ca.get(f'/impulso/metas/{meta.id}/').content.decode())
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
