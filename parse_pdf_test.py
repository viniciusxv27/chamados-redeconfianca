#!/usr/bin/env python
import sys
try:
    import pdfplumber
    with pdfplumber.open('/Users/teste/Downloads/Checklist Unificado_Março26.pdf') as pdf:
        for i, page in enumerate(pdf.pages):
            print(f'--- PAGE {i+1} ---')
            text = page.extract_text()
            if text:
                print(text)
            print()
            # Also try tables
            tables = page.extract_tables()
            if tables:
                for j, table in enumerate(tables):
                    print(f'  TABLE {j+1}:')
                    for row in table:
                        print(f'    {row}')
                    print()
except ImportError:
    print('pdfplumber not installed')
    sys.exit(1)
except Exception as e:
    print(f'Error: {e}')
    sys.exit(1)
