"""Aprovar vários comprovantes de uma vez em /cursos/gestao/.

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
from django.urls import reverse
from django.utils import timezone

from cursos.models import ConfiguracaoCursos, Comprovante, Curso
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
    loja = Sector.objects.create(name='Loja ZZ Lote')
    cfg = ConfiguracaoCursos.get()
    cfg.setores.add(loja)

    chefe = User.objects.create_user(
        username='lt.chefe', email='lt.chefe@exemplo-teste.local', password='S3nha!teste',
        sector=loja, first_name='Chefe', last_name='Lote',
        is_superuser=True, is_staff=True, hierarchy='SUPERADMIN')

    hoje = timezone.localdate()
    curso = Curso.objects.create(
        titulo='ZZ Curso do lote', tipo=Curso.FOCO, publicado=True,
        prazo=hoje + timedelta(days=10), criado_por=chefe)

    def pessoa(n):
        return User.objects.create_user(
            username=f'lt.p{n}', email=f'lt.p{n}@exemplo-teste.local',
            password='S3nha!teste', sector=loja,
            first_name=f'Pessoa{n}', last_name='Lote', hierarchy='PADRAO')

    envios = []
    for n in range(5):
        p = pessoa(n)
        envios.append(Comprovante.objects.create(
            curso=curso, colaborador=p, nome_original=f'c{n}.pdf', tamanho=10))

    ja_recusado = envios[4]
    ja_recusado.status = Comprovante.RECUSADO
    ja_recusado.observacao = 'faltou o nome'
    ja_recusado.save(update_fields=['status', 'observacao'])

    c = Client()
    c.force_login(chefe)
    url = reverse('cursos:aprovar_lote')   # sem caminho na mão: a rota pode mudar

    print('== A TELA ==')
    html = c.get(f'/cursos/gestao/?curso={curso.id}').content.decode()
    marcaveis = html.count('class="crs-marcar ')
    t('cada pendente tem caixinha', marcaveis == 4, marcaveis)
    t('existe o "marcar todos" da loja', 'crs-marcar-todos' in html)
    t('e ele diz quantos são', '4 pendentes desta loja' in html)
    t('a barra de ação existe', 'crsBarraLote' in html)
    t('com o botão pedido', 'Aprovar selecionados' in html)
    t('o formulário do lote é separado', 'id="crsFormLote"' in html)
    t('clique duplo não envia duas vezes', 'botao.disabled = true' in html)
    t('quem já foi recusado não ganha caixinha (são 5 pessoas, 4 pendentes)',
      marcaveis == 4, marcaveis)

    print('\n== APROVAR EM LOTE ==')
    tres = [e.id for e in envios[:3]]
    r = c.post(url, {'comprovantes': tres, 'voltar': f'/cursos/gestao/?curso={curso.id}'},
               follow=True)
    for e in envios[:3]:
        e.refresh_from_db()
    t('os três marcados foram aprovados',
      all(e.status == Comprovante.APROVADO for e in envios[:3]),
      [e.status for e in envios[:3]])
    t('com quem revisou', all(e.revisado_por_id == chefe.id for e in envios[:3]))
    t('e quando', all(e.revisado_em is not None for e in envios[:3]))
    envios[3].refresh_from_db()
    t('quem não foi marcado continua pendente',
      envios[3].status == Comprovante.PENDENTE, envios[3].status)
    t('a tela confirma quantos', '3 comprovantes aprovados' in r.content.decode())
    t('e volta para o quadro do curso certo',
      f'curso={curso.id}' in r.redirect_chain[-1][0], r.redirect_chain)

    print('\n== NÃO ATROPELA O QUE JÁ FOI REVISADO ==')
    r = c.post(url, {'comprovantes': [envios[3].id, ja_recusado.id]}, follow=True)
    ja_recusado.refresh_from_db()
    envios[3].refresh_from_db()
    t('o pendente foi aprovado', envios[3].status == Comprovante.APROVADO)
    t('o recusado continua recusado', ja_recusado.status == Comprovante.RECUSADO,
      ja_recusado.status)
    t('e o motivo da recusa não foi apagado', ja_recusado.observacao == 'faltou o nome')
    t('a tela avisa que um ficou de fora', '1 já tinha sido revisado' in r.content.decode())

    print('\n== VALIDAÇÕES ==')
    r = c.post(url, {}, follow=True)
    t('sem ninguém marcado, recusa', 'Marque pelo menos um' in r.content.decode())

    antes = Comprovante.objects.filter(status=Comprovante.APROVADO).count()
    c.post(url, {'comprovantes': ['abc', '', '999999999']}, follow=True)
    t('id inválido não quebra nem aprova nada',
      Comprovante.objects.filter(status=Comprovante.APROVADO).count() == antes)

    r = c.get(url)
    t('GET não aprova (405)', r.status_code == 405, r.status_code)

    print('\n== PARA ONDE VOLTA ==')
    r = c.post(url, {'comprovantes': [envios[0].id],
                     'voltar': 'https://site-de-fora.example.com/x'}, follow=True)
    t('destino externo é ignorado',
      not any('site-de-fora' in u for u, _ in r.redirect_chain), r.redirect_chain)
    t('e cai no quadro', any('/cursos/gestao/' in u for u, _ in r.redirect_chain),
      r.redirect_chain)

    print('\n== QUEM PODE ==')
    comum = User.objects.create_user(
        username='lt.comum', email='lt.comum@exemplo-teste.local', password='S3nha!teste',
        sector=loja, first_name='Comum', last_name='Lote', hierarchy='PADRAO')
    novo_envio = Comprovante.objects.create(
        curso=curso, colaborador=comum, nome_original='x.pdf', tamanho=1)
    cc = Client()
    cc.force_login(comum)
    r = cc.post(url, {'comprovantes': [novo_envio.id]}, follow=True)
    novo_envio.refresh_from_db()
    t('colaborador não aprova em lote', novo_envio.status == Comprovante.PENDENTE)
    t('e a tela avisa', 'gestores do módulo' in r.content.decode())
    t('nem vê o quadro', 'crsBarraLote' not in cc.get('/cursos/gestao/').content.decode())

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
