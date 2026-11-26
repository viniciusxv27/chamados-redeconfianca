"""Emissão de NF-e (modelo 55) pelo portal — saída, entrada e devolução.

- **ConfiguracaoNFe** (uma linha só): o interruptor (nasce DESLIGADO), o
  ambiente (nasce em HOMOLOGAÇÃO), o certificado A1 e a senha — os dois
  cifrados (Fernet) e digitados na tela de configuração, nunca no código —, e o
  responsável técnico que vai no XML.
- **EmitenteNFe**: cada CNPJ que emite (matriz ou filial), com IE, regime
  (CRT), endereço, série e o próximo número. Ligado à loja quando é loja.
- **NotaFiscalEmitida**: a nota, do rascunho à autorização (ou rejeição),
  com o XML assinado, o XML autorizado (nfeProc) e o retorno da SEFAZ.
- **ItemNotaFiscal**: os produtos (NCM, CFOP, CST/CSOSN, alíquotas).
- **EventoNotaFiscal**: cancelamento e carta de correção.

As regras ficam em ``financeiro/nfe.py``; a DANFE em ``financeiro/danfe.py``.
"""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

ZERO = Decimal('0')


class ConfiguracaoNFe(models.Model):
    AMBIENTES = [('2', 'Homologação (teste, sem valor fiscal)'), ('1', 'Produção (nota de verdade)')]

    ligado = models.BooleanField(default=False, verbose_name='Emissão ligada')
    ambiente = models.CharField(max_length=1, choices=AMBIENTES, default='2', verbose_name='Ambiente')
    certificado_cifrado = models.TextField(blank=True, default='', verbose_name='Certificado A1 (cifrado)')
    senha_cifrada = models.TextField(blank=True, default='', verbose_name='Senha do certificado (cifrada)')
    certificado_titular = models.CharField(max_length=200, blank=True, default='', verbose_name='Titular')
    certificado_cnpj = models.CharField(max_length=14, blank=True, default='', verbose_name='CNPJ do certificado')
    certificado_validade = models.DateTimeField(null=True, blank=True, verbose_name='Validade do certificado')
    resp_tec_cnpj = models.CharField(max_length=14, blank=True, default='', verbose_name='Resp. técnico: CNPJ')
    resp_tec_contato = models.CharField(max_length=60, blank=True, default='', verbose_name='Resp. técnico: contato')
    resp_tec_email = models.CharField(max_length=60, blank=True, default='', verbose_name='Resp. técnico: e-mail')
    resp_tec_fone = models.CharField(max_length=14, blank=True, default='', verbose_name='Resp. técnico: telefone')
    atualizado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+', verbose_name='Atualizado por')
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuração da NF-e'
        verbose_name_plural = 'Configuração da NF-e'

    def __str__(self):
        return f'NF-e · {"ligada" if self.ligado else "desligada"} · {self.get_ambiente_display()}'

    @classmethod
    def atual(cls):
        config, _ = cls.objects.get_or_create(pk=1)
        return config

    @property
    def tem_certificado(self):
        return bool(self.certificado_cifrado and self.senha_cifrada)

    @property
    def producao(self):
        return self.ambiente == '1'


class EmitenteNFe(models.Model):
    CRTS = [('1', '1 · Simples Nacional'), ('2', '2 · Simples Nacional, excesso de sublimite'),
            ('3', '3 · Regime normal'), ('4', '4 · Simples Nacional · MEI')]

    loja = models.ForeignKey('users.Sector', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                             verbose_name='Loja')
    cnpj = models.CharField(max_length=14, unique=True, verbose_name='CNPJ')
    razao_social = models.CharField(max_length=60, verbose_name='Razão social')
    nome_fantasia = models.CharField(max_length=60, blank=True, default='', verbose_name='Nome fantasia')
    ie = models.CharField(max_length=14, verbose_name='Inscrição estadual')
    crt = models.CharField(max_length=1, choices=CRTS, default='3', verbose_name='Regime tributário (CRT)')
    logradouro = models.CharField(max_length=60, verbose_name='Logradouro')
    numero = models.CharField(max_length=60, default='S/N', verbose_name='Número')
    complemento = models.CharField(max_length=60, blank=True, default='', verbose_name='Complemento')
    bairro = models.CharField(max_length=60, verbose_name='Bairro')
    codigo_municipio = models.CharField(max_length=7, verbose_name='Código IBGE do município')
    municipio = models.CharField(max_length=60, verbose_name='Município')
    uf = models.CharField(max_length=2, verbose_name='UF')
    cep = models.CharField(max_length=8, verbose_name='CEP')
    telefone = models.CharField(max_length=14, blank=True, default='', verbose_name='Telefone')
    serie = models.PositiveSmallIntegerField(default=1, verbose_name='Série')
    proximo_numero = models.PositiveIntegerField(default=1, verbose_name='Próximo número (produção)')
    proximo_numero_homologacao = models.PositiveIntegerField(default=1, verbose_name='Próximo número (homologação)')
    ativo = models.BooleanField(default=True, verbose_name='Ativo')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['razao_social', 'cnpj']
        verbose_name = 'Emitente de NF-e'
        verbose_name_plural = 'Emitentes de NF-e'

    def __str__(self):
        return f'{self.nome_fantasia or self.razao_social} · {self.cnpj_formatado}'

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Notas importadas antes de o CNPJ ser cadastrado passam a ser deste emitente (e da loja dele).
        NotaFiscalEmitida.objects.filter(emitente__isnull=True, nosso_cnpj=self.cnpj).update(emitente=self, loja=self.loja)

    @property
    def cnpj_formatado(self):
        c = self.cnpj
        return f'{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}' if len(c) == 14 else c

    @property
    def simples(self):
        return self.crt in ('1', '4')


class NotaFiscalEmitida(models.Model):
    TIPOS = [('1', 'Saída'), ('0', 'Entrada')]
    FINALIDADES = [('1', 'Normal'), ('2', 'Complementar'), ('3', 'Ajuste'), ('4', 'Devolução')]
    STATUS = [
        ('RASCUNHO', 'Rascunho'),
        ('PROCESSANDO', 'Em processamento na SEFAZ'),
        ('AUTORIZADA', 'Autorizada'),
        ('REJEITADA', 'Rejeitada'),
        ('DENEGADA', 'Denegada'),
        ('CANCELADA', 'Cancelada'),
    ]
    IND_IE = [('1', 'Contribuinte do ICMS (tem IE)'), ('2', 'Contribuinte isento de IE'), ('9', 'Não contribuinte')]
    PRESENCA = [('0', 'Não se aplica (complementar, ajuste, devolução)'), ('1', 'Operação presencial'),
                ('2', 'Pela internet'), ('3', 'Teleatendimento'), ('4', 'Entrega em domicílio'),
                ('9', 'Outros, não presencial')]
    FRETES = [('9', 'Sem frete'), ('0', 'Por conta do emitente (CIF)'), ('1', 'Por conta do destinatário (FOB)'),
              ('2', 'Por conta de terceiros'), ('3', 'Próprio, do remetente'), ('4', 'Próprio, do destinatário')]
    PAGAMENTOS = [('90', 'Sem pagamento'), ('01', 'Dinheiro'), ('15', 'Boleto'), ('17', 'PIX'),
                  ('03', 'Cartão de crédito'), ('04', 'Cartão de débito'), ('99', 'Outros')]
    # PORTAL: criada e enviada aqui. PROPRIA: nossa, emitida em outro sistema e importada pelo XML.
    # TERCEIRO: recebida (fornecedor ou outro emitente para um CNPJ nosso), importada pelo XML.
    ORIGENS = [('PORTAL', 'Emitida no portal'), ('PROPRIA', 'Importada (emitida em outro sistema)'),
               ('TERCEIRO', 'Recebida (de fornecedor/terceiro)')]

    origem = models.CharField(max_length=8, choices=ORIGENS, default='PORTAL', db_default='PORTAL', db_index=True,
                              verbose_name='Origem')
    # Vazio só em nota importada cujo CNPJ nosso ainda não é emitente cadastrado (nosso_cnpj diz qual é).
    emitente = models.ForeignKey(EmitenteNFe, on_delete=models.PROTECT, related_name='notas', null=True, blank=True,
                                 verbose_name='Emitente')
    nosso_cnpj = models.CharField(max_length=14, blank=True, default='', db_default='', db_index=True,
                                  verbose_name='CNPJ nosso na nota')
    nosso_nome = models.CharField(max_length=60, blank=True, default='', db_default='', verbose_name='Nome nosso na nota')
    loja = models.ForeignKey('users.Sector', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                             verbose_name='Loja')
    tipo_operacao = models.CharField(max_length=1, choices=TIPOS, default='1', verbose_name='Tipo')
    finalidade = models.CharField(max_length=1, choices=FINALIDADES, default='1', verbose_name='Finalidade')
    natureza = models.CharField(max_length=60, verbose_name='Natureza da operação')
    chave_referenciada = models.CharField(max_length=44, blank=True, default='', verbose_name='Chave da nota referenciada')

    dest_documento = models.CharField(max_length=14, verbose_name='CNPJ/CPF do destinatário')
    dest_nome = models.CharField(max_length=60, verbose_name='Nome / razão social')
    dest_ie = models.CharField(max_length=14, blank=True, default='', verbose_name='IE do destinatário')
    dest_ind_ie = models.CharField(max_length=1, choices=IND_IE, default='9', verbose_name='Indicador da IE')
    dest_email = models.CharField(max_length=60, blank=True, default='', verbose_name='E-mail')
    dest_telefone = models.CharField(max_length=14, blank=True, default='', verbose_name='Telefone')
    dest_logradouro = models.CharField(max_length=60, verbose_name='Logradouro')
    dest_numero = models.CharField(max_length=60, default='S/N', verbose_name='Número')
    dest_complemento = models.CharField(max_length=60, blank=True, default='', verbose_name='Complemento')
    dest_bairro = models.CharField(max_length=60, verbose_name='Bairro')
    dest_codigo_municipio = models.CharField(max_length=7, verbose_name='Código IBGE do município')
    dest_municipio = models.CharField(max_length=60, verbose_name='Município')
    dest_uf = models.CharField(max_length=2, verbose_name='UF')
    dest_cep = models.CharField(max_length=8, blank=True, default='', verbose_name='CEP')

    consumidor_final = models.BooleanField(default=False, verbose_name='Consumidor final')
    presenca = models.CharField(max_length=1, choices=PRESENCA, default='9', verbose_name='Presença do comprador')
    modalidade_frete = models.CharField(max_length=1, choices=FRETES, default='9', verbose_name='Frete')
    forma_pagamento = models.CharField(max_length=2, choices=PAGAMENTOS, default='90', verbose_name='Pagamento')
    valor_frete = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, verbose_name='Frete')
    valor_seguro = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, verbose_name='Seguro')
    valor_outras = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, verbose_name='Outras despesas')
    info_complementar = models.TextField(blank=True, default='', verbose_name='Informações complementares')

    # Totais (recalculados a cada gravação pelos itens)
    valor_produtos = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, verbose_name='Produtos')
    valor_desconto = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, verbose_name='Desconto')
    base_icms = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, verbose_name='Base do ICMS')
    valor_icms = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, verbose_name='ICMS')
    valor_pis = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, verbose_name='PIS')
    valor_cofins = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, verbose_name='Cofins')
    valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, verbose_name='Total da nota')

    # SEFAZ
    ambiente = models.CharField(max_length=1, default='2', verbose_name='Ambiente')
    serie = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='Série')
    numero = models.PositiveIntegerField(null=True, blank=True, verbose_name='Número')
    codigo_numerico = models.CharField(max_length=8, blank=True, default='', verbose_name='Código numérico (cNF)')
    chave = models.CharField(max_length=44, blank=True, default='', db_index=True, verbose_name='Chave de acesso')
    status = models.CharField(max_length=12, choices=STATUS, default='RASCUNHO', db_index=True, verbose_name='Situação')
    cstat = models.CharField(max_length=10, blank=True, default='', verbose_name='Código da SEFAZ')
    motivo = models.CharField(max_length=500, blank=True, default='', verbose_name='Resposta da SEFAZ')
    recibo = models.CharField(max_length=20, blank=True, default='', verbose_name='Recibo do lote')
    protocolo = models.CharField(max_length=20, blank=True, default='', verbose_name='Protocolo de autorização')
    emitida_em = models.DateTimeField(null=True, blank=True, verbose_name='Emissão (dhEmi)')
    autorizada_em = models.DateTimeField(null=True, blank=True, verbose_name='Autorizada em')
    cancelada_em = models.DateTimeField(null=True, blank=True, verbose_name='Cancelada em')
    xml_assinado = models.TextField(blank=True, default='', verbose_name='XML assinado (último envio)')
    xml_autorizado = models.TextField(blank=True, default='', verbose_name='XML autorizado (nfeProc)')
    historico = models.JSONField(default=list, blank=True, verbose_name='Conversa com a SEFAZ')

    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', verbose_name='Criada por')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-criado_em', '-id']
        verbose_name = 'NF-e emitida'
        verbose_name_plural = 'NF-e emitidas'
        constraints = [
            # A numeração é nossa: nota recebida de terceiro tem a numeração do fornecedor e fica de fora.
            models.UniqueConstraint(fields=['emitente', 'ambiente', 'serie', 'numero'], name='fin_nfe_numero_unico',
                                    condition=models.Q(numero__isnull=False) & ~models.Q(origem='TERCEIRO')),
            models.UniqueConstraint(fields=['chave'], name='fin_nfe_chave_unica', condition=~models.Q(chave='')),
        ]

    def __str__(self):
        numero = f'nº {self.numero}' if self.numero else 'rascunho'
        return f'NF-e {numero} · {self.dest_nome}'

    @property
    def editavel(self):
        return self.origem == 'PORTAL' and self.status in ('RASCUNHO', 'REJEITADA')

    @property
    def importada(self):
        return self.origem != 'PORTAL'

    @property
    def recebida(self):
        return self.origem == 'TERCEIRO'

    @property
    def so_resumo(self):
        """Recebida que veio só com o resumo da SEFAZ (sem o XML completo: sem itens nem DANFE)."""
        return self.importada and not self.xml_autorizado

    @property
    def nosso_rotulo(self):
        if self.emitente_id:
            return self.emitente.nome_fantasia or self.emitente.razao_social
        return self.nosso_nome or self.nosso_cnpj_formatado

    @property
    def nosso_cnpj_formatado(self):
        c = self.emitente.cnpj if self.emitente_id else self.nosso_cnpj
        return f'{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}' if len(c or '') == 14 else (c or '')

    @property
    def devolucao(self):
        return self.finalidade == '4'

    @property
    def rotulo_tipo(self):
        if self.devolucao:
            return 'Devolução (' + self.get_tipo_operacao_display().lower() + ')'
        return self.get_tipo_operacao_display()

    @property
    def dest_documento_formatado(self):
        d = self.dest_documento
        if len(d) == 14:
            return f'{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}'
        if len(d) == 11:
            return f'{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}'
        return d

    @property
    def pode_cancelar_ate(self):
        """Prazo comum de cancelamento: 24 h da autorização (depois a SEFAZ pode recusar)."""
        if not self.autorizada_em:
            return None
        return self.autorizada_em + timedelta(hours=24)

    def anotar(self, operacao, cstat='', motivo=''):
        self.historico = list(self.historico or []) + [{
            'quando': timezone.now().isoformat(timespec='seconds'), 'operacao': operacao,
            'cstat': str(cstat)[:10], 'motivo': str(motivo)[:300]}]


class ItemNotaFiscal(models.Model):
    nota = models.ForeignKey(NotaFiscalEmitida, on_delete=models.CASCADE, related_name='itens', verbose_name='Nota')
    ordem = models.PositiveSmallIntegerField(default=1, verbose_name='Item')
    codigo = models.CharField(max_length=60, verbose_name='Código do produto')
    descricao = models.CharField(max_length=120, verbose_name='Descrição')
    ean = models.CharField(max_length=14, blank=True, default='', verbose_name='GTIN/EAN')
    ncm = models.CharField(max_length=8, verbose_name='NCM')
    cest = models.CharField(max_length=7, blank=True, default='', verbose_name='CEST')
    cfop = models.CharField(max_length=4, verbose_name='CFOP')
    unidade = models.CharField(max_length=6, default='UN', verbose_name='Unidade')
    quantidade = models.DecimalField(max_digits=15, decimal_places=4, verbose_name='Quantidade')
    valor_unitario = models.DecimalField(max_digits=21, decimal_places=10, verbose_name='Valor unitário')
    valor_desconto = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, verbose_name='Desconto')
    origem = models.CharField(max_length=1, default='0', verbose_name='Origem da mercadoria')
    cst_icms = models.CharField(max_length=3, verbose_name='CST / CSOSN do ICMS')
    reducao_bc_icms = models.DecimalField(max_digits=7, decimal_places=4, default=ZERO, verbose_name='% redução da BC')
    aliquota_icms = models.DecimalField(max_digits=7, decimal_places=4, default=ZERO, verbose_name='% ICMS')
    cst_pis = models.CharField(max_length=2, default='49', verbose_name='CST do PIS')
    aliquota_pis = models.DecimalField(max_digits=7, decimal_places=4, default=ZERO, verbose_name='% PIS')
    cst_cofins = models.CharField(max_length=2, default='49', verbose_name='CST da Cofins')
    aliquota_cofins = models.DecimalField(max_digits=7, decimal_places=4, default=ZERO, verbose_name='% Cofins')
    info_adicional = models.CharField(max_length=500, blank=True, default='', verbose_name='Informação adicional')
    # Calculados
    valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, verbose_name='Valor dos produtos')
    base_icms = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, verbose_name='Base do ICMS')
    valor_icms = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, verbose_name='ICMS')
    valor_pis = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, verbose_name='PIS')
    valor_cofins = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, verbose_name='Cofins')

    class Meta:
        ordering = ['nota', 'ordem', 'id']
        verbose_name = 'Item da NF-e'
        verbose_name_plural = 'Itens da NF-e'

    def __str__(self):
        return f'{self.ordem}. {self.descricao}'


class EventoNotaFiscal(models.Model):
    TIPOS = [('110111', 'Cancelamento'), ('110110', 'Carta de correção')]

    nota = models.ForeignKey(NotaFiscalEmitida, on_delete=models.CASCADE, related_name='eventos', verbose_name='Nota')
    tipo = models.CharField(max_length=6, choices=TIPOS, verbose_name='Evento')
    sequencia = models.PositiveSmallIntegerField(default=1, verbose_name='Sequência')
    texto = models.TextField(verbose_name='Justificativa / correção')
    sucesso = models.BooleanField(default=False, verbose_name='Registrado')
    cstat = models.CharField(max_length=10, blank=True, default='', verbose_name='Código da SEFAZ')
    motivo = models.CharField(max_length=500, blank=True, default='', verbose_name='Resposta da SEFAZ')
    protocolo = models.CharField(max_length=20, blank=True, default='', verbose_name='Protocolo')
    xml_enviado = models.TextField(blank=True, default='', verbose_name='Evento assinado')
    xml_retorno = models.TextField(blank=True, default='', verbose_name='Retorno da SEFAZ')
    enviado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+', verbose_name='Enviado por')
    enviado_em = models.DateTimeField(default=timezone.now, verbose_name='Enviado em')

    class Meta:
        ordering = ['nota', 'enviado_em', 'id']
        verbose_name = 'Evento da NF-e'
        verbose_name_plural = 'Eventos da NF-e'

    def __str__(self):
        return f'{self.get_tipo_display()} · {self.nota_id}'
