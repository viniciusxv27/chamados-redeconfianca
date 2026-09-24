"""Usar localmente: uma cópia de trabalho da pasta (ou do arquivo) no computador.

Pedido: pelo menu do item, "Usar localmente" cria uma pasta temporária no
computador da pessoa (a sugestão é a Downloads), ela mexe nos arquivos por lá
com os programas que quiser e, ao terminar, volta no portal e escolhe
"Finalizar uso" — o que mudou sobe como nova versão, o que nasceu lá sobe como
arquivo novo, e a pasta temporária é apagada.

O que este módulo faz é o **manifesto**: diz o que vai para a cópia (arquivo a
arquivo, com o caminho dentro da pasta, o tamanho e a hora da última mudança) e
registra na auditoria o começo e o fim do uso. A escrita no computador é toda
do navegador (File System Access, Chrome e Edge) — ver
``static/js/rc-uso-local.js`` — e o que volta usa os mesmos endereços de sempre:
``file_replace`` para nova versão, ``upload`` para arquivo novo e ``mkdir`` para
pasta nova. Assim a permissão é conferida onde sempre foi.

Limites, porque isto é cópia inteira e não um arquivo só: 300 arquivos, 2 GB e
8 níveis de subpasta. Passou disso, a tela manda abrir uma subpasta específica
em vez de baixar o setor inteiro sem querer.

Arquivo nativo do Google (Documentos, Planilhas, Apresentações) fica de fora: não
tem bytes para copiar e editar — ele se edita no próprio Google. A tela conta
quais ficaram.
"""
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from . import audit, gdrive
from . import permissions as perms
from .permissions import ORDEM

logger = logging.getLogger('drive.uso_local')

LIMITE_ARQUIVOS = 300
LIMITE_BYTES = 2 * 1024 ** 3
LIMITE_PROFUNDIDADE = 8
PASTA_MIME = 'application/vnd.google-apps.folder'
CAMPOS = 'id,name,mimeType,size,modifiedTime,md5Checksum'


def e_pasta(meta):
    return (meta.get('mimeType') or '') == PASTA_MIME


def e_nativo_do_google(meta):
    """Documentos/Planilhas/Apresentações: não têm bytes para copiar."""
    mime = meta.get('mimeType') or ''
    return mime.startswith('application/vnd.google-apps') and mime != PASTA_MIME


def _arquivo(meta, caminho, pasta_id):
    return {
        'id': meta.get('id'),
        'nome': meta.get('name') or '',
        'caminho': caminho,
        'pasta_id': pasta_id,
        'tamanho': int(meta.get('size') or 0),
        'modificado': meta.get('modifiedTime') or '',
        'md5': meta.get('md5Checksum') or '',
        'url': reverse('drive:file_content', args=[meta.get('id')]) + '?dl=1',
    }


def _varrer(folder_id, prefixo, profundidade, estado):
    """Desce a pasta juntando arquivos, pastas e o que ficou de fora."""
    pagina = None
    while True:
        itens, pagina = gdrive.listar(folder_id, page_token=pagina, page_size=100)
        for meta in itens:
            if e_pasta(meta):
                caminho = f"{prefixo}{meta.get('name')}"
                if profundidade >= LIMITE_PROFUNDIDADE:
                    estado['fundo_demais'].append(caminho)
                    continue
                estado['pastas'].append({'id': meta.get('id'), 'caminho': caminho})
                _varrer(meta.get('id'), f'{caminho}/', profundidade + 1, estado)
                continue
            if e_nativo_do_google(meta):
                estado['ignorados'].append(meta.get('name') or '')
                continue
            if len(estado['arquivos']) >= LIMITE_ARQUIVOS or estado['bytes'] > LIMITE_BYTES:
                estado['excedeu'] = True
                return
            arquivo = _arquivo(meta, f"{prefixo}{meta.get('name')}", folder_id)
            estado['bytes'] += arquivo['tamanho']
            estado['arquivos'].append(arquivo)
        if not pagina:
            return


def montar(file_id):
    """O que vai para a cópia de trabalho de um arquivo ou de uma pasta."""
    meta = gdrive.obter(file_id, fields=CAMPOS)
    estado = {'arquivos': [], 'pastas': [], 'ignorados': [], 'fundo_demais': [],
              'bytes': 0, 'excedeu': False}

    if e_pasta(meta):
        _varrer(file_id, '', 1, estado)
    elif e_nativo_do_google(meta):
        estado['ignorados'].append(meta.get('name') or '')
    else:
        arquivo = _arquivo(meta, meta.get('name') or '', gdrive.pai_de(file_id) or '')
        estado['bytes'] = arquivo['tamanho']
        estado['arquivos'].append(arquivo)

    return {
        'id': file_id,
        'nome': meta.get('name') or 'Drive',
        'pasta': e_pasta(meta),
        'pasta_id': file_id if e_pasta(meta) else (estado['arquivos'][0]['pasta_id']
                                                  if estado['arquivos'] else ''),
        'arquivos': estado['arquivos'],
        'pastas': estado['pastas'],
        'ignorados': estado['ignorados'],
        'fundo_demais': estado['fundo_demais'],
        'bytes': estado['bytes'],
        'excedeu': estado['excedeu'],
        'limites': {'arquivos': LIMITE_ARQUIVOS, 'bytes': LIMITE_BYTES,
                    'profundidade': LIMITE_PROFUNDIDADE},
    }


@login_required
@require_GET
def manifesto(request, file_id):
    """O que a página precisa para montar a cópia no computador."""
    mapping, nivel = perms.file_allowed(request.user, file_id)
    if not mapping or nivel < ORDEM['DOWNLOAD']:
        audit.registrar(request.user, 'DENY', request=request, file_id=file_id,
                        detalhe='uso local')
        return JsonResponse({'erro': 'Você não pode baixar este item.'}, status=403)

    try:
        dados = montar(file_id)
    except gdrive.DriveError as exc:
        return JsonResponse({'erro': f'Google Drive: {exc}'}, status=502)

    dados['sector_id'] = mapping.sector_id
    dados['pode_enviar'] = nivel >= ORDEM['EDIT']
    dados['pode_criar'] = nivel >= ORDEM['UPLOAD']
    if not dados['arquivos'] and not dados['excedeu']:
        dados['aviso'] = ('Não há arquivo para copiar aqui.'
                          if not dados['ignorados']
                          else 'Só há arquivos do Google aqui — eles se editam no próprio Google.')
    audit.registrar(request.user, 'USO_LOCAL', request=request, file_id=file_id,
                    file_name=dados['nome'], sector=mapping.sector,
                    detalhe=f"início · {len(dados['arquivos'])} arquivo(s)")
    return JsonResponse(dados)


@login_required
@require_POST
def finalizar(request, file_id):
    """Fecha o uso local: guarda na auditoria o que a página conseguiu enviar."""
    mapping, nivel = perms.file_allowed(request.user, file_id)
    if not mapping or nivel < ORDEM['VIEW']:
        return JsonResponse({'erro': 'Item indisponível.'}, status=403)

    def numero(campo):
        try:
            return max(0, int(request.POST.get(campo) or 0))
        except (TypeError, ValueError):
            return 0

    resumo = (f"fim · {numero('versoes')} nova(s) versão(ões), {numero('novos')} novo(s), "
              f"{numero('apagados')} apagado(s) na cópia, {numero('falhas')} falha(s)")
    audit.registrar(request.user, 'USO_LOCAL', request=request, file_id=file_id,
                    file_name=(request.POST.get('nome') or '')[:255], sector=mapping.sector,
                    detalhe=resumo)
    return JsonResponse({'ok': True, 'resumo': resumo})
