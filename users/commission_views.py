"""
Views para exibição de dados de comissionamento
Busca dados da planilha Excel do OneDrive/SharePoint
Design inspirado na identidade visual VIVO
"""
import requests
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.conf import settings
from django.core.cache import cache
import json
import pandas as pd
from io import BytesIO
from users.models import User, Sector


# Configuração da planilha do OneDrive
EXCEL_SHARE_URL = "https://1drv.ms/x/c/871ee1819c7e2faa/IQDiTJg7g9b_R6wn6uXndz3UAXzjm8r7m27co8LHPJ6vyFQ"

# Nome das sheets na planilha
SHEET_GERENTE = "REMUNERAÇÃO GERENTE"
SHEET_CN = "REMUNERAÇÃO CN"

# Grupo que identifica gerentes
GERENTE_GROUP_NAME = "GERENTES (CHECKLIST)"


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


def get_sector_users(user):
    """Retorna lista de usuários do mesmo setor do gerente"""
    if not user.sector:
        return []
    
    return User.objects.filter(
        sector=user.sector,
        hierarchy='PADRAO',
        is_active=True
    ).exclude(id=user.id).order_by('first_name', 'last_name')


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


def fetch_excel_data(sheet_name, user_name):
    """
    Busca dados da planilha Excel do OneDrive
    Retorna os dados do usuário específico
    """
    cache_key = f"commission_data_{sheet_name}_{user_name.replace(' ', '_')}"
    cached_data = cache.get(cache_key)
    
    if cached_data:
        return cached_data
    
    try:
        download_urls = []
        original_url = EXCEL_SHARE_URL
        
        download_urls.append(get_excel_download_url(original_url))
        
        if '?' in original_url:
            download_urls.append(original_url + '&download=1')
        else:
            download_urls.append(original_url + '?download=1')
        
        if 'IQD' in original_url:
            import re
            match = re.search(r'(IQ[A-Za-z0-9_-]+)', original_url)
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
            return {
                'error': f'Erro ao baixar planilha. Último erro: {last_error}',
                'hint': 'Verifique se o link de compartilhamento permite visualização pública.'
            }
        
        excel_file = BytesIO(response.content)
        
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
    # Se o valor é menor ou igual a 3, provavelmente é decimal
    if value <= 3:
        return value * 100
    return value


def get_month_names():
    """
    Retorna os nomes dos meses M1, M2, M3 baseado no mês atual.
    Exemplo: Se estamos em Dezembro:
    - M1 = Outubro (2 meses atrás do atual)
    - M2 = Setembro (3 meses atrás)
    - M3 = Agosto (4 meses atrás)
    """
    from datetime import datetime
    
    meses_pt = {
        1: 'Janeiro', 2: 'Fevereiro', 3: 'Março', 4: 'Abril',
        5: 'Maio', 6: 'Junho', 7: 'Julho', 8: 'Agosto',
        9: 'Setembro', 10: 'Outubro', 11: 'Novembro', 12: 'Dezembro'
    }
    
    hoje = datetime.now()
    mes_atual = hoje.month
    
    # Função para calcular mês anterior (voltando X meses)
    def get_month_back(months_back):
        m = mes_atual - months_back
        if m <= 0:
            m += 12
        return m
    
    # M1 = 2 meses atrás (ex: Dezembro atual -> Outubro)
    # M2 = 3 meses atrás (ex: Dezembro atual -> Setembro)
    # M3 = 4 meses atrás (ex: Dezembro atual -> Agosto)
    return {
        'm1': meses_pt[get_month_back(2)],
        'm2': meses_pt[get_month_back(3)],
        'm3': meses_pt[get_month_back(4)],
    }


def process_commission_data(data, is_gerente=False):
    """
    Processa os dados brutos da planilha e organiza em seções
    """
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
        
        # Busca e converte os valores de atingimento (podem estar como decimais)
        pct_3_raw = safe_float(data.get(f'%ATING_{key}_3'))
        pct_2_raw = safe_float(data.get(f'%ATING_{key}_2'))
        pct_1_raw = safe_float(data.get(f'%ATING_{key}_1'))
        
        pilar_data = {
            'nome': pilar['nome'],
            'icon': pilar['icon'],
            'color': pilar['color'],
            'pct_3': convert_percentage(pct_3_raw),  # M3 - 3 meses atrás (ex: Agosto)
            'pct_2': convert_percentage(pct_2_raw),  # M2 - 2 meses atrás (ex: Setembro)
            'pct_1': convert_percentage(pct_1_raw),  # M1 - 1 mês atrás (ex: Outubro)
            'carteira': safe_float(data.get(cart_key) or data.get(f'ATING_CART_{key}')),
            'habilitado': data.get(f'H_{pilar["nome"].upper()}') or data.get(f'H_{key}'),
        }
        
        # Calcula média dos atingimentos
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
    # Calcula total se vier zerado da planilha
    total_comissoes = safe_float(data.get('Total Comissão'))
    if total_comissoes == 0:
        total_comissoes = sum(comissoes_valores.values())
    comissoes_valores['total'] = total_comissoes
    processed['comissoes'] = comissoes_valores
    
    # Bônus Carteira
    bonus_valores = {
        'movel': safe_float(data.get('BONUS_CARTEIRA_MÓVEL')),
        'fixa': safe_float(data.get('BONU_CARTEIRA_FIXA')),
        'smartphone': safe_float(data.get('BONUTS_CARTEIRA_SMARTPHONE')),
        'eletronicos_a': safe_float(data.get('BONUS_CARTEIRA_ELETRONICOS - A')),
        'eletronicos_b': safe_float(data.get('BONUS_CARTEIRA_ELETRONICOS - B')),
        'essenciais_a': safe_float(data.get('BONUS_CARTEIRA_ESSENCIAIS - A')),
        'essenciais_b': safe_float(data.get('BONUS_CARTEIRA_ESSENCIAIS - B')),
        'seguro': safe_float(data.get('BONUS_CARTEIRA_SEGURO')),
        'sva': safe_float(data.get('BONUS_CARTEIRA_SVA')),
    }
    # Calcula total se vier zerado da planilha
    total_bonus = safe_float(data.get('TOTAL BONUS LOJA'))
    if total_bonus == 0:
        total_bonus = sum(bonus_valores.values())
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
    # Calcula total se vier zerado da planilha
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
    
    # Habilitados
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
    
    # Dados brutos para tabela completa
    processed['raw_data'] = data
    
    return processed


@login_required
def commission_view(request):
    """
    View principal para exibir dados de comissionamento do usuário
    """
    user = request.user
    
    # Apenas usuários PADRAO podem ver comissionamento
    if user.hierarchy != 'PADRAO':
        messages.warning(request, 'Esta funcionalidade está disponível apenas para usuários padrão.')
        return redirect('dashboard')
    
    # Verifica se é gerente
    is_gerente = is_user_gerente(user)
    
    # Verifica se está visualizando outro usuário (apenas gerentes)
    viewing_user_id = request.GET.get('user')
    viewing_user = None
    sector_users = []
    
    if is_gerente:
        sector_users = get_sector_users(user)
        
        if viewing_user_id:
            try:
                viewing_user = User.objects.get(id=viewing_user_id, sector=user.sector, is_active=True)
            except User.DoesNotExist:
                messages.error(request, 'Usuário não encontrado ou não pertence ao seu setor.')
                return redirect('commission')
    
    # Define qual usuário buscar dados
    target_user = viewing_user if viewing_user else user
    target_is_gerente = is_user_gerente(target_user) if viewing_user else is_gerente
    
    # Determina qual sheet usar
    sheet_name = SHEET_GERENTE if target_is_gerente else SHEET_CN
    
    # Nome para busca na planilha
    user_full_name = target_user.get_full_name() or target_user.first_name
    
    # Buscar dados da planilha
    result = fetch_excel_data(sheet_name, user_full_name)
    
    # Obter nomes dos meses
    meses = get_month_names()
    
    context = {
        'user': user,
        'target_user': target_user,
        'viewing_other': viewing_user is not None,
        'is_gerente': is_gerente,
        'target_is_gerente': target_is_gerente,
        'sector_users': sector_users,
        'result': result,
        'meses': meses,
    }
    
    if result.get('success'):
        processed = process_commission_data(result['data'], target_is_gerente)
        context['data'] = processed
        context['charts_json'] = json.dumps(processed['charts'])
    
    return render(request, 'users/commission.html', context)


@login_required
def commission_api(request):
    """
    API para buscar dados de comissionamento via AJAX
    """
    user = request.user
    
    if user.hierarchy != 'PADRAO':
        return JsonResponse({'error': 'Não autorizado'}, status=403)
    
    user_id = request.GET.get('user_id')
    
    if user_id and is_user_gerente(user):
        try:
            target_user = User.objects.get(id=user_id, sector=user.sector)
        except User.DoesNotExist:
            return JsonResponse({'error': 'Usuário não encontrado'}, status=404)
    else:
        target_user = user
    
    target_is_gerente = is_user_gerente(target_user)
    sheet_name = SHEET_GERENTE if target_is_gerente else SHEET_CN
    user_full_name = target_user.get_full_name() or target_user.first_name
    
    result = fetch_excel_data(sheet_name, user_full_name)
    
    if result.get('success'):
        processed = process_commission_data(result['data'], target_is_gerente)
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
    user = request.user
    
    if user.hierarchy != 'PADRAO':
        messages.warning(request, 'Esta funcionalidade está disponível apenas para usuários padrão.')
        return redirect('dashboard')
    
    # Limpa o cache para este usuário
    for sheet in [SHEET_GERENTE, SHEET_CN]:
        user_full_name = user.get_full_name() or user.first_name
        cache_key = f"commission_data_{sheet}_{user_full_name.replace(' ', '_')}"
        cache.delete(cache_key)
    
    messages.success(request, 'Dados atualizados com sucesso!')
    return redirect('commission')
