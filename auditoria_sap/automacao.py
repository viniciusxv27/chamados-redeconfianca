"""Versões da Automação SAP: conferir o .zip que o SUPERADMIN sobe e entregar ao app da loja.

O zip é a pasta "AutomacaoSAP" inteira, do jeito que já se distribui hoje
(AutomacaoSAP.exe + _internal + config + logs). O app troca só o .exe e o
_internal: config e logs são da loja e nunca vêm do pacote.
"""
import hashlib
import re
import zipfile

from django.conf import settings

NOME_DO_EXE = 'automacaosap.exe'
# Entradas que o atualizador do app NÃO copia por cima (são da loja).
PASTAS_DA_LOJA = ('config', 'logs')
TAMANHO_MAXIMO = 300 * 1024 * 1024


class PacoteInvalido(ValueError):
    pass


def versao_do_nome(nome):
    """'AutomacaoSAP_v1.2.1.zip' → '1.2.1'."""
    achado = re.search(r'v?(\d+(?:\.\d+){1,3})', nome or '', re.I)
    return achado.group(1) if achado else ''


def _partes(nome):
    """Caminho da entrada do zip em partes. O zip de hoje vem com barra invertida (feito no Windows)."""
    return [p for p in nome.replace('\\', '/').split('/') if p not in ('', '.')]


def conferir_zip(arquivo):
    """Confere o pacote e devolve (sha256, tamanho). Levanta PacoteInvalido com o motivo em português."""
    arquivo.seek(0, 2)
    tamanho = arquivo.tell()
    arquivo.seek(0)
    if not tamanho:
        raise PacoteInvalido('O arquivo está vazio.')
    if tamanho > TAMANHO_MAXIMO:
        raise PacoteInvalido('O arquivo passa de 300 MB.')
    if not zipfile.is_zipfile(arquivo):
        raise PacoteInvalido('O arquivo não é um .zip.')
    arquivo.seek(0)
    with zipfile.ZipFile(arquivo) as zf:
        nomes = zf.namelist()
        tem_exe = tem_internal = False
        for nome in nomes:
            partes = _partes(nome)
            if nome.startswith(('/', '\\')) or re.match(r'^[A-Za-z]:', nome) or '..' in partes:
                raise PacoteInvalido(f'Caminho perigoso dentro do zip: {nome[:80]}')
            baixo = [p.lower() for p in partes]
            if baixo and baixo[-1] == NOME_DO_EXE and len(baixo) <= 2:
                tem_exe = True
            if '_internal' in baixo[:2]:
                tem_internal = True
        if not tem_exe:
            raise PacoteInvalido('Não achei o AutomacaoSAP.exe no zip (na raiz ou dentro da pasta AutomacaoSAP).')
        if not tem_internal:
            raise PacoteInvalido('Não achei a pasta _internal no zip: o .exe não funciona sem ela.')
        ruim = zf.testzip()
        if ruim:
            raise PacoteInvalido(f'O zip está corrompido ({ruim[:80]}).')
    arquivo.seek(0)
    resumo = hashlib.sha256()
    for bloco in iter(lambda: arquivo.read(1024 * 1024), b''):
        resumo.update(bloco)
    arquivo.seek(0)
    return resumo.hexdigest(), tamanho


def em_disco():
    return settings.STORAGES['default']['BACKEND'].endswith('FileSystemStorage')


def link_temporario(versao):
    """Link de 1 h no MinIO, já com o nome do arquivo (o bucket assina em SigV4)."""
    from cameras.servicos import link_do_instalador
    return link_do_instalador(versao.arquivo.name, f'AutomacaoSAP_v{versao.versao}.zip')


def chave_confere(request, chave_certa):
    import hmac
    enviada = request.headers.get('X-Chave-Automacao') or request.GET.get('chave') or ''
    return bool(chave_certa) and hmac.compare_digest(enviada.strip(), chave_certa)


# ── Instalador da Ponte ───────────────────────────────────────────────────────
# A imagem do portal já sai com o instalador (o Dockerfile roda ponte_sap/construir_instalador.py).
# O SUPERADMIN ainda pode enviar um pela tela: vale o de versão maior (empate: o da imagem).
def ponte_embutida():
    """(versão, caminho) do instalador gerado no build da imagem, ou None."""
    import re
    from pathlib import Path
    base = Path(settings.BASE_DIR) / 'ponte_sap'
    exe = base / 'dist' / 'PonteAutomacaoSAP.exe'
    if not exe.is_file():
        return None
    try:
        achado = re.search(r"^VERSAO = '([\d.]+)'", (base / 'ponte.py').read_text(encoding='utf-8'), re.M)
    except OSError:
        achado = None
    return (achado.group(1) if achado else '0.0.0'), exe


def ponte_oferecida():
    """O instalador que a página oferece: {'versao', 'origem': 'imagem'|'enviada', 'item'|'caminho'} ou None."""
    from .models import InstaladorPonteSAP, versao_em_tupla
    enviada = InstaladorPonteSAP.atual()
    embutida = ponte_embutida()
    if embutida and (enviada is None or versao_em_tupla(embutida[0]) >= versao_em_tupla(enviada.versao)):
        return {'versao': embutida[0], 'origem': 'imagem', 'caminho': embutida[1]}
    if enviada is not None:
        return {'versao': enviada.versao, 'origem': 'enviada', 'item': enviada}
    return None
