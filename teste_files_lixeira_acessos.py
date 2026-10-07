"""/files/: lixeira (nunca apaga), log de movimentações, acesso por pessoa e visão por usuário.

Pedidos:
- SUPERADMIN gerencia o conteúdo por usuário (libera/retira por pessoa em cada item e
  tem a visão "por usuário");
- logs de todas as movimentações para o SUPERADMIN;
- nunca excluir de vez: vai para a lixeira e o SUPERADMIN recupera;
- de quebra: só quem pode enviar arquivos gerencia (antes o PADRÃO apagava pastas) e a
  lista respeita a visibilidade (antes todo mundo via tudo).

Roda dentro de uma transação desfeita no fim — inclusive a migração nova, aplicada aqui
dentro. Arquivos vão para um armazenamento em memória e nenhuma notificação sai.
"""
import os
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()

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
    executor = MigrationExecutor(connection)
    alvo = [('files', '0006_lixeira_acesso_por_pessoa_e_log')]
    if executor.migration_plan(alvo):
        executor.migrate(alvo)

    from files import acesso
    from files.models import FileCategory, Folder, MovimentacaoArquivo, SharedFile

    memoria = InMemoryStorage()
    SharedFile._meta.get_field('file').storage = memoria
    pilha = [mock.patch('files.views.create_file_notifications', lambda *a, **k: None)]
    for p in pilha:
        p.start()

    def novo(username, **kw):
        return User.objects.create_user(username=username, email=f'{username}@exemplo-teste.local',
                                        password='S3nha!teste', first_name=username.split('.')[1].title(),
                                        last_name='Teste', **kw)

    setor_a = Sector.objects.create(name='ZZ Files Setor A')
    setor_b = Sector.objects.create(name='ZZ Files Setor B')
    chefe = novo('zzf.chefe', hierarchy='SUPERADMIN')
    adm = novo('zzf.adm', hierarchy='ADMINISTRATIVO')
    padrao = novo('zzf.padrao', hierarchy='PADRAO', sector=setor_a)
    padrao.sectors.add(setor_a)
    outro = novo('zzf.outro', hierarchy='PADRAO', sector=setor_b)
    outro.sectors.add(setor_b)

    publica = Folder.objects.create(name='ZZ Pública', created_by=adm)
    sub = Folder.objects.create(name='ZZ Sub', parent=publica, created_by=adm)
    so_b = Folder.objects.create(name='ZZ Só setor B', visibility='SECTOR', target_sector=setor_b, created_by=adm)
    cat_pub = FileCategory.objects.create(name='ZZ Cat pública', folder=publica)
    cat_sub = FileCategory.objects.create(name='ZZ Cat sub', folder=sub)
    cat_b = FileCategory.objects.create(name='ZZ Cat B', folder=so_b)

    def arquivo(titulo, categoria, por=adm, **kw):
        a = SharedFile(title=titulo, category=categoria, uploaded_by=por, **kw)
        a.file.save(f'{titulo}.pdf', ContentFile(b'%PDF-1.4 teste'), save=False)
        a.save()
        return a

    f_todos = arquivo('ZZ todos', cat_pub)
    f_setor_b = arquivo('ZZ setor B', cat_pub, visibility='SECTOR', target_sector=setor_b)
    f_na_pasta_b = arquivo('ZZ na pasta B', cat_b)
    f_do_outro = arquivo('ZZ do outro', cat_pub, visibility='USER', target_user=outro)
    f_sub = arquivo('ZZ sub', cat_sub)

    c_chefe, c_adm, c_padrao, c_outro = Client(), Client(), Client(), Client()
    c_chefe.force_login(chefe)
    c_adm.force_login(adm)
    c_padrao.force_login(padrao)
    c_outro.force_login(outro)

    def ids_na_lista(cliente, pasta=None):
        r = cliente.get('/files/' + (f'?folder={pasta.pk}' if pasta else ''))
        return r, {f.pk for f in r.context['files']} if r.context else set()

    print('== A LISTA RESPEITA A VISIBILIDADE ==')
    r, ids = ids_na_lista(c_padrao, publica)
    t('PADRÃO do setor A vê o arquivo para todos', f_todos.pk in ids)
    t('mas não o do setor B nem o de outra pessoa', f_setor_b.pk not in ids and f_do_outro.pk not in ids, ids)
    r = c_padrao.get('/files/')
    t('e a pasta só do setor B nem aparece', so_b.pk not in {f.pk for f in r.context['folders']})
    r = c_padrao.get(f'/files/?folder={so_b.pk}')
    t('nem abre pela URL', r.status_code == 302)
    _, ids_outro = ids_na_lista(c_outro, publica)
    t('quem é do setor B e o alvo do arquivo veem os deles',
      {f_todos.pk, f_setor_b.pk, f_do_outro.pk} <= ids_outro, ids_outro)
    _, ids_chefe = ids_na_lista(c_chefe, publica)
    t('SUPERADMIN vê tudo', {f_todos.pk, f_setor_b.pk, f_do_outro.pk} <= ids_chefe)
    r = c_padrao.get(f'/files/{f_setor_b.pk}/')
    t('abrir arquivo sem acesso pela URL é barrado', r.status_code == 302)
    t('e fica no log como tentativa', MovimentacaoArquivo.objects.filter(
        acao='NEGADO', item_id=f_setor_b.pk, usuario=padrao).exists())
    r = c_padrao.get(f'/files/{f_setor_b.pk}/download/')
    t('download sem acesso também', r.status_code == 302 and 's3' not in r.get('Location', ''))

    print('\n== SÓ QUEM PODE ENVIAR GERENCIA (O PADRÃO NÃO APAGA MAIS) ==')
    antes = Folder.objects.count()
    c_padrao.post('/files/create-folder/', {'name': 'ZZ invasora'})
    t('PADRÃO não cria pasta', Folder.objects.count() == antes)
    c_padrao.post(f'/files/delete-folder/{publica.pk}/')
    publica.refresh_from_db()
    t('nem exclui pasta', publica.excluido_em is None)
    r = c_padrao.post('/files/move-file/', data={'file_id': f_todos.pk, 'category_id': cat_sub.pk},
                      content_type='application/json')
    f_todos.refresh_from_db()
    t('nem move arquivo', f_todos.category_id == cat_pub.pk and r.json()['success'] is False)
    r = c_padrao.get('/files/')
    t('e os botões de gerenciar somem para ele', 'Nova Pasta' not in r.content.decode())
    c_adm.post('/files/create-folder/', {'name': 'ZZ do adm'})
    nova = Folder.objects.filter(name='ZZ do adm').first()
    t('ADMINISTRATIVO cria pasta, e fica no log', nova and MovimentacaoArquivo.objects.filter(
        acao='CRIAR_PASTA', item_id=nova.pk, usuario=adm).exists())
    r = c_adm.post('/files/move-file/', data={'file_id': f_sub.pk, 'category_id': cat_pub.pk},
                   content_type='application/json')
    t('mover fica no log com origem e destino', r.json()['success'] and MovimentacaoArquivo.objects.filter(
        acao='MOVER', item_id=f_sub.pk, detalhe__contains='ZZ Cat sub').exists())
    c_adm.post('/files/move-file/', data={'file_id': f_sub.pk, 'category_id': cat_sub.pk},
               content_type='application/json')

    print('\n== ENVIO E DOWNLOAD NO LOG ==')
    r = c_padrao.post('/files/upload/', {'title': 'ZZ enviado pelo padrao', 'category': cat_pub.pk,
                                         'visibility': 'ALL',
                                         'files': [SimpleUploadedFile('x.pdf', b'%PDF', 'application/pdf')]})
    t('PADRÃO não envia', not SharedFile.objects.filter(title='ZZ enviado pelo padrao').exists())
    r = c_adm.post('/files/upload/', {'title': 'ZZ enviado', 'category': cat_pub.pk, 'visibility': 'ALL',
                                      'folder': publica.pk,
                                      'files': [SimpleUploadedFile('x.pdf', b'%PDF', 'application/pdf')]})
    enviado = SharedFile.objects.filter(title='ZZ enviado').first()
    t('envio fica no log', enviado and MovimentacaoArquivo.objects.filter(acao='ENVIO', item_id=enviado.pk,
                                                                          usuario=adm).exists())
    c_padrao.get(f'/files/{f_todos.pk}/')
    t('visualização/download fica no log', MovimentacaoArquivo.objects.filter(
        acao='DOWNLOAD', item_id=f_todos.pk, usuario=padrao).exists())

    print('\n== ACESSO POR PESSOA (SUPERADMIN) ==')
    r = c_adm.get(f'/files/acesso/arquivo/{f_setor_b.pk}/')
    t('só o SUPERADMIN abre o acesso por pessoa', r.status_code == 302)
    r = c_chefe.get(f'/files/acesso/arquivo/{f_setor_b.pk}/')
    t('SUPERADMIN abre', r.status_code == 200 and 'Liberar para' in r.content.decode())
    c_chefe.post(f'/files/acesso/arquivo/{f_setor_b.pk}/', {'acao': 'liberar', 'pessoas': [padrao.pk]})
    _, ids = ids_na_lista(c_padrao, publica)
    t('liberado por pessoa: o PADRÃO do setor A passa a ver', f_setor_b.pk in ids)
    t('e a liberação fica no log com a pessoa', MovimentacaoArquivo.objects.filter(
        acao='LIBERAR', item_id=f_setor_b.pk, pessoa=padrao, usuario=chefe).exists())
    c_chefe.post(f'/files/acesso/arquivo/{f_setor_b.pk}/', {'acao': 'retirar', 'pessoa': padrao.pk})
    _, ids = ids_na_lista(c_padrao, publica)
    t('retirado: volta a não ver', f_setor_b.pk not in ids)
    t('retirada também no log', MovimentacaoArquivo.objects.filter(acao='RETIRAR', pessoa=padrao).exists())
    c_chefe.post(f'/files/acesso/pasta/{so_b.pk}/', {'acao': 'liberar', 'pessoas': [padrao.pk]})
    r = c_padrao.get('/files/')
    t('pasta liberada por pessoa aparece para ele', so_b.pk in {f.pk for f in r.context['folders']})
    _, ids = ids_na_lista(c_padrao, so_b)
    t('com os arquivos dentro', f_na_pasta_b.pk in ids)

    print('\n== VISÃO POR USUÁRIO (SUPERADMIN) ==')
    r = c_adm.get(f'/files/por-usuario/?usuario={padrao.pk}')
    t('só o SUPERADMIN abre', r.status_code == 302)
    r = c_chefe.get(f'/files/por-usuario/?usuario={padrao.pk}')
    ve = {a.pk for a in r.context['ve']}
    nao_ve = {a.pk for a in r.context['nao_ve']}
    t('mostra o que a pessoa vê e o que não vê', f_todos.pk in ve and f_na_pasta_b.pk in ve
      and f_do_outro.pk in nao_ve and f_setor_b.pk in nao_ve, (ve, nao_ve))
    t('e as pastas liberadas para ela', so_b in r.context['pastas_liberadas'])
    c_chefe.post('/files/por-usuario/', {'usuario': padrao.pk, 'tipo': 'arquivo', 'acao': 'liberar',
                                         'item': [f_do_outro.pk, f_setor_b.pk]})
    _, ids = ids_na_lista(c_padrao, publica)
    t('libera vários arquivos para a pessoa de uma vez', {f_do_outro.pk, f_setor_b.pk} <= ids, ids)
    c_chefe.post('/files/por-usuario/', {'usuario': padrao.pk, 'tipo': 'pasta', 'acao': 'retirar',
                                         'item': so_b.pk})
    r = c_padrao.get('/files/')
    t('e retira a pasta dela por lá', so_b.pk not in {f.pk for f in r.context['folders']})

    print('\n== EXCLUIR NUNCA APAGA: LIXEIRA ==')
    nome_no_storage = f_todos.file.name
    r = c_adm.post(f'/files/delete-folder/{publica.pk}/')
    publica.refresh_from_db()
    t('excluir a pasta manda para a lixeira (a linha continua no banco)', publica.excluido_em is not None
      and Folder.objects.filter(pk=publica.pk).exists())
    t('com subpastas, categorias e arquivos no mesmo lote',
      Folder.objects.get(pk=sub.pk).lote_exclusao == publica.lote_exclusao
      and FileCategory.objects.get(pk=cat_sub.pk).lote_exclusao == publica.lote_exclusao
      and SharedFile.objects.get(pk=f_todos.pk).lote_exclusao == publica.lote_exclusao)
    t('o arquivo continua no storage', memoria.exists(nome_no_storage))
    r = c_adm.get('/files/')
    t('some da lista', publica.pk not in {f.pk for f in r.context['folders']})
    r = c_outro.get(f'/files/{f_todos.pk}/')
    t('e não abre mais pela URL', r.status_code == 404)
    t('exclusão fica no log com o que foi junto', MovimentacaoArquivo.objects.filter(
        acao='EXCLUIR', tipo='PASTA', item_id=publica.pk, usuario=adm, detalhe__contains='arquivo').exists())
    r = c_adm.get('/files/lixeira/')
    t('só o SUPERADMIN abre a lixeira', r.status_code == 302)
    r = c_chefe.get('/files/lixeira/')
    principais = [l['principal'] for l in r.context['lotes']]
    t('a lixeira mostra o lote pela pasta que foi excluída', publica in principais
      and sub not in principais, principais)

    r = c_chefe.post(f'/files/lixeira/pasta/{publica.pk}/recuperar/')
    publica.refresh_from_db()
    t('recuperar devolve a pasta', publica.excluido_em is None)
    t('e tudo o que saiu junto', not SharedFile.objects.filter(pk__in=[f_todos.pk, f_sub.pk],
                                                               excluido_em__isnull=False).exists()
      and Folder.objects.get(pk=sub.pk).excluido_em is None)
    t('recuperação no log', MovimentacaoArquivo.objects.filter(acao='RESTAURAR', item_id=publica.pk,
                                                                 usuario=chefe).exists())

    print('\n-- arquivo do próprio autor e caminho que volta junto --')
    proprio = arquivo('ZZ meu', cat_sub, por=padrao)
    r = c_padrao.post(f'/files/delete-file/{proprio.pk}/')
    proprio.refresh_from_db()
    t('quem enviou manda o próprio arquivo para a lixeira', proprio.excluido_em is not None
      and proprio.excluido_por_id == padrao.pk)
    c_adm.post(f'/files/delete-category/{cat_sub.pk}/')
    cat_sub.refresh_from_db()
    t('categoria excluída depois, em outro lote', cat_sub.excluido_em is not None
      and cat_sub.lote_exclusao != proprio.lote_exclusao)
    c_chefe.post(f'/files/lixeira/arquivo/{proprio.pk}/recuperar/')
    proprio.refresh_from_db()
    cat_sub.refresh_from_db()
    t('recuperar o arquivo traz de volta a categoria onde ele mora', proprio.excluido_em is None
      and cat_sub.excluido_em is None)
    t('os outros arquivos da categoria seguem na lixeira (saíram no lote dela)',
      SharedFile.objects.get(pk=f_sub.pk).excluido_em is not None)
    t('o PADRÃO não exclui arquivo dos outros', c_padrao.post(f'/files/delete-file/{f_todos.pk}/').status_code == 302
      and SharedFile.objects.get(pk=f_todos.pk).excluido_em is None)
    t('nenhum item foi apagado do banco em todo o teste',
      SharedFile.objects.filter(pk__in=[f_todos.pk, f_setor_b.pk, f_na_pasta_b.pk, f_do_outro.pk, f_sub.pk,
                                        proprio.pk]).count() == 6)

    print('\n== MOVIMENTAÇÕES (SUPERADMIN) ==')
    r = c_adm.get('/files/movimentacoes/')
    t('só o SUPERADMIN abre o log', r.status_code == 302)
    r = c_chefe.get('/files/movimentacoes/')
    t('o log abre com tudo', r.status_code == 200 and r.context['pagina'].paginator.count >= 10)
    r = c_chefe.get('/files/movimentacoes/?acao=EXCLUIR')
    t('filtra por ação', r.context['pagina'].paginator.count >= 1
      and all(m.acao == 'EXCLUIR' for m in r.context['pagina']))
    r = c_chefe.get(f'/files/movimentacoes/?usuario={padrao.pk}')
    t('filtra por pessoa (quem fez ou a quem afetou)', any(m.pessoa_id == padrao.pk for m in r.context['pagina'])
      and any(m.usuario_id == padrao.pk for m in r.context['pagina']))
    r = c_chefe.get('/files/')
    html = r.content.decode()
    t('a lista do SUPERADMIN tem os atalhos', 'Movimentações' in html and 'Lixeira' in html
      and 'Por usuário' in html and 'Acesso por pessoa' in html)

    for p in pilha:
        p.stop()
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco (nem a migração).')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
