import os
import uuid

from django.db import models
from django.conf import settings
from django.utils import timezone

from core.storage import get_media_storage


# ─── Upload paths ────────────────────────────────────────────────────────────

def upload_document_pdf(instance, filename):
    """Caminho do PDF original enviado pelo administrador."""
    ext = (filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'pdf')
    token = uuid.uuid4().hex[:10]
    return os.path.join('documentos', 'originais', f"doc_{token}.{ext}")


def upload_signed_pdf(instance, filename):
    """Caminho do PDF assinado (original + folha de certificado)."""
    token = uuid.uuid4().hex[:10]
    return os.path.join(
        'documentos', 'assinados',
        f"assinado_{instance.document_id}_{instance.user_id}_{token}.pdf",
    )


# ─── Categoria ───────────────────────────────────────────────────────────────

class DocumentCategory(models.Model):
    """Categoria para agrupar documentos (ex.: Contratos, Políticas, Termos)."""

    name = models.CharField(max_length=120, unique=True, verbose_name='Nome')
    description = models.TextField(blank=True, verbose_name='Descrição')
    color = models.CharField(
        max_length=20, default='green', verbose_name='Cor',
        help_text='Cor do selo no portal (ex.: green, blue, purple, red, amber).',
    )
    icon = models.CharField(
        max_length=40, default='fa-file-contract', verbose_name='Ícone',
        help_text='Classe do Font Awesome (ex.: fa-file-contract).',
    )
    is_active = models.BooleanField(default=True, verbose_name='Ativa')

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='document_categories_created',
        verbose_name='Criada por',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Categoria de Documento'
        verbose_name_plural = 'Categorias de Documentos'
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def document_count(self):
        return self.documents.count()


# ─── Documento ───────────────────────────────────────────────────────────────

class Document(models.Model):
    """Documento (PDF) enviado para assinatura de um ou mais signatários."""

    title = models.CharField(max_length=200, verbose_name='Título')
    category = models.ForeignKey(
        DocumentCategory,
        on_delete=models.PROTECT,
        related_name='documents',
        verbose_name='Categoria',
    )
    description = models.TextField(blank=True, verbose_name='Descrição')
    pdf_file = models.FileField(
        upload_to=upload_document_pdf,
        storage=get_media_storage(),
        verbose_name='Arquivo PDF',
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='documents_created',
        verbose_name='Criado por',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Documento'
        verbose_name_plural = 'Documentos'
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    # Estatísticas de assinatura ------------------------------------------------
    @property
    def total_signers(self):
        return self.signatures.count()

    @property
    def signed_count(self):
        return self.signatures.filter(signed_at__isnull=False).count()

    @property
    def pending_count(self):
        return self.signatures.filter(signed_at__isnull=True).count()

    @property
    def is_fully_signed(self):
        total = self.total_signers
        return total > 0 and self.signed_count == total


# ─── Assinatura / Atribuição ─────────────────────────────────────────────────

class DocumentSignature(models.Model):
    """
    Atribuição de um documento a um signatário e o registro da assinatura.

    Um registro por (documento, usuário). Guarda os dados probatórios da
    assinatura eletrônica e o PDF assinado (original + folha de certificado).
    """

    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name='signatures',
        verbose_name='Documento',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='document_signatures',
        verbose_name='Signatário',
    )

    # Registro da assinatura ---------------------------------------------------
    signed_at = models.DateTimeField(null=True, blank=True, verbose_name='Assinado em')
    signature_image = models.TextField(
        blank=True, verbose_name='Assinatura (base64)',
        help_text='Imagem da assinatura em base64 (PNG).',
    )
    signature_ip = models.GenericIPAddressField(
        null=True, blank=True, verbose_name='IP da assinatura')
    signature_user_agent = models.TextField(
        blank=True, verbose_name='User-Agent da assinatura')
    signature_hash = models.CharField(
        max_length=64, blank=True, verbose_name='Hash de verificação',
        help_text='SHA-256 do conteúdo assinado para garantir integridade.',
    )
    signed_pdf = models.FileField(
        upload_to=upload_signed_pdf,
        storage=get_media_storage(),
        null=True, blank=True,
        verbose_name='PDF assinado',
    )

    # Metadados ----------------------------------------------------------------
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='document_assignments_made',
        verbose_name='Atribuído por',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Assinatura de Documento'
        verbose_name_plural = 'Assinaturas de Documentos'
        ordering = ['-created_at']
        unique_together = ['document', 'user']

    def __str__(self):
        status = 'assinado' if self.is_signed else 'pendente'
        return f"{self.document.title} – {self.user.full_name} ({status})"

    @property
    def is_signed(self):
        return bool(self.signed_at)
