"""Ferramentas do assistente para o portal inteiro e para documentos.

- ``mapa_do_portal`` e ``ler_pagina``: qualquer área do portal, lida como a própria
  pessoa a veria (``assistente/portal.py``). Só leitura.
- ``criar_documento``, ``revisar_documento`` e ``meus_documentos_assistente``: relatórios,
  análises, atas, planos e comunicados em Word, Excel, Markdown e PDF
  (``assistente/documentos.py``). Ficam só com a pessoa — não avisam ninguém, por isso
  não pedem confirmação.
"""
import json

from django.urls import reverse

from . import portal
from .documentos import limpar_tabelas
from .models import AssistenteDocumento

MAX_DOCUMENTO = 200_000


def _json(dados):
    return json.dumps(dados, ensure_ascii=False, default=str)


def _mapa(user, args):
    return _json({
        'areas': portal.mapa(),
        'como_usar': ('Abra a área com ler_pagina(caminho). Links dentro da página (ex.: [Detalhe](/tickets/12/)) '
                      'também podem ser abertos. Filtros vão na query string (ex.: /vendas/?de=2026-10-01). '
                      'O acesso é o da própria pessoa: se não tiver, a página avisa.'),
    })


def _ler(user, args):
    return _json(portal.ler_pagina(user, args.get('caminho'), args.get('parte') or 1))


def _links(doc):
    base = reverse('assistente:documento', args=[doc.pk])
    return {
        'pagina': base,
        'word': reverse('assistente:documento_baixar', args=[doc.pk, 'docx']),
        'excel': reverse('assistente:documento_baixar', args=[doc.pk, 'xlsx']),
        'markdown': reverse('assistente:documento_baixar', args=[doc.pk, 'md']),
        'pdf': base + '?imprimir=1',
    }


def _validar(args, parcial=False):
    titulo = ' '.join(str(args.get('titulo') or '').split())[:200]
    conteudo = str(args.get('conteudo') or '')
    if not parcial and not titulo:
        raise ValueError('Informe o título do documento.')
    if not parcial and not (conteudo.strip() or args.get('tabelas')):
        raise ValueError('O documento está vazio: escreva o conteúdo (Markdown) ou mande tabelas.')
    if len(conteudo) > MAX_DOCUMENTO:
        raise ValueError('O documento ficou grande demais — divida em mais de um.')
    return titulo, conteudo


def _criar(user, args):
    try:
        titulo, conteudo = _validar(args)
    except ValueError as exc:
        return str(exc)
    doc = AssistenteDocumento.objects.create(
        user=user, titulo=titulo, conteudo=conteudo, tabelas=limpar_tabelas(args.get('tabelas')))
    return _json({'ok': True, 'id': doc.pk, 'titulo': doc.titulo, 'links': _links(doc),
                  'orientacao': ('Entregue ao usuário os links (página, Word, Excel, PDF). Não repita o '
                                 'documento inteiro na resposta: um resumo de 2 a 4 linhas basta.')})


def _revisar(user, args):
    doc = AssistenteDocumento.objects.filter(user=user, pk=args.get('id') or 0).first()
    if not doc:
        return 'Documento não encontrado entre os seus.'
    try:
        titulo, conteudo = _validar(args, parcial=True)
    except ValueError as exc:
        return str(exc)
    if titulo:
        doc.titulo = titulo
    if 'conteudo' in args and conteudo.strip():
        doc.conteudo = conteudo
    if args.get('tabelas') is not None:
        doc.tabelas = limpar_tabelas(args.get('tabelas'))
    doc.versao += 1
    doc.save()
    return _json({'ok': True, 'id': doc.pk, 'versao': doc.versao, 'links': _links(doc)})


def _meus(user, args):
    docs = AssistenteDocumento.objects.filter(user=user)[:30]
    return _json([{'id': d.pk, 'titulo': d.titulo, 'versao': d.versao,
                   'atualizado_em': d.atualizado_em.strftime('%d/%m/%Y %H:%M'), 'links': _links(d),
                   'inicio': (d.conteudo or '')[:300]} for d in docs])


def _abrir(user, args):
    doc = AssistenteDocumento.objects.filter(user=user, pk=args.get('id') or 0).first()
    if not doc:
        return 'Documento não encontrado entre os seus.'
    return _json({'id': doc.pk, 'titulo': doc.titulo, 'versao': doc.versao, 'conteudo': doc.conteudo,
                  'tabelas': doc.tabelas})


_TABELAS = {
    'type': 'array',
    'description': ('Tabelas de dados (viram abas no Excel e tabelas no Word/PDF). Use para números que a '
                    'pessoa vai querer filtrar ou somar. Valores numéricos como número.'),
    'items': {'type': 'object', 'properties': {
        'titulo': {'type': 'string'},
        'colunas': {'type': 'array', 'items': {'type': 'string'}},
        'linhas': {'type': 'array', 'items': {'type': 'array', 'items': {}}},
    }, 'required': ['colunas', 'linhas']},
}

TOOLS_PORTAL = {
    'mapa_do_portal': {
        'fn': _mapa,
        'description': ('Lista as áreas do portal (caminho e o que cada uma tem). Use para descobrir onde '
                        'buscar algo que não tenha ferramenta própria.'),
        'input_schema': {'type': 'object', 'properties': {}, 'required': []},
    },
    'ler_pagina': {
        'fn': _ler,
        'description': (
            'Abre QUALQUER página do portal como o próprio usuário a veria (mesmas permissões) e devolve o '
            'conteúdo em texto: tabelas viram linhas com |, links ficam como [texto](/caminho). Serve para '
            'todas as áreas sem ferramenta própria (vendas, comissionamento, SAP, caixa, cartões, compras, '
            'inventário, chamados, checklists, limpeza, contestação, fibras, Power BI, cursos, quiz, etc.) e '
            'para os detalhes (abra os links que aparecem). Aceita filtros na query string. Só leitura: '
            'endereços de ação ou de download são recusados. Página longa vem em partes (parâmetro parte).'),
        'input_schema': {'type': 'object', 'properties': {
            'caminho': {'type': 'string', 'description': 'Caminho da página, ex.: /vendas/, /tickets/45/, /sap/painel/?loja=GLORIA'},
            'parte': {'type': 'integer', 'description': 'Parte da página (1, 2, ...) quando ela vier dividida.'}},
            'required': ['caminho']},
    },
    'criar_documento': {
        'fn': _criar,
        'description': (
            'Cria um documento para o usuário — relatório, análise detalhada, ata, plano de ação, comunicado, '
            'checklist, proposta — e devolve os links para abrir, baixar em Word (.docx), Excel (.xlsx), '
            'Markdown e imprimir em PDF. Escreva o conteúdo completo em Markdown (títulos #, listas, '
            '**negrito**, tabelas com |). Fica só com o usuário (não envia nada a ninguém).'),
        'input_schema': {'type': 'object', 'properties': {
            'titulo': {'type': 'string', 'description': 'Título do documento.'},
            'conteudo': {'type': 'string', 'description': 'O documento inteiro em Markdown.'},
            'tabelas': _TABELAS},
            'required': ['titulo', 'conteudo']},
    },
    'revisar_documento': {
        'fn': _revisar,
        'description': ('Atualiza um documento já criado (título, conteúdo inteiro e/ou tabelas) — para '
                        'ajustes pedidos pelo usuário. Mande o conteúdo COMPLETO novo, não só o trecho.'),
        'input_schema': {'type': 'object', 'properties': {
            'id': {'type': 'integer'}, 'titulo': {'type': 'string'}, 'conteudo': {'type': 'string'},
            'tabelas': _TABELAS}, 'required': ['id']},
    },
    'meus_documentos_assistente': {
        'fn': _meus,
        'description': 'Os documentos que o assistente já criou para o usuário (id, título, links).',
        'input_schema': {'type': 'object', 'properties': {}, 'required': []},
    },
    'abrir_documento': {
        'fn': _abrir,
        'description': 'O conteúdo completo de um documento do usuário (para revisar ou continuar).',
        'input_schema': {'type': 'object', 'properties': {'id': {'type': 'integer'}}, 'required': ['id']},
    },
}
