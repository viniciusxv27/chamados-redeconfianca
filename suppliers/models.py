from django.db import models
from django.conf import settings
from django.utils import timezone
import re

import re


class Supplier(models.Model):
    """Model para fornecedores"""
    
    name = models.CharField(
        max_length=200,
        verbose_name="Nome",
        help_text="Nome da empresa fornecedora"
    )
    cnpj = models.CharField(
        max_length=18,
        unique=True,
        verbose_name="CNPJ",
        help_text="CNPJ da empresa (formato: 00.000.000/0000-00)"
    )
    contact = models.CharField(
        max_length=200,
        verbose_name="Contato",
        help_text="Nome da pessoa de contato ou telefone/email"
    )
    area_of_operation = models.CharField(
        max_length=300,
        verbose_name="Onde atua",
        help_text="Área de atuação da empresa"
    )
    services = models.TextField(
        verbose_name="Descrição",
        help_text="Descrição dos serviços/produtos oferecidos"
    )
    # Categoria (25/11/2026): como a do gasto nos cartões — escolhe uma das que já existem ou cria nova.
    categoria = models.CharField(max_length=120, blank=True, default='', db_default='', db_index=True,
                                 verbose_name='Categoria')
    
    # Condições comerciais (11/11/2026) — nenhuma é obrigatória.
    preco = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='Preço (R$)')
    # Preço sob consulta (25/11/2026): muito fornecedor cobra pelo serviço pedido, então não há valor fixo.
    # ``preco_referencia`` diz a que o preço se refere ("por visita técnica") ou o que pedir na consulta.
    preco_sob_consulta = models.BooleanField(default=False, db_default=False, verbose_name='Preço sob consulta')
    preco_referencia = models.CharField(max_length=200, blank=True, default='', db_default='',
                                        verbose_name='Preço referente a')
    frete = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='Frete (R$)')
    tempo_entrega = models.CharField(max_length=120, blank=True, default='', db_default='',
                                     verbose_name='Tempo de entrega', help_text='Ex.: 5 dias úteis')

    # Campos adicionais para controle
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='created_suppliers',
        verbose_name="Criado por"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(
        default=True,
        verbose_name="Ativo"
    )
    
    class Meta:
        verbose_name = "Fornecedor"
        verbose_name_plural = "Fornecedores"
        ordering = ['name']
    
    def __str__(self):
        return self.name

    @property
    def preco_resumo(self):
        """Como o preço aparece nas telas: "R$ 150,00 · por visita", "Sob consulta · instalação" ou ""."""
        if self.preco_sob_consulta:
            base = 'Sob consulta'
        elif self.preco is not None:
            base = 'R$ ' + f'{self.preco:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
        else:
            return ''
        return f'{base} · {self.preco_referencia}' if self.preco_referencia else base
    
    def clean(self):
        """Validação customizada"""
        from django.core.exceptions import ValidationError
        
        # Validar CNPJ
        if self.cnpj:
            # Remove caracteres especiais
            cnpj_numbers = re.sub(r'[^0-9]', '', self.cnpj)
            
            # Verifica se tem 14 dígitos
            if len(cnpj_numbers) != 14:
                raise ValidationError({'cnpj': 'CNPJ deve conter exatamente 14 dígitos'})
            
            # Formatar CNPJ
            self.cnpj = f"{cnpj_numbers[:2]}.{cnpj_numbers[2:5]}.{cnpj_numbers[5:8]}/{cnpj_numbers[8:12]}-{cnpj_numbers[12:]}"
    
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)