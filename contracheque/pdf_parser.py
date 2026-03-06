import re
import io
import unicodedata
from decimal import Decimal, InvalidOperation

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    import pypdfium2 as pdfium
except ImportError:
    pdfium = None


def parse_currency(value_str):
    """Converte string monetária BR para Decimal. Ex: '1.234,56' -> Decimal('1234.56')"""
    if not value_str:
        return Decimal('0')
    cleaned = value_str.strip().replace('.', '').replace(',', '.')
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return Decimal('0')


def normalize_name(name):
    """Remove acentos, converte para maiúsculo, remove espaços extras."""
    if not name:
        return ''
    nfkd = unicodedata.normalize('NFKD', name)
    ascii_text = ''.join(c for c in nfkd if not unicodedata.combining(c))
    return ' '.join(ascii_text.upper().split())


def _default_payslip_data():
    """Retorna dict padrão com todos os campos de um contracheque."""
    return {
        'employee_name': '',
        'cpf': '',
        'job_title': '',
        'admission_date': '',
        'department': '',
        'base_salary': Decimal('0'),
        'total_earnings': Decimal('0'),
        'total_deductions': Decimal('0'),
        'net_pay': Decimal('0'),
        'fgts_base': Decimal('0'),
        'fgts_deposit': Decimal('0'),
        'irrf_base': Decimal('0'),
        'inss_base': Decimal('0'),
        'earnings_detail': [],
        'deductions_detail': [],
    }


def _extract_employee_info_from_text(lines, data):
    """
    Extrai nome do funcionário, cargo e data de admissão do texto da página.

    Padrão esperado no PDF:
        Código Nome do Funcionário CBO Departamento Filial
        9 ANA GABRIELLE DIAS 521110 2 1
        VENDEDORA Admissão: 16/01/2025
    """
    found = False
    for i, line in enumerate(lines):
        if 'Nome do Funcion' in line and 'CBO' in line:
            # Próxima linha: "CÓDIGO NOME_COMPLETO CBO_4a6DIGITOS DEPTO FILIAL"
            if i + 1 < len(lines):
                emp_line = lines[i + 1].strip()
                match = re.match(r'^\d+\s+(.+?)\s+\d{4,6}\s+\d+\s+\d+\s*$', emp_line)
                if match:
                    data['employee_name'] = match.group(1).strip()[:200]
                    found = True

            # Linha seguinte: "CARGO Admissão: DD/MM/AAAA"
            if i + 2 < len(lines):
                job_line = lines[i + 2].strip()
                job_match = re.match(r'^(.+?)\s+Admissão\s*:\s*([\d/.\-]+)', job_line)
                if job_match:
                    data['job_title'] = job_match.group(1).strip()[:150]
                    data['admission_date'] = job_match.group(2).strip()

            if found:
                break  # Só primeira ocorrência (página tem 2 cópias)

    return found


def _extract_base_values_from_text(lines, data):
    """
    Extrai valores de base do rodapé do contracheque.

    Padrão:
        Salário Base Sal. Contr. INSS Base Cálc. FGTS F.G.T.S do Mês Base Cálc. IRRF Faixa IRRF
        1.650,00 2.140,55 2.140,55 171,24 1.533,35 0,00
    """
    for i, line in enumerate(lines):
        if 'Salário Base' in line and ('INSS' in line or 'FGTS' in line):
            for j in range(i + 1, min(i + 4, len(lines))):
                values = re.findall(r'\d[\d.]*,\d+', lines[j])
                if len(values) >= 3:
                    data['base_salary'] = parse_currency(values[0])
                    data['inss_base'] = parse_currency(values[1])
                    data['fgts_base'] = parse_currency(values[2])
                    if len(values) >= 4:
                        data['fgts_deposit'] = parse_currency(values[3])
                    if len(values) >= 5:
                        data['irrf_base'] = parse_currency(values[4])
                    break
            break


def _extract_net_pay_from_text(lines, data):
    """
    Extrai Valor Líquido do texto.
    Padrão: "Valor Líquido 2.344,99"
    """
    for i, line in enumerate(lines):
        if 'Valor' in line and ('Líquido' in line or 'Liquido' in line):
            match = re.search(r'(?:Valor\s+L[íi]quido)\s+([\d.,]+)', line)
            if match:
                data['net_pay'] = parse_currency(match.group(1))
                return
            if i + 1 < len(lines):
                val_match = re.match(r'^\s*([\d.,]+)\s*$', lines[i + 1])
                if val_match:
                    data['net_pay'] = parse_currency(val_match.group(1))
                    return
            break


def _extract_totals_from_tables(tables, data):
    """
    Extrai Total de Vencimentos, Total de Descontos e Valor Líquido das tabelas.

    Formato nas cells da tabela:
        "Total de Vencimentos\\n2.841,56"
        "Total de Descontos\\n496,57"
        Cell "Valor Líquido" → próxima cell tem o valor
    """
    found_totals = False

    for table in tables:
        if found_totals:
            break
        for row in table:
            if not row:
                continue
            cells = [str(c).strip() if c else '' for c in row]

            for cell_idx, cell in enumerate(cells):
                if not cell:
                    continue

                if 'Total de Vencimentos' in cell:
                    parts = cell.split('\n')
                    for part in parts:
                        part = part.strip()
                        if part and 'Total' not in part and 'Vencimento' not in part:
                            val = parse_currency(part)
                            if val > 0:
                                data['total_earnings'] = val
                    found_totals = True

                if 'Total de Descontos' in cell:
                    parts = cell.split('\n')
                    for part in parts:
                        part = part.strip()
                        if part and 'Total' not in part and 'Desconto' not in part:
                            val = parse_currency(part)
                            if val > 0:
                                data['total_deductions'] = val
                    found_totals = True

                if 'Valor' in cell and ('Líquido' in cell or 'Liquido' in cell):
                    # Valor na mesma cell (multiline)
                    parts = cell.split('\n')
                    for part in parts:
                        part = part.strip()
                        if part and 'Valor' not in part and 'quido' not in part and re.match(r'^[\d.,]+$', part):
                            data['net_pay'] = parse_currency(part)
                            break
                    else:
                        # Procurar na próxima cell
                        if cell_idx + 1 < len(cells) and cells[cell_idx + 1]:
                            val_str = cells[cell_idx + 1].strip()
                            if re.match(r'^[\d.,]+$', val_str):
                                data['net_pay'] = parse_currency(val_str)


def _extract_line_items_from_tables(tables, data):
    """
    Extrai proventos e descontos detalhados das tabelas.

    Na tabela, a row de dados tem cells com valores separados por \\n:
        - Cell de descrições: "DIAS NORMAIS\\nPREMIO.\\nCOMISSÃO\\n..."
        - Cell de vencimentos: "1.650,00\\n701,01\\n..."
        - Cell de descontos: "168,32\\n266,82\\n..."

    Os primeiros N itens das descrições são vencimentos (N = qtd vencimentos),
    os últimos M itens são descontos (M = qtd descontos).
    """
    for table in tables:
        header_idx = None
        for i, row in enumerate(table):
            if not row:
                continue
            row_text = ' '.join(str(c) for c in row if c).lower()
            if ('código' in row_text or 'codigo' in row_text) and \
               ('vencimentos' in row_text or 'vencimento' in row_text) and \
               ('descontos' in row_text or 'desconto' in row_text):
                header_idx = i
                break

        if header_idx is None:
            continue

        data_row_idx = header_idx + 1
        if data_row_idx >= len(table):
            continue

        row = table[data_row_idx]
        if not row:
            continue

        cells = [str(c).strip() if c else '' for c in row]

        # Identificar cells com conteúdo
        description_cell = ''
        value_cells = []

        for cell in cells:
            if not cell:
                continue
            parts = [p.strip() for p in cell.split('\n') if p.strip()]
            if not parts:
                continue

            text_parts = [p for p in parts if not re.match(r'^[\d.,:\s]+$', p)]
            num_parts = [p for p in parts if re.match(r'^[\d.,]+$', p)]

            if len(text_parts) > len(num_parts) and len(text_parts) >= 2:
                if len(cell) > len(description_cell):
                    description_cell = cell
            elif num_parts:
                value_cells.append([parse_currency(p) for p in num_parts])

        if not description_cell:
            continue

        descriptions = [d.strip() for d in description_cell.split('\n') if d.strip()]

        venc_values = []
        desc_values = []

        if len(value_cells) >= 3:
            venc_values = value_cells[-2]
            desc_values = value_cells[-1]
        elif len(value_cells) == 2:
            venc_values = value_cells[0]
            desc_values = value_cells[1]
        elif len(value_cells) == 1:
            venc_values = value_cells[0]

        n_venc = len(venc_values)
        n_desc = len(desc_values)

        for i in range(min(n_venc, len(descriptions))):
            if venc_values[i] > 0:
                data['earnings_detail'].append({
                    'description': descriptions[i],
                    'value': str(venc_values[i]),
                })

        for i in range(n_desc):
            desc_idx = len(descriptions) - n_desc + i
            if 0 <= desc_idx < len(descriptions) and i < len(desc_values) and desc_values[i] > 0:
                data['deductions_detail'].append({
                    'description': descriptions[desc_idx],
                    'value': str(desc_values[i]),
                })

        if data['earnings_detail'] or data['deductions_detail']:
            break


def _parse_single_page(page):
    """
    Extrai os dados de contracheque de uma única página do PDF.
    Retorna dict com os dados ou None se não encontrar funcionário.
    """
    data = _default_payslip_data()

    try:
        text = page.extract_text() or ''
        lines = text.split('\n')

        # 1. Info do funcionário (nome, cargo, admissão)
        if not _extract_employee_info_from_text(lines, data):
            return None

        if not data['employee_name']:
            return None

        # 2. Valores base (Salário Base, INSS, FGTS, IRRF)
        _extract_base_values_from_text(lines, data)

        # 3. Valor Líquido do texto
        _extract_net_pay_from_text(lines, data)

        # 4. Totais das tabelas (Total Vencimentos, Total Descontos, Valor Líquido)
        tables = page.extract_tables()
        _extract_totals_from_tables(tables, data)

        # 5. Itens detalhados (proventos e descontos)
        _extract_line_items_from_tables(tables, data)

        # 6. CPF se presente
        cpf_match = re.search(r'CPF\s*:?\s*(\d{3}[.\s]?\d{3}[.\s]?\d{3}[.\s/-]?\d{2})', text)
        if cpf_match:
            data['cpf'] = cpf_match.group(1).strip()

        # 7. Fallback: calcular totais faltantes
        if data['total_earnings'] == 0 and data['net_pay'] > 0 and data['total_deductions'] > 0:
            data['total_earnings'] = data['net_pay'] + data['total_deductions']
        elif data['total_deductions'] == 0 and data['total_earnings'] > 0 and data['net_pay'] >= 0:
            data['total_deductions'] = data['total_earnings'] - data['net_pay']

    except Exception as e:
        data['error'] = str(e)

    return data


def extract_all_payslips(pdf_file):
    """
    Extrai TODOS os contracheques de um PDF com múltiplos funcionários.
    Cada página é o contracheque de um funcionário (com 2 cópias na página).

    Retorna lista de dicts, um por funcionário encontrado.
    """
    if pdfplumber is None:
        return [{'error': 'pdfplumber não está instalado. Execute: pip install pdfplumber'}]

    try:
        if hasattr(pdf_file, 'read'):
            pdf_bytes = pdf_file.read()
            pdf_file.seek(0)
            pdf_obj = pdfplumber.open(io.BytesIO(pdf_bytes))
        else:
            pdf_obj = pdfplumber.open(pdf_file)

        payslips = []
        seen_names = set()

        for page_idx, page in enumerate(pdf_obj.pages):
            page_data = _parse_single_page(page)

            if not page_data or not page_data.get('employee_name'):
                continue

            # Deduplicar por nome normalizado
            name_key = normalize_name(page_data['employee_name'])
            if name_key in seen_names:
                continue
            seen_names.add(name_key)

            # Guardar o número da página para extração individual
            page_data['_page_number'] = page_idx

            payslips.append(page_data)

        pdf_obj.close()
        return payslips

    except Exception as e:
        return [{'error': f'Erro ao processar PDF: {str(e)}'}]


def extract_payslip_data(pdf_file):
    """
    Extrai dados de um PDF de contracheque (compatibilidade com importação individual).
    Se o PDF tiver múltiplos funcionários, retorna os dados do primeiro.
    """
    results = extract_all_payslips(pdf_file)

    if not results:
        return _default_payslip_data()

    if len(results) == 1 and 'error' in results[0] and not results[0].get('employee_name'):
        return results[0]

    return results[0]


def extract_single_page_pdf(pdf_file, page_number):
    """
    Extrai uma única página de um PDF multi-página e retorna os bytes de um novo PDF
    contendo apenas essa página.

    Args:
        pdf_file: arquivo PDF (path string, bytes ou file-like object)
        page_number: índice da página (0-based)

    Returns:
        bytes do PDF de página única, ou None em caso de erro
    """
    if pdfium is None:
        return None

    try:
        if isinstance(pdf_file, bytes):
            pdf_bytes = pdf_file
        elif hasattr(pdf_file, 'read'):
            pdf_bytes = pdf_file.read()
            pdf_file.seek(0)
        else:
            with open(pdf_file, 'rb') as f:
                pdf_bytes = f.read()

        src_pdf = pdfium.PdfDocument(pdf_bytes)

        if page_number < 0 or page_number >= len(src_pdf):
            src_pdf.close()
            return None

        # Criar novo PDF com apenas a página desejada
        new_pdf = pdfium.PdfDocument.new()
        new_pdf.import_pages(src_pdf, [page_number])

        output = io.BytesIO()
        new_pdf.save(output)
        new_pdf.close()
        src_pdf.close()

        return output.getvalue()

    except Exception:
        return None


def find_employee_page_number(pdf_file, employee_name):
    """
    Encontra o número da página de um funcionário no PDF, buscando pelo nome.

    Args:
        pdf_file: arquivo PDF
        employee_name: nome do funcionário a buscar

    Returns:
        int (0-based page number) ou None se não encontrado
    """
    if pdfplumber is None or not employee_name:
        return None

    try:
        if isinstance(pdf_file, bytes):
            pdf_bytes = pdf_file
        elif hasattr(pdf_file, 'read'):
            pdf_bytes = pdf_file.read()
            pdf_file.seek(0)
        else:
            with open(pdf_file, 'rb') as f:
                pdf_bytes = f.read()

        pdf_obj = pdfplumber.open(io.BytesIO(pdf_bytes))
        target = normalize_name(employee_name)

        for i, page in enumerate(pdf_obj.pages):
            text = page.extract_text() or ''
            lines = text.split('\n')
            for li, line in enumerate(lines):
                if 'Nome do Funcion' in line and 'CBO' in line and li + 1 < len(lines):
                    emp_line = lines[li + 1].strip()
                    match = re.match(r'^\d+\s+(.+?)\s+\d{4,6}\s+\d+\s+\d+\s*$', emp_line)
                    if match:
                        page_name = normalize_name(match.group(1).strip())
                        if page_name == target:
                            pdf_obj.close()
                            return i
                    break  # Only check first occurrence per page

        pdf_obj.close()
        return None

    except Exception:
        return None


# ─── Parser de Informe de Rendimentos ─────────────────────────────────────────

def _parse_income_report_page(page):
    """
    Extrai dados de um informe de rendimentos de uma única página.
    Tabelas esperadas:
      Table 0: Cabeçalho (Ministério/Exercício)
      Table 1: Fonte Pagadora (CNPJ)
      Table 2: Beneficiário (CPF / Nome)
      Table 3: Rendimentos Tributáveis (5 linhas)
      Table 4: Rendimentos Isentos (9 linhas)
      Table 5: Tributação Exclusiva (3 linhas)
      Table 6: Assinatura
    """
    tables = page.extract_tables()
    if not tables or len(tables) < 5:
        return None

    data = {}

    # Exercício / Ano-Calendário do texto
    text = page.extract_text() or ''
    ex_match = re.search(r'EXERC[ÍI]CIO\s*:\s*(\d{4})', text)
    ac_match = re.search(r'ANO[- ]CALEND[ÁA]RIO\s*:\s*(\d{4})', text)
    data['exercise_year'] = int(ex_match.group(1)) if ex_match else 0
    data['base_year'] = int(ac_match.group(1)) if ac_match else 0

    # Table 2: CPF e Nome do beneficiário (skip Table 1 which has CNPJ)
    for t in tables:
        if not t or not t[0]:
            continue
        cell0 = str(t[0][0]) if t[0][0] else ''
        # Table 1 has "CNPJ/CPF", Table 2 has just "CPF" — skip CNPJ tables
        if 'CNPJ' in cell0:
            continue
        if 'CPF' in cell0 and 'Nome' in (str(t[0][1]) if len(t[0]) > 1 and t[0][1] else cell0):
            # CPF\n057.150.647-08
            cpf_match = re.search(r'(\d{3}[.\s]?\d{3}[.\s]?\d{3}[.\s/-]?\d{2})', cell0)
            data['cpf'] = cpf_match.group(1).strip() if cpf_match else ''
            # Nome\nFULANO DE TAL - 000001
            name_cell = str(t[0][1]) if len(t[0]) > 1 and t[0][1] else ''
            name_lines = [l.strip() for l in name_cell.split('\n') if l.strip()]
            raw_name = name_lines[-1] if name_lines else ''
            # Remove trailing " - 000001"
            raw_name = re.sub(r'\s*-\s*\d+\s*$', '', raw_name).strip()
            if raw_name.startswith('Nome'):
                raw_name = ''
            data['employee_name'] = raw_name
            break

    if not data.get('employee_name'):
        return None

    # Helper to get value from table row
    def _val(table, row_idx):
        try:
            row = table[row_idx]
            val_str = str(row[-1]).strip() if row[-1] else '0'
            return parse_currency(val_str)
        except (IndexError, TypeError):
            return Decimal('0')

    # Identify tables by content
    trib_table = None
    isento_table = None
    excl_table = None

    for t in tables:
        if not t:
            continue
        first_cell = str(t[0][0]) if t[0] and t[0][0] else ''
        if 'Total dos rendimentos' in first_cell:
            trib_table = t
        elif 'Parcela isenta' in first_cell:
            isento_table = t
        elif '13' in first_cell and ('salário' in first_cell.lower() or 'salario' in first_cell.lower()):
            excl_table = t

    # 3. Rendimentos Tributáveis (Table 3)
    if trib_table:
        data['total_rendimentos'] = _val(trib_table, 0)
        data['contribuicao_previdenciaria'] = _val(trib_table, 1)
        data['contribuicao_previdencia_privada'] = _val(trib_table, 2)
        data['pensao_alimenticia'] = _val(trib_table, 3)
        data['irrf'] = _val(trib_table, 4)

    # 4. Rendimentos Isentos (Table 4)
    if isento_table:
        data['parcela_isenta_aposentadoria'] = _val(isento_table, 0)
        data['parcela_isenta_13_aposentadoria'] = _val(isento_table, 1)
        data['diarias_ajuda_custo'] = _val(isento_table, 2)
        data['pensao_moletia_grave'] = _val(isento_table, 3)
        data['lucros_dividendos'] = _val(isento_table, 4)
        data['valores_titular_socio'] = _val(isento_table, 5)
        data['indenizacao_rescisao'] = _val(isento_table, 6)
        data['juros_mora'] = _val(isento_table, 7)
        data['outros_isentos'] = _val(isento_table, 8)

    # 5. Tributação Exclusiva (Table 5)
    if excl_table:
        data['decimo_terceiro'] = _val(excl_table, 0)
        data['irrf_13'] = _val(excl_table, 1)
        data['outros_exclusivos'] = _val(excl_table, 2)

    return data


def extract_all_income_reports(pdf_file):
    """
    Extrai todos os informes de rendimentos de um PDF multi-página.
    Uma página por beneficiário.
    Retorna lista de dicts.
    """
    if pdfplumber is None:
        return [{'error': 'pdfplumber não está instalado.'}]

    try:
        if hasattr(pdf_file, 'read'):
            pdf_bytes = pdf_file.read()
            pdf_file.seek(0)
            pdf_obj = pdfplumber.open(io.BytesIO(pdf_bytes))
        else:
            pdf_obj = pdfplumber.open(pdf_file)

        reports = []
        seen = set()

        for page_idx, page in enumerate(pdf_obj.pages):
            report = _parse_income_report_page(page)
            if not report or not report.get('employee_name'):
                continue

            key = normalize_name(report['employee_name'])
            if key in seen:
                continue
            seen.add(key)

            report['_page_number'] = page_idx
            reports.append(report)

        pdf_obj.close()
        return reports

    except Exception as e:
        return [{'error': f'Erro ao processar PDF: {str(e)}'}]
