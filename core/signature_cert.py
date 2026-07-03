"""
Geração do "Certificado de Assinatura Digital".

Recebe o PDF original (bytes) de um documento assinado e devolve um novo PDF
com uma folha extra ao final contendo: logo do portal, data/hora, dados do
signatário, imagem da assinatura, IP e ID do registro + hash de verificação.

Usa apenas Pillow (desenho da página) e pypdfium2 (merge) — sem novas
dependências além das já presentes no projeto.
"""
import base64
import io
import os

from django.conf import settings

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    Image = None

try:
    import pypdfium2 as pdfium
except ImportError:  # pragma: no cover
    pdfium = None


# A4 em 150 DPI (retrato)
_PAGE_W, _PAGE_H = 1240, 1754
_MARGIN = 110
_INK = (17, 24, 39)
_MUTED = (107, 114, 128)
_ACCENT = (22, 101, 52)
_LINE = (209, 213, 219)


def _font(size, bold=False):
    """Fonte DejaVu (cobertura completa de acentos) embutida no repositório."""
    fname = 'DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'
    path = os.path.join(settings.BASE_DIR, 'static', 'fonts', fname)
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:  # Pillow antigo sem parâmetro size
            return ImageFont.load_default()


def _logo_path():
    # logo3.png é escura (visível sobre fundo branco); logo.png é clara.
    for name in ('logo3.png', 'logo.png', 'logo-t.png'):
        candidate = os.path.join(settings.BASE_DIR, 'static', 'images', name)
        if os.path.exists(candidate):
            return candidate
    return None


def _decode_signature(signature_data_url):
    """Converte um data URL (data:image/png;base64,...) em Image RGBA."""
    if not signature_data_url or Image is None:
        return None
    try:
        if ',' in signature_data_url:
            _, b64 = signature_data_url.split(',', 1)
        else:
            b64 = signature_data_url
        raw = base64.b64decode(b64)
        return Image.open(io.BytesIO(raw)).convert('RGBA')
    except Exception:
        return None


def _build_certificate_pdf(*, doc_title, person_name, cpf, signed_at_str,
                           ip, record_id, signature_data_url, hash_value,
                           extra_lines=None):
    """Desenha a folha do certificado e devolve os bytes de um PDF de 1 página."""
    img = Image.new('RGB', (_PAGE_W, _PAGE_H), 'white')
    d = ImageDraw.Draw(img)

    f_title = _font(52, bold=True)
    f_sub = _font(28)
    f_label = _font(24)
    f_value = _font(30)
    f_small = _font(22)

    # ── Logo centralizada ──
    logo_path = _logo_path()
    y = 90
    if logo_path:
        try:
            logo = Image.open(logo_path).convert('RGBA')
            logo.thumbnail((240, 240))
            img.paste(logo, (int((_PAGE_W - logo.width) / 2), y), logo)
            y += logo.height + 30
        except Exception:
            y += 40
    else:
        y += 40

    # ── Título ──
    d.text((_PAGE_W / 2, y), 'Certificado de Assinatura Digital',
           font=f_title, anchor='ma', fill=_INK)
    y += 78
    d.text((_PAGE_W / 2, y), 'Rede Confiança – Portal do Colaborador',
           font=f_sub, anchor='ma', fill=_MUTED)
    y += 70
    d.line([(_MARGIN, y), (_PAGE_W - _MARGIN, y)], fill=_LINE, width=2)
    y += 50

    d.text((_MARGIN, y),
           'Este documento certifica que o registro abaixo foi assinado',
           font=f_small, fill=_MUTED)
    y += 32
    d.text((_MARGIN, y),
           'eletronicamente no Portal Rede Confiança, com os dados a seguir:',
           font=f_small, fill=_MUTED)
    y += 60

    rows = [
        ('Documento', doc_title or '—'),
        ('Signatário', person_name or '—'),
    ]
    if cpf:
        rows.append(('CPF', cpf))
    rows += [
        ('Data/hora da assinatura', signed_at_str or '—'),
        ('Endereço IP', ip or '—'),
        ('ID do registro', f'#{record_id}' if record_id is not None else '—'),
    ]
    for label, value in rows:
        d.text((_MARGIN, y), label.upper(), font=f_label, fill=_MUTED)
        d.text((_MARGIN, y + 30), str(value), font=f_value, fill=_INK)
        y += 92

    if extra_lines:
        for line in extra_lines:
            d.text((_MARGIN, y), str(line), font=f_small, fill=_MUTED)
            y += 34
        y += 10

    # ── Assinatura ──
    y += 20
    d.text((_MARGIN, y), 'ASSINATURA', font=f_label, fill=_MUTED)
    y += 40
    box_top = y
    box_h = 220
    d.rectangle([(_MARGIN, box_top), (_PAGE_W - _MARGIN, box_top + box_h)],
                outline=_LINE, width=2)
    sig = _decode_signature(signature_data_url)
    if sig is not None:
        max_w, max_h = (_PAGE_W - 2 * _MARGIN - 60), (box_h - 40)
        sig.thumbnail((max_w, max_h))
        # fundo branco para a assinatura transparente
        bg = Image.new('RGBA', sig.size, (255, 255, 255, 0))
        bg.alpha_composite(sig)
        px = int(_MARGIN + ((_PAGE_W - 2 * _MARGIN) - bg.width) / 2)
        py = int(box_top + (box_h - bg.height) / 2)
        img.paste(bg, (px, py), bg)
    y = box_top + box_h + 20
    d.text((_PAGE_W / 2, y), person_name or '', font=f_small, anchor='ma', fill=_INK)

    # ── Rodapé com hash ──
    fy = _PAGE_H - 130
    d.line([(_MARGIN, fy), (_PAGE_W - _MARGIN, fy)], fill=_LINE, width=2)
    fy += 24
    d.text((_MARGIN, fy),
           'Código de verificação (SHA-256):', font=f_small, fill=_MUTED)
    fy += 32
    d.text((_MARGIN, fy), hash_value or '—', font=f_small, fill=_ACCENT)
    fy += 40
    d.text((_MARGIN, fy),
           'A autenticidade pode ser conferida junto ao setor de Recursos Humanos.',
           font=f_small, fill=_MUTED)

    out = io.BytesIO()
    img.save(out, 'PDF', resolution=150.0)
    return out.getvalue()


def build_signed_pdf(original_pdf_bytes, **cert_kwargs):
    """
    Devolve os bytes de um PDF = (páginas originais) + (folha de certificado).

    cert_kwargs: doc_title, person_name, cpf, signed_at_str, ip, record_id,
                 signature_data_url, hash_value, extra_lines
    Retorna None em caso de erro (chamador faz fallback).
    """
    if pdfium is None or Image is None:
        return None
    try:
        cert_bytes = _build_certificate_pdf(**cert_kwargs)

        out = pdfium.PdfDocument.new()

        if original_pdf_bytes:
            src = pdfium.PdfDocument(original_pdf_bytes)
            out.import_pages(src)
        else:
            src = None

        cert = pdfium.PdfDocument(cert_bytes)
        out.import_pages(cert)

        buf = io.BytesIO()
        out.save(buf)

        out.close()
        cert.close()
        if src is not None:
            src.close()

        return buf.getvalue()
    except Exception:
        return None
