#!/usr/bin/env python3
import sys, io
sys.path.insert(0, '/Users/teste/Downloads/chamados-redeconfianca')
from contracheque.pdf_parser import _parse_single_page, parse_currency
import pdfplumber

pdf = pdfplumber.open('/Users/teste/Downloads/Recibo de Pagamento - Janeiro 2026.pdf')
print(f'Total pages: {len(pdf.pages)}')
print()

# Test first 5 pages
for page_idx in range(min(5, len(pdf.pages))):
    page = pdf.pages[page_idx]
    p = _parse_single_page(page)
    if not p:
        print(f'Page {page_idx}: No data found')
        continue
    print(f'--- Page {page_idx} ---')
    print(f'  Nome: {p.get("employee_name", "???")}')
    print(f'  Cargo: {p.get("job_title", "???")}')
    print(f'  Admissao: {p.get("admission_date", "???")}')
    print(f'  Salario Base: {p.get("base_salary", 0)}')
    print(f'  Total Vencimentos: {p.get("total_earnings", 0)}')
    print(f'  Total Descontos: {p.get("total_deductions", 0)}')
    print(f'  Valor Liquido: {p.get("net_pay", 0)}')
    print(f'  INSS Base: {p.get("inss_base", 0)}')
    print(f'  FGTS Base: {p.get("fgts_base", 0)}')
    print(f'  FGTS Deposito: {p.get("fgts_deposit", 0)}')
    print(f'  IRRF Base: {p.get("irrf_base", 0)}')
    print(f'  Proventos: {len(p.get("earnings_detail", []))} itens')
    for e in p.get('earnings_detail', []):
        print(f'    + {e["description"]}: R$ {e["value"]}')
    print(f'  Descontos: {len(p.get("deductions_detail", []))} itens')
    for d in p.get('deductions_detail', []):
        print(f'    - {d["description"]}: R$ {d["value"]}')
    if 'error' in p:
        print(f'  ERRO: {p["error"]}')
    print()

pdf.close()
