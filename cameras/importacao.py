"""Importa a planilha de câmeras (Loja, Coordenados, app, Login, Serial, Senha). A senha entra cifrada."""
import unicodedata

import openpyxl

from users.models import Sector

from .models import Canal, Gravador

CANAIS_INICIAIS = 4


def _chave(texto):
    limpo = unicodedata.normalize('NFKD', str(texto or '')).encode('ascii', 'ignore').decode()
    return ' '.join(limpo.upper().split())


def _texto(valor):
    return ' '.join(str(valor).split()) if valor is not None else ''


def _vazio(valor):
    return _texto(valor) in ('', '0')


def _setor_da_loja(loja, setores):
    chave = _chave(loja)
    iguais = [s for s in setores if _chave(s.name) == chave]
    if iguais:
        return iguais[0]
    parecidos = [s for s in setores if chave in _chave(s.name)]
    return parecidos[0] if len(parecidos) == 1 else None


def importar_planilha(arquivo):
    """Atualiza pela loja. Devolve {'criados', 'atualizados', 'sem_equipamento', 'sem_setor'}; nunca devolve senha."""
    linhas = list(openpyxl.load_workbook(arquivo, data_only=True, read_only=True).active.iter_rows(values_only=True))
    if not linhas:
        raise ValueError('A planilha está vazia.')
    cabecalho = {_chave(v): i for i, v in enumerate(linhas[0]) if v}
    faltando = [c for c in ('LOJA', 'LOGIN', 'SERIAL', 'SENHA') if c not in cabecalho]
    if faltando:
        raise ValueError('Faltam as colunas: ' + ', '.join(faltando).title() + '.')

    def campo(linha, nome):
        i = cabecalho.get(nome)
        return linha[i] if i is not None and i < len(linha) else None

    setores = list(Sector.objects.all())
    resultado = {'criados': 0, 'atualizados': 0, 'sem_equipamento': [], 'sem_setor': []}
    for linha in linhas[1:]:
        loja = _texto(campo(linha, 'LOJA'))
        if not loja:
            continue
        gravador = Gravador.objects.filter(loja__iexact=loja).first()
        criado = gravador is None
        gravador = gravador or Gravador(loja=loja)
        gravador.coordenador = _texto(campo(linha, 'COORDENADOS'))
        tem = not _vazio(campo(linha, 'SERIAL'))
        gravador.tem_equipamento = tem
        gravador.aplicativo = '' if _vazio(campo(linha, 'APP')) else _texto(campo(linha, 'APP'))
        gravador.serial = _texto(campo(linha, 'SERIAL')) if tem else ''
        gravador.usuario = _texto(campo(linha, 'LOGIN')) if tem else ''
        senha = campo(linha, 'SENHA')
        gravador.definir_senha(str(senha).strip() if tem and not _vazio(senha) else '')
        gravador.setor = gravador.setor or _setor_da_loja(loja, setores)
        gravador.save()
        resultado['criados' if criado else 'atualizados'] += 1
        if not tem:
            resultado['sem_equipamento'].append(loja)
        else:
            for numero in range(1, CANAIS_INICIAIS + 1):
                Canal.objects.get_or_create(gravador=gravador, numero=numero)
        if not gravador.setor:
            resultado['sem_setor'].append(loja)
    return resultado
