"""Leitura da exportação PostgreSQL do Confiança Acess original (sistema anterior ao portal).

O arquivo traz um ``INSERT`` por linha, com os textos em hexadecimal
(``convert_from(decode('…','hex'),'UTF8')``) e as tabelas de entidade no formato
``row_no, payload(JSON), entity_id, parent_id, status, due_at``. Aqui o arquivo
é lido sem banco nenhum: vira ``{tabela: [linha, ...]}`` com o ``payload`` já em
dicionário.
"""
import json
import re

_INSERT = re.compile(r'^INSERT INTO "(?P<tabela>[a-z_]+)" \((?P<colunas>[^)]*)\) VALUES \((?P<valores>.*)\);\s*$')
_HEX = re.compile(r"convert_from\(decode\('([0-9a-fA-F]*)','hex'\),'UTF8'\)")


def _valores(texto):
    """Separa os valores do VALUES: textos em hex, números, NULL e 'literais'."""
    saida, i, n = [], 0, len(texto)
    while i < n:
        if texto[i] in ', ':
            i += 1
            continue
        m = _HEX.match(texto, i)
        if m:
            saida.append(bytes.fromhex(m.group(1)).decode('utf-8'))
            i = m.end()
            continue
        if texto[i] == "'":
            j = i + 1
            partes = []
            while j < n:
                if texto[j] == "'" and j + 1 < n and texto[j + 1] == "'":
                    partes.append("'")
                    j += 2
                elif texto[j] == "'":
                    break
                else:
                    partes.append(texto[j])
                    j += 1
            saida.append(''.join(partes))
            i = j + 1
            continue
        j = texto.find(',', i)
        j = n if j == -1 else j
        bruto = texto[i:j].strip()
        if bruto.upper() == 'NULL':
            saida.append(None)
        else:
            try:
                saida.append(int(bruto))
            except ValueError:
                try:
                    saida.append(float(bruto))
                except ValueError:
                    saida.append(bruto)
        i = j
    return saida


def ler(caminho):
    tabelas = {}
    with open(caminho, encoding='utf-8') as arq:
        for linha in arq:
            m = _INSERT.match(linha)
            if not m:
                continue
            colunas = [c.strip().strip('"') for c in m.group('colunas').split(',')]
            valores = _valores(m.group('valores'))
            if len(valores) != len(colunas):
                raise ValueError(f'Linha mal formada em {m.group("tabela")}: {len(valores)} valores para {len(colunas)} colunas')
            registro = dict(zip(colunas, valores))
            if 'payload' in registro and isinstance(registro['payload'], str):
                try:
                    registro['payload'] = json.loads(registro['payload'])
                except ValueError:
                    pass
            tabelas.setdefault(m.group('tabela'), []).append(registro)
    return tabelas
