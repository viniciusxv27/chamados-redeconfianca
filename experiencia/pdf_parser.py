"""
Parser para extrair perguntas de PDFs de checklist do Programa Experiência Vivo.

Formato esperado do PDF (tabela):
ORDEM | PILAR | ITEM | GRAVIDADE | PONTUAÇÃO | PERGUNTA | DETALHAMENTO | CONTESTÁVEL?
"""
import re

import pdfplumber


GRAVIDADE_MAP = {
    'leve': 'leve',
    'média': 'media',
    'media': 'media',
    'grave': 'grave',
    'inegociável': 'inegociavel',
    'inegociavel': 'inegociavel',
    'inegociável -': 'inegociavel',
}


def _clean_text(text):
    """Remove quebras de linha extras e espaços duplicados."""
    if not text:
        return ''
    return re.sub(r'\s+', ' ', text.replace('\n', ' ')).strip()


def _parse_gravidade(raw):
    """Converte gravidade do PDF para o valor do model."""
    if not raw:
        return ''
    cleaned = raw.strip().lower().rstrip('.')
    # Handle "Inegociável -" prefix
    for key, val in GRAVIDADE_MAP.items():
        if cleaned.startswith(key):
            return val
    return ''


def _parse_contestavel(raw):
    """Converte Sim/Não para boolean."""
    if not raw:
        return True
    return raw.strip().lower().startswith('sim')


def parse_checklist_pdf(file_obj):
    """
    Extrai as perguntas de um PDF de checklist.
    
    Args:
        file_obj: File-like object (upload do Django ou path string)
        
    Returns:
        list of dicts com as perguntas extraídas:
        [
            {
                'ordem': int,
                'pilar': str,
                'item': str,
                'gravidade': str,  # leve, media, grave, inegociavel
                'pontuacao': int,
                'pergunta': str,
                'detalhamento': str,
                'contestavel': bool,
            },
            ...
        ]
    """
    questions = []

    with pdfplumber.open(file_obj) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    if not row or len(row) < 7:
                        continue

                    # Pula header e linhas sem ordem numérica
                    ordem_raw = (row[0] or '').strip()
                    if not ordem_raw or not ordem_raw.isdigit():
                        # Linhas de sub-info (ex: "Informar quantidade") - pular
                        continue

                    ordem = int(ordem_raw)
                    pilar = _clean_text(row[1])
                    item = _clean_text(row[2])
                    gravidade = _parse_gravidade(row[3])
                    
                    # Pontuação
                    pontuacao_raw = (row[4] or '').strip()
                    try:
                        pontuacao = int(pontuacao_raw)
                    except (ValueError, TypeError):
                        pontuacao = 0

                    pergunta = _clean_text(row[5])
                    detalhamento = _clean_text(row[6]) if len(row) > 6 else ''
                    contestavel = _parse_contestavel(row[7]) if len(row) > 7 else True

                    if pergunta:
                        questions.append({
                            'ordem': ordem,
                            'pilar': pilar,
                            'item': item,
                            'gravidade': gravidade,
                            'pontuacao': pontuacao,
                            'pergunta': pergunta,
                            'detalhamento': detalhamento,
                            'contestavel': contestavel,
                        })

    # Ordenar por ordem
    questions.sort(key=lambda q: q['ordem'])
    return questions
