"""Drive → editar no computador: o Office por WebDAV e a cópia de trabalho pela página.

Roda dentro de uma transação desfeita no fim: não grava nada no banco. Não fala
com o Google (Drive falso em memória), não avisa ninguém (o aviso ao setor vira
uma lista) e os temporários do WebDAV vão para uma pasta temporária apagada no fim.
"""
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path
from unittest import mock
from urllib.parse import quote

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
from django.utils import timezone

from drive import edicao_local, gdrive
from drive import permissions as perms
from drive.models import DriveAuditLog, DriveConfig, DrivePermission, EdicaoLocal, SectorDriveMapping
from users.models import Sector

User = get_user_model()
FOLDER = gdrive.FOLDER_MIME
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
NODE = shutil.which('node')
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def scripts_embutidos(html, marcador):
    blocos = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, flags=re.S)
    return [b for b in blocos if marcador in b]


def sintaxe_ok(codigo):
    if not NODE:
        return True, 'node ausente'
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as fh:
        fh.write(codigo)
        caminho = fh.name
    try:
        r = subprocess.run([NODE, '--check', caminho], capture_output=True, text=True)
        return r.returncode == 0, r.stderr[-800:]
    finally:
        os.unlink(caminho)


def hash_de(token):
    return hashlib.sha256(token.encode()).hexdigest()


class DriveFalso:
    """Um Drive em memória com a forma de resposta do Google: md5 e versão mudam a cada gravação."""

    def __init__(self):
        self.itens, self.conteudo, self.versoes, self.enviados, self.seq = {}, {}, [], [], 0

    def add(self, id_, nome, mime, pai, dados=b''):
        self.itens[id_] = {
            'id': id_, 'name': nome, 'mimeType': mime, 'parents': [pai] if pai else [], 'trashed': False,
            'size': str(len(dados)), 'modifiedTime': '2026-09-10T10:00:00.000Z', 'version': '1',
            'createdTime': '2026-09-01T10:00:00.000Z',
        }
        if not mime.startswith('application/vnd.google-apps.'):
            self.itens[id_]['md5Checksum'] = hashlib.md5(dados).hexdigest()
        self.conteudo[id_] = dados
        return dict(self.itens[id_])

    def obter(self, file_id, fields=None):
        if file_id not in self.itens:
            raise gdrive.DriveError('Google Drive respondeu 404: File not found')
        return dict(self.itens[file_id])

    def ancestrais(self, file_id, limite=30):
        ids, atual = [], file_id
        while atual and atual not in ids and len(ids) < limite:
            ids.append(atual)
            atual = (self.itens.get(atual, {}).get('parents') or [None])[0]
        return ids

    def baixar(self, file_id, preview=False):
        item = self.obter(file_id)
        return io.BytesIO(self.conteudo[file_id]), item['name'], item['mimeType']

    def gravar(self, file_id, dados):
        item = self.itens[file_id]
        item.update(md5Checksum=hashlib.md5(dados).hexdigest(), size=str(len(dados)),
                    version=str(int(item['version']) + 1))
        self.conteudo[file_id] = dados

    def nova_versao(self, file_id, stream, mimetype=None):
        stream.seek(0)
        dados = stream.read()
        self.gravar(file_id, dados)
        self.versoes.append((file_id, dados, mimetype))
        return dict(self.itens[file_id])

    def enviar(self, nome, mimetype, stream, parent_id):
        stream.seek(0)
        dados = stream.read()
        self.seq += 1
        novo = self.add(f'CONFLITO{self.seq}', nome, mimetype, parent_id, dados)
        self.enviados.append((nome, parent_id, dados))
        return novo


class CfgMeuDrive:
    """Configuração com a conta própria conectada (sem tocar na de verdade)."""
    usa_conta_propria = True
    oauth_refresh_token = 'refresh-falso'
    max_file_mb = 100
    max_file_bytes = 100 * 1024 * 1024

    @classmethod
    def get(cls):
        return cls


LOCKINFO = (b'<?xml version="1.0" encoding="utf-8"?><D:lockinfo xmlns:D="DAV:"><D:lockscope><D:exclusive/>'
            b'</D:lockscope><D:locktype><D:write/></D:locktype><D:owner><D:href>ZZ</D:href></D:owner></D:lockinfo>')
PROPPATCH = (b'<?xml version="1.0" encoding="utf-8"?><D:propertyupdate xmlns:D="DAV:" '
             b'xmlns:Z="urn:schemas-microsoft-com:"><D:set><D:prop><Z:Win32LastModifiedTime>'
             b'Thu, 10 Sep 2026 10:00:00 GMT</Z:Win32LastModifiedTime></D:prop></D:set></D:propertyupdate>')

drive = DriveFalso()
avisos_setor = []
temporarios = Path(tempfile.mkdtemp(prefix='zz-drive-edicao-'))
anon = Client()


def dav(metodo, caminho, corpo=b'', cabecalhos=None):
    return anon.generic(metodo, caminho, data=corpo, content_type='application/octet-stream', headers=cabecalhos)


marcador = transaction.atomic()
marcador.__enter__()
pilha = ExitStack()
try:
    for alvo, novo in (('obter', drive.obter), ('ancestrais', drive.ancestrais), ('baixar', drive.baixar),
                       ('nova_versao', drive.nova_versao), ('enviar', drive.enviar)):
        pilha.enter_context(mock.patch.object(gdrive, alvo, novo))
    pilha.enter_context(mock.patch(
        'drive.views._notificar',
        side_effect=lambda mapping, cfg, novo=True, quantos=1, ator=None, folder_id='':
        avisos_setor.append((mapping.sector_id, novo, ator.pk if ator else None))))
    pilha.enter_context(mock.patch.object(edicao_local, 'PASTA_AUXILIAR', temporarios))

    def usuario(apelido, nome, sobrenome):
        return User.objects.create_user(username=f'zzed.{apelido}', email=f'zzed.{apelido}@exemplo-teste.local',
                                        password='S3nha!teste', first_name=nome, last_name=sobrenome)

    editor = usuario('editor', 'ZZ', 'Editora')
    leitor = usuario('leitor', 'ZZ', 'Leitor')
    visitante = usuario('visita', 'ZZ', 'Visitante')
    estranho = usuario('estranho', 'ZZ', 'Estranho')
    colega = usuario('colega', 'ZZ', 'Colega')
    chefe = usuario('chefe', 'ZZ', 'Chefe')
    User.objects.filter(pk=chefe.pk).update(hierarchy='SUPERADMIN')

    setor = Sector.objects.create(name='ZZ Setor Edição Local')
    mapa = SectorDriveMapping.objects.create(sector=setor, folder_id='PASTA_SETOR_ZZ', folder_name='ZZ Raiz')

    def permitir(user, nivel):
        return DrivePermission.objects.create(mapping=mapa, alvo='USER', target_user=user, nivel=nivel)

    p_editor = permitir(editor, 'EDIT')
    permitir(leitor, 'DOWNLOAD')
    permitir(visitante, 'VIEW')
    permitir(colega, 'EDIT')

    drive.add('PASTA_SETOR_ZZ', 'ZZ Raiz', FOLDER, None)
    drive.add('ARQ_XLSX', 'Metas 01/2026 #1.xlsx', XLSX, 'PASTA_SETOR_ZZ', b'PK-planilha-original')
    drive.add('ARQ_PDF', 'Contrato.pdf', 'application/pdf', 'PASTA_SETOR_ZZ', b'%PDF-original')
    drive.add('ARQ_DOC', 'Ata', 'application/vnd.google-apps.document', 'PASTA_SETOR_ZZ')

    clientes = {}
    for user in (editor, leitor, visitante, estranho, colega, chefe):
        clientes[user.pk] = Client()
        clientes[user.pk].force_login(user)
    c_editor, c_leitor, c_visitante = clientes[editor.pk], clientes[leitor.pk], clientes[visitante.pk]
    c_estranho, c_colega, c_chefe = clientes[estranho.pk], clientes[colega.pk], clientes[chefe.pk]

    print('== PEÇAS ==')
    t('nome com barra e # vira nome de endereço', edicao_local.nome_no_endereco('Metas 01/2026 #1.xlsx') == 'Metas 01_2026 _1.xlsx')
    longo = edicao_local.nome_no_endereco('A' * 300 + '.xlsx')
    t('nome longo é cortado mantendo a extensão', len(longo) <= 150 and longo.endswith('.xlsx'), len(longo))
    t('".." não vira caminho', edicao_local.nome_no_endereco('..') == 'arquivo')
    t('Word, Excel e PowerPoint pelo protocolo certo',
      (edicao_local.esquema_office('ATA.DOCX'), edicao_local.esquema_office('x.xlsm'), edicao_local.esquema_office('a.pptx'))
      == ('ms-word', 'ms-excel', 'ms-powerpoint'))
    t('PDF e Power BI: cópia no computador',
      edicao_local.modo_de_edicao({'id': 'x', 'name': 'a.pdf', 'mimeType': 'application/pdf'}) == 'arquivo'
      and edicao_local.modo_de_edicao({'id': 'x', 'name': 'p.pbix', 'mimeType': 'application/octet-stream'}) == 'arquivo')
    t('Docs do Google e pastas: não se editam no computador',
      edicao_local.modo_de_edicao({'id': 'x', 'name': 'Ata', 'mimeType': 'application/vnd.google-apps.document'}) == ''
      and edicao_local.modo_de_edicao({'id': 'x', 'name': 'P', 'mimeType': FOLDER}) == '')
    conflito = edicao_local._nome_de_conflito('B' * 250 + '.xlsx', editor, timezone.now())
    t('nome de conflito cabe no Drive e mantém a extensão',
      len(conflito) <= 255 and conflito.endswith('.xlsx') and 'conflito de edição' in conflito, len(conflito))
    ctx = edicao_local.contexto_da_tela
    meta_pdf = {'id': 'x', 'name': 'a.pdf', 'mimeType': 'application/pdf'}
    meta_xlsx = {'id': 'x', 'name': 'a.xlsx', 'mimeType': XLSX}
    t('cartão: PDF só para quem edita', ctx(meta_pdf, True, False, '/u/') is None and ctx(meta_pdf, True, True, '/u/')['modo'] == 'arquivo')
    t('cartão: planilha em leitura para quem só baixa',
      ctx(meta_xlsx, True, False, '/u/') == {'modo': 'office', 'programa': 'Excel', 'pode_salvar': False, 'url_iniciar': '/u/'})
    t('cartão: quem só vê não tem', ctx(meta_xlsx, False, False, '/u/') is None)

    print('\n== TELA DO ARQUIVO ==')
    html = c_editor.get('/drive/file/ARQ_XLSX/').content.decode()
    t('planilha: cartão "Editar no computador"', 'id="edicao-local"' in html and 'Editar no computador' in html)
    t('abre no Excel', 'data-modo="office"' in html and 'data-programa="Excel"' in html and 'Abrir no Excel' in html)
    t('quem edita pode salvar', 'data-pode-salvar="1"' in html)
    t('alternativa para quem não tem o Office', 'Editar com outro programa' in html)
    t('endereço para abrir a edição', 'data-url-iniciar="/drive/file/ARQ_XLSX/editar-no-computador/"' in html)
    t('carrega o módulo', 'js/rc-edicao-local.js' in html)
    t('sem comentário de template vazando', '{#' not in html and '{% comment' not in html)
    blocos = scripts_embutidos(html, 'RCEdicaoLocal.montar')
    t('um script do cartão', len(blocos) == 1, len(blocos))
    if blocos:
        valido, erro = sintaxe_ok(blocos[0])
        t('o script do cartão tem sintaxe válida (node --check)', valido, erro)
    html = c_leitor.get('/drive/file/ARQ_XLSX/').content.decode()
    t('quem só baixa: abre em modo leitura', 'data-pode-salvar="0"' in html and 'Abrir no computador' in html and 'modo leitura' in html)
    t('quem só baixa: sem "outro programa"', 'Editar com outro programa' not in html)
    t('quem só baixa um PDF: sem cartão (o Baixar já serve)', 'id="edicao-local"' not in c_leitor.get('/drive/file/ARQ_PDF/').content.decode())
    html = c_editor.get('/drive/file/ARQ_PDF/').content.decode()
    t('PDF de quem edita: cópia de trabalho no computador', 'data-modo="arquivo"' in html and 'cópia de trabalho' in html)
    t('Doc do Google: sem cartão', 'id="edicao-local"' not in c_editor.get('/drive/file/ARQ_DOC/').content.decode())

    print('\n== ABRIR PARA EDITAR ==')
    URL_XLSX = '/drive/file/ARQ_XLSX/editar-no-computador/'
    URL_PDF = '/drive/file/ARQ_PDF/editar-no-computador/'
    nome_dav = quote('Metas 01_2026 _1.xlsx')
    r = c_editor.post(URL_XLSX)
    j = r.json()
    tok = j.get('token', '')
    t('abre (200)', r.status_code == 200, r.status_code)
    t('planilha vai pelo Excel', j.get('modo') == 'office' and j.get('programa') == 'Excel')
    t('protocolo do Office em modo edição, com https',
      j.get('uri_office') == f'ms-excel:ofe|u|https://testserver/drive/dav/{tok}/{nome_dav}', j.get('uri_office'))
    sessao = EdicaoLocal.objects.get(token_hash=hash_de(tok))
    t('token forte', len(tok) >= 40, len(tok))
    t('no banco fica só o hash do token', tok not in sessao.token_hash and sessao.user_id == editor.pk)
    t('guarda a versão que abriu (md5)', sessao.md5_base == drive.itens['ARQ_XLSX']['md5Checksum'])
    t('vale 12 horas', timedelta(hours=11, minutes=59) < sessao.expira_em - timezone.now() <= timedelta(hours=12))
    t('fica na auditoria', DriveAuditLog.objects.filter(user=editor, acao='VIEW', file_id='ARQ_XLSX',
                                                        detalhe='editar no computador').exists())

    j = c_leitor.post(URL_XLSX).json()
    tok_leitor = j.get('token', '')
    t('quem só baixa: modo leitura do Office (ofv)',
      j.get('somente_leitura') is True and j.get('uri_office', '').startswith('ms-excel:ofv|u|'), j.get('uri_office'))
    r = c_leitor.post(URL_PDF)
    t('quem só baixa: cópia para salvar de volta é recusada', r.status_code == 403 and 'permissão' in r.json().get('error', ''))
    t('quem só baixa: nem pedindo a planilha como cópia', c_leitor.post(URL_XLSX, {'modo': 'arquivo'}).status_code == 403)
    t('quem só vê: proibido', c_visitante.post(URL_XLSX).status_code == 403)
    t('e a negação fica na auditoria', DriveAuditLog.objects.filter(user=visitante, acao='DENY', file_id='ARQ_XLSX').exists())
    t('sem acesso ao setor: proibido', c_estranho.post(URL_XLSX).status_code == 403)
    r = c_editor.post('/drive/file/ARQ_DOC/editar-no-computador/')
    t('Doc do Google: explica que é no Google', r.status_code == 400 and 'Google' in r.json().get('error', ''))
    j = c_editor.post(URL_PDF).json()
    tok_pdf = j.get('token', '')
    t('PDF: cópia no computador, sem protocolo do Office', j.get('modo') == 'arquivo' and 'uri_office' not in j)
    t('PDF: com o endereço do conteúdo para baixar', j.get('url_conteudo') == '/drive/file/ARQ_PDF/content/?dl=1')
    t('"outro programa": a planilha vai como cópia', c_editor.post(URL_XLSX, {'modo': 'arquivo'}).json().get('modo') == 'arquivo')
    t('GET não abre edição', c_editor.get(URL_XLSX).status_code == 405)
    t('sem login: vai para o login', anon.post(URL_XLSX).status_code == 302)
    t('id inválido: 404', c_editor.post('/drive/file/a.b/editar-no-computador/').status_code == 404)

    print('\n== WEBDAV: O OFFICE ABRINDO E SALVANDO ==')
    base = f'/drive/dav/{tok}/'
    arq = base + nome_dav
    r = dav('OPTIONS', '/drive/dav/')
    t('descoberta: OPTIONS na pasta de cima responde DAV', r.status_code == 200 and r['DAV'] == '1,2' and r['MS-Author-Via'] == 'DAV')
    r = dav('OPTIONS', arq)
    t('OPTIONS no arquivo com os métodos WebDAV', r.status_code == 200 and 'LOCK' in r['Allow'] and 'PROPFIND' in r['Allow'])
    md5 = drive.itens['ARQ_XLSX']['md5Checksum']
    r = dav('PROPFIND', arq, cabecalhos={'Depth': '0'})
    corpo = r.content.decode()
    t('PROPFIND do arquivo (207)', r.status_code == 207, r.status_code)
    t('com tamanho, etag e trava suportada', '<D:getcontentlength>20</D:getcontentlength>' in corpo
      and f'<D:getetag>"{md5}"</D:getetag>' in corpo and '<D:supportedlock>' in corpo, corpo[:300])
    t('com o nome que o Office mostra', '<D:displayname>Metas 01_2026 _1.xlsx</D:displayname>' in corpo)
    r = dav('PROPFIND', base, cabecalhos={'Depth': '1'})
    corpo = r.content.decode()
    t('PROPFIND da pasta lista o arquivo', r.status_code == 207 and '<D:collection/>' in corpo and f'/drive/dav/{tok}/{nome_dav}' in corpo)
    r = dav('HEAD', arq)
    t('HEAD com tamanho e etag', r.status_code == 200 and r['Content-Length'] == '20' and r['ETag'] == f'"{md5}"')
    r = dav('GET', arq)
    t('GET entrega o arquivo do Drive', r.status_code == 200 and r.content == b'PK-planilha-original')
    dav('GET', arq)
    t('abrir no computador fica na auditoria uma vez', DriveAuditLog.objects.filter(
        user=editor, acao='DOWNLOAD', file_id='ARQ_XLSX', detalhe='aberto no computador').count() == 1)
    t('token errado: link vencido (403, sem pedir senha)', dav('PROPFIND', f'/drive/dav/{"x" * 43}/{nome_dav}').status_code == 403)
    t('token da cópia no computador não abre pelo WebDAV', dav('GET', f'/drive/dav/{tok_pdf}/Contrato.pdf').status_code == 403)

    r = dav('LOCK', arq, LOCKINFO, {'Timeout': 'Second-3600', 'Depth': '0'})
    trava = r.get('Lock-Token', '').strip('<>')
    t('LOCK: o Office trava o arquivo', r.status_code == 200 and trava.startswith('opaquelocktoken:'), r.status_code)
    t('LOCK responde com o dono da trava', '<D:owner>ZZ Editora</D:owner>' in r.content.decode())
    j = c_editor.get(f'/drive/edicao/{tok}/status/').json()
    t('status: aberto no programa', j.get('aberto_no_programa') is True and j.get('salvamentos') == 0, j)
    t('status da edição de outra pessoa: não encontrado', c_colega.get(f'/drive/edicao/{tok}/status/').status_code == 404)

    tok_colega = c_colega.post(URL_XLSX).json().get('token', '')
    arq_colega = f'/drive/dav/{tok_colega}/{nome_dav}'
    r = dav('LOCK', arq_colega, LOCKINFO, {'Timeout': 'Second-3600'})
    t('outra pessoa abrindo: arquivo em edição (423)', r.status_code == 423 and 'ZZ Editora' in r.content.decode(), r.status_code)
    corpo = dav('PROPFIND', arq_colega, cabecalhos={'Depth': '0'}).content.decode()
    t('e o PROPFIND dela mostra quem está editando', '<D:activelock>' in corpo and 'ZZ Editora' in corpo)
    r = dav('PUT', arq_colega, b'por cima', {'If': '(<opaquelocktoken:qualquer>)'})
    t('e ela não salva por cima (423)', r.status_code == 423 and not drive.versoes, r.status_code)

    r = dav('LOCK', arq, b'', {'If': f'(<{trava}>)', 'Timeout': 'Second-3600'})
    t('renovar a trava', r.status_code == 200 and r['Lock-Token'] == f'<{trava}>', r.status_code)
    t('renovar sem dizer a trava: 412', dav('LOCK', arq, b'').status_code == 412)

    r = dav('PUT', arq, b'PK-planilha-editada', {'If': f'(<{trava}>)'})
    t('Ctrl+S no Office: salvo (204)', r.status_code == 204, r.status_code)
    t('vira nova versão no Drive, com o tipo do arquivo',
      drive.versoes and drive.versoes[-1] == ('ARQ_XLSX', b'PK-planilha-editada', XLSX), drive.versoes[-1:])
    sessao.refresh_from_db()
    t('conta o salvamento e guarda a nova base',
      sessao.salvamentos == 1 and sessao.md5_base == drive.itens['ARQ_XLSX']['md5Checksum'])
    t('fica na auditoria como nova versão', DriveAuditLog.objects.filter(
        user=editor, acao='VERSION', file_id='ARQ_XLSX', detalhe='editado no computador (Office)').exists())
    t('avisa o setor (sem contar a própria pessoa)', avisos_setor == [(setor.pk, False, editor.pk)], avisos_setor)
    dav('PUT', arq, b'PK-planilha-editada-2', {'If': f'(<{trava}>)'})
    t('salvar de novo: outra versão', len(drive.versoes) == 2 and drive.conteudo['ARQ_XLSX'] == b'PK-planilha-editada-2')
    t('sem avisar o setor a cada Ctrl+S', len(avisos_setor) == 1, avisos_setor)
    r = dav('PUT', arq, b'PK-sem-if')
    t('a mesma edição salvando sem o cabeçalho da trava: aceito', r.status_code == 204 and drive.conteudo['ARQ_XLSX'] == b'PK-sem-if')
    r = dav('PROPPATCH', arq, PROPPATCH)
    t('PROPPATCH (atributos do Windows): confirmado', r.status_code == 207 and 'Win32LastModifiedTime' in r.content.decode())
    antes = len(drive.versoes)
    r = dav('PUT', arq, b'')
    t('PUT vazio não apaga o documento', r.status_code == 204 and len(drive.versoes) == antes and drive.conteudo['ARQ_XLSX'] == b'PK-sem-if')
    j = c_editor.get(f'/drive/edicao/{tok}/status/').json()
    t('status: quantas vezes salvou', j.get('salvamentos') == 3 and j.get('ultimo_salvamento_em'), j)

    print('\n== CONFLITO: ALGUÉM MUDOU O ARQUIVO NO MEIO ==')
    drive.gravar('ARQ_XLSX', b'mudado-no-google')
    r = dav('PUT', arq, b'versao-da-editora', {'If': f'(<{trava}>)'})
    t('o salvamento não se perde (204)', r.status_code == 204, r.status_code)
    t('e não passa por cima da alteração da outra pessoa', drive.conteudo['ARQ_XLSX'] == b'mudado-no-google')
    nome_conflito, pai_conflito, dados_conflito = drive.enviados[-1] if drive.enviados else ('', '', b'')
    t('vira cópia de conflito na mesma pasta', pai_conflito == 'PASTA_SETOR_ZZ' and dados_conflito == b'versao-da-editora')
    t('com o nome dizendo o que houve', re.fullmatch(
        r'Metas 01/2026 #1 \(conflito de edição — ZZ Editora — \d\d-\d\d-\d{4} \d\dh\d\d\)\.xlsx', nome_conflito) is not None,
      nome_conflito)
    t('fica na auditoria', DriveAuditLog.objects.filter(user=editor, acao='UPLOAD', file_name=nome_conflito).exists())
    j = c_editor.get(f'/drive/edicao/{tok}/status/').json()
    t('status mostra o conflito', (j.get('conflito') or {}).get('nome') == nome_conflito, j.get('conflito'))
    dav('PUT', arq, b'versao-da-editora-2', {'If': f'(<{trava}>)'})
    t('salvar de novo atualiza a mesma cópia de conflito', len(drive.enviados) == 1
      and drive.versoes[-1][:2] == ('CONFLITO1', b'versao-da-editora-2') and drive.conteudo['ARQ_XLSX'] == b'mudado-no-google')
    dav('GET', arq)
    dav('PUT', arq, b'depois-de-reabrir', {'If': f'(<{trava}>)'})
    t('reabrir (baixar de novo) volta a salvar no próprio arquivo',
      drive.conteudo['ARQ_XLSX'] == b'depois-de-reabrir' and len(drive.enviados) == 1)

    print('\n== TEMPORÁRIOS DO PROGRAMA ==')
    pasta_sessao = temporarios / str(sessao.pk)
    aux = base + quote('~$Metas.xlsx')
    r = dav('PUT', aux, b'dono-temporario')
    t('temporário do Office: aceito (201)', r.status_code == 201, r.status_code)
    t('fica fora do Drive', drive.conteudo['ARQ_XLSX'] == b'depois-de-reabrir'
      and (pasta_sessao / '~$Metas.xlsx').read_bytes() == b'dono-temporario')
    r = dav('GET', aux)
    t('e dá para ler de volta', r.status_code == 200 and b''.join(r.streaming_content) == b'dono-temporario')
    t('aparece na listagem da pasta', '~$Metas.xlsx' in dav('PROPFIND', base, cabecalhos={'Depth': '1'}).content.decode())
    t('trava no temporário: aceita sem mexer no arquivo', dav('LOCK', aux, LOCKINFO).status_code == 200)
    r = dav('MOVE', aux, cabecalhos={'Destination': f'http://testserver{base}{quote("~$Outro.xlsx")}'})
    t('renomear temporário', r.status_code == 201 and (pasta_sessao / '~$Outro.xlsx').exists(), r.status_code)
    t('apagar temporário', dav('DELETE', base + quote('~$Outro.xlsx')).status_code == 204)
    t('o arquivo do Drive não se apaga por aqui', dav('DELETE', arq).status_code == 403)
    t('nem se move por aqui', dav('MOVE', arq, cabecalhos={'Destination': f'http://testserver{base}novo.xlsx'}).status_code == 403)
    dav('PUT', base + 'x.tmp', b'x')
    t('destino fora da pasta de edição: recusado', dav('MOVE', base + 'x.tmp', cabecalhos={
        'Destination': 'http://testserver/drive/dav/outro-token-qualquer-0000000/x.tmp'}).status_code == 502)
    dav('PUT', base + 'salvando.tmp', b'salvo-por-renomear')
    r = dav('MOVE', base + 'salvando.tmp', cabecalhos={'Destination': f'http://testserver{arq}', 'Overwrite': 'T'})
    t('salvar por temporário + renomear vira nova versão', r.status_code == 204
      and drive.conteudo['ARQ_XLSX'] == b'salvo-por-renomear', r.status_code)
    t('e o temporário some', not (pasta_sessao / 'salvando.tmp').exists())
    with mock.patch.object(DriveConfig, 'max_file_bytes', new=property(lambda self: 10)):
        antes = len(drive.versoes)
        r = dav('PUT', arq, b'grande-demais!')
        t('passa do limite do Drive: 413', r.status_code == 413 and len(drive.versoes) == antes, r.status_code)

    print('\n== PERMISSÕES NO MEIO DA EDIÇÃO ==')
    arq_leitor = f'/drive/dav/{tok_leitor}/{nome_dav}'
    t('quem só baixa: lê pelo Office', dav('GET', arq_leitor).status_code == 200)
    t('quem só baixa: não trava (o Office abre em leitura)', dav('LOCK', arq_leitor, LOCKINFO).status_code == 403)
    antes = len(drive.versoes)
    t('quem só baixa: não salva', dav('PUT', arq_leitor, b'nao').status_code == 403 and len(drive.versoes) == antes)
    p_editor.nivel = 'DOWNLOAD'
    p_editor.save()
    r = dav('PUT', arq, b'sem-edicao', {'If': f'(<{trava}>)'})
    sessao.refresh_from_db()
    t('perdeu a edição no meio: não salva mais', r.status_code == 403 and len(drive.versoes) == antes, r.status_code)
    t('e a edição passa a ser só leitura', sessao.pode_salvar is False)
    p_editor.delete()
    EdicaoLocal.objects.filter(pk=sessao.pk).update(permissao_conferida_em=timezone.now() - timedelta(minutes=6))
    t('perdeu todo o acesso: nem lê (conferido de novo após 5 minutos)', dav('GET', arq).status_code == 403)
    p_editor = permitir(editor, 'EDIT')

    print('\n== DESTRAVAR, REABRIR, VENCER, ENCERRAR ==')
    tok2 = c_editor.post(URL_XLSX).json()['token']
    arq2 = f'/drive/dav/{tok2}/{nome_dav}'
    sessao.refresh_from_db()
    t('reabrir libera a trava que a própria pessoa deixou', sessao.lock_token == '')
    r = dav('LOCK', arq2, LOCKINFO)
    trava2 = r.get('Lock-Token', '').strip('<>')
    t('e a nova edição trava normalmente', r.status_code == 200, r.status_code)
    t('UNLOCK com a trava errada: 409', dav('UNLOCK', arq2, cabecalhos={'Lock-Token': '<opaquelocktoken:errada>'}).status_code == 409)
    t('UNLOCK (fechou o Excel)', dav('UNLOCK', arq2, cabecalhos={'Lock-Token': f'<{trava2}>'}).status_code == 204)
    r = dav('LOCK', arq_colega, LOCKINFO)
    trava_colega = r.get('Lock-Token', '').strip('<>')
    t('liberado: a colega consegue travar', r.status_code == 200, r.status_code)
    t('com a colega editando, a editora não salva por cima (423)', dav('PUT', arq2, b'x').status_code == 423)
    dav('UNLOCK', arq_colega, cabecalhos={'Lock-Token': f'<{trava_colega}>'})

    s2 = EdicaoLocal.objects.get(token_hash=hash_de(tok2))
    EdicaoLocal.objects.filter(pk=s2.pk).update(expira_em=timezone.now() - timedelta(minutes=1))
    t('link vencido: 403', dav('PROPFIND', arq2, cabecalhos={'Depth': '0'}).status_code == 403)
    t('status mostra vencida', c_editor.get(f'/drive/edicao/{tok2}/status/').json().get('expirada') is True)

    tok3 = c_editor.post(URL_XLSX).json()['token']
    arq3 = f'/drive/dav/{tok3}/{nome_dav}'
    s3 = EdicaoLocal.objects.get(token_hash=hash_de(tok3))
    EdicaoLocal.objects.filter(pk=s3.pk).update(expira_em=timezone.now() + timedelta(minutes=10))
    dav('LOCK', arq3, LOCKINFO)
    s3.refresh_from_db()
    t('usar prorroga a edição (+2 h)', s3.expira_em - timezone.now() > timedelta(hours=1, minutes=55))
    EdicaoLocal.objects.filter(pk=s3.pk).update(criado_em=timezone.now() - timedelta(hours=23),
                                                expira_em=timezone.now() + timedelta(minutes=10))
    dav('LOCK', arq3, b'', {'If': f'(<{EdicaoLocal.objects.get(pk=s3.pk).lock_token}>)'})
    s3.refresh_from_db()
    t('mas nunca passa de 24 h desde que abriu', s3.expira_em - timezone.now() <= timedelta(hours=1))
    dav('PUT', f'/drive/dav/{tok3}/{quote("~$temp.xlsx")}', b'x')
    r = c_editor.post(f'/drive/edicao/{tok3}/encerrar/')
    t('encerrar a edição', r.status_code == 200 and r.json().get('ok'))
    t('depois de encerrar, o link não abre mais', dav('GET', arq3).status_code == 403)
    t('temporários apagados', not (temporarios / str(s3.pk)).exists())
    t('ninguém encerra a edição de outra pessoa', c_colega.post(f'/drive/edicao/{tok2}/encerrar/').status_code == 404)

    print('\n== CÓPIA NO COMPUTADOR (PDF, POWER BI…) ==')

    def enviar(cliente, token, nome, dados):
        return cliente.post(f'/drive/edicao/{token}/enviar/',
                            {'arquivo': SimpleUploadedFile(nome, dados, content_type='application/pdf')})

    avisos_antes = len(avisos_setor)
    r = enviar(c_editor, tok_pdf, 'Contrato.pdf', b'%PDF-editado')
    j = r.json()
    t('a versão da cópia chega (200)', r.status_code == 200 and j.get('ok') and j.get('conflito') is False, r.content[:200])
    t('vira nova versão do PDF no Drive', drive.conteudo['ARQ_PDF'] == b'%PDF-editado')
    t('fica na auditoria', DriveAuditLog.objects.filter(
        user=editor, acao='VERSION', file_id='ARQ_PDF', detalhe='editado no computador (cópia no computador)').exists())
    t('e avisa o setor', len(avisos_setor) == avisos_antes + 1)
    t('arquivo de outro tipo: recusado', enviar(c_editor, tok_pdf, 'Contrato.docx', b'x').status_code == 400)
    t('arquivo vazio: recusado', enviar(c_editor, tok_pdf, 'Contrato.pdf', b'').status_code == 400)
    t('token de outra pessoa: não serve', enviar(c_colega, tok_pdf, 'Contrato.pdf', b'x').status_code == 410)
    t('sem login: vai para o login', enviar(anon, tok_pdf, 'Contrato.pdf', b'x').status_code == 302)
    t('só aceita POST', c_editor.get(f'/drive/edicao/{tok_pdf}/enviar/').status_code == 405)

    s_pdf = EdicaoLocal.objects.get(token_hash=hash_de(tok_pdf))
    drive.gravar('ARQ_PDF', b'%PDF-da-colega')
    EdicaoLocal.objects.filter(pk=s_pdf.pk).update(expira_em=timezone.now() - timedelta(minutes=1))
    r = enviar(c_editor, tok_pdf, 'Contrato.pdf', b'%PDF-editora')
    t('edição vencida: 410 (a página renova)', r.status_code == 410 and r.json().get('expirada') is True)
    tok_pdf2 = c_editor.post(URL_PDF, {'modo': 'arquivo', 'anterior': tok_pdf}).json()['token']
    s_pdf2 = EdicaoLocal.objects.get(token_hash=hash_de(tok_pdf2))
    s_pdf.refresh_from_db()
    t('renovar mantém a versão que a cópia abriu',
      s_pdf2.md5_base == s_pdf.md5_base and s_pdf2.md5_base != drive.itens['ARQ_PDF']['md5Checksum'])
    t('e encerra a edição anterior', s_pdf.encerrado_em is not None)
    j = enviar(c_editor, tok_pdf2, 'Contrato.pdf', b'%PDF-editora').json()
    t('a colega salvou no meio: vira cópia de conflito', j.get('conflito') is True
      and drive.conteudo['ARQ_PDF'] == b'%PDF-da-colega' and drive.enviados[-1][2] == b'%PDF-editora', j)
    tok_nova = c_editor.post(URL_PDF, {'modo': 'arquivo', 'anterior': tok_colega}).json()['token']
    s_nova = EdicaoLocal.objects.get(token_hash=hash_de(tok_nova))
    t('"anterior" de outra pessoa é ignorado (base = versão atual)',
      s_nova.md5_base == drive.itens['ARQ_PDF']['md5Checksum'] and not s_nova.conflito_file_id)
    travada = EdicaoLocal.objects.create(
        token_hash='z' * 64, user=colega, file_id='ARQ_PDF', file_name='Contrato.pdf', modo='office', pode_salvar=True,
        expira_em=timezone.now() + timedelta(hours=1), lock_token='opaquelocktoken:zz-colega',
        lock_expira_em=timezone.now() + timedelta(minutes=30))
    r = enviar(c_editor, tok_nova, 'Contrato.pdf', b'%PDF-com-trava')
    t('arquivo aberto no Office por outra pessoa: espera (423)',
      r.status_code == 423 and 'ZZ Colega' in r.json().get('error', '') and drive.conteudo['ARQ_PDF'] == b'%PDF-da-colega')
    travada.delete()
    DrivePermission.objects.filter(mapping=mapa, target_user=editor).delete()
    r = enviar(c_editor, tok_nova, 'Contrato.pdf', b'%PDF-sem-acesso')
    t('sem permissão: 403 e nada salvo', r.status_code == 403 and drive.conteudo['ARQ_PDF'] == b'%PDF-da-colega', r.status_code)
    p_editor = permitir(editor, 'EDIT')
    j = c_editor.get(f'/drive/edicao/{tok_pdf2}/status/').json()
    t('status da cópia: salvamentos e conflito', j.get('salvamentos') == 1 and j.get('conflito'), j)

    print('\n== MEU DRIVE (SUPERADMIN) ==')
    drive.add('MEU_XLSX', 'Pessoal.xlsx', XLSX, 'root', b'PK-pessoal')
    exige = lambda request: CfgMeuDrive if perms.is_superadmin(request.user) else None  # noqa: E731
    with mock.patch('drive.views._exige_meu_drive', side_effect=exige), \
            mock.patch.object(edicao_local, 'DriveConfig', CfgMeuDrive):
        html = c_chefe.get('/drive/meu-drive/a/MEU_XLSX/').content.decode()
        t('Meu Drive: cartão na tela do arquivo', 'id="edicao-local"' in html
          and 'data-url-iniciar="/drive/meu-drive/a/MEU_XLSX/editar-no-computador/"' in html)
        j = c_chefe.post('/drive/meu-drive/a/MEU_XLSX/editar-no-computador/').json()
        t('Meu Drive: abre no Excel', j.get('modo') == 'office'
          and j.get('uri_office', '').startswith('ms-excel:ofe|u|https://testserver/drive/dav/'), j)
        t('Meu Drive: conteúdo pelo endereço do Meu Drive', j.get('url_conteudo') == '/drive/meu-drive/a/MEU_XLSX/conteudo/?dl=1')
        tok_meu = j.get('token', '')
        avisos_antes = len(avisos_setor)
        r = dav('PUT', f'/drive/dav/{tok_meu}/Pessoal.xlsx', b'PK-pessoal-editado')
        t('Meu Drive: salva a versão', r.status_code == 204 and drive.conteudo['MEU_XLSX'] == b'PK-pessoal-editado', r.status_code)
        t('Meu Drive: não avisa setor nenhum', len(avisos_setor) == avisos_antes)
        t('Meu Drive: quem não é SUPERADMIN não abre',
          c_editor.post('/drive/meu-drive/a/MEU_XLSX/editar-no-computador/').status_code == 403)
        User.objects.filter(pk=chefe.pk).update(hierarchy='PADRAO')
        r = dav('PUT', f'/drive/dav/{tok_meu}/Pessoal.xlsx', b'PK-depois-de-sair')
        t('deixou de ser SUPERADMIN: não salva mais', r.status_code == 403 and drive.conteudo['MEU_XLSX'] == b'PK-pessoal-editado')

finally:
    pilha.close()
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    shutil.rmtree(temporarios, ignore_errors=True)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
