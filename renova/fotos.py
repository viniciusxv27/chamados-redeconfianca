"""Fotos do aparelho: leitura do envio, conferência e gravação no armazenamento.

A tela já reduz a foto no navegador antes de enviar, mas o servidor não confia
nisso: abre cada arquivo com o Pillow (o tipo declarado pelo navegador é só uma
declaração), gira conforme o EXIF do celular, limita o tamanho e grava sempre
em JPEG — sem os metadados da câmera (localização, aparelho).
"""
import io
import logging

from django.core.files.base import ContentFile
from PIL import Image, ImageOps

from . import checklist

logger = logging.getLogger(__name__)

FOTO_MAX_BYTES = 15 * 1024 * 1024       # o que chega do navegador, antes de reduzir
FOTO_LADO_MAX = 1920                    # px do maior lado gravado
FOTO_QUALIDADE = 85


class FotoInvalida(ValueError):
    pass


def normalizar(arquivo):
    """Arquivo enviado → bytes JPEG prontos para gravar. FotoInvalida se não for foto."""
    if arquivo.size > FOTO_MAX_BYTES:
        raise FotoInvalida('passa de 15 MB')
    try:
        arquivo.seek(0)
        with Image.open(arquivo) as bruta:
            if bruta.format not in ('JPEG', 'PNG', 'WEBP', 'MPO', 'HEIF', 'HEIC'):
                raise FotoInvalida('não é JPG, PNG ou WEBP')
            imagem = ImageOps.exif_transpose(bruta)
            imagem.thumbnail((FOTO_LADO_MAX, FOTO_LADO_MAX))
            if imagem.mode not in ('RGB', 'L'):
                fundo = Image.new('RGB', imagem.size, (255, 255, 255))
                com_alfa = imagem.convert('RGBA')
                fundo.paste(com_alfa, mask=com_alfa.split()[-1])
                imagem = fundo
            saida = io.BytesIO()
            imagem.convert('RGB').save(saida, format='JPEG', quality=FOTO_QUALIDADE, optimize=True)
    except FotoInvalida:
        raise
    except Exception as exc:                                    # noqa: BLE001 — qualquer falha: não é foto
        raise FotoInvalida('não abriu como imagem') from exc
    finally:
        try:
            arquivo.seek(0)
        except Exception:                                       # noqa: BLE001
            pass
    return saida.getvalue()


def ler_fotos(arquivos):
    """(fotos, erros) do envio.

    ``arquivos`` é o request.FILES. ``fotos`` é uma lista [(tipo, ordem, bytes)]
    na ordem do checklist; ``erros`` tem a chave 'fotos' com o que faltou ou foi
    recusado nas fotos do aparelho (a tela mostra tudo junto, na etapa das fotos) e
    a chave 'foto_consulta' com o print da consulta do IMEI, que fica na etapa 1.
    """
    fotos, problemas, faltando = [], [], []
    for chave, titulo, _, _, obrigatoria in checklist.FOTOS:
        arquivo = arquivos.get(f'foto_{chave}')
        if not arquivo:
            if obrigatoria:
                faltando.append(titulo)
            continue
        try:
            fotos.append((chave, 0, normalizar(arquivo)))
        except FotoInvalida as exc:
            problemas.append(f'{titulo}: {exc}')
    avarias = arquivos.getlist(f'foto_{checklist.FOTO_AVARIA}') if hasattr(arquivos, 'getlist') else []
    if len(avarias) > checklist.FOTOS_AVARIA_MAX:
        problemas.append(f'Mande no máximo {checklist.FOTOS_AVARIA_MAX} fotos de avarias.')
        avarias = avarias[:checklist.FOTOS_AVARIA_MAX]
    for ordem, arquivo in enumerate(avarias, start=1):
        try:
            fotos.append((checklist.FOTO_AVARIA, ordem, normalizar(arquivo)))
        except FotoInvalida as exc:
            problemas.append(f'{checklist.FOTO_AVARIA_TITULO} {ordem}: {exc}')
    erros = {}
    chave, titulo, _, _, _ = checklist.FOTO_CONSULTA
    print_da_consulta = arquivos.get(f'foto_{chave}')
    if not print_da_consulta:
        erros['foto_consulta'] = ('Anexe o print da consulta do IMEI mostrando que o aparelho não tem restrição — '
                                  'sem ele a avaliação não segue.')
    else:
        try:
            fotos.append((chave, 0, normalizar(print_da_consulta)))
        except FotoInvalida as exc:
            erros['foto_consulta'] = f'{titulo}: {exc}. Anexe o print de novo.'

    mensagens = []
    if faltando:
        mensagens.append('Tire as fotos obrigatórias: ' + ', '.join(faltando) + '.')
    if problemas:
        mensagens.append('Foto recusada — ' + '; '.join(problemas) + '.')
    if mensagens:
        erros['fotos'] = ' '.join(mensagens)
    return fotos, erros


def gravar_fotos(renova, fotos):
    """Cria as FotoRenova. Se uma falhar, apaga do armazenamento as que já subiram e repassa o erro."""
    from .models import FotoRenova

    criadas = []
    try:
        for tipo, ordem, conteudo in fotos:
            foto = FotoRenova(renova=renova, tipo=tipo, ordem=ordem)
            foto.arquivo.save(f'{tipo}.jpg', ContentFile(conteudo), save=False)
            criadas.append(foto)
            foto.save()
    except Exception:
        apagar_arquivos(criadas)
        raise
    return criadas


def apagar_arquivos(fotos):
    """Tira os arquivos do armazenamento (excluir o registro não apaga o arquivo no S3)."""
    for foto in fotos:
        try:
            if foto.arquivo:
                foto.arquivo.delete(save=False)
        except Exception as exc:                                # noqa: BLE001 — o registro sai mesmo assim
            logger.warning('Foto %s do Renova não saiu do armazenamento: %s', foto.pk, exc)
