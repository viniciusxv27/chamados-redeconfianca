"""
Views para exibição de dados de comissionamento
Busca dados da planilha Excel do OneDrive/SharePoint
Design inspirado na identidade visual VIVO

Visões:
- CN: Vê seu comissionamento, valores por pilar, lista de vendas
- Gerente: Vê todos os CNs da loja, valores de cada CN, atingimentos por pilar
- Coordenador: Vê todos os gerentes e CNs, atingimento das lojas
"""
import requests
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
import json
import pandas as pd
from io import BytesIO
from users.models import User, Sector
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


# ============================================================================
# CONFIGURAÇÕES DAS PLANILHAS
# ============================================================================

# Planilha de Comissionamento
EXCEL_COMISSAO_URL = "https://1drv.ms/x/c/871ee1819c7e2faa/IQDiTJg7g9b_R6wn6uXndz3UAXzjm8r7m27co8LHPJ6vyFQ"

# Planilha de Vendas e Metas
EXCEL_VENDAS_URL = "https://1drv.ms/x/c/871ee1819c7e2faa/IQAVeQ-dgEiBTYG0UlK7URSLAQ5r634qBo9-GicO2D8ZfmY"

# Nome das sheets na planilha de comissionamento
SHEET_GERENTE = "REMUNERAÇÃO GERENTE"
SHEET_CN = "REMUNERAÇÃO CN"
SHEET_COORDENADOR = "REMUNERAÇÃO COO e SNP"

# Nome da sheet na planilha de vendas (será detectado automaticamente)
SHEET_VENDAS = "VENDAS"

# Grupos que identificam cada tipo de usuário
GERENTE_GROUP_NAME = "GERENTES (CHECKLIST)"
COORDENADOR_GROUP_NAME = "COORDENADORES"


# ============================================================================
# FUNÇÕES HELPER PARA VERIFICAR TIPO DE USUÁRIO
# ============================================================================

def is_user_gerente(user):
    """Verifica se o usuário está no grupo GERENTES (CHECKLIST)"""
    from communications.models import CommunicationGroup
    try:
        gerente_group = CommunicationGroup.objects.filter(name__icontains="GERENTES").first()
        if gerente_group:
            return user in gerente_group.members.all()
    except:
        pass
    return False


def is_user_coordenador(user):
    """Verifica se o usuário está no grupo COORDENADORES"""
    from communications.models import CommunicationGroup
    try:
        coord_group = CommunicationGroup.objects.filter(name__icontains="COORDENADORES").first()
        if coord_group:
            return user in coord_group.members.all()
    except:
        pass
    return False


def get_user_role(user):
    """
    Retorna o papel do usuário no sistema de comissionamento:
    - 'coordenador': Pode ver todos os gerentes e CNs
    - 'gerente': Pode ver CNs da sua loja
    - 'cn': Vê apenas seu próprio comissionamento
    """
    if is_user_coordenador(user):
        return 'coordenador'
    elif is_user_gerente(user):
        return 'gerente'
    else:
        return 'cn'


def get_sector_users(user, include_gerentes=False):
    """Retorna lista de usuários do mesmo setor"""
    if not user.sector:
        return User.objects.none()
    
    queryset = User.objects.filter(
        sector=user.sector,
        hierarchy='PADRAO',
        is_active=True
    ).exclude(id=user.id).order_by('first_name', 'last_name')
    
    return queryset


def get_all_cns_for_gerente(user):
    """Retorna todos os CNs que o gerente pode ver (mesmo setor/loja)"""
    if not user.sector:
        return User.objects.none()
    
    return User.objects.filter(
        sector=user.sector,
        hierarchy='PADRAO',
        is_active=True
    ).exclude(id=user.id).order_by('first_name', 'last_name')


def get_coordenador_scope(user):
    """
    Retorna os usuários que o coordenador pode ver.
    Coordenador vê todos os gerentes e CNs.
    """
    from communications.models import CommunicationGroup
    
    # Pegar todos os gerentes
    gerentes = []
    gerente_group = CommunicationGroup.objects.filter(name__icontains="GERENTES").first()
    if gerente_group:
        gerentes = list(gerente_group.members.filter(is_active=True))
    
    # Pegar todos os CNs (usuários PADRAO que não são gerentes)
    gerente_ids = [g.id for g in gerentes]
    cns = User.objects.filter(
        hierarchy='PADRAO',
        is_active=True
    ).exclude(id__in=gerente_ids + [user.id])
    
    return {
        'gerentes': gerentes,
        'cns': cns,
    }


def get_lojas_do_coordenador(user):
    """Retorna as lojas/setores que o coordenador supervisiona"""
    from communications.models import CommunicationGroup
    
    # Pegar todos os gerentes para identificar as lojas
    gerente_group = CommunicationGroup.objects.filter(name__icontains="GERENTES").first()
    if not gerente_group:
        return []
    
    gerentes = gerente_group.members.filter(is_active=True)
    
    # Agrupar por setor (loja)
    lojas = {}
    for gerente in gerentes:
        if gerente.sector:
            if gerente.sector.id not in lojas:
                lojas[gerente.sector.id] = {
                    'setor': gerente.sector,
                    'gerente': gerente,
                    'cns': []
                }
    
    # Adicionar CNs a cada loja
    for loja_id, loja_data in lojas.items():
        loja_data['cns'] = User.objects.filter(
            sector_id=loja_id,
            hierarchy='PADRAO',
            is_active=True
        ).exclude(id=loja_data['gerente'].id)
    
    return list(lojas.values())


# ============================================================================
# FUNÇÕES DE DOWNLOAD E PROCESSAMENTO DE PLANILHAS
# ============================================================================

def get_excel_download_url(share_url):
    """
    Converte URL de compartilhamento do OneDrive para URL de download direto
    """
    import base64
    import re
    
    if 'download=1' in share_url:
        return share_url
    
    if 'resid=' in share_url:
        match = re.search(r'resid=([^&]+)', share_url)
        if match:
            resid = match.group(1)
            return f"https://onedrive.live.com/download?resid={resid}"
    
    encoded_url = base64.urlsafe_b64encode(share_url.encode()).decode()
    encoded_url = encoded_url.rstrip('=')
    sharing_token = f"u!{encoded_url}"
    
    return f"https://api.onedrive.com/v1.0/shares/{sharing_token}/root/content"


def download_excel_file(excel_url, cache_key_prefix="excel"):
    """
    Baixa arquivo Excel do OneDrive e retorna como BytesIO
    """
    cache_key = f"{cache_key_prefix}_file_content"
    cached_content = cache.get(cache_key)
    
    if cached_content:
        return BytesIO(cached_content), None
    
    download_urls = []
    download_urls.append(get_excel_download_url(excel_url))
    
    if '?' in excel_url:
        download_urls.append(excel_url + '&download=1')
    else:
        download_urls.append(excel_url + '?download=1')
    
    if 'IQ' in excel_url:
        import re
        match = re.search(r'(IQ[A-Za-z0-9_-]+)', excel_url)
        if match:
            file_id = match.group(1)
            download_urls.append(f"https://onedrive.live.com/download.aspx?resid={file_id}")
    
    response = None
    last_error = None
    
    for url in download_urls:
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = requests.get(url, timeout=30, headers=headers, allow_redirects=True)
            
            if response.status_code == 200:
                content_type = response.headers.get('Content-Type', '')
                if 'excel' in content_type or 'spreadsheet' in content_type or 'octet-stream' in content_type or len(response.content) > 1000:
                    break
                else:
                    last_error = f'Conteúdo não é Excel: {content_type}'
                    response = None
            else:
                last_error = f'HTTP {response.status_code}'
                response = None
        except Exception as e:
            last_error = str(e)
            continue
    
    if response is None or response.status_code != 200:
        return None, f'Erro ao baixar planilha. Último erro: {last_error}'
    
    # Cache por 5 minutos
    cache.set(cache_key, response.content, 300)
    
    return BytesIO(response.content), None


def fetch_excel_data(sheet_name, user_name, excel_url=None):
    """
    Busca dados da planilha Excel do OneDrive
    Retorna os dados do usuário específico
    """
    if excel_url is None:
        excel_url = EXCEL_COMISSAO_URL
        
    cache_key = f"commission_data_{sheet_name}_{user_name.replace(' ', '_')}"
    cached_data = cache.get(cache_key)
    
    if cached_data:
        return cached_data
    
    try:
        excel_file, error = download_excel_file(excel_url, f"comissao_{sheet_name}")
        if error:
            return {'error': error}
        
        try:
            df = pd.read_excel(excel_file, sheet_name=sheet_name, engine='openpyxl')
        except Exception as e:
            return {'error': f'Erro ao ler planilha: {str(e)}'}
        
        nome_col = None
        for col in df.columns:
            if 'NOME' in str(col).upper():
                nome_col = col
                break
        
        if nome_col is None:
            nome_col = df.columns[0]
        
        def normalize_name(name):
            if pd.isna(name):
                return ""
            return str(name).strip().upper()
        
        user_name_normalized = normalize_name(user_name)
        
        user_row = None
        for idx, row in df.iterrows():
            row_name = normalize_name(row.get(nome_col, ''))
            if row_name == user_name_normalized:
                user_row = row
                break
            elif user_name_normalized in row_name or row_name in user_name_normalized:
                user_row = row
                break
        
        if user_row is None:
            return {'error': 'Usuário não encontrado na planilha', 'user_name': user_name}
        
        data = {}
        for col in df.columns:
            value = user_row.get(col)
            if pd.isna(value):
                data[str(col)] = None
            elif isinstance(value, (int, float)):
                data[str(col)] = float(value) if isinstance(value, float) else int(value)
            else:
                data[str(col)] = str(value)
        
        result = {
            'success': True,
            'data': data,
            'sheet': sheet_name
        }
        
        cache.set(cache_key, result, 300)
        
        return result
        
    except requests.exceptions.Timeout:
        return {'error': 'Tempo limite excedido ao acessar a planilha'}
    except Exception as e:
        return {'error': f'Erro ao processar dados: {str(e)}'}


def fetch_all_users_from_sheet(sheet_name, excel_url=None):
    """
    Busca todos os usuários de uma sheet da planilha
    Retorna lista com dados de todos os usuários
    """
    if excel_url is None:
        excel_url = EXCEL_COMISSAO_URL
        
    cache_key = f"commission_all_users_{sheet_name}"
    cached_data = cache.get(cache_key)
    
    if cached_data:
        return cached_data
    
    try:
        excel_file, error = download_excel_file(excel_url, f"comissao_{sheet_name}")
        if error:
            return {'error': error}
        
        try:
            df = pd.read_excel(excel_file, sheet_name=sheet_name, engine='openpyxl')
        except Exception as e:
            return {'error': f'Erro ao ler planilha: {str(e)}'}
        
        nome_col = None
        for col in df.columns:
            if 'NOME' in str(col).upper():
                nome_col = col
                break
        
        if nome_col is None:
            nome_col = df.columns[0]
        
        users_data = []
        for idx, row in df.iterrows():
            nome = row.get(nome_col)
            if pd.isna(nome) or not str(nome).strip():
                continue
            
            data = {}
            for col in df.columns:
                value = row.get(col)
                if pd.isna(value):
                    data[str(col)] = None
                elif isinstance(value, (int, float)):
                    data[str(col)] = float(value) if isinstance(value, float) else int(value)
                else:
                    data[str(col)] = str(value)
            
            users_data.append({
                'nome': str(nome).strip(),
                'data': data
            })
        
        result = {
            'success': True,
            'users': users_data,
            'sheet': sheet_name
        }
        
        cache.set(cache_key, result, 300)
        
        return result
        
    except Exception as e:
        return {'error': f'Erro ao processar dados: {str(e)}'}


def fetch_metas_por_pilar(user_name, is_gerente=False, user_sector=None):
    """
    Busca Total, Pago e Exclusão por pilar de sheets específicas.
    
    Para Gerente: busca por Filial (setor da loja)
    Para CN: busca por Vendedor (nome)
    
    Sheets de Pago:
    - BASE PAGA FIXA, BASE PAGA MOVEL, BASE PAGA SEGURO, 
    - BASE PAGA SVA, BASE PAGA SMARTPHONE, BASE PAGA ESSENCIAIS
    
    Sheets de Exclusão:
    - BASE DE EXCLUSÃO, BASE EXCLUSÃO PRODUTOS
    """
    # Cache key diferente para gerente vs CN
    cache_suffix = f"gerente_{user_sector}" if is_gerente else f"cn_{user_name}"
    cache_key = f"metas_pilar_{cache_suffix.replace(' ', '_')}"
    cached_data = cache.get(cache_key)
    
    if cached_data:
        return cached_data
    
    # Mapeamento de sheets para pilares (busca parcial)
    sheets_pago_mapping = {
        'MOVEL': 'movel',
        'MÓVEL': 'movel',
        'FIXA': 'fixa',
        'SMARTPHONE': 'smartphone',
        'ESSENCIAIS': 'essenciais',
        'SEGURO': 'seguro',
        'SVA': 'sva',
    }
    
    # Inicializar metas
    metas = {
        'movel': {'total': 0, 'pago': 0, 'exclusao': 0},
        'fixa': {'total': 0, 'pago': 0, 'exclusao': 0},
        'smartphone': {'total': 0, 'pago': 0, 'exclusao': 0},
        'eletronicos': {'total': 0, 'pago': 0, 'exclusao': 0},
        'essenciais': {'total': 0, 'pago': 0, 'exclusao': 0},
        'seguro': {'total': 0, 'pago': 0, 'exclusao': 0},
        'sva': {'total': 0, 'pago': 0, 'exclusao': 0},
    }
    
    # Mapeamento de texto do pilar para chave
    pilar_mapping = {
        'MOVEL': 'movel',
        'MÓVEL': 'movel',
        'MOV': 'movel',
        'FIXA': 'fixa',
        'FIX': 'fixa',
        'SMART': 'smartphone',
        'SMARTPHONE': 'smartphone',
        'ELETRO': 'eletronicos',
        'ELETRÔNICO': 'eletronicos',
        'ELETRONICOS': 'eletronicos',
        'ELETRÔNICOS': 'eletronicos',
        'ESSEN': 'essenciais',
        'ESSENCIAL': 'essenciais',
        'ESSENCIAIS': 'essenciais',
        'SEG': 'seguro',
        'SEGURO': 'seguro',
        'SEGUROS': 'seguro',
        'SVA': 'sva',
    }
    
    def normalize_text(text):
        if pd.isna(text):
            return ""
        return str(text).strip().upper()
    
    def normalize_sector(sector_name):
        """
        Normaliza nome do setor para comparação.
        Sistema: "Loja Serra Sede" -> "SERRA SEDE"
        Planilha: "SERRA SEDE"
        """
        if not sector_name:
            return ""
        normalized = str(sector_name).strip().upper()
        # Remover prefixos comuns
        prefixes = ['LOJA ', 'LOJA_', 'PDV ', 'PDV_']
        for prefix in prefixes:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):]
        # Substituições comuns
        replacements = {
            'SA PADUA': 'SANTO ANTONIO DE PADUA',
            'SÃO PADUA': 'SANTO ANTONIO DE PADUA',
            'S.A. PADUA': 'SANTO ANTONIO DE PADUA',
            'STO ANTONIO DE PADUA': 'SANTO ANTONIO DE PADUA',
        }
        for old, new in replacements.items():
            if old in normalized:
                normalized = normalized.replace(old, new)
        return normalized
    
    def sectors_match(sector1, sector2):
        """Verifica se dois setores são equivalentes"""
        s1 = normalize_sector(sector1)
        s2 = normalize_sector(sector2)
        if not s1 or not s2:
            return False
        # Comparação exata
        if s1 == s2:
            return True
        # Um contém o outro
        if s1 in s2 or s2 in s1:
            return True
        # Comparar palavras principais
        words1 = set(s1.split())
        words2 = set(s2.split())
        # Se há interseção significativa de palavras
        common = words1 & words2
        if len(common) >= 1 and len(common) >= min(len(words1), len(words2)) * 0.5:
            return True
        return False
    
    user_name_normalized = normalize_text(user_name)
    user_sector_normalized = normalize_sector(user_sector) if user_sector else ""
    
    try:
        excel_file, error = download_excel_file(EXCEL_VENDAS_URL, "vendas_metas")
        if error:
            return metas
        
        # Ler todas as sheets disponíveis
        try:
            excel_file.seek(0)
            xl = pd.ExcelFile(excel_file, engine='openpyxl')
            available_sheets = xl.sheet_names
        except Exception as e:
            return metas
        
        # Processar sheets de PAGO
        for sheet_name in available_sheets:
            sheet_upper = sheet_name.upper()
            
            # Verificar se é uma sheet de BASE PAGA
            if 'BASE' in sheet_upper and 'PAGA' in sheet_upper:
                # Identificar qual pilar
                pilar_key = None
                for pilar_text, pkey in sheets_pago_mapping.items():
                    if pilar_text in sheet_upper:
                        pilar_key = pkey
                        break
                
                if pilar_key:
                    try:
                        df = pd.read_excel(xl, sheet_name=sheet_name)
                        
                        # Encontrar coluna de Receita
                        receita_col = None
                        for col in df.columns:
                            if str(col).strip().upper() == 'RECEITA':
                                receita_col = col
                                break
                        
                        if is_gerente:
                            # GERENTE: buscar por Filial (setor)
                            filial_col = None
                            for col in df.columns:
                                if str(col).strip().upper() == 'FILIAL':
                                    filial_col = col
                                    break
                            
                            if filial_col is None:
                                continue
                            
                            # Somar Receita da filial
                            total_receita = 0
                            for idx, row in df.iterrows():
                                row_filial = str(row.get(filial_col, '')).strip()
                                if sectors_match(row_filial, user_sector):
                                    if receita_col:
                                        val = row.get(receita_col)
                                        if pd.notna(val) and isinstance(val, (int, float)):
                                            total_receita += float(val)
                                    else:
                                        total_receita += 1  # Fallback: contar se não tiver Receita
                            
                            metas[pilar_key]['pago'] += total_receita
                        else:
                            # CN: buscar por Vendedor (nome)
                            nome_col = None
                            for col in df.columns:
                                if str(col).strip().upper() == 'VENDEDOR':
                                    nome_col = col
                                    break
                            
                            if nome_col is None:
                                continue
                            
                            # Somar Receita do vendedor
                            total_receita = 0
                            for idx, row in df.iterrows():
                                row_name = normalize_text(row.get(nome_col, ''))
                                if row_name and (row_name == user_name_normalized or 
                                                user_name_normalized in row_name or 
                                                row_name in user_name_normalized):
                                    if receita_col:
                                        val = row.get(receita_col)
                                        if pd.notna(val) and isinstance(val, (int, float)):
                                            total_receita += float(val)
                                    else:
                                        total_receita += 1  # Fallback: contar se não tiver Receita
                            
                            metas[pilar_key]['pago'] += total_receita
                        
                    except Exception as e:
                        continue
        
        # Processar sheets de EXCLUSÃO
        for sheet_name in available_sheets:
            sheet_upper = sheet_name.upper()
            
            # Verificar se é uma sheet de EXCLUSÃO
            if 'EXCLUS' in sheet_upper or 'EXCLUSA' in sheet_upper:
                try:
                    df = pd.read_excel(xl, sheet_name=sheet_name)
                    
                    # Coluna de pilar
                    pilar_col = None
                    for col in df.columns:
                        if str(col).strip().upper() == 'PILAR':
                            pilar_col = col
                            break
                    
                    # Encontrar coluna de Receita
                    receita_col = None
                    for col in df.columns:
                        if str(col).strip().upper() == 'RECEITA':
                            receita_col = col
                            break
                    
                    if is_gerente:
                        # GERENTE: buscar por Filial
                        filial_col = None
                        for col in df.columns:
                            if str(col).strip().upper() == 'FILIAL':
                                filial_col = col
                                break
                        
                        if filial_col is None:
                            continue
                        
                        for idx, row in df.iterrows():
                            row_filial = str(row.get(filial_col, '')).strip()
                            if sectors_match(row_filial, user_sector):
                                if pilar_col:
                                    pilar_val = str(row.get(pilar_col, '')).strip().upper()
                                    for key, value in pilar_mapping.items():
                                        if key in pilar_val or pilar_val == key:
                                            # Somar Receita
                                            if receita_col:
                                                val = row.get(receita_col)
                                                if pd.notna(val) and isinstance(val, (int, float)):
                                                    metas[value]['exclusao'] += float(val)
                                                else:
                                                    metas[value]['exclusao'] += 1
                                            else:
                                                metas[value]['exclusao'] += 1
                                            break
                    else:
                        # CN: buscar por Vendedor
                        nome_col = None
                        for col in df.columns:
                            if str(col).strip().upper() == 'VENDEDOR':
                                nome_col = col
                                break
                        
                        if nome_col is None:
                            continue
                        
                        for idx, row in df.iterrows():
                            row_name = normalize_text(row.get(nome_col, ''))
                            if row_name and (row_name == user_name_normalized or 
                                            user_name_normalized in row_name or 
                                            row_name in user_name_normalized):
                                if pilar_col:
                                    pilar_val = str(row.get(pilar_col, '')).strip().upper()
                                    for key, value in pilar_mapping.items():
                                        if key in pilar_val or pilar_val == key:
                                            # Somar Receita
                                            if receita_col:
                                                val = row.get(receita_col)
                                                if pd.notna(val) and isinstance(val, (int, float)):
                                                    metas[value]['exclusao'] += float(val)
                                                else:
                                                    metas[value]['exclusao'] += 1
                                            else:
                                                metas[value]['exclusao'] += 1
                                            break
                    
                except Exception as e:
                    continue
        
        # Calcular Total = Pago + Exclusão para cada pilar
        for pilar_key in metas:
            metas[pilar_key]['total'] = metas[pilar_key]['pago'] + metas[pilar_key]['exclusao']
        
        cache.set(cache_key, metas, 300)
        
        return metas
        
    except Exception as e:
        return metas


def fetch_vendas_data(user_name):
    """
    Busca dados de vendas do usuário da planilha de vendas e metas
    """
    cache_key = f"vendas_data_{user_name.replace(' ', '_')}"
    cached_data = cache.get(cache_key)
    
    if cached_data:
        return cached_data
    
    try:
        excel_file, error = download_excel_file(EXCEL_VENDAS_URL, "vendas")
        if error:
            return {'error': error, 'vendas': [], 'metas_pilar': {}}
        
        try:
            # Tentar ler a primeira sheet se SHEET_VENDAS não existir
            try:
                df = pd.read_excel(excel_file, sheet_name=SHEET_VENDAS, engine='openpyxl')
            except:
                excel_file.seek(0)
                df = pd.read_excel(excel_file, sheet_name=0, engine='openpyxl')
        except Exception as e:
            return {'error': f'Erro ao ler planilha de vendas: {str(e)}', 'vendas': [], 'metas_pilar': {}}
        
        # Encontrar coluna de nome
        nome_col = None
        for col in df.columns:
            col_upper = str(col).upper()
            if 'NOME' in col_upper or 'VENDEDOR' in col_upper or 'CN' in col_upper:
                nome_col = col
                break
        
        if nome_col is None:
            nome_col = df.columns[0]
        
        def normalize_name(name):
            if pd.isna(name):
                return ""
            return str(name).strip().upper()
        
        user_name_normalized = normalize_name(user_name)
        
        # Buscar todas as linhas do usuário (pode ter múltiplas vendas)
        user_rows = []
        for idx, row in df.iterrows():
            row_name = normalize_name(row.get(nome_col, ''))
            if row_name == user_name_normalized or user_name_normalized in row_name or row_name in user_name_normalized:
                row_data = {}
                for col in df.columns:
                    value = row.get(col)
                    if pd.isna(value):
                        row_data[str(col)] = None
                    elif isinstance(value, (int, float)):
                        row_data[str(col)] = float(value) if isinstance(value, float) else int(value)
                    else:
                        row_data[str(col)] = str(value)
                user_rows.append(row_data)
        
        # Buscar metas por pilar das sheets específicas (Total, Pago, Exclusão)
        metas_pilar = fetch_metas_por_pilar(user_name)
        
        result = {
            'success': True,
            'vendas': user_rows,
            'total_vendas': len(user_rows),
            'metas_pilar': metas_pilar
        }
        
        cache.set(cache_key, result, 300)
        
        return result
        
    except Exception as e:
        return {'error': f'Erro ao processar vendas: {str(e)}', 'vendas': [], 'metas_pilar': {}}


def fetch_all_vendas():
    """
    Busca todas as vendas da planilha
    """
    cache_key = "vendas_all_data"
    cached_data = cache.get(cache_key)
    
    if cached_data:
        return cached_data
    
    try:
        excel_file, error = download_excel_file(EXCEL_VENDAS_URL, "vendas_all")
        if error:
            return {'error': error, 'vendas': [], 'colunas': []}
        
        try:
            try:
                df = pd.read_excel(excel_file, sheet_name=SHEET_VENDAS, engine='openpyxl')
            except:
                excel_file.seek(0)
                df = pd.read_excel(excel_file, sheet_name=0, engine='openpyxl')
        except Exception as e:
            return {'error': f'Erro ao ler planilha de vendas: {str(e)}', 'vendas': [], 'colunas': []}
        
        all_vendas = []
        for idx, row in df.iterrows():
            row_data = {}
            for col in df.columns:
                value = row.get(col)
                if pd.isna(value):
                    row_data[str(col)] = None
                elif isinstance(value, (int, float)):
                    row_data[str(col)] = float(value) if isinstance(value, float) else int(value)
                else:
                    row_data[str(col)] = str(value)
            all_vendas.append(row_data)
        
        result = {
            'success': True,
            'vendas': all_vendas,
            'colunas': list(df.columns),
            'total': len(all_vendas)
        }
        
        cache.set(cache_key, result, 300)
        
        return result
        
    except Exception as e:
        return {'error': f'Erro ao processar vendas: {str(e)}', 'vendas': [], 'colunas': []}


# ============================================================================
# FUNÇÕES DE PROCESSAMENTO DE DADOS
# ============================================================================

def safe_float(value, default=0):
    """Converte valor para float de forma segura"""
    if value is None:
        return default
    try:
        return float(value)
    except:
        return default


def convert_percentage(value):
    """
    Converte valor decimal para percentual se necessário.
    Valores <= 3 são considerados decimais (ex: 0.92 = 92%, 1.5 = 150%)
    """
    if value is None or value == 0:
        return 0
    if value <= 3:
        return value * 100
    return value


def get_month_names():
    """
    Retorna os nomes dos meses M1, M2, M3 baseado no mês atual.
    """
    from datetime import datetime
    
    meses_pt = {
        1: 'Janeiro', 2: 'Fevereiro', 3: 'Março', 4: 'Abril',
        5: 'Maio', 6: 'Junho', 7: 'Julho', 8: 'Agosto',
        9: 'Setembro', 10: 'Outubro', 11: 'Novembro', 12: 'Dezembro'
    }
    
    hoje = datetime.now()
    mes_atual = hoje.month
    
    def get_month_back(months_back):
        m = mes_atual - months_back
        if m <= 0:
            m += 12
        return m
    
    return {
        'm1': meses_pt[get_month_back(2)],
        'm2': meses_pt[get_month_back(3)],
        'm3': meses_pt[get_month_back(4)],
    }


def process_commission_data(data, is_gerente=False, metas_pilar=None):
    """
    Processa os dados brutos da planilha e organiza em seções.
    metas_pilar: dict com {pilar_key: {'total': x, 'pago': y, 'exclusao': z}}
    """
    if metas_pilar is None:
        metas_pilar = {}
    
    processed = {
        'info': {
            'nome': data.get('NOME', ''),
            'cargo': data.get('CARGO', ''),
            'pdv': data.get('PDV', ''),
            'coordenador': data.get('COORDENADOR (A)', ''),
        },
        'pilares': [],
        'comissoes': {},
        'bonus': {},
        'alto_desempenho': {},
        'hunter': {},
        'aceleradores': {},
        'descontos': {},
        'totais': {},
        'charts': {}
    }
    
    # Mapeamento de chave para metas_pilar
    pilar_to_meta_key = {
        'MOVEL': 'movel',
        'FIXA': 'fixa',
        'SMART': 'smartphone',
        'ELETRO': 'eletronicos',
        'ESSEN': 'essenciais',
        'SEG': 'seguro',
        'SVA': 'sva',
    }
    
    # Pilares com atingimentos
    pilares_config = [
        {'nome': 'Móvel', 'key': 'MOVEL', 'icon': '📱', 'color': '#660099'},
        {'nome': 'Fixa', 'key': 'FIXA', 'icon': '🏠', 'color': '#9B30FF'},
        {'nome': 'Smartphone', 'key': 'SMART', 'icon': '📲', 'color': '#BA55D3'},
        {'nome': 'Eletrônicos', 'key': 'ELETRO', 'icon': '💻', 'color': '#9370DB'},
        {'nome': 'Essenciais', 'key': 'ESSEN', 'icon': '⭐', 'color': '#8A2BE2'},
        {'nome': 'Seguro', 'key': 'SEG', 'icon': '🛡️', 'color': '#7B68EE'},
        {'nome': 'SVA', 'key': 'SVA', 'icon': '📦', 'color': '#6A5ACD'},
    ]
    
    for pilar in pilares_config:
        key = pilar['key']
        cart_key = f'ATING_CART_{key}' if is_gerente else f'ATING_{key}_PDV'
        
        pct_3_raw = safe_float(data.get(f'%ATING_{key}_3'))
        pct_2_raw = safe_float(data.get(f'%ATING_{key}_2'))
        pct_1_raw = safe_float(data.get(f'%ATING_{key}_1'))
        
        # Buscar metas (Total, Pago, Exclusão) deste pilar
        meta_key = pilar_to_meta_key.get(key, key.lower())
        pilar_metas = metas_pilar.get(meta_key, {'total': 0, 'pago': 0, 'exclusao': 0})
        
        pilar_data = {
            'nome': pilar['nome'],
            'icon': pilar['icon'],
            'color': pilar['color'],
            'pct_3': convert_percentage(pct_3_raw),
            'pct_2': convert_percentage(pct_2_raw),
            'pct_1': convert_percentage(pct_1_raw),
            'carteira': safe_float(data.get(cart_key) or data.get(f'ATING_CART_{key}')),
            'habilitado': data.get(f'H_{pilar["nome"].upper()}') or data.get(f'H_{key}'),
            # Dados de metas: Total, Pago, Exclusão
            'total': pilar_metas.get('total', 0),
            'pago': pilar_metas.get('pago', 0),
            'exclusao': pilar_metas.get('exclusao', 0),
        }
        
        ating_values = [pilar_data['pct_3'], pilar_data['pct_2'], pilar_data['pct_1']]
        valid_values = [v for v in ating_values if v > 0]
        pilar_data['media'] = sum(valid_values) / len(valid_values) if valid_values else 0
        
        processed['pilares'].append(pilar_data)
    
    # Comissões por pilar
    comissoes_valores = {
        'movel': safe_float(data.get('COM_MÓVEL')),
        'fixa': safe_float(data.get('COM_FIXA')),
        'smartphone': safe_float(data.get('COM_SMARTPHONE')),
        'eletronicos_a': safe_float(data.get('COM_ELETRONICOS - A')),
        'eletronicos_b': safe_float(data.get('COM_ELETRONICOS - B')),
        'essenciais_a': safe_float(data.get('COM_ESSENCIAIS - A')),
        'essenciais_b': safe_float(data.get('COM_ESSENCIAIS - B')),
        'seguro': safe_float(data.get('COM_SEGURO')),
        'sva': safe_float(data.get('COM_SVA')),
    }
    total_comissoes = safe_float(data.get('Total Comissão'))
    if total_comissoes == 0:
        total_comissoes = sum(comissoes_valores.values())
    comissoes_valores['total'] = total_comissoes
    processed['comissoes'] = comissoes_valores
    
    # Bônus Carteira - nomes exatos das colunas da planilha REMUNERAÇÃO GERENTE/CN
    bonus_valores = {
        'movel': safe_float(data.get('BONUS_CARTEIRA_MÓVEL')),
        'fixa': safe_float(data.get('BONU_CARTEIRA_FIXA')),  # Nota: BONU sem S (nome real na planilha)
        'smartphone': safe_float(data.get('BONUTS_CARTEIRA_SMARTPHONE')),
        'eletronicos_a': safe_float(data.get('BONUS_CARTEIRA_ELETRONICOS - A')),
        'eletronicos_b': safe_float(data.get('BONUS_CARTEIRA_ELETRONICOS - B')),
        'essenciais_a': safe_float(data.get('BONUS_CARTEIRA_ESSENCIAIS - A')),
        'essenciais_b': safe_float(data.get('BONUS_CARTEIRA_ESSENCIAIS - B')),
        'seguro': safe_float(data.get('BONUS_CARTEIRA_SEGURO')),
        'sva': safe_float(data.get('BONUS_CARTEIRA_SVA')),
    }
    # Juntar eletrônicos e essenciais em valores únicos para exibição
    bonus_valores['eletronicos'] = bonus_valores['eletronicos_a'] + bonus_valores['eletronicos_b']
    bonus_valores['essenciais'] = bonus_valores['essenciais_a'] + bonus_valores['essenciais_b']
    
    # Total Bônus Carteira = TOTAL BONUS LOJA (soma dos valores individuais de bônus carteira)
    total_bonus = safe_float(data.get('TOTAL BONUS LOJA'))
    if total_bonus == 0:
        total_bonus = sum([bonus_valores['movel'], bonus_valores['fixa'], bonus_valores['smartphone'], 
                          bonus_valores['eletronicos'], bonus_valores['essenciais'], 
                          bonus_valores['seguro'], bonus_valores['sva']])
    bonus_valores['total'] = total_bonus
    processed['bonus'] = bonus_valores
    
    # Alto Desempenho
    alto_desempenho_valores = {
        'movel': safe_float(data.get('ALTO_DESEM_MÓVEL')),
        'fixa': safe_float(data.get('ALTO_DESEM_FIXA')),
        'smartphone': safe_float(data.get('ALTO_DESEM_SMARTPHONE')),
        'eletronicos_a': safe_float(data.get('ALTO_DESEM_ELETRONICOS - A')),
        'eletronicos_b': safe_float(data.get('ALTO_DESEM_ELETRONICOS - B')),
        'essenciais_a': safe_float(data.get('ALTO_DESEM_ESSENCIAIS - A')),
        'essenciais_b': safe_float(data.get('ALTO_DESEM_ESSENCIAIS - B')),
        'seguro': safe_float(data.get('ALTO_DESEM_SEGURO')),
        'sva': safe_float(data.get('ALTO_DESEM_SVA')),
    }
    total_alto_desempenho = safe_float(data.get('TOTAL PREMIAÇÃO ALTO DESEMPENHO'))
    if total_alto_desempenho == 0:
        total_alto_desempenho = sum(alto_desempenho_valores.values())
    alto_desempenho_valores['total'] = total_alto_desempenho
    processed['alto_desempenho'] = alto_desempenho_valores
    
    # Hunter
    processed['hunter'] = {
        'movel': safe_float(data.get('HUNTER_MOVEL')),
        'fixa': safe_float(data.get('HUNTER_FIXA')),
        'smartphone': safe_float(data.get('HUNTER_SMARTPHONE')),
        'eletronicos': safe_float(data.get('HUNTER_ELETRONICOS')),
        'essenciais': safe_float(data.get('HUNTER_ESSENCIAIS')),
        'seguros': safe_float(data.get('HUNTER_SEGUROS')),
        'sva': safe_float(data.get('HUNTER_SVA')),
    }
    
    # Aceleradores e IQ
    processed['aceleradores'] = {
        'seis_pilares': safe_float(data.get('ACELERADOR_6 PILARES')),
        'total_aceleradores': safe_float(data.get('REMUNERAÇÃO TOTAL + ACELERADORES')),
        'iq_acelerador_movel': safe_float(data.get('IQ_ACELADOR MÓVEL')),
        'iq_acelerador_fixa': safe_float(data.get('IQ_ACELERADOR FIXA')),
        'iq_deflator_movel': safe_float(data.get('IQ_DEFLATOR MÓVEL')),
        'iq_deflator_fixa': safe_float(data.get('IQ_DEFLATOR FIXA')),
        'remuneracao_iq': safe_float(data.get('REMUNERÇÃO COM IQ')),
    }
    
    # Descontos
    processed['descontos'] = {
        'advertencia': safe_float(data.get('DESCONTO_ADVERTÊNCIA')),
        'excedente': safe_float(data.get('DESC_PAGAMENTO EXCEDENTE DO MÊS ANTERIOR')),
        'price': safe_float(data.get('DESCONTO_PRICE')),
        'total': safe_float(data.get('DESCONTO_TOTAL')),
    }
    
    # Totais
    if is_gerente:
        processed['totais'] = {
            'comissao_cargo': safe_float(data.get('COMISSÃO_GERENTE')),
            'premiacao_cargo': safe_float(data.get('PREMIAÇÃO_GERENTE')),
            'iq_cargo': safe_float(data.get('IQ_GERENTE')),
            'remuneracao_cargo': safe_float(data.get('REMUNERAÇÃO FINAL GERENTE')),
            'total_comissao_premiacao': safe_float(data.get('TOTAL COMISSÃO + PREMIAÇÃO')),
            'remuneracao_final': safe_float(data.get('REMUNERAÇÃO FINAL TOTAL')),
            'tfp_movel': safe_float(data.get('TFP MÓVEL')),
            'tfp_fixa': safe_float(data.get('TFP FIXA')),
        }
        processed['totais']['cargo_label'] = 'Gerente'
    else:
        processed['totais'] = {
            'comissao_cargo': safe_float(data.get('COMISSÃO_CN')),
            'premiacao_cargo': safe_float(data.get('PREMIAÇÃO_CN')),
            'iq_cargo': safe_float(data.get('CN_PLENO')),
            'remuneracao_cargo': safe_float(data.get('REMUNERAÇÃO_FINAL')),
            'total_comissao_premiacao': safe_float(data.get('TOTAL COMISSÃO + PREMIAÇÃO')),
            'remuneracao_final': safe_float(data.get('REMUNERAÇÃO_FINAL_TOTAL')),
            'tfp_movel': safe_float(data.get('TFP MÓVEL')),
            'tfp_fixa': safe_float(data.get('TFP FIXA')),
            'cn_pleno': safe_float(data.get('CN PLENO')),
            'cn_lider': safe_float(data.get('CN LÍDER')),
            'campanha': safe_float(data.get('CAMPANHA')),
        }
        processed['totais']['cargo_label'] = 'CN'
    
    processed['habilitados'] = {
        'pilares_67': data.get('6/7PILARES'),
    }
    
    # Dados para gráficos
    processed['charts'] = {
        'atingimentos': {
            'labels': [p['nome'] for p in processed['pilares']],
            'data': [p['media'] for p in processed['pilares']],
            'colors': [p['color'] for p in processed['pilares']],
        },
        'comissoes': {
            'labels': ['Móvel', 'Fixa', 'Smart', 'Eletro', 'Essen', 'Seguro', 'SVA'],
            'data': [
                processed['comissoes']['movel'],
                processed['comissoes']['fixa'],
                processed['comissoes']['smartphone'],
                processed['comissoes']['eletronicos_a'] + processed['comissoes']['eletronicos_b'],
                processed['comissoes']['essenciais_a'] + processed['comissoes']['essenciais_b'],
                processed['comissoes']['seguro'],
                processed['comissoes']['sva'],
            ],
        },
        'composicao': {
            'labels': ['Comissão', 'Bônus', 'Alto Desempenho', 'Hunter', 'Aceleradores'],
            'data': [
                processed['comissoes']['total'],
                processed['bonus']['total'],
                processed['alto_desempenho']['total'],
                sum(processed['hunter'].values()),
                processed['aceleradores']['seis_pilares'],
            ],
        },
    }
    
    processed['raw_data'] = data
    
    return processed


def process_simple_commission_data(data, is_gerente=False):
    """
    Versão simplificada para listar múltiplos usuários
    """
    pilares_config = [
        {'nome': 'Móvel', 'key': 'MOVEL'},
        {'nome': 'Fixa', 'key': 'FIXA'},
        {'nome': 'Smartphone', 'key': 'SMART'},
        {'nome': 'Eletrônicos', 'key': 'ELETRO'},
        {'nome': 'Essenciais', 'key': 'ESSEN'},
        {'nome': 'Seguro', 'key': 'SEG'},
        {'nome': 'SVA', 'key': 'SVA'},
    ]
    
    pilares = []
    for pilar in pilares_config:
        key = pilar['key']
        pct_1_raw = safe_float(data.get(f'%ATING_{key}_1'))
        pilares.append({
            'nome': pilar['nome'],
            'pct': convert_percentage(pct_1_raw),
        })
    
    return {
        'nome': data.get('NOME', ''),
        'pdv': data.get('PDV', ''),
        'pilares': pilares,
        'remuneracao_final': safe_float(data.get('REMUNERAÇÃO_FINAL_TOTAL') or data.get('REMUNERAÇÃO FINAL TOTAL')),
        'comissao_total': safe_float(data.get('Total Comissão')),
    }


def process_coordenador_commission_data(data):
    """
    Processa os dados de comissionamento específicos para Coordenador.
    A planilha de COO/SNP tem colunas diferentes da de Gerente/CN.
    """
    processed = {
        'info': {
            'nome': data.get('NOME', ''),
            'cargo': 'Coordenador',
        },
        'pilares': [],
        'comissoes': {},
        'bonus': {},
        'alto_desempenho': {},
        'hunter': {},
        'aceleradores': {},
        'descontos': {},
        'totais': {},
        'charts': {}
    }
    
    # Pilares com atingimentos
    pilares_config = [
        {'nome': 'Móvel', 'key': 'MOVEL', 'icon': '📱', 'color': '#660099'},
        {'nome': 'Fixa', 'key': 'FIXA', 'icon': '🏠', 'color': '#9B30FF'},
        {'nome': 'Smartphone', 'key': 'SMART', 'icon': '📲', 'color': '#BA55D3'},
        {'nome': 'Eletrônicos', 'key': 'ELETRO', 'icon': '💻', 'color': '#9370DB'},
        {'nome': 'Essenciais', 'key': 'ESSEN', 'icon': '⭐', 'color': '#8A2BE2'},
        {'nome': 'Seguro', 'key': 'SEG', 'icon': '🛡️', 'color': '#7B68EE'},
        {'nome': 'SVA', 'key': 'SVA', 'icon': '📦', 'color': '#6A5ACD'},
    ]
    
    for pilar in pilares_config:
        key = pilar['key']
        
        pct_3_raw = safe_float(data.get(f'%ATING_{key}_3'))
        pct_2_raw = safe_float(data.get(f'%ATING_{key}_2'))
        pct_1_raw = safe_float(data.get(f'%ATING_{key}_1'))
        
        pilar_data = {
            'nome': pilar['nome'],
            'icon': pilar['icon'],
            'color': pilar['color'],
            'pct_3': convert_percentage(pct_3_raw),
            'pct_2': convert_percentage(pct_2_raw),
            'pct_1': convert_percentage(pct_1_raw),
            'habilitado': data.get(f'H_{pilar["nome"].upper()}') or data.get(f'H_{key}'),
            # Coordenador não tem Total/Pago/Exclusão por pilar
            'total': 0,
            'pago': 0,
            'exclusao': 0,
        }
        
        ating_values = [pilar_data['pct_3'], pilar_data['pct_2'], pilar_data['pct_1']]
        valid_values = [v for v in ating_values if v > 0]
        pilar_data['media'] = sum(valid_values) / len(valid_values) if valid_values else 0
        
        processed['pilares'].append(pilar_data)
    
    # Comissões por pilar
    comissoes_valores = {
        'movel': safe_float(data.get('COM_MÓVEL')),
        'fixa': safe_float(data.get('COM_FIXA')),
        'smartphone': safe_float(data.get('COM_SMARTPHONE')),
        'eletronicos_a': safe_float(data.get('COM_ELETRONICOS - A')),
        'eletronicos_b': safe_float(data.get('COM_ELETRONICOS - B')),
        'essenciais_a': safe_float(data.get('COM_ESSENCIAIS - A')),
        'essenciais_b': safe_float(data.get('COM_ESSENCIAIS - B')),
        'seguro': safe_float(data.get('COM_SEGURO')),
        'sva': safe_float(data.get('COM_SVA')),
    }
    # Total de comissões = REMUNERÇÃO PILARES
    total_comissoes = safe_float(data.get('REMUNERÇÃO PILARES'))
    if total_comissoes == 0:
        total_comissoes = sum(comissoes_valores.values())
    comissoes_valores['total'] = total_comissoes
    processed['comissoes'] = comissoes_valores
    
    # Coordenador não tem Bônus Carteira separado
    processed['bonus'] = {
        'movel': 0, 'fixa': 0, 'smartphone': 0,
        'eletronicos_a': 0, 'eletronicos_b': 0, 'eletronicos': 0,
        'essenciais_a': 0, 'essenciais_b': 0, 'essenciais': 0,
        'seguro': 0, 'sva': 0, 'total': 0
    }
    
    # Coordenador não tem Alto Desempenho separado
    processed['alto_desempenho'] = {
        'movel': 0, 'fixa': 0, 'smartphone': 0,
        'eletronicos_a': 0, 'eletronicos_b': 0,
        'essenciais_a': 0, 'essenciais_b': 0,
        'seguro': 0, 'sva': 0, 'total': 0
    }
    
    # Hunter
    processed['hunter'] = {
        'movel': safe_float(data.get('HUNTER_MOVEL')),
        'fixa': safe_float(data.get('HUNTER_FIXA')),
        'smartphone': safe_float(data.get('HUNTER_SMARTPHONE')),
        'eletronicos': safe_float(data.get('HUNTER_ELETRONICOS')),
        'essenciais': safe_float(data.get('HUNTER_ESSENCIAIS')),
        'seguros': safe_float(data.get('HUNTER_SEGUROS')),
        'sva': safe_float(data.get('HUNTER_SVA')),
    }
    
    # Aceleradores e IQ
    processed['aceleradores'] = {
        'seis_pilares': safe_float(data.get('6/7_PILARES')),
        'campanha': safe_float(data.get('CAMPANHA')),
        'total_aceleradores': safe_float(data.get('REMUNERÇÃO BRUTA')),
        'iq_acelerador_movel': safe_float(data.get('IQ_ACELADOR MÓVEL')),
        'iq_acelerador_fixa': safe_float(data.get('IQ_ACELERADOR FIXA')),
        'iq_deflator_movel': safe_float(data.get('IQ_DEFLATOR MÓVEL')),
        'iq_deflator_fixa': safe_float(data.get('IQ_DEFLATOR FIXA')),
        'remuneracao_iq': safe_float(data.get('REMUNERÇÃO COM IQ')),
    }
    
    # Descontos
    processed['descontos'] = {
        'advertencia': safe_float(data.get('DESCONTO_ADVERTÊNCIA')),
        'excedente': safe_float(data.get('DESC_PAGAMENTO EXCEDENTE DO MÊS ANTERIOR')),
        'price': safe_float(data.get('DESCONTO_PRICE')),
        'total': safe_float(data.get('DESCONTO_TOTAL')),
    }
    
    # Totais específicos de Coordenador
    processed['totais'] = {
        'comissao_cargo': safe_float(data.get('COMISSÃO_COO/SNP')),
        'premiacao_cargo': safe_float(data.get('PREMIAÇÃO_COO/SNP')),
        'remuneracao_pilares': safe_float(data.get('REMUNERÇÃO PILARES')),
        'remuneracao_bruta': safe_float(data.get('REMUNERÇÃO BRUTA')),
        'remuneracao_iq': safe_float(data.get('REMUNERÇÃO COM IQ')),
        'remuneracao_final': safe_float(data.get(' REMUNERAÇÃO FINAL COO/SNP')),  # Nota: tem espaço no início
        'cargo_label': 'Coordenador',
    }
    
    processed['habilitados'] = {
        'pilares_67': data.get('M|F|T|A'),
    }
    
    # Dados para gráficos
    processed['charts'] = {
        'atingimentos': {
            'labels': [p['nome'] for p in processed['pilares']],
            'data': [p['media'] for p in processed['pilares']],
            'colors': [p['color'] for p in processed['pilares']],
        },
        'comissoes': {
            'labels': ['Móvel', 'Fixa', 'Smart', 'Eletro', 'Essen', 'Seguro', 'SVA'],
            'data': [
                processed['comissoes']['movel'],
                processed['comissoes']['fixa'],
                processed['comissoes']['smartphone'],
                processed['comissoes']['eletronicos_a'] + processed['comissoes']['eletronicos_b'],
                processed['comissoes']['essenciais_a'] + processed['comissoes']['essenciais_b'],
                processed['comissoes']['seguro'],
                processed['comissoes']['sva'],
            ],
        },
        'composicao': {
            'labels': ['Comissão', 'Hunter', 'Campanha'],
            'data': [
                processed['comissoes']['total'],
                sum(processed['hunter'].values()),
                processed['aceleradores']['campanha'],
            ],
        },
    }
    
    processed['raw_data'] = data
    
    return processed


def fetch_lojas_por_coordenador(coordenador_nome):
    """
    Busca as lojas (PDVs) que pertencem a um coordenador específico.
    Lê a sheet REMUNERAÇÃO GERENTE e filtra pela coluna COORDENADOR (A).
    """
    cache_key = f"lojas_coordenador_{coordenador_nome.replace(' ', '_')}"
    cached_data = cache.get(cache_key)
    
    if cached_data:
        return cached_data
    
    try:
        excel_file, error = download_excel_file(EXCEL_COMISSAO_URL, "lojas_coord")
        if error:
            return []
        
        df = pd.read_excel(excel_file, sheet_name=SHEET_GERENTE, engine='openpyxl')
        
        coord_col = None
        for col in df.columns:
            if 'COORDENADOR' in str(col).upper():
                coord_col = col
                break
        
        if coord_col is None:
            return []
        
        def normalize_name(name):
            if pd.isna(name):
                return ""
            return str(name).strip().upper()
        
        coord_normalized = normalize_name(coordenador_nome)
        
        lojas = []
        for idx, row in df.iterrows():
            row_coord = normalize_name(row.get(coord_col, ''))
            # Comparar primeiro nome
            if row_coord and (row_coord == coord_normalized or 
                             coord_normalized in row_coord or 
                             row_coord in coord_normalized):
                pdv = row.get('PDV', '')
                gerente = row.get('NOME', '')
                if pd.notna(pdv) and str(pdv).strip():
                    print(str(gerente).strip())
                    print(pd.notna(gerente))
                    lojas.append({
                        'pdv': str(pdv).strip(),
                        'gerente': str(gerente).strip() if pd.notna(gerente) else '',
                    })
        
        cache.set(cache_key, lojas, 300)
        return lojas
        
    except Exception as e:
        return []


# ============================================================================
# VIEWS
# ============================================================================

@login_required
def commission_view(request):
    """
    View principal para exibir dados de comissionamento
    Redireciona para a visão apropriada baseado no tipo de usuário
    """
    user = request.user
    role = get_user_role(user)
    
    # Coordenador tem visão especial
    if role == 'coordenador':
        return commission_coordenador_view(request)
    
    # Gerente pode ver equipe ou seu próprio
    if role == 'gerente':
        return commission_gerente_view(request)
    
    # CN padrão
    return commission_cn_view(request)


@login_required
def commission_cn_view(request):
    """
    Visão do CN:
    - Vê seu comissionamento
    - Valores por pilar
    - Lista de vendas
    """
    user = request.user
    
    # Determina qual sheet usar
    is_gerente = is_user_gerente(user)
    sheet_name = SHEET_GERENTE if is_gerente else SHEET_CN
    
    # Nome para busca na planilha
    user_full_name = user.get_full_name() or user.first_name
    
    # Setor do usuário
    user_sector = user.sector.name if hasattr(user, 'sector') and user.sector else None
    
    # Buscar dados de comissionamento
    result = fetch_excel_data(sheet_name, user_full_name)
    
    # Buscar dados de vendas
    vendas_result = fetch_vendas_data(user_full_name)
    
    # Obter nomes dos meses
    meses = get_month_names()
    
    context = {
        'user': user,
        'target_user': user,
        'viewing_other': False,
        'is_gerente': is_gerente,
        'target_is_gerente': is_gerente,
        'sector_users': [],
        'result': result,
        'meses': meses,
        'vendas': vendas_result.get('vendas', []) if vendas_result.get('success') else [],
        'role': 'cn',
    }
    
    if result.get('success'):
        # Obter metas por pilar - CN busca por nome, Gerente busca por filial
        metas_pilar = fetch_metas_por_pilar(user_full_name, is_gerente, user_sector)
        processed = process_commission_data(result['data'], is_gerente, metas_pilar)
        context['data'] = processed
        context['charts_json'] = json.dumps(processed['charts'])
    
    return render(request, 'users/commission.html', context)


@login_required
def commission_gerente_view(request):
    """
    Visão do Gerente:
    - Consegue ver todos os CNs da loja
    - Consegue ver valores que cada CN está gerando
    - Atingimentos por pilar
    """
    user = request.user
    
    # Verificar se está visualizando um CN específico
    viewing_user_id = request.GET.get('user')
    viewing_user = None
    
    # Buscar CNs do setor
    sector_cns = get_all_cns_for_gerente(user)
    
    if viewing_user_id:
        try:
            viewing_user = User.objects.get(id=viewing_user_id, is_active=True)
            # Verificar se o CN é do mesmo setor
            if viewing_user.sector != user.sector:
                messages.error(request, 'Este usuário não pertence à sua loja.')
                return redirect('commission')
        except User.DoesNotExist:
            messages.error(request, 'Usuário não encontrado.')
            return redirect('commission')
    
    # Define qual usuário buscar dados
    if viewing_user:
        target_user = viewing_user
        target_is_gerente = is_user_gerente(viewing_user)
    else:
        target_user = user
        target_is_gerente = True
    
    sheet_name = SHEET_GERENTE if target_is_gerente else SHEET_CN
    user_full_name = target_user.get_full_name() or target_user.first_name
    
    # Buscar dados de comissionamento
    result = fetch_excel_data(sheet_name, user_full_name)
    
    # Buscar resumo de todos os CNs da loja
    cns_resumo = []
    all_cns_result = fetch_all_users_from_sheet(SHEET_CN)
    
    if all_cns_result.get('success'):
        # Filtrar apenas CNs do mesmo setor
        for cn_data in all_cns_result['users']:
            # Buscar usuário no sistema pelo nome
            cn_nome = cn_data['nome'].upper()
            for cn in sector_cns:
                cn_full_name = (cn.get_full_name() or cn.first_name).upper()
                if cn_full_name in cn_nome or cn_nome in cn_full_name:
                    processed = process_simple_commission_data(cn_data['data'])
                    processed['user_id'] = cn.id
                    processed['user_obj'] = cn
                    cns_resumo.append(processed)
                    break
    
    meses = get_month_names()
    
    context = {
        'user': user,
        'target_user': target_user,
        'viewing_other': viewing_user is not None,
        'is_gerente': True,
        'target_is_gerente': target_is_gerente,
        'sector_users': sector_cns,
        'cns_resumo': cns_resumo,
        'result': result,
        'meses': meses,
        'role': 'gerente',
    }
    
    if result.get('success'):
        # Buscar metas por pilar - Gerente busca por filial, CN busca por nome
        target_name = target_user.get_full_name() or target_user.first_name
        target_sector = target_user.sector.name if hasattr(target_user, 'sector') and target_user.sector else None
        metas_pilar = fetch_metas_por_pilar(target_name, target_is_gerente, target_sector)
        processed = process_commission_data(result['data'], target_is_gerente, metas_pilar)
        context['data'] = processed
        context['charts_json'] = json.dumps(processed['charts'])
    
    return render(request, 'users/commission_gerente.html', context)


@login_required
def commission_coordenador_view(request):
    """
    Visão do Coordenador:
    - Consegue ver todos os gerentes e CNs
    - Consegue ver atingimento das lojas
    """
    user = request.user
    
    # Verificar se está visualizando um usuário específico
    viewing_user_id = request.GET.get('user')
    viewing_user = None
    
    if viewing_user_id:
        try:
            viewing_user = User.objects.get(id=viewing_user_id, is_active=True)
        except User.DoesNotExist:
            messages.error(request, 'Usuário não encontrado.')
            return redirect('commission')
    
    # Primeiro nome do coordenador para filtrar
    user_first_name = user.first_name.strip().upper() if user.first_name else ''
    
    # Buscar lojas deste coordenador específico da planilha
    lojas_coordenador = fetch_lojas_por_coordenador(user_first_name)
    pdvs_coordenador = [l['pdv'].upper() for l in lojas_coordenador]
    
    # Buscar dados de todos os gerentes e filtrar pelos que pertencem a este coordenador
    gerentes_data = []
    all_gerentes_result = fetch_all_users_from_sheet(SHEET_GERENTE)
    
    if all_gerentes_result.get('success'):
        for gerente_data in all_gerentes_result['users']:
            pdv = str(gerente_data['data'].get('PDV', '')).strip().upper()
            coord = str(gerente_data['data'].get('COORDENADOR (A)', '')).strip().upper()
            
            # Filtrar apenas gerentes deste coordenador
            if user_first_name in coord or pdv in pdvs_coordenador:
                processed = process_simple_commission_data(gerente_data['data'], is_gerente=True)
                processed['pdv'] = gerente_data['data'].get('PDV', '')
                processed['coordenador'] = gerente_data['data'].get('COORDENADOR (A)', '')
                gerentes_data.append(processed)
    
    # Criar lojas diretamente a partir de gerentes_data (mesma fonte que Desempenho dos Gerentes)
    lojas = []
    for gerente in gerentes_data:
        pdv = gerente.get('pdv', '')
        nome = gerente.get('nome', '')
        # Buscar o gerente no Django pelo nome
        gerente_user = None
        cns = []
        sector = None
        
        if nome:
            # Tentar encontrar o usuário pelo nome
            nome_parts = nome.split()
            if len(nome_parts) >= 2:
                gerente_user = User.objects.filter(
                    first_name__iexact=nome_parts[0],
                    last_name__icontains=nome_parts[-1],
                    is_active=True
                ).first()
            if not gerente_user:
                gerente_user = User.objects.filter(
                    first_name__iexact=nome_parts[0] if nome_parts else nome,
                    is_active=True
                ).first()
            
            if gerente_user and gerente_user.sector:
                sector = gerente_user.sector
                cns = User.objects.filter(
                    sector=sector,
                    is_active=True
                ).exclude(
                    groups__name__in=['Gerente', 'Gerentes', 'Coordenador', 'Coordenadores']
                )
        
        lojas.append({
            'pdv': pdv,
            'nome_gerente': nome,
            'setor': sector,
            'gerente': gerente_user,
            'cns': cns,
            'gerente_data': gerente,  # Dados da planilha
        })
    
    # Se visualizando usuário específico
    if viewing_user:
        target_is_gerente = is_user_gerente(viewing_user)
        sheet_name = SHEET_GERENTE if target_is_gerente else SHEET_CN
        user_full_name = viewing_user.get_full_name() or viewing_user.first_name
        result = fetch_excel_data(sheet_name, user_full_name)
    else:
        # Coordenador visualizando seus próprios dados
        # Na planilha de coordenador, busca pelo PRIMEIRO NOME apenas
        sheet_name = SHEET_COORDENADOR
        result = fetch_excel_data(sheet_name, user_first_name)
    
    meses = get_month_names()
    
    context = {
        'user': user,
        'target_user': viewing_user if viewing_user else user,
        'viewing_other': viewing_user is not None,
        'lojas': lojas,
        'lojas_coordenador': lojas_coordenador,
        'gerentes_data': gerentes_data,
        'result': result,
        'meses': meses,
        'role': 'coordenador',
    }
    
    if viewing_user and result.get('success'):
        target_is_gerente = is_user_gerente(viewing_user)
        # Buscar metas por pilar - Gerente busca por filial, CN busca por nome
        target_name = viewing_user.get_full_name() or viewing_user.first_name
        target_sector = viewing_user.sector.name if hasattr(viewing_user, 'sector') and viewing_user.sector else None
        metas_pilar = fetch_metas_por_pilar(target_name, target_is_gerente, target_sector)
        processed = process_commission_data(result['data'], target_is_gerente, metas_pilar)
        context['data'] = processed
        context['charts_json'] = json.dumps(processed['charts'])
        context['target_is_gerente'] = target_is_gerente
    elif not viewing_user and result.get('success'):
        # Coordenador visualizando seus próprios dados - usar função específica
        processed = process_coordenador_commission_data(result['data'])
        context['data'] = processed
        context['charts_json'] = json.dumps(processed['charts'])
        context['target_is_gerente'] = False
        context['is_coordenador'] = True
    
    return render(request, 'users/commission_coordenador.html', context)


@login_required
def commission_api(request):
    """
    API para buscar dados de comissionamento via AJAX
    """
    user = request.user
    user_id = request.GET.get('user_id')
    role = get_user_role(user)
    
    # Verificar permissões
    if user_id:
        if role == 'cn':
            return JsonResponse({'error': 'Não autorizado'}, status=403)
        
        try:
            target_user = User.objects.get(id=user_id, is_active=True)
            
            # Gerente só pode ver CNs do seu setor
            if role == 'gerente' and target_user.sector != user.sector:
                return JsonResponse({'error': 'Não autorizado'}, status=403)
                
        except User.DoesNotExist:
            return JsonResponse({'error': 'Usuário não encontrado'}, status=404)
    else:
        target_user = user
    
    target_is_gerente = is_user_gerente(target_user)
    sheet_name = SHEET_GERENTE if target_is_gerente else SHEET_CN
    user_full_name = target_user.get_full_name() or target_user.first_name
    target_sector = target_user.sector.name if hasattr(target_user, 'sector') and target_user.sector else None
    
    result = fetch_excel_data(sheet_name, user_full_name)
    
    if result.get('success'):
        # Buscar metas por pilar - Gerente busca por filial, CN busca por nome
        metas_pilar = fetch_metas_por_pilar(user_full_name, target_is_gerente, target_sector)
        processed = process_commission_data(result['data'], target_is_gerente, metas_pilar)
        return JsonResponse({
            'success': True,
            'data': processed,
            'user_name': user_full_name
        })
    
    return JsonResponse(result)


@login_required
def commission_refresh(request):
    """
    Força atualização dos dados de comissionamento (limpa cache)
    """
    # Limpa todos os caches de comissionamento
    from django.core.cache import cache
    
    # Como não podemos listar as chaves no cache padrão, 
    # limpamos as chaves conhecidas
    user = request.user
    user_full_name = (user.get_full_name() or user.first_name).replace(' ', '_')
    
    for sheet in [SHEET_GERENTE, SHEET_CN]:
        cache.delete(f"commission_data_{sheet}_{user_full_name}")
        cache.delete(f"commission_all_users_{sheet}")
        cache.delete(f"comissao_{sheet}_file_content")
    
    cache.delete("vendas_all_data")
    cache.delete(f"vendas_data_{user_full_name}")
    cache.delete("vendas_file_content")
    cache.delete("vendas_all_file_content")
    
    messages.success(request, 'Dados atualizados com sucesso!')
    return redirect('commission')


@login_required
def export_commission_excel(request):
    """
    Exporta dados de comissionamento para Excel
    """
    user = request.user
    role = get_user_role(user)
    
    wb = Workbook()
    ws = wb.active
    ws.title = 'Comissionamento'
    
    # Estilos
    header_font = Font(bold=True, color='FFFFFF', size=11)
    header_fill = PatternFill(start_color='660099', end_color='660099', fill_type='solid')
    border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )
    
    if role == 'coordenador':
        # Exportar dados de todos os gerentes
        headers = ['Nome', 'PDV', 'Móvel %', 'Fixa %', 'Smart %', 'Eletro %', 'Essen %', 'Seguro %', 'SVA %', 'Remuneração Final']
        for col, header in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.border = border
        
        all_result = fetch_all_users_from_sheet(SHEET_GERENTE)
        if all_result.get('success'):
            for row, user_data in enumerate(all_result['users'], start=2):
                processed = process_simple_commission_data(user_data['data'], is_gerente=True)
                ws.cell(row=row, column=1, value=processed['nome']).border = border
                ws.cell(row=row, column=2, value=processed['pdv']).border = border
                for col, pilar in enumerate(processed['pilares'], start=3):
                    ws.cell(row=row, column=col, value=f"{pilar['pct']:.1f}%").border = border
                ws.cell(row=row, column=10, value=f"R$ {processed['remuneracao_final']:.2f}").border = border
    
    elif role == 'gerente':
        # Exportar dados dos CNs do setor
        headers = ['Nome', 'Móvel %', 'Fixa %', 'Smart %', 'Eletro %', 'Essen %', 'Seguro %', 'SVA %', 'Comissão Total', 'Remuneração Final']
        for col, header in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.border = border
        
        sector_cns = get_all_cns_for_gerente(user)
        all_result = fetch_all_users_from_sheet(SHEET_CN)
        
        row = 2
        if all_result.get('success'):
            for cn in sector_cns:
                cn_full_name = (cn.get_full_name() or cn.first_name).upper()
                for user_data in all_result['users']:
                    if cn_full_name in user_data['nome'].upper() or user_data['nome'].upper() in cn_full_name:
                        processed = process_simple_commission_data(user_data['data'])
                        ws.cell(row=row, column=1, value=processed['nome']).border = border
                        for col, pilar in enumerate(processed['pilares'], start=2):
                            ws.cell(row=row, column=col, value=f"{pilar['pct']:.1f}%").border = border
                        ws.cell(row=row, column=9, value=f"R$ {processed['comissao_total']:.2f}").border = border
                        ws.cell(row=row, column=10, value=f"R$ {processed['remuneracao_final']:.2f}").border = border
                        row += 1
                        break
    
    else:
        # CN - Exportar dados próprios
        user_full_name = user.get_full_name() or user.first_name
        user_sector = user.sector.name if hasattr(user, 'sector') and user.sector else None
        result = fetch_excel_data(SHEET_CN, user_full_name)
        
        if result.get('success'):
            # Buscar metas por pilar - CN busca por nome
            metas_pilar = fetch_metas_por_pilar(user_full_name, False, user_sector)
            processed = process_commission_data(result['data'], False, metas_pilar)
            
            ws['A1'] = 'Comissionamento - ' + user_full_name
            ws['A1'].font = Font(bold=True, size=14)
            
            ws['A3'] = 'Pilar'
            ws['B3'] = 'Atingimento'
            ws['C3'] = 'Comissão'
            for col in ['A', 'B', 'C']:
                ws[f'{col}3'].font = header_font
                ws[f'{col}3'].fill = header_fill
            
            row = 4
            for pilar in processed['pilares']:
                ws.cell(row=row, column=1, value=pilar['nome'])
                ws.cell(row=row, column=2, value=f"{pilar['media']:.1f}%")
                row += 1
    
    # Ajustar larguras
    for col in range(1, 12):
        ws.column_dimensions[get_column_letter(col)].width = 15
    
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    from datetime import datetime
    filename = f'comissionamento_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    wb.save(response)
    return response
