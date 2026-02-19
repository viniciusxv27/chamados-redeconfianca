from django import forms
from django.core.exceptions import ValidationError
from .models import (
    Asset, Product, ProductMedia, InventoryItem, 
    StockMovement, InventoryCategory, InventoryManager, ItemRequest, ItemRequestItem
)
from users.models import User, Sector


# CSS Classes para Tailwind
INPUT_CLASSES = 'mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm'
SELECT_CLASSES = 'mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm'
TEXTAREA_CLASSES = 'mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm'
CHECKBOX_CLASSES = 'h-4 w-4 text-indigo-600 focus:ring-indigo-500 border-gray-300 rounded'
FILE_CLASSES = 'mt-1 block w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-md file:border-0 file:text-sm file:font-semibold file:bg-indigo-50 file:text-indigo-700 hover:file:bg-indigo-100'


class InventoryCategoryForm(forms.ModelForm):
    """Formulário para categorias de inventário"""
    class Meta:
        model = InventoryCategory
        fields = ['name', 'description', 'color', 'icon', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': INPUT_CLASSES,
                'placeholder': 'Nome da categoria'
            }),
            'description': forms.Textarea(attrs={
                'class': TEXTAREA_CLASSES,
                'rows': 3,
                'placeholder': 'Descrição da categoria'
            }),
            'color': forms.TextInput(attrs={
                'class': INPUT_CLASSES,
                'type': 'color'
            }),
            'icon': forms.TextInput(attrs={
                'class': INPUT_CLASSES,
                'placeholder': 'fas fa-box'
            }),
            'is_active': forms.CheckboxInput(attrs={
                'class': CHECKBOX_CLASSES
            }),
        }


class ProductForm(forms.ModelForm):
    """Formulário para cadastro de produtos"""
    class Meta:
        model = Product
        fields = [
            'name', 'sku', 'description', 'category', 'brand', 'model',
            'size', 'size_custom', 'unit_of_measure', 'min_stock', 
            'max_stock', 'unit_cost', 'location', 'notes', 'is_active'
        ]
        widgets = {
            'name': forms.TextInput(attrs={
                'class': INPUT_CLASSES,
                'placeholder': 'Nome do produto'
            }),
            'sku': forms.TextInput(attrs={
                'class': INPUT_CLASSES,
                'placeholder': 'Código único (ex: PROD-001)'
            }),
            'description': forms.Textarea(attrs={
                'class': TEXTAREA_CLASSES,
                'rows': 4,
                'placeholder': 'Descrição detalhada do produto'
            }),
            'category': forms.Select(attrs={
                'class': SELECT_CLASSES
            }),
            'brand': forms.TextInput(attrs={
                'class': INPUT_CLASSES,
                'placeholder': 'Marca do produto'
            }),
            'model': forms.TextInput(attrs={
                'class': INPUT_CLASSES,
                'placeholder': 'Modelo do produto'
            }),
            'size': forms.Select(attrs={
                'class': SELECT_CLASSES
            }),
            'size_custom': forms.TextInput(attrs={
                'class': INPUT_CLASSES,
                'placeholder': 'Tamanho específico'
            }),
            'unit_of_measure': forms.TextInput(attrs={
                'class': INPUT_CLASSES,
                'placeholder': 'UN, KG, L, M, CX'
            }),
            'min_stock': forms.NumberInput(attrs={
                'class': INPUT_CLASSES,
                'min': 0
            }),
            'max_stock': forms.NumberInput(attrs={
                'class': INPUT_CLASSES,
                'min': 0
            }),
            'unit_cost': forms.NumberInput(attrs={
                'class': INPUT_CLASSES,
                'step': '0.01',
                'min': 0
            }),
            'location': forms.TextInput(attrs={
                'class': INPUT_CLASSES,
                'placeholder': 'Ex: Prateleira A-1'
            }),
            'notes': forms.Textarea(attrs={
                'class': TEXTAREA_CLASSES,
                'rows': 3,
                'placeholder': 'Observações adicionais'
            }),
            'is_active': forms.CheckboxInput(attrs={
                'class': CHECKBOX_CLASSES
            }),
        }

    def clean_sku(self):
        sku = self.cleaned_data['sku']
        if Product.objects.filter(sku=sku).exclude(pk=self.instance.pk).exists():
            raise ValidationError('Já existe um produto com este código SKU.')
        return sku.upper()


class ProductMediaForm(forms.ModelForm):
    """Formulário para upload de mídia do produto"""
    class Meta:
        model = ProductMedia
        fields = ['media_type', 'file', 'title', 'is_primary', 'order']
        widgets = {
            'media_type': forms.Select(attrs={
                'class': SELECT_CLASSES
            }),
            'file': forms.FileInput(attrs={
                'class': FILE_CLASSES,
                'accept': 'image/*,video/*'
            }),
            'title': forms.TextInput(attrs={
                'class': INPUT_CLASSES,
                'placeholder': 'Título da mídia'
            }),
            'is_primary': forms.CheckboxInput(attrs={
                'class': CHECKBOX_CLASSES
            }),
            'order': forms.NumberInput(attrs={
                'class': INPUT_CLASSES,
                'min': 0
            }),
        }


class InventoryItemForm(forms.ModelForm):
    """Formulário para cadastro de itens de inventário"""
    class Meta:
        model = InventoryItem
        fields = [
            'product', 'inventory_number', 'serial_number', 'batch_number',
            'status', 'condition', 'purchase_date', 'purchase_price',
            'warranty_expiry', 'location', 'notes', 'photo'
        ]
        widgets = {
            'product': forms.Select(attrs={
                'class': SELECT_CLASSES
            }),
            'inventory_number': forms.TextInput(attrs={
                'class': INPUT_CLASSES,
                'placeholder': 'Número único do item (ex: INV-001)'
            }),
            'serial_number': forms.TextInput(attrs={
                'class': INPUT_CLASSES,
                'placeholder': 'Número de série do fabricante'
            }),
            'batch_number': forms.TextInput(attrs={
                'class': INPUT_CLASSES,
                'placeholder': 'Número do lote'
            }),
            'status': forms.Select(attrs={
                'class': SELECT_CLASSES
            }),
            'condition': forms.Select(attrs={
                'class': SELECT_CLASSES
            }),
            'purchase_date': forms.DateInput(attrs={
                'class': INPUT_CLASSES,
                'type': 'date'
            }),
            'purchase_price': forms.NumberInput(attrs={
                'class': INPUT_CLASSES,
                'step': '0.01',
                'min': 0
            }),
            'warranty_expiry': forms.DateInput(attrs={
                'class': INPUT_CLASSES,
                'type': 'date'
            }),
            'location': forms.TextInput(attrs={
                'class': INPUT_CLASSES,
                'placeholder': 'Localização atual do item'
            }),
            'notes': forms.Textarea(attrs={
                'class': TEXTAREA_CLASSES,
                'rows': 3,
                'placeholder': 'Observações sobre o item'
            }),
            'photo': forms.FileInput(attrs={
                'class': FILE_CLASSES,
                'accept': 'image/*'
            }),
        }

    def clean_inventory_number(self):
        inv_number = self.cleaned_data['inventory_number']
        if InventoryItem.objects.filter(inventory_number=inv_number).exclude(pk=self.instance.pk).exists():
            raise ValidationError('Já existe um item com este número de inventário.')
        return inv_number.upper()


class StockEntryForm(forms.ModelForm):
    """Formulário para entrada de estoque"""
    ENTRY_REASON_CHOICES = [
        ('purchase', 'Compra'),
        ('donation', 'Doação'),
        ('return_from_user', 'Devolução de Usuário'),
        ('return_from_maintenance', 'Retorno de Manutenção'),
        ('inventory_adjustment', 'Ajuste de Inventário'),
        ('initial_stock', 'Estoque Inicial'),
    ]
    
    reason = forms.ChoiceField(
        choices=ENTRY_REASON_CHOICES,
        widget=forms.Select(attrs={'class': SELECT_CLASSES}),
        label='Motivo da Entrada'
    )
    
    from_user = forms.ModelChoiceField(
        queryset=User.objects.filter(is_active=True),
        required=False,
        widget=forms.Select(attrs={'class': SELECT_CLASSES}),
        label='De (Usuário)',
        help_text='Selecione se for devolução de usuário'
    )
    
    class Meta:
        model = StockMovement
        fields = ['inventory_item', 'reason', 'from_user', 'from_location', 
                  'notes', 'document_reference']
        widgets = {
            'inventory_item': forms.Select(attrs={
                'class': SELECT_CLASSES
            }),
            'from_location': forms.TextInput(attrs={
                'class': INPUT_CLASSES,
                'placeholder': 'Local de origem'
            }),
            'notes': forms.Textarea(attrs={
                'class': TEXTAREA_CLASSES,
                'rows': 3,
                'placeholder': 'Observações sobre a entrada'
            }),
            'document_reference': forms.TextInput(attrs={
                'class': INPUT_CLASSES,
                'placeholder': 'Ex: NF-12345'
            }),
        }

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.movement_type = 'entry'
        if commit:
            instance.save()
        return instance


class StockExitForm(forms.ModelForm):
    """Formulário para saída de estoque"""
    EXIT_REASON_CHOICES = [
        ('assigned_to_user', 'Atribuído a Usuário'),
        ('assigned_to_sector', 'Atribuído a Setor'),
        ('maintenance', 'Enviado para Manutenção'),
        ('disposed', 'Descarte'),
        ('lost', 'Perda/Extravio'),
        ('damaged', 'Dano'),
        ('other', 'Outro'),
    ]
    
    reason = forms.ChoiceField(
        choices=EXIT_REASON_CHOICES,
        widget=forms.Select(attrs={'class': SELECT_CLASSES}),
        label='Motivo da Saída'
    )
    
    to_user = forms.ModelChoiceField(
        queryset=User.objects.filter(is_active=True),
        required=False,
        widget=forms.Select(attrs={'class': SELECT_CLASSES}),
        label='Para (Usuário)'
    )
    
    to_sector = forms.ModelChoiceField(
        queryset=Sector.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': SELECT_CLASSES}),
        label='Para (Setor)'
    )
    
    class Meta:
        model = StockMovement
        fields = ['inventory_item', 'reason', 'to_user', 'to_sector', 
                  'to_location', 'notes', 'document_reference']
        widgets = {
            'inventory_item': forms.Select(attrs={
                'class': SELECT_CLASSES
            }),
            'to_location': forms.TextInput(attrs={
                'class': INPUT_CLASSES,
                'placeholder': 'Local de destino'
            }),
            'notes': forms.Textarea(attrs={
                'class': TEXTAREA_CLASSES,
                'rows': 3,
                'placeholder': 'Observações sobre a saída'
            }),
            'document_reference': forms.TextInput(attrs={
                'class': INPUT_CLASSES,
                'placeholder': 'Ex: REQ-12345'
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Filtrar apenas itens disponíveis
        self.fields['inventory_item'].queryset = InventoryItem.objects.filter(
            status='available'
        ).select_related('product')

    def clean(self):
        cleaned_data = super().clean()
        reason = cleaned_data.get('reason')
        to_user = cleaned_data.get('to_user')
        to_sector = cleaned_data.get('to_sector')
        
        # Validações específicas por motivo
        if reason == 'assigned_to_user' and not to_user:
            raise ValidationError('Para atribuição a usuário, selecione o usuário.')
        if reason == 'assigned_to_sector' and not to_sector:
            raise ValidationError('Para atribuição a setor, selecione o setor.')
        
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.movement_type = 'exit'
        if commit:
            instance.save()
            # Atualizar o status do item
            item = instance.inventory_item
            reason = self.cleaned_data.get('reason')
            
            if reason in ['assigned_to_user', 'assigned_to_sector']:
                item.status = 'in_use'
                item.assigned_to = self.cleaned_data.get('to_user')
                item.assigned_sector = self.cleaned_data.get('to_sector')
                from django.utils import timezone
                item.assigned_date = timezone.now()
            elif reason == 'maintenance':
                item.status = 'maintenance'
            elif reason == 'disposed':
                item.status = 'disposed'
            elif reason == 'damaged':
                item.status = 'damaged'
            
            item.save()
        return instance


class InventoryManagerForm(forms.ModelForm):
    """Formulário para gestores de inventário"""
    user = forms.ModelChoiceField(
        queryset=User.objects.filter(is_active=True),
        widget=forms.Select(attrs={'class': SELECT_CLASSES}),
        label='Usuário'
    )
    
    class Meta:
        model = InventoryManager
        fields = [
            'user', 'can_manage_products', 'can_manage_items',
            'can_register_entries', 'can_register_exits',
            'can_view_reports', 'can_manage_managers', 'can_approve_requests', 'is_active'
        ]
        widgets = {
            'can_manage_products': forms.CheckboxInput(attrs={'class': CHECKBOX_CLASSES}),
            'can_manage_items': forms.CheckboxInput(attrs={'class': CHECKBOX_CLASSES}),
            'can_register_entries': forms.CheckboxInput(attrs={'class': CHECKBOX_CLASSES}),
            'can_register_exits': forms.CheckboxInput(attrs={'class': CHECKBOX_CLASSES}),
            'can_view_reports': forms.CheckboxInput(attrs={'class': CHECKBOX_CLASSES}),
            'can_manage_managers': forms.CheckboxInput(attrs={'class': CHECKBOX_CLASSES}),
            'can_approve_requests': forms.CheckboxInput(attrs={'class': CHECKBOX_CLASSES}),
            'is_active': forms.CheckboxInput(attrs={'class': CHECKBOX_CLASSES}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Excluir usuários que já são gestores
        existing_managers = InventoryManager.objects.values_list('user_id', flat=True)
        if self.instance and self.instance.pk:
            existing_managers = existing_managers.exclude(pk=self.instance.pk)
        self.fields['user'].queryset = User.objects.filter(
            is_active=True
        ).exclude(id__in=existing_managers)


class BulkInventoryItemForm(forms.Form):
    """Formulário para cadastro em lote de itens de inventário"""
    product = forms.ModelChoiceField(
        queryset=Product.objects.filter(is_active=True),
        widget=forms.Select(attrs={'class': SELECT_CLASSES}),
        label='Produto'
    )
    quantity = forms.IntegerField(
        min_value=1,
        max_value=100,
        widget=forms.NumberInput(attrs={
            'class': INPUT_CLASSES,
            'min': 1,
            'max': 100
        }),
        label='Quantidade'
    )
    prefix = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={
            'class': INPUT_CLASSES,
            'placeholder': 'Prefixo do número de inventário'
        }),
        label='Prefixo'
    )
    start_number = forms.IntegerField(
        min_value=1,
        widget=forms.NumberInput(attrs={
            'class': INPUT_CLASSES,
            'min': 1
        }),
        label='Número inicial'
    )
    condition = forms.ChoiceField(
        choices=InventoryItem.CONDITION_CHOICES,
        widget=forms.Select(attrs={'class': SELECT_CLASSES}),
        label='Condição'
    )
    location = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={
            'class': INPUT_CLASSES,
            'placeholder': 'Localização dos itens'
        }),
        label='Localização'
    )
    purchase_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={
            'class': INPUT_CLASSES,
            'type': 'date'
        }),
        label='Data de Compra'
    )
    purchase_price = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=2,
        widget=forms.NumberInput(attrs={
            'class': INPUT_CLASSES,
            'step': '0.01',
            'min': 0
        }),
        label='Preço de Compra (por unidade)'
    )


class ItemRequestForm(forms.ModelForm):
    """Formulário para a solicitação (cabeçalho - apenas motivo)"""
    class Meta:
        model = ItemRequest
        fields = ['reason']
        widgets = {
            'reason': forms.Textarea(attrs={
                'class': TEXTAREA_CLASSES,
                'rows': 4,
                'placeholder': 'Descreva o motivo da solicitação...'
            }),
        }


class ItemRequestItemForm(forms.ModelForm):
    """Formulário para cada item da solicitação"""
    class Meta:
        model = ItemRequestItem
        fields = ['product', 'quantity']
        widgets = {
            'product': forms.Select(attrs={'class': SELECT_CLASSES}),
            'quantity': forms.NumberInput(attrs={
                'class': INPUT_CLASSES,
                'min': 1,
                'value': 1
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['product'].queryset = Product.objects.filter(
            is_active=True
        ).order_by('name')
        self.fields['product'].label_from_instance = lambda obj: f"{obj.name} (Disponível: {obj.current_stock})"

    def clean(self):
        cleaned_data = super().clean()
        product = cleaned_data.get('product')
        quantity = cleaned_data.get('quantity')
        if product and quantity:
            if product.current_stock < quantity:
                raise ValidationError(
                    f'Estoque insuficiente para "{product.name}". Disponível: {product.current_stock} unidade(s).'
                )
        return cleaned_data


class ItemRequestReviewForm(forms.Form):
    """Formulário para aprovação/rejeição de solicitação"""
    review_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'class': TEXTAREA_CLASSES,
            'rows': 3,
            'placeholder': 'Observações sobre a aprovação/rejeição...'
        }),
        label='Observações'
    )


class ItemRequestCounterProposalForm(forms.Form):
    """Formulário para contraproposta do gestor"""
    counterproposal_notes = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={
            'class': TEXTAREA_CLASSES,
            'rows': 3,
            'placeholder': 'Explique o motivo da contraproposta...'
        }),
        label='Motivo da Contraproposta'
    )


class ItemRequestCounterProposalResponseForm(forms.Form):
    """Formulário para resposta do solicitante à contraproposta"""
    response_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'class': TEXTAREA_CLASSES,
            'rows': 3,
            'placeholder': 'Observações sobre sua decisão...'
        }),
        label='Observações'
    )


class ItemRequestDeliveryForm(forms.Form):
    """Formulário para marcar entrega de solicitação"""
    delivery_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'class': TEXTAREA_CLASSES,
            'rows': 3,
            'placeholder': 'Observações sobre a entrega...'
        }),
        label='Observações da Entrega'
    )


# ============================================================================
# FORMULÁRIO LEGADO - MANTIDO PARA COMPATIBILIDADE
# ============================================================================
class AssetForm(forms.ModelForm):
    class Meta:
        model = Asset
        fields = [
            'patrimonio_numero', 
            'nome', 
            'imei_serial',
            'localizado', 
            'setor', 
            'pdv', 
            'estado_fisico', 
            'observacoes',
            'photo'
        ]
        widgets = {
            'patrimonio_numero': forms.TextInput(attrs={
                'class': INPUT_CLASSES,
                'placeholder': 'Ex: PAT001'
            }),
            'nome': forms.TextInput(attrs={
                'class': INPUT_CLASSES,
                'placeholder': 'Nome do ativo'
            }),
            'imei_serial': forms.TextInput(attrs={
                'class': INPUT_CLASSES,
                'placeholder': 'IMEI ou Número de Série'
            }),
            'localizado': forms.TextInput(attrs={
                'class': INPUT_CLASSES,
                'placeholder': 'Local onde o ativo está localizado'
            }),
            'setor': forms.TextInput(attrs={
                'class': INPUT_CLASSES,
                'placeholder': 'Setor responsável'
            }),
            'pdv': forms.TextInput(attrs={
                'class': INPUT_CLASSES,
                'placeholder': 'PDV'
            }),
            'estado_fisico': forms.Select(attrs={
                'class': SELECT_CLASSES
            }),
            'observacoes': forms.Textarea(attrs={
                'class': TEXTAREA_CLASSES,
                'rows': 4,
                'placeholder': 'Observações adicionais sobre o ativo'
            }),
            'photo': forms.ClearableFileInput(attrs={
                'class': FILE_CLASSES,
                'accept': 'image/*'
            }),
        }

    def clean_patrimonio_numero(self):
        patrimonio_numero = self.cleaned_data['patrimonio_numero']
        if Asset.objects.filter(patrimonio_numero=patrimonio_numero).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Já existe um ativo com este número de patrimônio.')
        return patrimonio_numero
