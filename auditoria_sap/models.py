"""Visão SAP: espelho local da auditoria SAP × Vivo go.

A fonte é a view ``vw_auditoria_visao_geral`` do MySQL do SAP, que não tem
chave e leva ~30 s para ser lida inteira. Por isso o portal guarda um espelho:
a tela filtra, ordena e pagina em cima do Postgres (rápido), e a leitura do
MySQL acontece só quando alguém manda atualizar.

Cada linha ganha uma ``chave`` — o sha1 das colunas que identificam a
divergência (tipo do erro, venda, documento, produto, serial, ordem, nota e os
dois valores). Medido em 24/09/2026: 4.022 linhas, 4.022 chaves distintas. É
essa chave que segura o "resolvida", que é do portal e não do SAP.
"""
import hashlib

from django.conf import settings
from django.db import models
from django.utils import timezone

# As colunas que dizem *qual* divergência é esta. Mudou alguma delas, é outra
# linha — e é isso que se quer: a auditoria mudou, a marcação não vale mais.
COLUNAS_IDENTIDADE = (
    'TIPO_ERRO', 'DATA_VENDA', 'PDV', 'ID_VENDA', 'DOCUMENTO_VIVOGO', 'DOCUMENTO_SAP',
    'SKU_VIVOGO', 'COD_MATERIAL_SAP', 'SERIAL_VIVOGO', 'SERIAL_SAP', 'ORDEM_SAP',
    'NUM_FAT_SAP', 'VALOR_SAP', 'VALOR_VIVO_GO',
)


def chave_de(linha):
    """sha1 das colunas de identidade da linha da view."""
    partes = []
    for coluna in COLUNAS_IDENTIDADE:
        valor = linha.get(coluna)
        partes.append('' if valor is None else str(valor))
    return hashlib.sha1('\x1f'.join(partes).encode('utf-8')).hexdigest()


class LinhaAuditoria(models.Model):
    """Uma divergência da auditoria, espelhada do SAP."""

    chave = models.CharField(max_length=40, unique=True, verbose_name='Chave')

    # ── o que veio do SAP (o resto da linha fica em `dados`) ────────────────
    tipo_erro = models.CharField(max_length=60, blank=True, db_index=True, verbose_name='Tipo do erro')
    data_venda = models.DateField(null=True, blank=True, db_index=True, verbose_name='Data da venda')
    pdv = models.CharField(max_length=120, blank=True, db_index=True, verbose_name='PDV')
    loja_vivogo = models.CharField(max_length=120, blank=True, verbose_name='Loja Vivo go')
    id_venda = models.CharField(max_length=60, blank=True, db_index=True, verbose_name='ID da venda')
    nome_cliente = models.CharField(max_length=180, blank=True, verbose_name='Cliente')
    documento_vivogo = models.CharField(max_length=30, blank=True, verbose_name='Documento')
    produto_vivogo = models.CharField(max_length=255, blank=True, verbose_name='Produto Vivo go')
    produto_sap = models.CharField(max_length=255, blank=True, verbose_name='Produto SAP')
    sku_vivogo = models.CharField(max_length=60, blank=True, verbose_name='SKU Vivo go')
    serial_vivogo = models.CharField(max_length=300, blank=True, verbose_name='Serial Vivo go')
    serial_sap = models.TextField(blank=True, verbose_name='Serial SAP')
    ordem_sap = models.CharField(max_length=30, blank=True, verbose_name='Ordem SAP')
    num_fat_sap = models.CharField(max_length=40, blank=True, verbose_name='Nota fiscal')
    status_nf = models.CharField(max_length=60, blank=True, verbose_name='Status da NF')
    valor_sap = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True,
                                    verbose_name='Valor SAP')
    valor_vivogo = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True,
                                       verbose_name='Valor Vivo go')
    diferenca_valor = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True,
                                          verbose_name='Diferença de valor')
    situacao_valor = models.CharField(max_length=180, blank=True, verbose_name='Situação do valor')
    status_documento = models.CharField(max_length=40, blank=True, verbose_name='Documento')
    status_produto = models.CharField(max_length=40, blank=True, verbose_name='Produto')
    status_valor = models.CharField(max_length=40, blank=True, verbose_name='Valor')
    status_loja = models.CharField(max_length=40, blank=True, verbose_name='Loja')
    status_data = models.CharField(max_length=40, blank=True, verbose_name='Data')
    diferenca_dias = models.IntegerField(null=True, blank=True, verbose_name='Diferença em dias')
    diferenca_explicita = models.TextField(blank=True, verbose_name='O que está diferente')
    pontuacao_indicios = models.IntegerField(null=True, blank=True, verbose_name='Pontuação dos indícios')
    dados = models.JSONField(default=dict, blank=True, verbose_name='Linha completa')

    # ── controle do espelho ─────────────────────────────────────────────────
    ativa = models.BooleanField(default=True, db_index=True, verbose_name='Ainda está na auditoria')
    visto_em = models.DateTimeField(default=timezone.now, verbose_name='Visto pela última vez em')
    sumiu_em = models.DateTimeField(null=True, blank=True, verbose_name='Sumiu da auditoria em')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    # ── o que é do portal ───────────────────────────────────────────────────
    resolvida = models.BooleanField(default=False, db_index=True, verbose_name='Resolvida')
    resolvida_em = models.DateTimeField(null=True, blank=True, verbose_name='Marcada em')
    resolvida_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='linhas_sap_marcadas', verbose_name='Quem marcou')
    observacao = models.TextField(blank=True, verbose_name='Observação')

    class Meta:
        verbose_name = 'Linha da auditoria SAP'
        verbose_name_plural = 'Linhas da auditoria SAP'
        ordering = ['-data_venda', 'pdv', 'id_venda']
        indexes = [
            models.Index(fields=['ativa', 'resolvida']),
            models.Index(fields=['tipo_erro', 'data_venda']),
            models.Index(fields=['pdv', 'data_venda']),
        ]

    def __str__(self):
        return f'{self.tipo_erro} — venda {self.id_venda or "?"} ({self.pdv})'

    @property
    def diferenca_em_reais(self):
        """Quanto de dinheiro está em jogo nesta linha (sempre positivo)."""
        return abs(self.diferenca_valor) if self.diferenca_valor is not None else None

    @property
    def situacao(self):
        return 'RESOLVIDA' if self.resolvida else 'ABERTA'

    @property
    def voltou(self):
        """Foi marcada como resolvida mas continua aparecendo na auditoria."""
        return bool(self.resolvida and self.ativa)

    def marcar(self, usuario, resolvida, observacao=''):
        """Marca (ou desmarca) como resolvida, guardando quem foi.

        Toda passagem fica no histórico: desmarcar não apaga quem tinha
        marcado, senão a pergunta "quem deu isso como resolvido?" ficaria sem
        resposta justamente quando ela importa.
        """
        self.resolvida = bool(resolvida)
        self.resolvida_em = timezone.now()
        self.resolvida_por = usuario if getattr(usuario, 'pk', None) else None
        if observacao:
            self.observacao = observacao[:2000]
        self.save(update_fields=['resolvida', 'resolvida_em', 'resolvida_por',
                                 'observacao', 'atualizado_em'])
        return MarcacaoAuditoria.objects.create(
            linha=self, usuario=self.resolvida_por, resolvida=self.resolvida,
            observacao=(observacao or '')[:2000])


class MarcacaoAuditoria(models.Model):
    """Cada vez que alguém marcou ou desmarcou uma linha."""

    linha = models.ForeignKey(LinhaAuditoria, on_delete=models.CASCADE,
                              related_name='marcacoes', verbose_name='Linha')
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='marcacoes_sap', verbose_name='Quem')
    resolvida = models.BooleanField(verbose_name='Marcou como resolvida')
    observacao = models.TextField(blank=True, verbose_name='Observação')
    quando = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='Quando')

    class Meta:
        verbose_name = 'Marcação da auditoria SAP'
        verbose_name_plural = 'Marcações da auditoria SAP'
        ordering = ['-quando']

    def __str__(self):
        quem = self.usuario.full_name if self.usuario else 'alguém'
        return f'{quem} {"resolveu" if self.resolvida else "reabriu"} em {self.quando:%d/%m/%Y %H:%M}'


class SincronizacaoAuditoria(models.Model):
    """Uma leitura do MySQL do SAP — para a tela dizer de quando são os dados."""

    quando = models.DateTimeField(auto_now_add=True, verbose_name='Quando')
    por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='sincronizacoes_sap', verbose_name='Quem mandou atualizar')
    novas = models.IntegerField(default=0, verbose_name='Linhas novas')
    atualizadas = models.IntegerField(default=0, verbose_name='Linhas que mudaram')
    sumiram = models.IntegerField(default=0, verbose_name='Linhas que saíram')
    total = models.IntegerField(default=0, verbose_name='Linhas na auditoria')
    segundos = models.FloatField(default=0, verbose_name='Tempo (s)')
    erro = models.TextField(blank=True, verbose_name='Erro')

    class Meta:
        verbose_name = 'Sincronização da auditoria SAP'
        verbose_name_plural = 'Sincronizações da auditoria SAP'
        ordering = ['-quando']

    def __str__(self):
        return f'{self.quando:%d/%m/%Y %H:%M} — {self.total} linhas'

    @property
    def deu_certo(self):
        return not self.erro
