"""Dois consertos: marcar pago em lote (500) e anexos no Projeto Foco.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import Client

from communications.models import CommunicationGroup
from contestacao.models import (Contestation, ExclusionRecord,
                                ExclusionSyncBatch, TipoBase)
from impulso.models import GRUPO_ADM, GRUPO_GESTOR, ProjetoAnexo, ProjetoFoco

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

    def novo(username, grupos=(), **kw):
        u = User.objects.create_user(
            username=username, email=f'{username}@exemplo-teste.local',
            password='S3nha!teste', first_name=username.split('.')[1].title(),
            last_name='Teste', **kw)
        for g in grupos:
            u.communication_groups.add(g)
        return u

    print('== MARCAR PAGO EM LOTE (era 500) ==')
    chefe = novo('pl.chefe', hierarchy='SUPERADMIN')
    comum = novo('pl.comum')
    cc = Client()
    cc.force_login(chefe)

    # O caminho que estourava primeiro: nada selecionado.
    r = cc.post('/contestacao/marcar-pago-lote/', {})
    t('sem seleção não dá 500', r.status_code == 302, r.status_code)
    t('e volta para a tela certa, com o filtro',
      r['Location'] == '/contestacao/gerenciar/?status=confirmed', r['Location'])

    r = cc.post('/contestacao/marcar-pago-lote/', {'contestation_ids': ['abc', '']})
    t('id inválido não dá 500', r.status_code == 302, r.status_code)

    # E o caminho de sucesso, com dado de verdade.
    lote = ExclusionSyncBatch.objects.create(record_type=TipoBase.EXCLUSAO,
                                             record_count=1, created_by=chefe)
    registro = ExclusionRecord.objects.create(
        sync_batch=lote, filial='ZZ FILIAL', vendedor='ZZ Vendedor', receita=100,
        pilar='ZZ', numero_venda='ZZ1', data_venda='01/07/2026',
        nome_cliente='ZZ Cliente', record_type=TipoBase.EXCLUSAO)
    contestacao = Contestation.objects.create(
        exclusion=registro, requester=chefe, reason='ZZ motivo',
        status='confirmed', payment_status='pending_payment')

    r = cc.post('/contestacao/marcar-pago-lote/',
                {'contestation_ids': [str(contestacao.id)]}, follow=True)
    contestacao.refresh_from_db()
    t('marca como paga', contestacao.payment_status == 'paid', contestacao.payment_status)
    t('registra a data do pagamento', contestacao.paid_at is not None)
    t('avisa quantas foram', '1 contestação(ões) marcada(s) como paga(s)' in r.content.decode())

    from contestacao.models import ContestationHistory
    t('fica no histórico',
      ContestationHistory.objects.filter(contestation=contestacao, action='paid').exists())

    r = cc.post('/contestacao/marcar-pago-lote/',
                {'contestation_ids': [str(contestacao.id)]}, follow=True)
    t('marcar de novo não quebra nem duplica',
      'Nenhuma contestação válida' in r.content.decode())

    ce = Client()
    ce.force_login(comum)
    r = ce.post('/contestacao/marcar-pago-lote/', {'contestation_ids': [str(contestacao.id)]},
                follow=True)
    t('quem não gerencia é barrado', 'Sem permissão' in r.content.decode())

    r = cc.get('/contestacao/marcar-pago-lote/')
    t('GET só redireciona', r.status_code == 302, r.status_code)

    print('\n== ANEXOS NO PROJETO FOCO ==')
    gestor = novo('pl.gestor', [adm, ges])
    membro = novo('pl.membro', [adm])
    fora = novo('pl.fora', [adm])

    projeto = ProjetoFoco.objects.create(
        nome='ZZ Projeto com anexo', descricao='ZZ', criado_por=gestor)
    projeto.membros.add(membro)

    cg = Client()
    cg.force_login(gestor)
    cm = Client()
    cm.force_login(membro)
    cf = Client()
    cf.force_login(fora)

    r = cg.post(f'/impulso/conectar/projetos/{projeto.id}/anexo/', {
        'titulo': 'ZZ Planilha do projeto',
        'arquivo': SimpleUploadedFile('plano.xlsx', b'PK\x03\x04 zz')}, follow=True)
    arquivo = ProjetoAnexo.objects.filter(projeto=projeto,
                                          tipo=ProjetoAnexo.Tipo.ARQUIVO).first()
    t('gestor anexa arquivo', arquivo is not None, r.status_code)
    t('guarda quem enviou', arquivo and arquivo.enviado_por_id == gestor.id)
    t('o nome aparece bonito', arquivo and arquivo.nome_exibicao == 'ZZ Planilha do projeto')

    r = cm.post(f'/impulso/conectar/projetos/{projeto.id}/anexo/', {
        'url': 'https://exemplo.local/referencia'}, follow=True)
    link = ProjetoAnexo.objects.filter(projeto=projeto, tipo=ProjetoAnexo.Tipo.LINK).first()
    t('membro do projeto anexa link', link is not None)
    t('sem título, mostra a URL', link and link.nome_exibicao == 'https://exemplo.local/referencia')

    r = cg.post(f'/impulso/conectar/projetos/{projeto.id}/anexo/', {}, follow=True)
    t('sem arquivo nem link, avisa',
      'Envie um arquivo ou informe um link' in r.content.decode())
    t('e não cria anexo vazio', ProjetoAnexo.objects.filter(projeto=projeto).count() == 2)

    print('\n== QUEM VÊ E QUEM APAGA ==')
    r = cf.post(f'/impulso/conectar/projetos/{projeto.id}/anexo/', {
        'url': 'https://exemplo.local/invasor'}, follow=True)
    t('quem não é do projeto não anexa',
      not ProjetoAnexo.objects.filter(url='https://exemplo.local/invasor').exists())
    t('e a tela explica', 'não faz parte deste projeto' in r.content.decode())

    t('quem enviou pode apagar o seu', link.pode_mexer(membro))
    t('quem criou o projeto apaga qualquer um', link.pode_mexer(gestor))
    t('colega não apaga anexo alheio', not arquivo.pode_mexer(membro))
    t('quem está fora não apaga', not arquivo.pode_mexer(fora))

    r = cm.post(f'/impulso/conectar/projetos/{projeto.id}/anexo/{arquivo.id}/excluir/',
                follow=True)
    t('membro não apaga anexo do gestor',
      ProjetoAnexo.objects.filter(id=arquivo.id).exists())
    t('a tela recusa', 'não pode excluir este anexo' in r.content.decode())

    r = cm.post(f'/impulso/conectar/projetos/{projeto.id}/anexo/{link.id}/excluir/',
                follow=True)
    t('membro apaga o anexo dele', not ProjetoAnexo.objects.filter(id=link.id).exists())

    r = cg.get(f'/impulso/conectar/projetos/{projeto.id}/anexo/')
    t('GET não anexa (405)', r.status_code == 405, r.status_code)

    print('\n== A TELA DO PROJETO ==')
    html = cg.get(f'/impulso/conectar/projetos/{projeto.id}/').content.decode()
    t('tem a seção de anexos', 'Anexos' in html)
    t('lista o anexo enviado', 'ZZ Planilha do projeto' in html)
    t('tem o formulário para anexar',
      f'/impulso/conectar/projetos/{projeto.id}/anexo/' in html
      and 'enctype="multipart/form-data"' in html)
    t('gestor vê a lixeira do anexo dele',
      f'/impulso/conectar/projetos/{projeto.id}/anexo/{arquivo.id}/excluir/' in html)

    html_membro = cm.get(f'/impulso/conectar/projetos/{projeto.id}/').content.decode()
    t('membro vê a lista mas não a lixeira do outro',
      'ZZ Planilha do projeto' in html_membro
      and f'/anexo/{arquivo.id}/excluir/' not in html_membro)

    r = cf.get(f'/impulso/conectar/projetos/{projeto.id}/', follow=True)
    t('quem está fora não abre o projeto',
      'não faz parte deste projeto' in r.content.decode())

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
