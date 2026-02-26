import re
import io
from decimal import Decimal, InvalidOperation

try:
    import pdfplumber
except ImportError:
    pdfplumber = None


def parse_currency(value_str):
    """Converte string monetária BR para Decimal. Ex: '1.234,56' -> Decimal('1234.56')"""
    if not value_str:
        return Decimal('0')
    cleaned = value_str.strip().replace('.', '').replace(',', '.')
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return Decimal('0')


def extract_payslip_data(pdf_file):
    """
    Extrai dados de um PDF de contracheque.
    Retorna dict com campos preenchidos.
    """
    if pdfplumber is None:
        return {'error': 'pdfplumber não está instalado. Execute: pip install pdfplumber'}

    data = {
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

    try:
        if hasattr(pdf_file, 'read'):
            pdf_bytes = pdf_file.read()
            pdf_file.seek(0)
            pdf_obj = pdfplumber.open(io.BytesIO(pdf_bytes))
        else:
            pdf_obj = pdfplumber.open(pdf_file)

        full_text = ''
        for page in pdf_obj.pages:
            text = page.extract_text() or ''
            full_text += text + '\n'

        lines = full_text.split('\n')

        # Tentar extrair nome do funcionário
        for line in lines:
            if 'nome:' in line.lower() or 'funcionário' in line.lower() or 'funcionario' in line.lower():
                parts = re.split(r'nome\s*:?\s*', line, flags=re.IGNORECASE)
                if len(parts) > 1:
                    data['employee_name'] = parts[1].strip()[:200]
                    break

        # CPF
        cpf_match = re.search(r'CPF\s*:?\s*([\d.\-/]+)', full_text, re.IGNORECASE)
        if cpf_match:
            data['cpf'] = cpf_match.group(1).strip()

        # Cargo / Função
        func_match = re.search(r'(?:cargo|função|funcao)\s*:?\s*(.+)', full_text, re.IGNORECASE)
        if func_match:
            data['job_title'] = func_match.group(1).strip()[:150]

        # Data de admissão
        adm_match = re.search(r'(?:admissão|admissao|adm\.?)\s*:?\s*([\d/.\-]+)', full_text, re.IGNORECASE)
        if adm_match:
            data['admission_date'] = adm_match.group(1).strip()

        # Departamento
        dep_match = re.search(r'(?:depart(?:amento)?|setor|lotação|lotacao)\s*:?\s*(.+)', full_text, re.IGNORECASE)
        if dep_match:
            data['department'] = dep_match.group(1).strip()[:200]

        # Salário base
        sal_match = re.search(r'(?:sal[áa]rio\s*base|salario\s*base)\s*[:\s]*([\d.,]+)', full_text, re.IGNORECASE)
        if sal_match:
            data['base_salary'] = parse_currency(sal_match.group(1))

        # Total de proventos
        prov_match = re.search(r'(?:total\s*(?:de\s*)?proventos?|total\s*vencimentos?)\s*[:\s]*([\d.,]+)', full_text, re.IGNORECASE)
        if prov_match:
            data['total_earnings'] = parse_currency(prov_match.group(1))

        # Total de descontos
        desc_match = re.search(r'(?:total\s*(?:de\s*)?descontos?)\s*[:\s]*([\d.,]+)', full_text, re.IGNORECASE)
        if desc_match:
            data['total_deductions'] = parse_currency(desc_match.group(1))

        # Líquido
        liq_match = re.search(r'(?:l[íi]quido|valor\s*l[íi]quido|a\s*receber)\s*[:\s]*([\d.,]+)', full_text, re.IGNORECASE)
        if liq_match:
            data['net_pay'] = parse_currency(liq_match.group(1))

        # FGTS
        fgts_base_match = re.search(r'(?:base\s*(?:de\s*)?fgts|fgts\s*base)\s*[:\s]*([\d.,]+)', full_text, re.IGNORECASE)
        if fgts_base_match:
            data['fgts_base'] = parse_currency(fgts_base_match.group(1))

        fgts_dep_match = re.search(r'(?:dep[óo]sito\s*fgts|fgts\s*dep[óo]sito|fgts\s*[\d.,]+\s*([\d.,]+))', full_text, re.IGNORECASE)
        if fgts_dep_match:
            val = fgts_dep_match.group(1) if fgts_dep_match.group(1) else ''
            if val:
                data['fgts_deposit'] = parse_currency(val)

        # IRRF base
        irrf_match = re.search(r'(?:base\s*(?:de\s*)?irrf|irrf\s*base)\s*[:\s]*([\d.,]+)', full_text, re.IGNORECASE)
        if irrf_match:
            data['irrf_base'] = parse_currency(irrf_match.group(1))

        # INSS base
        inss_match = re.search(r'(?:base\s*(?:de\s*)?inss|inss\s*base)\s*[:\s]*([\d.,]+)', full_text, re.IGNORECASE)
        if inss_match:
            data['inss_base'] = parse_currency(inss_match.group(1))

        # --- Tentar extrair table de proventos e descontos ---
        for page in pdf_obj.pages:
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    if not row or not any(row):
                        continue
                    cells = [str(c).strip() if c else '' for c in row]
                    # Procurar linhas com código/descrição/referência/proventos/descontos
                    # Formato típico: [cod, descricao, ref, proventos, descontos]
                    if len(cells) >= 4:
                        desc = cells[1] if len(cells) > 1 else ''
                        # Valor provento (penúltima ou coluna de proventos)
                        prov_val = cells[-2] if len(cells) >= 5 else ''
                        desc_val = cells[-1] if len(cells) >= 5 else ''

                        if not desc or desc.lower() in ('descrição', 'descricao', 'evento', ''):
                            continue

                        prov_parsed = parse_currency(prov_val)
                        desc_parsed = parse_currency(desc_val)

                        if prov_parsed > 0:
                            data['earnings_detail'].append({
                                'description': desc,
                                'value': str(prov_parsed),
                            })
                        if desc_parsed > 0:
                            data['deductions_detail'].append({
                                'description': desc,
                                'value': str(desc_parsed),
                            })

        pdf_obj.close()

        # Se não encontrou nome, tentar pegar da primeira linha com texto útil
        if not data['employee_name']:
            for line in lines:
                line_clean = line.strip()
                if line_clean and len(line_clean) > 5 and not any(k in line_clean.lower() for k in ['recibo', 'pagamento', 'empresa', 'cnpj', 'código', 'codigo']):
                    data['employee_name'] = line_clean[:200]
                    break

    except Exception as e:
        data['error'] = str(e)

    return data
