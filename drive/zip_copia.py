"""Baixar uma cópia zipada: a pasta inteira (ou um arquivo) num .zip só.

Pedido: "criar uma função dentro do drive de criar uma cópia dos arquivos
zipados". É o caminho para levar uma pasta inteira de uma vez — mandar por
e-mail, guardar fora, abrir num computador sem portal — sem baixar arquivo por
arquivo.

O manifesto é o mesmo do "usar localmente" (``uso_local.montar``): ele já sabe
descer a pasta, montar o caminho de cada arquivo e deixar de fora o que é
nativo do Google (Documentos/Planilhas não têm bytes para copiar).

O zip é montado num arquivo temporário e devolvido como download. O limite é
menor que o do "usar localmente" de propósito: aqui o servidor é quem baixa
tudo do Google e comprime, e são só três workers — uma pasta de 2 GB seguraria
um deles por vários minutos, e o portal inteiro sentiria. Passou do limite, a
tela manda abrir uma subpasta.
"""
import logging
import tempfile
import zipfile

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, JsonResponse
from django.views.decorators.http import require_GET

from . import audit, gdrive, uso_local
from . import permissions as perms
from .permissions import ORDEM

logger = logging.getLogger('drive.zip')

# Cabe uma pasta de trabalho inteira, não o setor inteiro.
LIMITE_ARQUIVOS = 300
LIMITE_BYTES = 500 * 1024 ** 2


class ZipGrandeDemais(Exception):
    """A pasta passa do que dá para zipar numa requisição."""


def _nome_do_arquivo(nome):
    """Nome do .zip sem os caracteres que atrapalham no Windows/macOS."""
    limpo = ''.join(c for c in (nome or 'drive') if c not in '\\/:*?"<>|').strip()
    return (limpo or 'drive')[:80] + '.zip'


def montar_zip(file_id):
    """(arquivo temporário do zip, nome sugerido, resumo). Fecha por conta de quem chama."""
    manifesto = uso_local.montar(file_id)
    arquivos = manifesto['arquivos']

    if manifesto['excedeu'] or len(arquivos) > LIMITE_ARQUIVOS or manifesto['bytes'] > LIMITE_BYTES:
        raise ZipGrandeDemais(
            f'Esta pasta passa do limite do ZIP ({LIMITE_ARQUIVOS} arquivos ou '
            f'{LIMITE_BYTES // (1024 ** 2)} MB). Abra uma subpasta e baixe em partes.')
    if not arquivos:
        vazio = ('Só há arquivos do Google (Documentos, Planilhas) aqui — eles não têm '
                 'bytes para zipar.' if manifesto['ignorados'] else 'Não há arquivos para zipar aqui.')
        raise ZipGrandeDemais(vazio)

    temporario = tempfile.NamedTemporaryFile(suffix='.zip')
    dentro = set()
    com_erro = []
    # compresslevel=1: foto e PDF quase não comprimem; o que interessa aqui é
    # juntar tudo num arquivo só, sem fazer o servidor suar.
    with zipfile.ZipFile(temporario, 'w', zipfile.ZIP_DEFLATED, allowZip64=True,
                         compresslevel=1) as pacote:
        for arquivo in arquivos:
            caminho = arquivo['caminho'] or arquivo['nome']
            # Dois arquivos com o mesmo nome na mesma pasta: o segundo ganha sufixo.
            base, n = caminho, 2
            while caminho in dentro:
                pedaco = base.rsplit('.', 1)
                caminho = f'{pedaco[0]} ({n}).{pedaco[1]}' if len(pedaco) == 2 else f'{base} ({n})'
                n += 1
            dentro.add(caminho)
            try:
                conteudo, _nome, _mime = gdrive.baixar(arquivo['id'])
            except gdrive.DriveError as exc:
                com_erro.append(arquivo['nome'])
                logger.warning('ZIP: %s não entrou (%s)', arquivo['nome'], exc)
                continue
            with pacote.open(caminho, 'w') as destino:
                for pedaco_bytes in iter(lambda: conteudo.read(1024 * 256), b''):
                    destino.write(pedaco_bytes)
        if com_erro:
            pacote.writestr(
                'ARQUIVOS-QUE-NAO-ENTRARAM.txt',
                'O Google não devolveu estes arquivos na hora de zipar:\n\n'
                + '\n'.join(f'- {nome}' for nome in com_erro)
                + '\n\nTente baixá-los direto pelo portal.\n')

    temporario.flush()
    temporario.seek(0)
    resumo = {
        'arquivos': len(arquivos) - len(com_erro),
        'bytes': manifesto['bytes'],
        'ignorados': manifesto['ignorados'],
        'com_erro': com_erro,
        'nome': manifesto['nome'],
    }
    return temporario, _nome_do_arquivo(manifesto['nome']), resumo


@login_required
@require_GET
def baixar_zip(request, file_id):
    """Devolve a pasta (ou o arquivo) zipada, se a pessoa pode baixar dali."""
    raiz, nivel = perms.file_allowed(request.user, file_id)
    if not raiz or nivel < ORDEM['DOWNLOAD']:
        from .views import _deny
        _deny(request, file_id=file_id, detalhe='zip')

    try:
        temporario, nome, resumo = montar_zip(file_id)
    except ZipGrandeDemais as exc:
        return JsonResponse({'ok': False, 'msg': str(exc)}, status=400)
    except gdrive.DriveNaoConfigurado as exc:
        return JsonResponse({'ok': False, 'msg': str(exc)}, status=400)
    except gdrive.DriveError as exc:
        return JsonResponse({'ok': False, 'msg': f'Google Drive: {exc}'}, status=400)

    detalhe = f"zip · {resumo['arquivos']} arquivo(s)"
    if resumo['ignorados']:
        detalhe += f" · {len(resumo['ignorados'])} do Google fora"
    if resumo['com_erro']:
        detalhe += f" · {len(resumo['com_erro'])} sem resposta"
    audit.registrar(request.user, 'DOWNLOAD', request=request, file_id=file_id,
                    file_name=resumo['nome'], sector=raiz.sector, detalhe=detalhe)

    resposta = FileResponse(temporario, as_attachment=True, filename=nome,
                            content_type='application/zip')
    resposta['Cache-Control'] = 'no-store'
    return resposta
