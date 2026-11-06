"""Facilitador: a grade de cards (todos) e o cadastro (SUPERADMIN)."""
import secrets

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db.models import F, Q
from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from .models import Recurso

MAX_ARQUIVO_MB = 200
# O QR code de arquivo baixa sem login (o celular que lê o QR quase nunca está
# logado no portal): o link é assinado e vale por este tempo.
QR_VALIDADE_HORAS = 24
_SAL_QR = 'facilitador.qr'
# Página HTML: maior que isso não é página, é arquivo para baixar.
MAX_PAGINA_MB = 20
# A página roda isolada (origem opaca): o JavaScript dela funciona, mas não enxerga o
# login de quem abre nem fala com o portal em nome dele. Formulários, pop-ups e
# downloads continuam valendo.
SANDBOX_PAGINA = ('sandbox allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox '
                  'allow-modals allow-downloads')


def token_do_qr(recurso):
    """Link assinado do arquivo. Trocar o arquivo invalida os QR já gerados."""
    return signing.dumps({'r': recurso.pk, 'a': recurso.arquivo.name}, salt=_SAL_QR, compress=True)


def pode_gerenciar(user):
    return bool(user and user.is_authenticated
                and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def _so_superadmin(request):
    if not pode_gerenciar(request.user):
        raise PermissionDenied('Só o SUPERADMIN gerencia o Facilitador.')


def _visivel(request, pk):
    """O recurso, se quem pede pode vê-lo (oculto só o SUPERADMIN vê)."""
    recurso = get_object_or_404(Recurso, pk=pk)
    if not recurso.ativo and not pode_gerenciar(request.user):
        raise Http404
    return recurso


class RecursoForm(forms.ModelForm):
    class Meta:
        model = Recurso
        fields = ['tipo', 'titulo', 'categoria', 'descricao', 'arquivo', 'url', 'ordem', 'ativo']
        widgets = {'descricao': forms.Textarea(attrs={'rows': 6})}

    def clean(self):
        dados = super().clean()
        tipo = dados.get('tipo')
        if tipo == Recurso.TIPO_LINK:
            if not dados.get('url'):
                self.add_error('url', 'Informe o link.')
        elif tipo == Recurso.TIPO_ARQUIVO:
            enviado = self.files.get('arquivo')
            if not enviado and not self.instance.arquivo:
                self.add_error('arquivo', 'Escolha o arquivo.')
            if enviado and enviado.size > MAX_ARQUIVO_MB * 1024 * 1024:
                self.add_error('arquivo', f'O arquivo passa de {MAX_ARQUIVO_MB} MB.')
        return dados


def _salvar(request, form, anterior=None):
    """Grava o recurso cuidando do arquivo: metadados do novo, apagar o que saiu."""
    arquivo_antigo = anterior.arquivo.name if anterior and anterior.arquivo else ''
    recurso = form.save(commit=False)
    enviado = request.FILES.get('arquivo')
    if recurso.tipo == Recurso.TIPO_LINK:
        recurso.arquivo = None
        recurso.nome_original = recurso.content_type = ''
        recurso.tamanho = 0
    else:
        recurso.url = ''
        if enviado:
            recurso.nome_original = enviado.name[:255]
            recurso.content_type = (enviado.content_type or '')[:120]
            recurso.tamanho = enviado.size
    # HTML ganha link de página na hora; o link sobrevive à troca por outro HTML.
    if recurso.e_html and recurso.arquivo:
        recurso.token_pagina = recurso.token_pagina or _novo_token()
    else:
        recurso.token_pagina = ''
    if not recurso.pk:
        recurso.criado_por = request.user
    recurso.save()
    # O arquivo trocado (ou que virou link) não fica esquecido no storage.
    if arquivo_antigo and arquivo_antigo != (recurso.arquivo.name if recurso.arquivo else ''):
        try:
            Recurso._meta.get_field('arquivo').storage.delete(arquivo_antigo)
        except Exception:
            pass
    return recurso


@login_required
def inicio(request):
    gestor = pode_gerenciar(request.user)
    recursos = Recurso.objects.all() if gestor else Recurso.objects.filter(ativo=True)
    categorias = sorted({c for c in recursos.values_list('categoria', flat=True) if c}, key=str.lower)
    busca = (request.GET.get('q') or '').strip()
    categoria = (request.GET.get('categoria') or '').strip()
    tipo = (request.GET.get('tipo') or '').strip().upper()
    if busca:
        recursos = recursos.filter(Q(titulo__icontains=busca) | Q(descricao__icontains=busca)
                                   | Q(categoria__icontains=busca) | Q(nome_original__icontains=busca))
    if categoria:
        recursos = recursos.filter(categoria__iexact=categoria)
    if tipo in (Recurso.TIPO_ARQUIVO, Recurso.TIPO_LINK):
        recursos = recursos.filter(tipo=tipo)
    return render(request, 'facilitador/inicio.html', {
        'recursos': recursos, 'categorias': categorias, 'busca': busca, 'categoria': categoria,
        'tipo': tipo, 'pode_gerenciar': gestor,
    })


@login_required
def detalhe(request, pk):
    recurso = _visivel(request, pk)
    qr_url = recurso.url if recurso.e_link else ''
    link_pagina = link_da_pagina(request, recurso) if recurso.e_html and recurso.arquivo else ''
    if link_pagina and recurso.ativo:
        qr_url = link_pagina        # a página abre no celular; não precisa do link de 24 h
    elif not recurso.e_link and recurso.arquivo and recurso.ativo:
        qr_url = request.build_absolute_uri(reverse('facilitador:baixar_qr', args=[token_do_qr(recurso)]))
    return render(request, 'facilitador/detalhe.html', {
        'recurso': recurso, 'pode_gerenciar': pode_gerenciar(request.user),
        'qr_url': qr_url, 'qr_horas': QR_VALIDADE_HORAS, 'link_pagina': link_pagina,
    })


@login_required
def baixar(request, pk):
    recurso = _visivel(request, pk)
    if recurso.e_link or not recurso.arquivo:
        raise Http404
    ver = request.GET.get('ver') == '1' and (recurso.e_pdf or recurso.e_imagem)
    if not ver:
        Recurso.objects.filter(pk=pk).update(acessos=F('acessos') + 1)
    try:
        conteudo = recurso.arquivo.open('rb')
    except (FileNotFoundError, OSError):
        raise Http404('Arquivo não encontrado.')
    return FileResponse(conteudo, as_attachment=not ver,
                        filename=recurso.nome_original or recurso.arquivo.name.rsplit('/', 1)[-1],
                        content_type=recurso.content_type or None)


def baixar_qr(request, token):
    """Download pelo QR code, sem login: vale o link assinado (e o item visível)."""
    try:
        dados = signing.loads(token, salt=_SAL_QR, max_age=QR_VALIDADE_HORAS * 3600)
    except signing.SignatureExpired:
        return render(request, 'facilitador/qr_invalido.html', {'expirado': True, 'horas': QR_VALIDADE_HORAS}, status=410)
    except signing.BadSignature:
        return render(request, 'facilitador/qr_invalido.html', {'expirado': False}, status=404)
    recurso = Recurso.objects.filter(pk=dados.get('r'), ativo=True, tipo=Recurso.TIPO_ARQUIVO).first()
    if not recurso or not recurso.arquivo or recurso.arquivo.name != dados.get('a'):
        return render(request, 'facilitador/qr_invalido.html', {'expirado': False}, status=404)
    try:
        conteudo = recurso.arquivo.open('rb')
    except (FileNotFoundError, OSError):
        return render(request, 'facilitador/qr_invalido.html', {'expirado': False}, status=404)
    Recurso.objects.filter(pk=recurso.pk).update(acessos=F('acessos') + 1)
    return FileResponse(conteudo, as_attachment=True,
                        filename=recurso.nome_original or recurso.arquivo.name.rsplit('/', 1)[-1],
                        content_type=recurso.content_type or None)


def _novo_token():
    return secrets.token_urlsafe(16)


def link_da_pagina(request, recurso):
    if not recurso.token_pagina:
        return ''
    return request.build_absolute_uri(reverse('facilitador:pagina_publica', args=[recurso.token_pagina]))


def _servir_html(recurso):
    """O HTML como página, isolado do portal (ver SANDBOX_PAGINA)."""
    if recurso.tamanho > MAX_PAGINA_MB * 1024 * 1024:
        raise Http404('Página grande demais para abrir; baixe o arquivo.')
    try:
        with recurso.arquivo.open('rb') as arquivo:
            conteudo = arquivo.read(MAX_PAGINA_MB * 1024 * 1024 + 1)
    except (FileNotFoundError, OSError):
        raise Http404('Arquivo não encontrado.')
    try:
        conteudo.decode('utf-8')
        tipo = 'text/html; charset=utf-8'
    except UnicodeDecodeError:
        tipo = 'text/html'          # o navegador segue o <meta charset> da própria página
    resposta = HttpResponse(conteudo, content_type=tipo)
    resposta['Content-Security-Policy'] = SANDBOX_PAGINA
    resposta['X-Content-Type-Options'] = 'nosniff'
    resposta['Referrer-Policy'] = 'no-referrer'
    resposta['Cache-Control'] = 'private, no-cache'
    return resposta


@login_required
@xframe_options_sameorigin
def pagina(request, pk):
    """A página dentro do portal (e a prévia da tela de detalhe, com ?previa=1, que não conta acesso)."""
    recurso = _visivel(request, pk)
    if not recurso.e_html or not recurso.arquivo:
        raise Http404
    if request.GET.get('previa') != '1':
        Recurso.objects.filter(pk=pk).update(acessos=F('acessos') + 1)
    return _servir_html(recurso)


def pagina_publica(request, token):
    """O link da página: abre sem login enquanto o item estiver visível para todos."""
    recurso = (Recurso.objects.filter(token_pagina=token, ativo=True, tipo=Recurso.TIPO_ARQUIVO).first()
               if token else None)
    if not recurso or not recurso.e_html or not recurso.arquivo:
        return render(request, 'facilitador/qr_invalido.html', {'expirado': False, 'pagina': True}, status=404)
    Recurso.objects.filter(pk=recurso.pk).update(acessos=F('acessos') + 1)
    return _servir_html(recurso)


@login_required
@require_POST
def novo_link(request, pk):
    _so_superadmin(request)
    recurso = get_object_or_404(Recurso, pk=pk)
    if recurso.e_html and recurso.arquivo:
        recurso.token_pagina = _novo_token()
        recurso.save(update_fields=['token_pagina', 'atualizado_em'])
        messages.success(request, 'Link novo gerado. O link antigo e os QR codes impressos com ele pararam de funcionar.')
    return redirect('facilitador:detalhe', pk=recurso.pk)


@login_required
def ir(request, pk):
    recurso = _visivel(request, pk)
    if not recurso.e_link or not recurso.url:
        raise Http404
    Recurso.objects.filter(pk=pk).update(acessos=F('acessos') + 1)
    return HttpResponseRedirect(recurso.url)


@login_required
def novo(request):
    _so_superadmin(request)
    form = RecursoForm(request.POST or None, request.FILES or None,
                       initial={'tipo': request.GET.get('tipo', Recurso.TIPO_ARQUIVO).upper()})
    if request.method == 'POST' and form.is_valid():
        recurso = _salvar(request, form)
        messages.success(request, f'"{recurso.titulo}" publicado no Facilitador.'
                                  + (' A página HTML já tem link próprio: veja abaixo.' if recurso.token_pagina else ''))
        return redirect('facilitador:detalhe', pk=recurso.pk)
    return render(request, 'facilitador/form.html', {
        'form': form, 'recurso': None, 'categorias': _categorias(), 'max_mb': MAX_ARQUIVO_MB,
    })


@login_required
def editar(request, pk):
    _so_superadmin(request)
    recurso = get_object_or_404(Recurso, pk=pk)
    anterior = Recurso.objects.get(pk=pk)
    form = RecursoForm(request.POST or None, request.FILES or None, instance=recurso)
    if request.method == 'POST' and form.is_valid():
        recurso = _salvar(request, form, anterior)
        messages.success(request, 'Alterações salvas.')
        return redirect('facilitador:detalhe', pk=recurso.pk)
    return render(request, 'facilitador/form.html', {
        'form': form, 'recurso': anterior, 'categorias': _categorias(), 'max_mb': MAX_ARQUIVO_MB,
    })


@login_required
@require_POST
def alternar(request, pk):
    _so_superadmin(request)
    recurso = get_object_or_404(Recurso, pk=pk)
    recurso.ativo = not recurso.ativo
    recurso.save(update_fields=['ativo', 'atualizado_em'])
    messages.success(request, f'"{recurso.titulo}" agora está {"visível para todos" if recurso.ativo else "oculto"}.')
    return redirect('facilitador:detalhe', pk=recurso.pk)


@login_required
@require_POST
def excluir(request, pk):
    _so_superadmin(request)
    recurso = get_object_or_404(Recurso, pk=pk)
    titulo = recurso.titulo
    if recurso.arquivo:
        try:
            recurso.arquivo.delete(save=False)
        except Exception:
            pass
    recurso.delete()
    messages.success(request, f'"{titulo}" excluído do Facilitador.')
    return redirect('facilitador:inicio')


def _categorias():
    return sorted({c for c in Recurso.objects.values_list('categoria', flat=True) if c}, key=str.lower)
