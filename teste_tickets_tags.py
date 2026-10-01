"""Chamados: etiquetas para agrupar (nota fiscal, DANFE…).

Pedido: "Possibilidade de categorizar os chamados com 'tags' como forma de
agrupá-los. Para que, por exemplo, possa ser possível sinalizar os chamados que
possuem notas fiscais, bem como os chamados que possuem DANFEs."

A categoria já existia, mas é uma só por chamado e serve para rotear o
atendimento. A etiqueta é outra coisa: várias por chamado, e é por ela que se
acha o grupo depois.

O que este teste cobre:

- etiquetar e desetiquetar um chamado, e criar etiqueta no próprio chamado;
- filtrar a lista por etiqueta — e por duas, que pede os chamados com as duas;
- a etiqueta aparece no chamado e na lista;
- quem não mexe no chamado não etiqueta;
- a lista não vira uma consulta por chamado (prefetch).

Roda dentro de uma transação desfeita.
"""
import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-tk-tags'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-tk-tags2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext

from tickets.models import Category, Ticket, TicketTag
from users.models import Sector

User = get_user_model()
ok = fail = 0
LISTA = '/tickets/'


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
    setor = Sector.objects.create(name='ZZ Setor Etiquetas')
    categoria = Category.objects.create(name='ZZ Categoria', sector=setor)
    chefe = User.objects.create_user(
        username='zztg.chefe', email='zztg.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN', is_superuser=True,
        sector=setor)
    autor = User.objects.create_user(
        username='zztg.autor', email='zztg.autor@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Autor', hierarchy='PADRAO', sector=setor)
    estranho = User.objects.create_user(
        username='zztg.outro', email='zztg.outro@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Outro', hierarchy='PADRAO', sector=setor)

    def chamado(titulo):
        return Ticket.objects.create(title=titulo, description='ZZ', sector=setor,
                                     category=categoria, created_by=autor)

    t1, t2, t3 = chamado('ZZ com nota'), chamado('ZZ com danfe'), chamado('ZZ com os dois')

    nf = TicketTag.objects.create(nome='Nota fiscal', cor='#16A34A', criada_por=chefe)
    danfe = TicketTag.objects.create(nome='DANFE', cor='#2563EB', criada_por=chefe)
    t1.tags.add(nf)
    t2.tags.add(danfe)
    t3.tags.add(nf, danfe)

    c = Client(); c.force_login(chefe)
    ca = Client(); ca.force_login(autor)
    ce = Client(); ce.force_login(estranho)

    print('== ETIQUETAR ==')
    r = c.post(f'/tickets/{t1.id}/etiquetas/', {'tags': [str(nf.id), str(danfe.id)]})
    t('põe etiqueta', r.status_code == 302 and set(t1.tags.values_list('nome', flat=True))
      == {'Nota fiscal', 'DANFE'})
    c.post(f'/tickets/{t1.id}/etiquetas/', {'tags': [str(nf.id)]})
    t('e tira', list(t1.tags.values_list('nome', flat=True)) == ['Nota fiscal'])

    c.post(f'/tickets/{t1.id}/etiquetas/', {'tags': [str(nf.id)], 'nova_tag': 'Reembolso'})
    t('cria etiqueta nova no próprio chamado',
      TicketTag.objects.filter(nome='Reembolso').exists()
      and t1.tags.filter(nome='Reembolso').exists())
    c.post(f'/tickets/{t1.id}/etiquetas/', {'tags': [str(nf.id)], 'nova_tag': 'reembolso'})
    t('e não duplica quem já existe (só muda a caixa)',
      TicketTag.objects.filter(nome__iexact='reembolso').count() == 1)
    t1.tags.set([nf])

    print('\n== FILTRAR ==')
    def ids(url):
        return {t.id for t in c.get(url).context['tickets']} & {t1.id, t2.id, t3.id}

    t('por uma etiqueta', ids(f'{LISTA}?tag={nf.id}') == {t1.id, t3.id},
      ids(f'{LISTA}?tag={nf.id}'))
    t('por outra', ids(f'{LISTA}?tag={danfe.id}') == {t2.id, t3.id})
    t('por duas: só quem tem as duas',
      ids(f'{LISTA}?tag={nf.id}&tag={danfe.id}') == {t3.id},
      ids(f'{LISTA}?tag={nf.id}&tag={danfe.id}'))
    t('sem filtro, todos aparecem', ids(LISTA) == {t1.id, t2.id, t3.id})
    t('etiqueta inexistente não quebra', c.get(f'{LISTA}?tag=99999999').status_code == 200)
    t('e lixo no filtro também não', c.get(f'{LISTA}?tag=bobagem').status_code == 200)

    print('\n== NA TELA ==')
    html = c.get(LISTA).content.decode()
    t('a lista mostra as etiquetas', 'Nota fiscal' in html and 'DANFE' in html)
    t('e deixa marcar para agrupar', 'name="tag"' in html and 'marque para agrupar' in html)
    detalhe = c.get(f'/tickets/{t3.id}/').content.decode()
    t('o chamado mostra as suas', 'Nota fiscal' in detalhe and 'DANFE' in detalhe)
    t('com link para o grupo', f'?tag={nf.id}' in detalhe)
    t('e o campo de criar etiqueta', 'nova_tag' in detalhe)

    print('\n== QUEM PODE ==')
    r = ce.post(f'/tickets/{t2.id}/etiquetas/', {'tags': [str(nf.id)]})
    t('quem não mexe no chamado não etiqueta',
      not t2.tags.filter(id=nf.id).exists(), list(t2.tags.all()))
    t('o autor do chamado etiqueta',
      ca.post(f'/tickets/{t2.id}/etiquetas/', {'tags': [str(nf.id), str(danfe.id)]})
      and t2.tags.count() == 2)
    t('e quem não pode nem vê o botão',
      'nova_tag' not in ce.get(f'/tickets/{t2.id}/').content.decode()
      if ce.get(f'/tickets/{t2.id}/').status_code == 200 else True)

    print('\n== CUSTO ==')
    with CaptureQueriesContext(connection) as consultas:
        c.get(LISTA)
    com_tags = sum(1 for q in consultas.captured_queries if 'ticket_tags' in q['sql'])
    t('as etiquetas da lista saem numa consulta só', com_tags <= 1, com_tags)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
