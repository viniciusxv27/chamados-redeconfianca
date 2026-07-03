"""
Parser de "Folha de Ponto" (relatório de ponto mensal).

Cada colaborador ocupa uma ou mais páginas consecutivas (agrupadas por CPF).
Extrai identificação, bloco de totais/resumo e as marcações diárias.
"""
import io
import re
import unicodedata

try:
    import pdfplumber
except ImportError:  # pragma: no cover
    pdfplumber = None

try:
    import pypdfium2 as pdfium
except ImportError:  # pragma: no cover
    pdfium = None


def normalize_name(name):
    """Remove acentos, maiúsculo, espaços colapsados."""
    if not name:
        return ''
    nfkd = unicodedata.normalize('NFKD', name)
    ascii_text = ''.join(c for c in nfkd if not unicodedata.combining(c))
    return ' '.join(ascii_text.upper().split())


def clean_cpf(cpf):
    return re.sub(r'\D', '', cpf or '')


def _hhmm(text, pattern):
    m = re.search(pattern, text)
    return m.group(1).strip() if m else ''


def _parse_header(text):
    """Identificação do colaborador e período."""
    data = {}
    lines = text.split('\n')

    m = re.search(r'(\d{2}/\d{2}/\d{4})\s+a\s+(\d{2}/\d{2}/\d{4})', text)
    if m:
        data['period_start'] = m.group(1)
        data['period_end'] = m.group(2)
        end = m.group(2).split('/')
        data['month'] = int(end[1])
        data['year'] = int(end[2])

    m = re.search(r'Nome:\s*(.+?)\s+CNPJ:', text)
    if m:
        data['employer_name'] = m.group(1).strip()[:200]

    m = re.search(r'Nome:\s*(.+?)\s+CPF:\s*([\d.\-]+)', text)
    if m:
        data['employee_name'] = m.group(1).strip()[:200]
        data['cpf'] = clean_cpf(m.group(2))

    m = re.search(r'Admiss[ãa]o:\s*(\d{2}/\d{2}/\d{4})', text)
    if m:
        data['admission_date'] = m.group(1)

    m = re.search(r'Fun[çc][ãa]o:\s*(.+?)\s+Centro de Custo:', text)
    job = m.group(1).strip() if m else ''
    if not job:
        for i, ln in enumerate(lines):
            if 'CPF:' in ln and 'Nome:' in ln:
                for j in range(i + 1, min(i + 4, len(lines))):
                    cand = lines[j].strip()
                    if cand and re.match(r'^[A-ZÀ-Ú0-9 ]{4,}$', cand) \
                            and 'ADMISS' not in cand and 'QUADRO' not in cand \
                            and 'PERÍODO' not in cand:
                        job = cand
                        break
                break
    data['job_title'] = job[:150]
    return data


def _parse_summary(text):
    """Bloco de totais / resumo do cartão de ponto."""
    s = {}
    m = re.search(
        r'Total:\s*(-?\d{1,3}:\d{2})(?:\s+(-?\d{1,3}:\d{2}))?'
        r'(?:\s+(-?\d{1,3}:\d{2}))?(?:\s+(-?\d{1,3}:\d{2}))?', text)
    if m:
        s['total_trabalhadas'] = m.group(1) or ''
        s['total_abono'] = m.group(2) or ''
        s['total_previstas'] = m.group(3) or ''
        s['total_saldo'] = m.group(4) or ''
    s['trabalhadas_abono'] = _hhmm(text, r'Trabalhadas \+ Abono:\s*(-?\d{1,3}:\d{2})')
    m = re.search(r'Dias Faltosos:\s*(\d+)', text)
    s['dias_faltosos'] = int(m.group(1)) if m else 0
    s['faltas_horas'] = _hhmm(text, r'Faltas em Horas:\s*(-?\d{1,3}:\d{2})')
    s['saldo_anterior'] = _hhmm(text, r'Saldo Anterior de Banco de Horas:\s*(-?\d{1,3}:\d{2})')
    s['saldo_acumulado'] = _hhmm(text, r'Saldo Acumulado at[ée][^:]*:\s*(-?\d{1,3}:\d{2})')
    s['horas_extras'] = _hhmm(text, r'Horas Extras Totais:\s*(-?\d{1,3}:\d{2})')
    s['atrasos'] = _hhmm(text, r'Atrasos:\s*(-?\d{1,3}:\d{2})')
    return s


def _parse_daily(text):
    """Marcações diárias como lista de dicts."""
    days = []
    for ln in text.split('\n'):
        m = re.match(
            r'^(\d{2}/\d{2})\s+([a-zà-ú]+(?:-feira)?|s[áa]bado|domingo)\s*(.*)$',
            ln.strip(), re.IGNORECASE)
        if m:
            days.append({
                'dia': m.group(1),
                'semana': m.group(2).lower(),
                'registro': m.group(3).strip(),
            })
    return days


def _default_record():
    return {
        'employee_name': '', 'cpf': '', 'job_title': '', 'admission_date': '',
        'employer_name': '', 'period_start': '', 'period_end': '',
        'total_trabalhadas': '', 'total_abono': '', 'total_previstas': '',
        'total_saldo': '', 'trabalhadas_abono': '', 'faltas_horas': '',
        'saldo_anterior': '', 'saldo_acumulado': '', 'horas_extras': '',
        'atrasos': '', 'dias_faltosos': 0, 'daily_records': [],
    }


def extract_all_folhas(pdf_file):
    """
    Extrai todas as folhas de ponto de um PDF, agrupando páginas
    consecutivas do mesmo colaborador (mesmo CPF).

    Retorna lista de dicts. Cada dict inclui:
      - campos de dados (employee_name, cpf, totais, daily_records...)
      - '_pages': lista de índices (0-based) das páginas do colaborador
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

        grupos = []
        atual = None

        for idx, page in enumerate(pdf_obj.pages):
            text = page.extract_text() or ''
            hdr = _parse_header(text)
            cpf = hdr.get('cpf', '')
            name = hdr.get('employee_name', '')

            if not cpf and not name:
                # página sem cabeçalho de colaborador: anexa à anterior
                if atual is not None:
                    atual['_pages'].append(idx)
                    atual['daily_records'].extend(_parse_daily(text))
                continue

            key = cpf or normalize_name(name)

            if atual is not None and atual['_key'] == key:
                atual['_pages'].append(idx)
                atual['daily_records'].extend(_parse_daily(text))
                if not atual.get('total_trabalhadas'):
                    for k, v in _parse_summary(text).items():
                        if v:
                            atual[k] = v
            else:
                if atual is not None:
                    grupos.append(atual)
                rec = _default_record()
                rec.update(hdr)
                rec.update(_parse_summary(text))
                rec['daily_records'] = _parse_daily(text)
                rec['_key'] = key
                rec['_pages'] = [idx]
                atual = rec

        if atual is not None:
            grupos.append(atual)

        pdf_obj.close()
        return grupos

    except Exception as e:
        return [{'error': f'Erro ao processar PDF: {str(e)}'}]


def extract_pages_pdf(pdf_file, page_numbers):
    """
    Recorta uma ou mais páginas (0-based) de um PDF e devolve os bytes de um
    novo PDF contendo apenas essas páginas.
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

        src = pdfium.PdfDocument(pdf_bytes)
        total = len(src)
        valid = [p for p in page_numbers if 0 <= p < total]
        if not valid:
            src.close()
            return None

        new_pdf = pdfium.PdfDocument.new()
        new_pdf.import_pages(src, valid)

        out = io.BytesIO()
        new_pdf.save(out)
        new_pdf.close()
        src.close()
        return out.getvalue()
    except Exception:
        return None
