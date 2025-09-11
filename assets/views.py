from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.utils import timezone
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.utils import get_column_letter
from .models import Asset
from .forms import AssetForm
from core.middleware import log_action


@login_required
def asset_list(request):
    """Lista todos os ativos com funcionalidade de busca e paginação"""
    query = request.GET.get('q', '')
    estado_filter = request.GET.get('estado', '')
    setor_filter = request.GET.get('setor', '')
    
    assets = Asset.objects.all()
    
    # Filtros
    if query:
        assets = assets.filter(
            Q(patrimonio_numero__icontains=query) |
            Q(nome__icontains=query) |
            Q(localizado__icontains=query) |
            Q(setor__icontains=query) |
            Q(pdv__icontains=query)
        )
    
    if estado_filter:
        assets = assets.filter(estado_fisico=estado_filter)
    
    if setor_filter:
        assets = assets.filter(setor__icontains=setor_filter)
    
    # Paginação
    paginator = Paginator(assets, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Para os filtros
    estados = Asset.ESTADO_FISICO_CHOICES
    setores = Asset.objects.values_list('setor', flat=True).distinct().order_by('setor')
    
    context = {
        'page_obj': page_obj,
        'query': query,
        'estado_filter': estado_filter,
        'setor_filter': setor_filter,
        'estados': estados,
        'setores': setores,
        'total_assets': assets.count(),
    }
    
    return render(request, 'assets/list.html', context)


@login_required
def asset_detail(request, pk):
    """Exibe detalhes de um ativo específico"""
    asset = get_object_or_404(Asset, pk=pk)
    
    context = {
        'asset': asset,
    }
    
    return render(request, 'assets/detail.html', context)


@login_required
def asset_create(request):
    """Cria um novo ativo"""
    if request.method == 'POST':
        form = AssetForm(request.POST, request.FILES)
        if form.is_valid():
            asset = form.save(commit=False)
            asset.created_by = request.user
            asset.save()
            messages.success(request, f'Ativo {asset.patrimonio_numero} criado com sucesso!')
            return redirect('assets:detail', pk=asset.pk)
    else:
        form = AssetForm()
    
    context = {
        'form': form,
        'title': 'Cadastrar Novo Ativo',
    }
    
    return render(request, 'assets/form.html', context)


@login_required
def asset_edit(request, pk):
    """Edita um ativo existente"""
    asset = get_object_or_404(Asset, pk=pk)
    
    if request.method == 'POST':
        form = AssetForm(request.POST, request.FILES, instance=asset)
        if form.is_valid():
            form.save()
            messages.success(request, f'Ativo {asset.patrimonio_numero} atualizado com sucesso!')
            return redirect('assets:detail', pk=asset.pk)
    else:
        form = AssetForm(instance=asset)
    
    context = {
        'form': form,
        'asset': asset,
        'title': f'Editar Ativo - {asset.patrimonio_numero}',
    }
    
    return render(request, 'assets/form.html', context)


@login_required
def asset_delete(request, pk):
    """Deleta um ativo"""
    asset = get_object_or_404(Asset, pk=pk)
    
    if request.method == 'POST':
        patrimonio_numero = asset.patrimonio_numero
        asset.delete()
        messages.success(request, f'Ativo {patrimonio_numero} removido com sucesso!')
        return redirect('assets:list')
    
    context = {
        'asset': asset,
    }
    
    return render(request, 'assets/delete.html', context)


@login_required
def export_assets_excel(request):
    """Exportar dados de ativos em Excel (exceto fotos)"""
    # Criar workbook
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Ativos"
    
    # Definir cabeçalhos
    headers = [
        'Nº Patrimônio', 'Nome', 'Localizado', 'Setor', 'PDV', 
        'Estado Físico', 'Observações', 'Criado por', 'Data Criação', 'Última Atualização'
    ]
    
    # Estilizar cabeçalhos
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center")
    
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
    
    # Buscar dados dos ativos
    assets = Asset.objects.all().select_related('created_by').order_by('patrimonio_numero')
    
    # Preencher dados
    for row, asset in enumerate(assets, 2):
        ws.cell(row=row, column=1, value=asset.patrimonio_numero)
        ws.cell(row=row, column=2, value=asset.nome)
        ws.cell(row=row, column=3, value=asset.localizado)
        ws.cell(row=row, column=4, value=asset.setor)
        ws.cell(row=row, column=5, value=asset.pdv)
        ws.cell(row=row, column=6, value=asset.get_estado_fisico_display())
        ws.cell(row=row, column=7, value=asset.observacoes or "")
        ws.cell(row=row, column=8, value=asset.created_by.full_name)
        ws.cell(row=row, column=9, value=asset.created_at.strftime("%Y-%m-%d %H:%M:%S"))
        ws.cell(row=row, column=10, value=asset.updated_at.strftime("%Y-%m-%d %H:%M:%S"))
    
    # Ajustar largura das colunas
    for col in range(1, len(headers) + 1):
        column_letter = get_column_letter(col)
        max_length = 0
        for row in ws[column_letter]:
            try:
                if len(str(row.value)) > max_length:
                    max_length = len(str(row.value))
            except:
                pass
        adjusted_width = min(max_length + 2, 50)
        ws.column_dimensions[column_letter].width = adjusted_width
    
    # Preparar response
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="ativos_{timezone.now().strftime("%Y%m%d_%H%M%S")}.xlsx"'
    
    # Salvar workbook na response
    wb.save(response)
    
    # Log da ação
    log_action(
        request.user,
        'ASSET_EXPORT',
        f'Exportação de dados de ativos realizada',
        request
    )
    
    return response


@login_required
def import_assets_excel(request):
    """Importar ativos de Excel"""
    if request.method == 'POST':
        if 'excel_file' not in request.FILES:
            messages.error(request, 'Nenhum arquivo foi enviado.')
            return redirect('assets:list')
        
        excel_file = request.FILES['excel_file']
        
        try:
            # Ler o arquivo Excel
            wb = openpyxl.load_workbook(excel_file)
            ws = wb.active
            
            created_count = 0
            updated_count = 0
            error_count = 0
            errors = []
            
            # Processar cada linha (pular cabeçalho)
            for row_num, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
                try:
                    patrimonio_numero, nome, localizado, setor, pdv, estado_fisico, observacoes, created_by, date_created, last_updated = row
                    
                    if not patrimonio_numero:  # Nº Patrimônio é obrigatório
                        continue
                    
                    # Converter estado físico de display para valor
                    estado_fisico_value = estado_fisico
                    if estado_fisico:
                        for value, display in Asset.ESTADO_FISICO_CHOICES:
                            if display.lower() == estado_fisico.lower():
                                estado_fisico_value = value
                                break
                    
                    # Verificar se ativo já existe pelo número de patrimônio
                    asset, created = Asset.objects.get_or_create(
                        patrimonio_numero=patrimonio_numero,
                        defaults={
                            'nome': nome or '',
                            'localizado': localizado or '',
                            'setor': setor or '',
                            'pdv': pdv or '',
                            'estado_fisico': estado_fisico_value or 'bom',
                            'observacoes': observacoes or '',
                            'created_by': request.user,  # Sempre o usuário que está importando
                        }
                    )
                    
                    if created:
                        created_count += 1
                    else:
                        # Atualizar ativo existente
                        if nome:
                            asset.nome = nome
                        if localizado:
                            asset.localizado = localizado
                        if setor:
                            asset.setor = setor
                        if pdv:
                            asset.pdv = pdv
                        if estado_fisico_value:
                            asset.estado_fisico = estado_fisico_value
                        if observacoes is not None:
                            asset.observacoes = observacoes
                        
                        asset.save()
                        updated_count += 1
                        
                except Exception as e:
                    error_count += 1
                    errors.append(f'Linha {row_num}: {str(e)}')
                    continue
            
            # Mensagem de resultado
            if created_count > 0 or updated_count > 0:
                message = f'Importação concluída! {created_count} ativos criados, {updated_count} ativos atualizados.'
                if error_count > 0:
                    message += f' {error_count} erros encontrados.'
                messages.success(request, message)
            else:
                messages.warning(request, 'Nenhum ativo foi importado.')
            
            if errors:
                for error in errors[:5]:  # Mostrar apenas os primeiros 5 erros
                    messages.error(request, error)
            
            # Log da ação
            log_action(
                request.user,
                'ASSET_IMPORT',
                f'Importação de ativos: {created_count} criados, {updated_count} atualizados, {error_count} erros',
                request
            )
            
        except Exception as e:
            messages.error(request, f'Erro ao processar arquivo: {str(e)}')
    
    return redirect('assets:list')
