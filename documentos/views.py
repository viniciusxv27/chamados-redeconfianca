import hashlib
import json
import uuid

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponse, Http404
from django.utils import timezone
from django.db.models import Q, Count
from django.views.decorators.http import require_POST, require_http_methods
from django.core.files.base import ContentFile
from django.views.decorators.clickjacking import xframe_options_sameorigin

from .models import DocumentCategory, Document, DocumentSignature
from users.models import User


HIERARCHY_RANK = {
    'PADRAO': 0,
    'ADMINISTRATIVO': 1,
    'SUPERVISOR': 2,
    'ADMIN': 3,
    'SUPERADMIN': 4,
}


def is_superadmin(user):
    return HIERARCHY_RANK.get(getattr(user, 'hierarchy', 'PADRAO'), 0) >= HIERARCHY_RANK['SUPERADMIN']


def get_client_ip(request):
    """IP real do cliente, respeitando o proxy reverso."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def _read_pdf_bytes(field_file):
    """Lê os bytes de um FileField (local ou S3/MinIO)."""
    if not field_file or not field_file.name:
        return None
    try:
        field_file.open('rb')
        data = field_file.read()
        field_file.close()
        return data or None
    except Exception:
        return None


def _notify_assignment(signatures, document, actor):
    """Notifica (best-effort) os signatários de um novo documento atribuído."""
    try:
        from core.models import NotificationMixin
        from django.urls import reverse
        users = [s.user for s in signatures]
        NotificationMixin.create_notifications_for_users(
            users=users,
            title='Novo documento para assinatura',
            message=f'"{document.title}" foi atribuído a você para assinatura digital.',
            notification_type='SYSTEM',
            related_object_id=document.pk,
            related_url=reverse('documentos:my_documents'),
        )
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# ÁREA DO USUÁRIO (signatário)
# ══════════════════════════════════════════════════════════════════════════════

@login_required
def my_documents(request):
    """Lista os documentos atribuídos ao usuário logado (pendentes e assinados)."""
    signatures = (
        DocumentSignature.objects
        .filter(user=request.user, document__is_visible=True)
        .select_related('document', 'document__category')
        .order_by('-created_at')
    )

    status = request.GET.get('status', '').strip()
    if status == 'pending':
        signatures = signatures.filter(signed_at__isnull=True)
    elif status == 'signed':
        signatures = signatures.filter(signed_at__isnull=False)

    pending_total = DocumentSignature.objects.filter(
        user=request.user, signed_at__isnull=True).count()
    signed_total = DocumentSignature.objects.filter(
        user=request.user, signed_at__isnull=False).count()

    return render(request, 'documentos/my_documents.html', {
        'signatures': signatures,
        'status': status,
        'pending_total': pending_total,
        'signed_total': signed_total,
        'is_admin': is_superadmin(request.user),
    })


@login_required
def document_detail(request, pk):
    """Detalhe de um documento atribuído: visualização do PDF + assinatura."""
    signature = get_object_or_404(
        DocumentSignature.objects.select_related('document', 'document__category'),
        pk=pk,
    )

    if not signature.document.is_visible and not is_superadmin(request.user):
        messages.error(request, 'Este documento ainda não foi liberado para visualização.')
        return redirect('documentos:my_documents')

    if signature.user != request.user and not is_superadmin(request.user):
        messages.error(request, 'Você não tem permissão para acessar este documento.')
        return redirect('documentos:my_documents')

    return render(request, 'documentos/document_detail.html', {
        'signature': signature,
        'document': signature.document,
        'can_sign': signature.user == request.user and not signature.is_signed,
    })


@login_required
@xframe_options_sameorigin
def document_pdf(request, pk):
    """Serve o PDF original (inline).

    O portal roda com X_FRAME_OPTIONS no padrão do Django (DENY), então sem este
    decorator o navegador recusa a renderização dentro do <iframe> da tela de
    detalhe — mesmo sendo a mesma origem.
    """
    signature = get_object_or_404(DocumentSignature.objects.select_related('document'), pk=pk)

    if signature.user != request.user and not is_superadmin(request.user):
        return HttpResponse('Sem permissão.', status=403)

    pdf_bytes = _read_pdf_bytes(signature.document.pdf_file)
    if not pdf_bytes:
        raise Http404('PDF não encontrado.')

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="documento_{signature.document_id}.pdf"'
    return response


@login_required
def document_signed_pdf(request, pk):
    """Baixa o PDF assinado (original + folha de Certificado de Assinatura)."""
    signature = get_object_or_404(DocumentSignature.objects.select_related('document', 'user'), pk=pk)

    if signature.user != request.user and not is_superadmin(request.user):
        return HttpResponse('Sem permissão.', status=403)

    if not signature.is_signed:
        messages.error(request, 'Este documento ainda não foi assinado.')
        return redirect('documentos:document_detail', pk=signature.pk)

    # PDF assinado persistido no armazenamento.
    signed_bytes = _read_pdf_bytes(signature.signed_pdf)

    # Fallback: gera na hora caso a persistência tenha falhado no passado.
    if not signed_bytes:
        signed_bytes = _build_signed_bytes(signature)
        if signed_bytes:
            try:
                signature.signed_pdf.save(f'assinado_{signature.pk}.pdf',
                                          ContentFile(signed_bytes), save=True)
            except Exception:
                pass

    if not signed_bytes:
        messages.error(request, 'Não foi possível gerar o documento assinado.')
        return redirect('documentos:document_detail', pk=signature.pk)

    response = HttpResponse(signed_bytes, content_type='application/pdf')
    response['Content-Disposition'] = (
        f'attachment; filename="documento_assinado_{signature.document_id}_{signature.user_id}.pdf"'
    )
    return response


def _build_signed_bytes(signature):
    """Monta os bytes do PDF assinado usando a folha de certificado do portal."""
    from core.signature_cert import build_signed_pdf

    document = signature.document
    original_bytes = _read_pdf_bytes(document.pdf_file)
    signed_str = (
        timezone.localtime(signature.signed_at).strftime('%d/%m/%Y às %H:%M')
        if signature.signed_at else ''
    )
    extra = [f'Categoria: {document.category.name}']
    if document.description:
        extra.append(document.description[:120])

    return build_signed_pdf(
        original_bytes,
        doc_title=document.title,
        person_name=signature.user.full_name,
        cpf=getattr(signature.user, 'cpf', '') or '',
        signed_at_str=signed_str,
        ip=signature.signature_ip,
        record_id=signature.pk,
        signature_data_url=signature.signature_image,
        hash_value=signature.signature_hash,
        extra_lines=extra,
    )


@login_required
def api_sign_document(request, pk):
    """API para assinar digitalmente um documento atribuído ao usuário."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Método não permitido'}, status=405)

    signature = get_object_or_404(
        DocumentSignature.objects.select_related('document', 'user'), pk=pk)

    if not signature.document.is_visible:
        return JsonResponse(
            {'success': False, 'error': 'Este documento ainda não foi liberado.'}, status=403)

    if signature.user != request.user:
        return JsonResponse({'error': 'Sem permissão para assinar este documento.'}, status=403)

    if signature.is_signed:
        return JsonResponse({'error': 'Este documento já foi assinado.'}, status=409)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Dados inválidos.'}, status=400)

    signature_data = body.get('signature', '')
    if not signature_data:
        return JsonResponse({'error': 'Assinatura não fornecida.'}, status=400)

    if not signature_data.startswith('data:image/png;base64,'):
        return JsonResponse({'error': 'Formato de assinatura inválido.'}, status=400)

    document = signature.document
    signed_at = timezone.now()
    client_ip = get_client_ip(request)
    user_agent = request.META.get('HTTP_USER_AGENT', '')

    # Hash de verificação (SHA-256 dos dados do documento + assinatura).
    hash_content = (
        f"{signature.pk}|{document.pk}|{signature.user.pk}|"
        f"{document.title}|{signed_at.isoformat()}|{client_ip}|"
        f"{signature_data[:100]}"
    )
    signature_hash = hashlib.sha256(hash_content.encode('utf-8')).hexdigest()

    signature.signed_at = signed_at
    signature.signature_image = signature_data
    signature.signature_ip = client_ip or None
    signature.signature_user_agent = user_agent[:500]
    signature.signature_hash = signature_hash

    # Gera e persiste o PDF assinado (original + folha de certificado).
    signed_bytes = _build_signed_bytes(signature)
    if signed_bytes:
        try:
            signature.signed_pdf.save(
                f'assinado_{signature.pk}.pdf', ContentFile(signed_bytes), save=False)
        except Exception:
            pass

    signature.save()

    return JsonResponse({
        'success': True,
        'message': 'Documento assinado com sucesso.',
        'signed_at': timezone.localtime(signed_at).strftime('%d/%m/%Y às %H:%M'),
        'hash': signature_hash[:16] + '...',
    })


# ══════════════════════════════════════════════════════════════════════════════
# ÁREA ADMINISTRATIVA (SUPERADMIN)
# ══════════════════════════════════════════════════════════════════════════════

def _require_admin(request):
    if not is_superadmin(request.user):
        messages.error(request, 'Acesso restrito.')
        return False
    return True


@login_required
def admin_documents(request):
    """Painel de documentos (SUPERADMIN)."""
    if not _require_admin(request):
        return redirect('documentos:my_documents')

    documents = (
        Document.objects
        .select_related('category', 'created_by')
        .annotate(
            signers_total=Count('signatures', distinct=True),
            signers_signed=Count('signatures', filter=Q(signatures__signed_at__isnull=False), distinct=True),
        )
        .order_by('-created_at')
    )

    search = request.GET.get('q', '').strip()
    category_id = request.GET.get('category', '').strip()

    if search:
        documents = documents.filter(
            Q(title__icontains=search) | Q(description__icontains=search))
    if category_id:
        documents = documents.filter(category_id=category_id)

    categories = DocumentCategory.objects.all()

    return render(request, 'documentos/admin_documents.html', {
        'documents': documents,
        'categories': categories,
        'search': search,
        'selected_category': category_id,
    })


@login_required
def admin_document_create(request):
    """Cria um documento e atribui a signatários (SUPERADMIN)."""
    if not _require_admin(request):
        return redirect('documentos:my_documents')

    categories = DocumentCategory.objects.filter(is_active=True)
    # Inativos entram na lista: o caso mais comum de assinatura por link é
    # justamente o ex-colaborador, que já perdeu o acesso ao portal e mesmo
    # assim precisa assinar (rescisão, termo de devolução).
    users = User.objects.all().order_by('-is_active', 'first_name', 'last_name')

    if request.method == 'POST':
        title = (request.POST.get('title') or '').strip()
        category_id = request.POST.get('category')
        description = (request.POST.get('description') or '').strip()
        pdf_file = request.FILES.get('pdf_file')
        user_ids = request.POST.getlist('users')
        is_visible = request.POST.get('is_visible') == 'on'

        errors = []
        if not title:
            errors.append('Informe o título do documento.')
        if not category_id:
            errors.append('Selecione uma categoria.')
        if not pdf_file:
            errors.append('Envie o arquivo PDF.')
        elif not pdf_file.name.lower().endswith('.pdf'):
            errors.append('O arquivo deve ser um PDF.')
        if not user_ids:
            errors.append('Selecione pelo menos um signatário.')

        if errors:
            for e in errors:
                messages.error(request, e)
            return render(request, 'documentos/admin_document_form.html', {
                'categories': categories,
                'users': users,
                'form_data': request.POST,
                'selected_users': [int(u) for u in user_ids if u.isdigit()],
            })

        document = Document.objects.create(
            title=title,
            category_id=category_id,
            description=description,
            pdf_file=pdf_file,
            is_visible=is_visible,
            created_by=request.user,
        )

        selected = User.objects.filter(id__in=user_ids)
        created_sigs = [
            DocumentSignature.objects.create(
                document=document, user=u, assigned_by=request.user)
            for u in selected
        ]
        # Só avisa quem já pode abrir o documento — notificar sobre algo
        # invisível levaria a pessoa a uma tela de "não liberado".
        if document.is_visible:
            # Notificar quem está inativo não leva a lugar nenhum: ele não
            # entra no portal. Para essa pessoa a via é o link público.
            _notify_assignment([s for s in created_sigs if s.user.is_active],
                               document, request.user)

        messages.success(
            request,
            f'Documento "{document.title}" criado e atribuído a {len(created_sigs)} signatário(s).'
            + ('' if document.is_visible
               else ' Ele está oculto: os signatários só verão quando você liberar.'),
        )
        return redirect('documentos:admin_document_detail', pk=document.pk)

    return render(request, 'documentos/admin_document_form.html', {
        'categories': categories,
        'users': users,
        'selected_users': [],
    })


@login_required
def admin_document_detail(request, pk):
    """Detalhe administrativo do documento: signatários e status (SUPERADMIN)."""
    if not _require_admin(request):
        return redirect('documentos:my_documents')

    document = get_object_or_404(
        Document.objects.select_related('category', 'created_by'), pk=pk)
    signatures = document.signatures.select_related('user').order_by(
        'signed_at', 'user__first_name')

    # Usuários que ainda não são signatários deste documento (para adicionar).
    existing_ids = signatures.values_list('user_id', flat=True)
    available_users = User.objects.filter(is_active=True).exclude(
        id__in=existing_ids).order_by('first_name', 'last_name')

    return render(request, 'documentos/admin_document_detail.html', {
        'document': document,
        'signatures': signatures,
        'available_users': available_users,
    })


@login_required
def admin_add_signers(request, pk):
    """Adiciona signatários a um documento existente (SUPERADMIN)."""
    if not _require_admin(request):
        return redirect('documentos:my_documents')

    document = get_object_or_404(Document, pk=pk)

    if request.method == 'POST':
        user_ids = request.POST.getlist('users')
        selected = User.objects.filter(id__in=user_ids)
        created_sigs = []
        for u in selected:
            sig, created = DocumentSignature.objects.get_or_create(
                document=document, user=u,
                defaults={'assigned_by': request.user},
            )
            if created:
                created_sigs.append(sig)
        if created_sigs:
            _notify_assignment([g for g in created_sigs if g.user.is_active],
                               document, request.user)
            messages.success(request, f'{len(created_sigs)} signatário(s) adicionado(s).')
        else:
            messages.info(request, 'Nenhum novo signatário adicionado.')

    return redirect('documentos:admin_document_detail', pk=document.pk)


@login_required
def admin_remove_signer(request, pk):
    """Remove um signatário (apenas se ainda não assinou) (SUPERADMIN)."""
    if not _require_admin(request):
        return redirect('documentos:my_documents')

    signature = get_object_or_404(DocumentSignature, pk=pk)
    document_pk = signature.document_id

    if request.method == 'POST':
        if signature.is_signed:
            messages.error(request, 'Não é possível remover um signatário que já assinou.')
        else:
            signature.delete()
            messages.success(request, 'Signatário removido.')

    return redirect('documentos:admin_document_detail', pk=document_pk)


@login_required
def admin_remind_signer(request, pk):
    """Envia um lembrete de assinatura via WhatsApp (Z-API) ao signatário (SUPERADMIN)."""
    if not _require_admin(request):
        return redirect('documentos:my_documents')

    signature = get_object_or_404(
        DocumentSignature.objects.select_related('document', 'user'), pk=pk)
    document_pk = signature.document_id

    if request.method != 'POST':
        return redirect('documentos:admin_document_detail', pk=document_pk)

    if signature.is_signed:
        messages.info(request, 'Este signatário já assinou o documento.')
        return redirect('documentos:admin_document_detail', pk=document_pk)

    user = signature.user
    phone = (getattr(user, 'phone', '') or '').strip()
    if not phone:
        messages.error(
            request,
            f'{user.full_name} não possui um número de WhatsApp cadastrado.',
        )
        return redirect('documentos:admin_document_detail', pk=document_pk)

    from django.urls import reverse
    sign_link = request.build_absolute_uri(
        reverse('documentos:document_detail', kwargs={'pk': signature.pk}))

    wa_message = (
        f"Olá, {user.first_name or user.username}! 👋\n\n"
        "Este é um lembrete: você tem um documento pendente de assinatura no "
        "Sistema Rede Confiança.\n\n"
        f"📄 *{signature.document.title}*\n\n"
        f"Acesse o link abaixo para revisar e assinar:\n{sign_link}\n\n"
        "Obrigado!"
    )

    try:
        from core.zapi import send_whatsapp_message
        ok, detail = send_whatsapp_message(phone, wa_message)
    except Exception:  # noqa: BLE001
        ok, detail = False, 'falha inesperada'

    if ok:
        messages.success(
            request, f'Lembrete enviado para {user.full_name} via WhatsApp.')
    else:
        messages.error(
            request,
            'Não foi possível enviar o lembrete via WhatsApp agora. '
            'Tente novamente em instantes.',
        )

    return redirect('documentos:admin_document_detail', pk=document_pk)


@login_required
@require_POST
def admin_document_edit(request, pk):
    """Edita um documento: nome (título) e obrigatoriedade de assinatura (SUPERADMIN).

    Mantém o rastro de quem mexeu na obrigatoriedade — os mesmos campos usados
    pelo toggle — para a tela continuar mostrando "alterado por … em …".
    """
    if not _require_admin(request):
        return redirect('documentos:my_documents')

    documento = get_object_or_404(Document, pk=pk)

    novo_titulo = (request.POST.get('title') or '').strip()
    nova_obrigatoriedade = request.POST.get('assinatura_obrigatoria') == 'on'

    if not novo_titulo:
        messages.error(request, 'Informe o nome do documento.')
        return redirect('documentos:admin_document_detail', pk=documento.pk)

    campos = []
    if novo_titulo != documento.title:
        documento.title = novo_titulo
        campos.append('title')

    if nova_obrigatoriedade != documento.assinatura_obrigatoria:
        documento.assinatura_obrigatoria = nova_obrigatoriedade
        documento.obrigatoriedade_alterada_em = timezone.now()
        documento.obrigatoriedade_alterada_por = request.user
        campos += ['assinatura_obrigatoria', 'obrigatoriedade_alterada_em',
                   'obrigatoriedade_alterada_por']

    if not campos:
        messages.info(request, 'Nada foi alterado.')
        return redirect('documentos:admin_document_detail', pk=documento.pk)

    documento.save(update_fields=campos)
    messages.success(request, f'Documento "{documento.title}" atualizado.')
    return redirect('documentos:admin_document_detail', pk=documento.pk)


@login_required
@require_POST
def admin_toggle_obrigatoriedade(request, pk):
    """Liga/desliga a obrigatoriedade de assinatura do documento.

    Desligada, quem ainda não assinou para de receber o popup de pendência —
    o documento continua lá e pode ser assinado, só não cobra mais. Serve para
    o caso em que a assinatura deixou de fazer sentido mas o documento não
    deve sumir.
    """
    if not _require_admin(request):
        return redirect('documentos:my_documents')

    documento = get_object_or_404(Document, pk=pk)
    documento.assinatura_obrigatoria = not documento.assinatura_obrigatoria
    documento.obrigatoriedade_alterada_em = timezone.now()
    documento.obrigatoriedade_alterada_por = request.user
    documento.save(update_fields=['assinatura_obrigatoria', 'obrigatoriedade_alterada_em',
                                  'obrigatoriedade_alterada_por'])

    pendentes = documento.signatures.filter(signed_at__isnull=True).count()
    if documento.assinatura_obrigatoria:
        messages.success(
            request,
            f'Assinatura voltou a ser obrigatória. {pendentes} pessoa(s) voltam a receber o aviso.')
    else:
        messages.success(
            request,
            f'Obrigatoriedade removida. {pendentes} pessoa(s) deixam de receber o popup de '
            f'pendência — o documento continua disponível para assinatura.')
    return redirect('documentos:admin_document_detail', pk=documento.pk)


@login_required
def admin_delete_document(request, pk):
    """Exclui um documento e todas as suas atribuições (SUPERADMIN)."""
    if not _require_admin(request):
        return redirect('documentos:my_documents')

    document = get_object_or_404(Document, pk=pk)
    if request.method == 'POST':
        title = document.title
        document.delete()
        messages.success(request, f'Documento "{title}" excluído.')
        return redirect('documentos:admin_documents')

    return redirect('documentos:admin_document_detail', pk=document.pk)


# ─── Categorias ──────────────────────────────────────────────────────────────

@login_required
def admin_categories(request):
    """Lista e cria categorias de documentos (SUPERADMIN)."""
    if not _require_admin(request):
        return redirect('documentos:my_documents')

    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        description = (request.POST.get('description') or '').strip()
        color = (request.POST.get('color') or 'green').strip()
        icon = (request.POST.get('icon') or 'fa-file-contract').strip()

        if not name:
            messages.error(request, 'Informe o nome da categoria.')
        elif DocumentCategory.objects.filter(name__iexact=name).exists():
            messages.error(request, 'Já existe uma categoria com esse nome.')
        else:
            DocumentCategory.objects.create(
                name=name, description=description, color=color,
                icon=icon, created_by=request.user,
            )
            messages.success(request, f'Categoria "{name}" criada.')
        return redirect('documentos:admin_categories')

    categories = DocumentCategory.objects.annotate(
        docs_count=Count('documents')).order_by('name')

    return render(request, 'documentos/admin_categories.html', {
        'categories': categories,
    })


@login_required
def admin_category_edit(request, pk):
    """Edita uma categoria (SUPERADMIN)."""
    if not _require_admin(request):
        return redirect('documentos:my_documents')

    category = get_object_or_404(DocumentCategory, pk=pk)

    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        if not name:
            messages.error(request, 'Informe o nome da categoria.')
        elif DocumentCategory.objects.filter(name__iexact=name).exclude(pk=pk).exists():
            messages.error(request, 'Já existe uma categoria com esse nome.')
        else:
            category.name = name
            category.description = (request.POST.get('description') or '').strip()
            category.color = (request.POST.get('color') or 'green').strip()
            category.icon = (request.POST.get('icon') or 'fa-file-contract').strip()
            category.is_active = request.POST.get('is_active') == 'on'
            category.save()
            messages.success(request, 'Categoria atualizada.')

    return redirect('documentos:admin_categories')


@login_required
def admin_category_delete(request, pk):
    """Exclui uma categoria sem documentos vinculados (SUPERADMIN)."""
    if not _require_admin(request):
        return redirect('documentos:my_documents')

    category = get_object_or_404(DocumentCategory, pk=pk)
    if request.method == 'POST':
        if category.documents.exists():
            messages.error(
                request, 'Não é possível excluir uma categoria com documentos vinculados.')
        else:
            name = category.name
            category.delete()
            messages.success(request, f'Categoria "{name}" excluída.')

    return redirect('documentos:admin_categories')


# ══════════════════════════════════════════════════════════════════════════════
# ASSINATURA POR LINK PÚBLICO
#
# Para quem não entra no portal — na prática, o ex-colaborador que precisa
# assinar a própria rescisão. O endereço é a credencial, e vale para UM
# signatário: um link por pessoa, nunca um para o documento inteiro.
#
# A assinatura feita por aqui é gravada com `signed_via_link=True` e com o nome
# e CPF que a pessoa digitou. Sem essa marca, um registro assinado por link
# ficaria indistinguível de um assinado com login — e valem coisas diferentes.
# ══════════════════════════════════════════════════════════════════════════════

def _assinatura_por_token(token):
    """A assinatura daquele link, ou None se o link não vale mais."""
    assinatura = (DocumentSignature.objects
                  .select_related('document', 'user', 'document__category')
                  .filter(public_token=token).first())
    if assinatura is None or not assinatura.public_enabled:
        return None
    if not assinatura.document.is_visible:
        return None
    return assinatura


@require_http_methods(['GET'])
def public_sign_page(request, token):
    """Página de assinatura aberta por endereço, sem login."""
    assinatura = _assinatura_por_token(token)
    if assinatura is None:
        # Link inexistente, revogado e documento não liberado respondem igual:
        # quem tem o endereço não deve conseguir distinguir os casos.
        return render(request, 'documentos/public_sign.html',
                      {'indisponivel': True}, status=404)

    return render(request, 'documentos/public_sign.html', {
        'assinatura': assinatura,
        'documento': assinatura.document,
        'ja_assinado': assinatura.is_signed,
    })


@xframe_options_sameorigin
@require_http_methods(['GET'])
def public_sign_pdf(request, token):
    """Serve o PDF para quem abriu o link.

    Mesmo motivo do `document_pdf`: sem liberar o frame na mesma origem, o
    navegador recusa mostrar o PDF dentro do <iframe> da página de assinatura.
    """
    assinatura = _assinatura_por_token(token)
    if assinatura is None:
        raise Http404

    conteudo = _read_pdf_bytes(assinatura.document.pdf_file)
    if not conteudo:
        raise Http404('PDF não encontrado.')

    resposta = HttpResponse(conteudo, content_type='application/pdf')
    resposta['Content-Disposition'] = f'inline; filename="documento_{assinatura.document_id}.pdf"'
    return resposta


@require_POST
def api_public_sign(request, token):
    """Recebe a assinatura feita pelo link público."""
    assinatura = _assinatura_por_token(token)
    if assinatura is None:
        return JsonResponse({'error': 'Este link não está mais válido.'}, status=404)

    if assinatura.is_signed:
        return JsonResponse({'error': 'Este documento já foi assinado.'}, status=409)

    try:
        corpo = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Dados inválidos.'}, status=400)

    dados = corpo.get('signature', '')
    if not dados.startswith('data:image/png;base64,'):
        return JsonResponse({'error': 'Assinatura não fornecida.'}, status=400)

    nome = (corpo.get('nome') or '').strip()
    documento_pessoal = (corpo.get('cpf') or '').strip()
    if len(nome) < 5:
        return JsonResponse({'error': 'Escreva seu nome completo.'}, status=400)

    agora = timezone.now()
    ip = get_client_ip(request)
    navegador = request.META.get('HTTP_USER_AGENT', '')

    # O hash inclui o que a pessoa declarou e a marca de que veio por link:
    # o que se prova depois é exatamente isto, e não uma sessão autenticada.
    conteudo = (
        f"{assinatura.pk}|{assinatura.document.pk}|{assinatura.user.pk}|"
        f"{assinatura.document.title}|{agora.isoformat()}|{ip}|LINK|"
        f"{nome}|{documento_pessoal}|{dados[:100]}"
    )

    assinatura.signed_at = agora
    assinatura.signature_image = dados
    assinatura.signature_ip = ip or None
    assinatura.signature_user_agent = navegador[:500]
    assinatura.signature_hash = hashlib.sha256(conteudo.encode('utf-8')).hexdigest()
    assinatura.signed_via_link = True
    assinatura.signer_declared_name = nome[:150]
    assinatura.signer_declared_doc = documento_pessoal[:30]

    bytes_assinados = _build_signed_bytes(assinatura)
    if bytes_assinados:
        try:
            assinatura.signed_pdf.save(
                f'assinado_{assinatura.pk}.pdf', ContentFile(bytes_assinados), save=False)
        except Exception:
            pass

    assinatura.save()

    return JsonResponse({
        'success': True,
        'message': 'Documento assinado com sucesso.',
        'signed_at': timezone.localtime(agora).strftime('%d/%m/%Y às %H:%M'),
        'hash': assinatura.signature_hash[:16] + '...',
    })


@login_required
@require_POST
def admin_toggle_public_link(request, pk):
    """Liga, desliga ou troca o endereço público de um signatário."""
    if not _require_admin(request):
        return redirect('documentos:my_documents')

    assinatura = get_object_or_404(DocumentSignature, pk=pk)
    acao = (request.POST.get('acao') or '').strip()

    if acao == 'desligar':
        assinatura.public_enabled = False
        assinatura.save(update_fields=['public_enabled'])
        messages.success(request, 'Link desativado. Quem tiver o endereço não abre mais.')
    elif acao == 'ligar':
        assinatura.public_enabled = True
        assinatura.save(update_fields=['public_enabled'])
        messages.success(request, 'Link reativado.')
    elif acao == 'novo':
        # Trocar o token é o que realmente derruba um endereço já enviado —
        # desativar e reativar devolveria exatamente o mesmo link.
        assinatura.public_token = uuid.uuid4()
        assinatura.public_enabled = True
        assinatura.save(update_fields=['public_token', 'public_enabled'])
        messages.success(request, 'Endereço novo gerado. O anterior parou de funcionar.')
    else:
        messages.error(request, 'Ação desconhecida.')

    return redirect('documentos:admin_document_detail', pk=assinatura.document_id)
