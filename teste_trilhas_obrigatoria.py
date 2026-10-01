"""Trilhas: quem é obrigado, quem tranca o portal e a assinatura do fim.

Pedidos:

- "Deixar para definir quem é obrigatório assistir";
- "só gestor tem a possibilidade de deixar travar o portal";
- "colocar uma assinatura final, de que ele assistiu esse video".

O que este teste cobre:

- a trilha guarda quem é obrigatório e o período (já existia, aqui fica preso);
- **travar o portal** é um campo novo que **só ADMIN/SUPERADMIN** liga: o
  supervisor não vê a opção e, se mandar o campo pelo POST, ele é ignorado;
- passado o prazo sem concluir, o portal redireciona para a tela da trilha;
  concluir (e assinar, quando pedido) destrava;
- a assinatura exige ter concluído, o nome igual ao do cadastro e a declaração
  marcada; fica guardada com data e IP;
- SUPERADMIN nunca é travado (senão ninguém conserta nada).

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
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-tr'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-tr2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.db import transaction
from django.test import Client
from django.utils import timezone

from knowledge_trails.bloqueio import pode_travar_portal, trilha_que_trava
from knowledge_trails.models import KnowledgeTrail, TrailProgress, TrailSignature
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


def limpar():
    caches['local'].clear()


marcador = transaction.atomic()
marcador.__enter__()
try:
    setor = Sector.objects.create(name='ZZ Setor Trilha')
    chefe = User.objects.create_user(
        username='zztr.chefe', email='zztr.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN', sector=setor)
    gestor = User.objects.create_user(
        username='zztr.gestor', email='zztr.gestor@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Gestor', hierarchy='ADMIN', sector=setor)
    supervisor = User.objects.create_user(
        username='zztr.sup', email='zztr.sup@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Supervisor', hierarchy='SUPERVISOR', sector=setor)
    ana = User.objects.create_user(
        username='zztr.ana', email='zztr.ana@exemplo-teste.local', password='S3nha!teste',
        first_name='Ana', last_name='Trilha', hierarchy='PADRAO', sector=setor)

    print('== QUEM PODE TRAVAR ==')
    t('gestor pode', pode_travar_portal(gestor) and pode_travar_portal(chefe))
    t('supervisor não', not pode_travar_portal(supervisor))
    t('colaborador também não', not pode_travar_portal(ana))

    ontem = timezone.localdate() - timedelta(days=1)
    trilha = KnowledgeTrail.objects.create(
        title='ZZ Trilha obrigatória', description='ZZ', sector=setor,
        created_by=chefe, blocks_portal=True, require_signature=True,
        mandatory_start_date=ontem - timedelta(days=10), mandatory_end_date=ontem)
    trilha.mandatory_users.add(ana)

    print('\n== O BLOQUEIO ==')
    limpar()
    t('quem não concluiu é travado', trilha_que_trava(ana) == trilha)
    t('quem não é obrigatório não é travado', trilha_que_trava(supervisor) is None)
    t('e o SUPERADMIN nunca é travado', trilha_que_trava(chefe) is None)

    c = Client(); c.force_login(ana)
    limpar()
    r = c.get('/')
    t('o portal manda para a trilha',
      r.status_code == 302 and f'/trilhas/trail/{trilha.id}/bloqueada/' in r['Location'],
      (r.status_code, r.get('Location')))
    t('a tela do bloqueio abre e explica',
      'obrigatória pendente' in c.get(f'/trilhas/trail/{trilha.id}/bloqueada/').content.decode())
    t('e as trilhas continuam acessíveis (é a saída)',
      c.get('/trilhas/').status_code == 200)

    # Abrir a trilha já cria o progresso: aqui só marcamos como concluída.
    progresso, _ = TrailProgress.objects.update_or_create(
        user=ana, trail=trilha,
        defaults={'status': 'completed', 'completed_at': timezone.now()})
    limpar()
    t('concluir sem assinar ainda trava (a trilha pede assinatura)',
      trilha_que_trava(ana) == trilha)

    print('\n== A ASSINATURA ==')
    r = c.post(f'/trilhas/trail/{trilha.id}/assinar/',
               {'typed_name': 'Nome Errado', 'aceite': 'on'})
    t('nome diferente do cadastro não assina', not TrailSignature.objects.filter(user=ana).exists())
    r = c.post(f'/trilhas/trail/{trilha.id}/assinar/', {'typed_name': 'Ana Trilha'})
    t('sem marcar a declaração também não', not TrailSignature.objects.filter(user=ana).exists())

    r = c.post(f'/trilhas/trail/{trilha.id}/assinar/',
               {'typed_name': 'ana trilha', 'aceite': 'on'})
    assinatura = TrailSignature.objects.filter(user=ana, trail=trilha).first()
    t('assina (sem se importar com maiúsculas)', assinatura is not None)
    t('guardando a declaração', 'assisti e compreendi' in (assinatura.declaration or ''))
    t('e a data', assinatura.signed_at is not None)
    t('o portal destrava na hora', trilha_que_trava(ana) is None)
    limpar()
    t('e a navegação volta', c.get('/trilhas/').status_code == 200)

    r = c.post(f'/trilhas/trail/{trilha.id}/assinar/',
               {'typed_name': 'Ana Trilha', 'aceite': 'on'})
    t('assinar de novo não duplica', TrailSignature.objects.filter(user=ana, trail=trilha).count() == 1)

    outra = KnowledgeTrail.objects.create(title='ZZ Sem conclusão', description='ZZ',
                                          sector=setor, created_by=chefe, require_signature=True)
    cb = Client(); cb.force_login(gestor)
    cb.post(f'/trilhas/trail/{outra.id}/assinar/', {'typed_name': 'ZZ Gestor', 'aceite': 'on'})
    t('não dá para assinar sem ter concluído',
      not TrailSignature.objects.filter(trail=outra).exists())

    print('\n== NA TELA ==')
    html = c.get(f'/trilhas/trail/{trilha.id}/').content.decode()
    t('a trilha assinada mostra a assinatura', 'Assinada por' in html and 'Ana Trilha' in html)
    TrailSignature.objects.filter(user=ana, trail=trilha).delete()
    html = c.get(f'/trilhas/trail/{trilha.id}/').content.decode()
    t('e quem concluiu sem assinar vê o campo',
      'Falta só assinar' in html and 'typed_name' in html)

    cg = Client(); cg.force_login(gestor)
    cs = Client(); cs.force_login(supervisor)
    t('o formulário mostra a opção de travar para o gestor',
      'name="blocks_portal"' in cg.get('/trilhas/create/').content.decode())
    t('e esconde do supervisor',
      'name="blocks_portal"' not in cs.get('/trilhas/create/').content.decode())
    t('a assinatura é oferecida para os dois',
      'name="require_signature"' in cg.get('/trilhas/create/').content.decode()
      and 'name="require_signature"' in cs.get('/trilhas/create/').content.decode())

    nova = KnowledgeTrail.objects.create(title='ZZ Editável', description='ZZ', sector=setor,
                                         created_by=chefe)
    cs.post(f'/trilhas/manage/{nova.id}/edit/' if False else f'/trilhas/edit/{nova.id}/',
            {'title': 'ZZ Editável', 'description': 'ZZ', 'sector': str(setor.id),
             'blocks_portal': 'on', 'difficulty': 'beginner', 'estimated_hours': '1'})
    nova.refresh_from_db()
    t('supervisor mandando o campo pelo POST não trava ninguém', not nova.blocks_portal)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    caches['local'].clear()
    print('\nrollback: nada gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
