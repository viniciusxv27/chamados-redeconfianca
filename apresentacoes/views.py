"""Telas e API do Assistente de Apresentações."""
import io
import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, HttpResponse, HttpResponseRedirect, JsonResponse, StreamingHttpResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from . import formato, importacao, tarefas
from .context_processors import limpar_cache_do_menu
from .models import (Apresentacao, ConexaoCanva, ConfiguracaoApresentacoes, MensagemIA, Midia, TarefaIA,
                     TemplateApresentacao, VersaoApresentacao)
from .padrao import garantir_template_padrao
from .permissoes import (apresentacoes_visiveis, configuracao, e_superadmin, pode_editar, pode_editar_template,
                         pode_usar, pode_ver_midia, templates_visiveis)

logger = logging.getLogger(__name__)
User = get_user_model()

LIMITES_UPLOAD = {  # tipo: (extensões, bytes)
    Midia.Tipo.IMAGEM: ({'.jpg', '.jpeg', '.png', '.webp', '.gif'}, 15 * 1024 * 1024),
    Midia.Tipo.VIDEO: ({'.mp4', '.webm', '.mov', '.m4v'}, 200 * 1024 * 1024),
    Midia.Tipo.AUDIO: ({'.mp3', '.wav', '.ogg', '.webm', '.m4a', '.aac'}, 25 * 1024 * 1024),
}
LIMITE_MATERIAL = 40 * 1024 * 1024
MAX_ANEXOS = 12
MAX_DOCUMENTO_BYTES = 8 * 1024 * 1024
OPCOES_PUBLICO = ('Colaboradores das lojas', 'Gerentes e coordenadores', 'Escritório (ADM)', 'Diretoria', 'Clientes')
OPCOES_TOM = ('Profissional e motivador', 'Didático, passo a passo', 'Comercial e persuasivo', 'Executivo e objetivo')
MODELOS_TEXTO = ('gpt-4.1', 'gpt-4.1-mini', 'gpt-5', 'gpt-5-mini', 'gpt-4o', 'gpt-4o-mini')
MODELOS_IMAGEM = ('gpt-image-1', 'gpt-image-1-mini', 'gpt-image-1.5', 'dall-e-3')
MODELOS_VIDEO = ('sora-2', 'sora-2-pro')
MODELOS_VOZ = ('gpt-4o-mini-tts', 'tts-1-hd', 'tts-1')
VOZES = ('nova', 'shimmer', 'coral', 'sage', 'alloy', 'ash', 'ballad', 'echo', 'fable', 'onyx', 'verse')


# ---------------------------------------------------------------------------
# Acesso
# ---------------------------------------------------------------------------
def _quer_json(request):
    return request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in (
        request.headers.get('accept') or '') or request.content_type == 'application/json'


def modulo_liberado(view):
    @wraps(view)
    @login_required
    def envoltorio(request, *args, **kwargs):
        if not pode_usar(request.user):
            if _quer_json(request) or request.path.endswith(('/documento/', '/ia/', '/midias/')):
                return JsonResponse({'ok': False, 'erro': 'Você não tem acesso ao Assistente de Apresentações.'},
                                    status=403)
            messages.error(request, 'O Assistente de Apresentações ainda não foi liberado para você.')
            return redirect('dashboard')
        return view(request, *args, **kwargs)
    return envoltorio


def _erro(mensagem, status=400, **extra):
    return JsonResponse({'ok': False, 'erro': mensagem, **extra}, status=status)


def _corpo_json(request):
    if len(request.body or b'') > MAX_DOCUMENTO_BYTES:
        raise ValueError('Conteúdo grande demais.')
    try:
        dados = json.loads(request.body or b'{}')
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError('JSON inválido.') from exc
    if not isinstance(dados, dict):
        raise ValueError('JSON inválido.')
    return dados


def _apresentacao_editavel(request, pk):
    apresentacao = get_object_or_404(Apresentacao.objects.select_related('template', 'dono'), pk=pk)
    if not pode_editar(request.user, apresentacao):
        return None
    return apresentacao


def _contexto(request, aba, **extra):
    user = request.user
    return {
        'ap_aba': aba,
        'ap_superadmin': e_superadmin(user),
        'ap_modulos_sem_apresentacao': None,
        **extra,
    }


# ---------------------------------------------------------------------------
# Páginas
# ---------------------------------------------------------------------------
@modulo_liberado
def inicio(request):
    user = request.user
    qs = apresentacoes_visiveis(user).annotate(n_midias=Count('midias'))
    filtros = {'q': (request.GET.get('q') or '').strip(), 'pessoa': request.GET.get('pessoa') or '',
               'origem': request.GET.get('origem') or '', 'status': request.GET.get('status') or ''}
    if filtros['q']:
        qs = qs.filter(Q(titulo__icontains=filtros['q']) | Q(pedido__icontains=filtros['q'])
                       | Q(dono__first_name__icontains=filtros['q']) | Q(dono__last_name__icontains=filtros['q']))
    if e_superadmin(user) and filtros['pessoa'].isdigit():
        qs = qs.filter(dono_id=int(filtros['pessoa']))
    if filtros['origem'] in Apresentacao.Origem.values:
        qs = qs.filter(origem=filtros['origem'])
    if filtros['status'] in Apresentacao.Status.values:
        qs = qs.filter(status=filtros['status'])
    pagina = Paginator(qs.order_by('-atualizado_em'), 24).get_page(request.GET.get('pagina'))
    for ap in pagina:
        ap.capa = _capa(ap)
    pessoas = (User.objects.filter(apresentacoes__isnull=False).distinct().order_by('first_name', 'last_name')
               if e_superadmin(user) else [])
    return render(request, 'apresentacoes/inicio.html', _contexto(
        request, 'inicio', pagina=pagina, filtros=filtros, pessoas=pessoas,
        total=apresentacoes_visiveis(user).count(), origens=Apresentacao.Origem.choices,
        status_opcoes=Apresentacao.Status.choices, gerando=apresentacoes_visiveis(user).filter(
            status__in=[Apresentacao.Status.GERANDO, Apresentacao.Status.PERGUNTAS]).count()))


def _resolver_src(src):
    if not src:
        return ''
    if src.startswith('static:'):
        return static(src[len('static:'):])
    return src


def _com_capas(templates):
    """Miniaturas dos layouts (fundo resolvido) e o tema, para os cartões de escolha de template."""
    for template in templates:
        template.capas = [{'fundo': _resolver_src((l.get('fundo') or {}).get('imagem')),
                           'nome': l.get('nome') or l.get('layout')} for l in template.layouts[:5]]
        tema = template.tema or formato.TEMA_PADRAO
        template.fonte_titulo = tema.get('fonte_titulo') or 'Montserrat'
        template.fonte_texto = tema.get('fonte_texto') or 'Montserrat'
        template.cores = list((tema.get('cores') or {}).values())[:5]
    return templates


def _capa(apresentacao):
    """O que a lista mostra do primeiro slide: fundo, título e contagem."""
    slides = apresentacao.slides
    if not slides:
        return {'fundo': '', 'cor': '#1C1026', 'titulo': apresentacao.titulo}
    primeiro = slides[0]
    titulo = ''
    for el in primeiro.get('elementos') or []:
        if el.get('tipo') == 'texto' and el.get('slot') == 'titulo':
            titulo = formato.html_para_texto(el.get('html'))
            break
    tema = (apresentacao.documento or {}).get('tema') or formato.TEMA_PADRAO
    cor = primeiro.get('fundo', {}).get('cor') or ''
    if cor.startswith('tema:'):
        cor = tema.get('cores', {}).get(cor[5:], '#1C1026')
    return {'fundo': _resolver_src(primeiro.get('fundo', {}).get('imagem')), 'cor': cor or '#1C1026',
            'titulo': titulo or apresentacao.titulo, 'fonte': tema.get('fonte_titulo') or 'Montserrat'}


@modulo_liberado
def nova(request):
    user = request.user
    garantir_template_padrao()
    modelos = _com_capas(list(templates_visiveis(user).filter(status=TemplateApresentacao.Status.PRONTO)))
    if request.method == 'POST':
        pedido = (request.POST.get('pedido') or '').strip()
        if len(pedido) < 10:
            messages.error(request, 'Conte com um pouco mais de detalhe o que a apresentação precisa ter.')
            return redirect('apresentacoes:nova')
        template = next((t for t in modelos if str(t.pk) == request.POST.get('template')), None) or (
            modelos[0] if modelos else None)
        arquivos = request.FILES.getlist('anexos')[:MAX_ANEXOS]
        opcoes = _opcoes_do_pedido(request.POST, template)
        with transaction.atomic():
            apresentacao = Apresentacao.objects.create(
                titulo=formato.texto(request.POST.get('titulo'), 200).strip() or 'Nova apresentação',
                pedido=formato.texto(pedido, 8000), dono=user, template=template, status=Apresentacao.Status.GERANDO,
                opcoes=opcoes, documento=formato.documento_vazio(template.tema if template else None))
            anexos, texto_material, avisos = _salvar_anexos(arquivos, user, apresentacao)
            if texto_material:
                apresentacao.opcoes = {**opcoes, 'texto_material': texto_material[:30000]}
                apresentacao.save(update_fields=['opcoes'])
            MensagemIA.objects.create(apresentacao=apresentacao, papel=MensagemIA.Papel.USUARIO, texto=pedido,
                                      dados={'anexos': [m.pk for m in anexos]})
            try:
                tarefas.criar(TarefaIA.Tipo.GERAR, user, apresentacao=apresentacao,
                              parametros={'anexos': [m.pk for m in anexos]})
            except tarefas.TarefaRecusada as exc:
                apresentacao.status = Apresentacao.Status.RASCUNHO
                apresentacao.save(update_fields=['status'])
                messages.error(request, str(exc))
        for aviso in avisos:
            messages.warning(request, aviso)
        return redirect('apresentacoes:editor', pk=apresentacao.pk)
    return render(request, 'apresentacoes/nova.html', _contexto(
        request, 'nova', modelos=modelos, publicos=OPCOES_PUBLICO, tons=OPCOES_TOM,
        principal=next((t for t in modelos if t.principal), modelos[0] if modelos else None)))


def _opcoes_do_pedido(post, template):
    tema = (template.tema if template else None) or formato.TEMA_PADRAO
    quantidade = post.get('quantidade') or ''
    return {
        'publico': formato.texto(post.get('publico_outro') or post.get('publico'), 200),
        'tom': formato.texto(post.get('tom'), 200),
        'quantidade': int(quantidade) if quantidade.isdigit() and 1 <= int(quantidade) <= 40 else None,
        'fonte_titulo': formato.fonte(post.get('fonte_titulo')) or tema.get('fonte_titulo'),
        'fonte_texto': formato.fonte(post.get('fonte_texto')) or tema.get('fonte_texto'),
        'imagens_ia': post.get('imagens_ia') == 'on',
        'narracao': post.get('narracao') == 'on',
        'video_abertura': post.get('video_abertura') == 'on',
    }


def _salvar_anexos(arquivos, user, apresentacao):
    """Imagens viram prints; PDF vira páginas (imagens) + texto; PowerPoint vira texto."""
    anexos, textos, avisos = [], [], []
    for arquivo in arquivos:
        nome = os.path.basename(arquivo.name or 'anexo')[:180]
        extensao = os.path.splitext(nome)[1].lower()
        if arquivo.size > LIMITE_MATERIAL:
            avisos.append(f'"{nome}" passou de 40 MB e ficou de fora.')
            continue
        conteudo = arquivo.read()
        try:
            if extensao == '.pdf':
                for numero, pagina in enumerate(importacao.paginas_do_pdf(conteudo, 10), start=1):
                    anexos.append(tarefas.salvar_midia(
                        pagina, f'{nome}-p{numero}.png', dono=user, tipo=Midia.Tipo.IMAGEM, origem=Midia.Origem.PRINT,
                        apresentacao=apresentacao, mime='image/png'))
                textos.append(f'[{nome}]\n' + importacao.texto_do_pdf(conteudo))
            elif extensao == '.pptx':
                textos.append(f'[{nome}]\n' + importacao.texto_do_pptx(conteudo))
            elif extensao in LIMITES_UPLOAD[Midia.Tipo.IMAGEM][0]:
                _validar_imagem(conteudo)
                anexos.append(tarefas.salvar_midia(conteudo, nome, dono=user, tipo=Midia.Tipo.IMAGEM,
                                                   origem=Midia.Origem.PRINT, apresentacao=apresentacao,
                                                   mime=mimetypes.guess_type(nome)[0] or 'image/png'))
            else:
                avisos.append(f'"{nome}": envie imagem, PDF ou PowerPoint.')
        except Exception as exc:                                # noqa: BLE001 — um anexo ruim não perde o pedido
            logger.warning('Anexo %s recusado: %s', nome, exc)
            avisos.append(f'"{nome}" não pôde ser lido e ficou de fora.')
    return anexos[:20], '\n\n'.join(t for t in textos if t.strip()), avisos


def _validar_imagem(conteudo):
    from PIL import Image
    with Image.open(io.BytesIO(conteudo)) as img:
        img.verify()


def _config_do_editor(request, apresentacao):
    tarefa = (TarefaIA.objects.filter(apresentacao=apresentacao, tipo=TarefaIA.Tipo.GERAR)
              .order_by('-criado_em').first())
    if tarefa:
        tarefas.conferir_parada(tarefa)
    em_andamento = tarefa if tarefa and tarefa.em_andamento else None
    cfg = configuracao()
    return {
        'modo': 'apresentacao',
        'id': apresentacao.pk,
        'titulo': apresentacao.titulo,
        'status': apresentacao.status,
        'erro': apresentacao.erro,
        'urls': {
            'documento': reverse('apresentacoes:documento', args=[apresentacao.pk]),
            'midias': reverse('apresentacoes:midias_upload'),
            'midias_lista': reverse('apresentacoes:midias_lista', args=[apresentacao.pk]),
            'ia': reverse('apresentacoes:ia', args=[apresentacao.pk]),
            'tarefa': reverse('apresentacoes:tarefa', args=[0]),
            'responder': reverse('apresentacoes:responder', args=[0]),
            'mensagens': reverse('apresentacoes:mensagens', args=[apresentacao.pk]),
            'pptx': reverse('apresentacoes:exportar_pptx', args=[apresentacao.pk]),
            'canva': reverse('apresentacoes:exportar_canva', args=[apresentacao.pk]),
            'video': reverse('apresentacoes:video', args=[apresentacao.pk]),
            'versoes': reverse('apresentacoes:versoes', args=[apresentacao.pk]),
            'apresentar': reverse('apresentacoes:apresentar', args=[apresentacao.pk]),
            'voltar': reverse('apresentacoes:inicio'),
            'limpar_fundo': '',
        },
        'csrf': get_token(request),
        'static_url': settings.STATIC_URL,
        'fontes_url': static('apresentacoes/fontes.json'),
        'icones_url': static('apresentacoes/icones.json'),
        'tema_escuro': getattr(request.user, 'theme', '') == 'dark',
        'pode_ia': bool(getattr(settings, 'OPENAI_API_KEY', '')),
        'canva_configurado': cfg.canva_configurado,
        'canva_url': apresentacao.canva_edit_url,
        'tarefa_em_andamento': {'id': em_andamento.pk, 'tipo': em_andamento.tipo} if em_andamento else None,
        'dono': apresentacao.dono.get_full_name() if apresentacao.dono_id != request.user.pk else '',
    }


@modulo_liberado
def editor(request, pk):
    apresentacao = _apresentacao_editavel(request, pk)
    if apresentacao is None:
        messages.error(request, 'Essa apresentação não é sua.')
        return redirect('apresentacoes:inicio')
    return render(request, 'apresentacoes/editor.html', {
        'apres_config': _config_do_editor(request, apresentacao), 'titulo': apresentacao.titulo})


@modulo_liberado
def apresentar(request, pk):
    apresentacao = _apresentacao_editavel(request, pk)
    if apresentacao is None:
        messages.error(request, 'Essa apresentação não é sua.')
        return redirect('apresentacoes:inicio')
    return render(request, 'apresentacoes/apresentar.html', {
        'titulo': apresentacao.titulo,
        'apres_config': {
            'id': apresentacao.pk, 'titulo': apresentacao.titulo, 'static_url': settings.STATIC_URL,
            'fontes_url': static('apresentacoes/fontes.json'),
            'urls': {'documento': reverse('apresentacoes:documento', args=[apresentacao.pk]),
                     'voltar': reverse('apresentacoes:editor', args=[apresentacao.pk])},
        }})


# ---------------------------------------------------------------------------
# Documento, versões e mensagens
# ---------------------------------------------------------------------------
@modulo_liberado
def documento(request, pk):
    apresentacao = _apresentacao_editavel(request, pk)
    if apresentacao is None:
        return _erro('Sem permissão para esta apresentação.', 403)
    if request.method == 'GET':
        return JsonResponse({'ok': True, 'titulo': apresentacao.titulo, 'documento': apresentacao.documento or
                             formato.documento_vazio(), 'revisao': apresentacao.revisao,
                             'status': apresentacao.status, 'pode_editar': True})
    if request.method != 'POST':
        return _erro('Método não permitido.', 405)
    try:
        dados = _corpo_json(request)
    except ValueError as exc:
        return _erro(str(exc))
    if not isinstance(dados.get('documento'), dict):
        return _erro('Documento ausente.')
    revisao = dados.get('revisao')
    limpo = formato.sanear_documento(dados['documento'])
    erro_midia = _midias_permitidas(request.user, limpo, apresentacao=apresentacao)
    if erro_midia:
        return _erro(erro_midia, 403)
    with transaction.atomic():
        atual = Apresentacao.objects.select_for_update().get(pk=apresentacao.pk)
        if not isinstance(revisao, int) or revisao != atual.revisao:
            return _erro('Esta apresentação foi alterada em outro lugar.', 409, revisao=atual.revisao)
        atual.documento = limpo
        titulo = formato.texto(dados.get('titulo'), 200).strip()
        if titulo:
            atual.titulo = titulo
        if atual.status in (Apresentacao.Status.RASCUNHO, Apresentacao.Status.ERRO) and limpo['slides']:
            atual.status = Apresentacao.Status.PRONTA
        atual.revisao += 1
        atual.save(update_fields=['documento', 'titulo', 'status', 'revisao', 'atualizado_em'])
    return JsonResponse({'ok': True, 'revisao': atual.revisao, 'documento': limpo, 'titulo': atual.titulo})


def _midias_permitidas(user, documento, apresentacao=None, template=None):
    """Toda mídia referenciada precisa ser visível para quem salva (não dá para "pegar" a de outra pessoa)."""
    ids = formato.midias_usadas(documento)
    if not ids:
        return ''
    midias = list(Midia.objects.filter(pk__in=ids).select_related('apresentacao'))
    if len(midias) != len(ids):
        return 'O documento usa mídia que não existe mais.'
    for midia in midias:
        if apresentacao is not None and midia.apresentacao_id == apresentacao.pk:
            continue
        if template is not None and midia.template_id == template.pk:
            continue
        if not pode_ver_midia(user, midia):
            return 'O documento usa mídia de outra pessoa.'
    return ''


@modulo_liberado
@require_GET
def versoes(request, pk):
    apresentacao = _apresentacao_editavel(request, pk)
    if apresentacao is None:
        return _erro('Sem permissão.', 403)
    lista = [{'id': v.pk, 'motivo': v.motivo, 'titulo': v.titulo, 'slides': len((v.documento or {}).get('slides') or []),
              'criado_em': timezone.localtime(v.criado_em).strftime('%d/%m/%Y %H:%M'),
              'restaurar': reverse('apresentacoes:restaurar', args=[apresentacao.pk, v.pk])}
             for v in apresentacao.versoes.all()[:50]]
    return JsonResponse({'ok': True, 'versoes': lista})


@modulo_liberado
@require_POST
def restaurar(request, pk, versao_id):
    apresentacao = _apresentacao_editavel(request, pk)
    if apresentacao is None:
        return _erro('Sem permissão.', 403)
    versao = get_object_or_404(VersaoApresentacao, pk=versao_id, apresentacao=apresentacao)
    with transaction.atomic():
        atual = Apresentacao.objects.select_for_update().get(pk=apresentacao.pk)
        VersaoApresentacao.objects.create(apresentacao=atual, documento=atual.documento, titulo=atual.titulo,
                                          motivo='Antes de restaurar uma versão', criado_por=request.user)
        atual.documento = formato.sanear_documento(versao.documento)
        atual.revisao += 1
        atual.save(update_fields=['documento', 'revisao', 'atualizado_em'])
    return JsonResponse({'ok': True, 'revisao': atual.revisao, 'documento': atual.documento})


@modulo_liberado
@require_GET
def mensagens(request, pk):
    apresentacao = _apresentacao_editavel(request, pk)
    if apresentacao is None:
        return _erro('Sem permissão.', 403)
    lista = [{'id': m.pk, 'papel': m.papel, 'texto': m.texto, 'dados': m.dados,
              'criado_em': timezone.localtime(m.criado_em).strftime('%d/%m %H:%M')}
             for m in apresentacao.mensagens.all()[:200]]
    return JsonResponse({'ok': True, 'mensagens': lista})


@modulo_liberado
@require_POST
def duplicar(request, pk):
    apresentacao = _apresentacao_editavel(request, pk)
    if apresentacao is None:
        messages.error(request, 'Sem permissão.')
        return redirect('apresentacoes:inicio')
    copia = Apresentacao.objects.create(
        titulo=f'{apresentacao.titulo} (cópia)'[:200], pedido=apresentacao.pedido, dono=request.user,
        template=apresentacao.template, documento=apresentacao.documento, status=(
            Apresentacao.Status.PRONTA if apresentacao.slides else Apresentacao.Status.RASCUNHO),
        origem=apresentacao.origem, modulo=apresentacao.modulo, opcoes=apresentacao.opcoes)
    # A cópia usa as mesmas mídias: elas continuam ligadas à original, e quem copia é o dono das duas
    # (ou o SUPERADMIN), então pode_ver_midia continua valendo.
    messages.success(request, 'Apresentação duplicada.')
    return redirect('apresentacoes:editor', pk=copia.pk)


@modulo_liberado
@require_POST
def excluir(request, pk):
    apresentacao = _apresentacao_editavel(request, pk)
    if apresentacao is None:
        messages.error(request, 'Sem permissão.')
        return redirect('apresentacoes:inicio')
    titulo = apresentacao.titulo
    arquivos = [m.arquivo for m in apresentacao.midias.all() if m.arquivo]
    apresentacao.delete()
    for arquivo in arquivos:
        try:
            arquivo.delete(save=False)
        except Exception as exc:                                # noqa: BLE001 — o registro já saiu
            logger.warning('Arquivo de mídia não foi apagado do storage: %s', exc)
    messages.success(request, f'"{titulo}" excluída.')
    return redirect('apresentacoes:inicio')


# ---------------------------------------------------------------------------
# Mídias
# ---------------------------------------------------------------------------
def _tipo_pela_extensao(extensao):
    for tipo, (extensoes, _) in LIMITES_UPLOAD.items():
        if extensao in extensoes:
            return tipo
    return None


@modulo_liberado
@require_POST
def midias_upload(request):
    arquivo = request.FILES.get('arquivo')
    if not arquivo:
        return _erro('Nenhum arquivo enviado.')
    nome = os.path.basename(request.POST.get('nome') or arquivo.name or 'arquivo')[:180]
    extensao = os.path.splitext(arquivo.name or nome)[1].lower()
    tipo = _tipo_pela_extensao(extensao)
    if extensao == '.webm' and (request.POST.get('tipo') or '').upper() == 'VIDEO':
        tipo = Midia.Tipo.VIDEO
    if tipo is None:
        return _erro('Formato não aceito. Envie imagem (JPG, PNG, WEBP, GIF), vídeo (MP4, WEBM, MOV) ou áudio (MP3).')
    if arquivo.size > LIMITES_UPLOAD[tipo][1]:
        return _erro(f'Arquivo grande demais (máximo {LIMITES_UPLOAD[tipo][1] // (1024 * 1024)} MB).')
    apresentacao = template = None
    if (request.POST.get('apresentacao') or '').isdigit():
        apresentacao = _apresentacao_editavel(request, int(request.POST['apresentacao']))
        if apresentacao is None:
            return _erro('Sem permissão para esta apresentação.', 403)
    if (request.POST.get('template') or '').isdigit():
        template = get_object_or_404(TemplateApresentacao, pk=int(request.POST['template']))
        if not pode_editar_template(request.user, template):
            return _erro('Sem permissão para este template.', 403)
    conteudo = arquivo.read()
    largura = altura = None
    if tipo == Midia.Tipo.IMAGEM:
        try:
            _validar_imagem(conteudo)
        except Exception:                                       # noqa: BLE001
            return _erro('O arquivo não é uma imagem válida.')
    origem = request.POST.get('origem') or ''
    origem = origem if origem in (Midia.Origem.UPLOAD, Midia.Origem.PRINT, Midia.Origem.CAPTURA) else Midia.Origem.UPLOAD
    if template is not None and origem == Midia.Origem.UPLOAD:
        origem = Midia.Origem.TEMPLATE
    midia = tarefas.salvar_midia(conteudo, nome, dono=request.user, tipo=tipo, origem=origem,
                                 apresentacao=apresentacao, template=template,
                                 mime=arquivo.content_type or mimetypes.guess_type(nome)[0] or '',
                                 largura=largura, altura=altura)
    return JsonResponse({'ok': True, 'midia': midia.como_json()})


@modulo_liberado
@require_GET
def midias_lista(request, pk):
    apresentacao = _apresentacao_editavel(request, pk)
    if apresentacao is None:
        return _erro('Sem permissão.', 403)
    filtro = Q(apresentacao=apresentacao)
    if apresentacao.template_id:
        filtro |= Q(template_id=apresentacao.template_id, tipo=Midia.Tipo.IMAGEM)
    midias = Midia.objects.filter(filtro).exclude(tipo=Midia.Tipo.DOCUMENTO).order_by('-criado_em')[:300]
    return JsonResponse({'ok': True, 'midias': [m.como_json() for m in midias]})


_RE_RANGE = re.compile(r'^bytes=(\d*)-(\d*)$')


@login_required
@require_GET
def midia(request, pk):
    item = get_object_or_404(Midia.objects.select_related('apresentacao'), pk=pk)
    if not pode_ver_midia(request.user, item):
        return HttpResponse(status=404)
    tipo_conteudo = item.mime or mimetypes.guess_type(item.arquivo.name)[0] or 'application/octet-stream'
    baixar = request.GET.get('download') == '1'
    # Vídeo no storage público: o <video> busca direto do MinIO (com Range), sem passar o arquivo pelo portal.
    if item.tipo == Midia.Tipo.VIDEO and getattr(settings, 'USE_S3', False) and not baixar:
        return HttpResponseRedirect(item.arquivo.url)
    try:
        arquivo = item.arquivo.open('rb')
    except Exception:                                           # noqa: BLE001
        return HttpResponse(status=404)
    tamanho = item.tamanho or getattr(item.arquivo, 'size', 0)
    faixa = _RE_RANGE.match(request.headers.get('Range') or '')
    if faixa and tamanho and item.tipo in (Midia.Tipo.VIDEO, Midia.Tipo.AUDIO):
        inicio_txt, fim_txt = faixa.groups()
        inicio = int(inicio_txt) if inicio_txt else max(0, tamanho - int(fim_txt or 0))
        fim = min(int(fim_txt), tamanho - 1) if fim_txt and inicio_txt else tamanho - 1
        if inicio >= tamanho:
            arquivo.close()
            return HttpResponse(status=416)
        arquivo.seek(inicio)
        restante = fim - inicio + 1

        def pedacos():
            faltam = restante
            try:
                while faltam > 0:
                    bloco = arquivo.read(min(512 * 1024, faltam))
                    if not bloco:
                        break
                    faltam -= len(bloco)
                    yield bloco
            finally:
                arquivo.close()
        resposta = StreamingHttpResponse(pedacos(), status=206, content_type=tipo_conteudo)
        resposta['Content-Range'] = f'bytes {inicio}-{fim}/{tamanho}'
        resposta['Content-Length'] = str(restante)
    else:
        resposta = FileResponse(arquivo, content_type=tipo_conteudo, as_attachment=baixar,
                                filename=item.nome or os.path.basename(item.arquivo.name))
    resposta['Accept-Ranges'] = 'bytes'
    resposta['Cache-Control'] = 'private, max-age=86400'
    resposta['X-Content-Type-Options'] = 'nosniff'
    return resposta


# ---------------------------------------------------------------------------
# IA
# ---------------------------------------------------------------------------
ACOES_IA = {
    'editar': TarefaIA.Tipo.EDITAR, 'refazer_slide': TarefaIA.Tipo.REFAZER_SLIDE,
    'novo_slide': TarefaIA.Tipo.NOVO_SLIDE, 'texto': TarefaIA.Tipo.TEXTO, 'imagem': TarefaIA.Tipo.IMAGEM,
    'video': TarefaIA.Tipo.VIDEO, 'narracao': TarefaIA.Tipo.NARRACAO,
}


@modulo_liberado
@require_POST
def ia(request, pk):
    apresentacao = _apresentacao_editavel(request, pk)
    if apresentacao is None:
        return _erro('Sem permissão para esta apresentação.', 403)
    try:
        dados = _corpo_json(request)
    except ValueError as exc:
        return _erro(str(exc))
    tipo = ACOES_IA.get(dados.get('acao'))
    if tipo is None:
        return _erro('Ação de IA desconhecida.')
    instrucao = formato.texto(dados.get('instrucao'), 4000).strip()
    if tipo in (TarefaIA.Tipo.EDITAR, TarefaIA.Tipo.NOVO_SLIDE, TarefaIA.Tipo.IMAGEM, TarefaIA.Tipo.VIDEO) \
            and not instrucao:
        return _erro('Diga o que você quer que a IA faça.')
    ids = [int(i) for i in (dados.get('midias') or []) if str(i).isdigit()][:MAX_ANEXOS]
    midias_ok = [m.pk for m in Midia.objects.filter(pk__in=ids) if pode_ver_midia(request.user, m)]
    parametros = {
        'instrucao': instrucao, 'slide_id': formato.texto(dados.get('slide_id'), 40),
        'elemento_id': formato.texto(dados.get('elemento_id'), 40), 'midias': midias_ok,
        'opcoes': dados.get('opcoes') if isinstance(dados.get('opcoes'), dict) else {},
    }
    if tipo == TarefaIA.Tipo.TEXTO:
        parametros['html'] = formato.sanear_html(dados.get('html'))
    if tipo == TarefaIA.Tipo.NARRACAO and isinstance(dados.get('slides'), list):
        parametros['slides'] = [formato.texto(s, 40) for s in dados['slides'][:300]]
    if isinstance(dados.get('documento'), dict):
        limpo = formato.sanear_documento(dados['documento'])
        if not _midias_permitidas(request.user, limpo, apresentacao=apresentacao):
            parametros['documento'] = limpo
    try:
        tarefa = tarefas.criar(tipo, request.user, apresentacao=apresentacao, parametros=parametros)
    except tarefas.TarefaRecusada as exc:
        return _erro(str(exc), 429)
    if instrucao and tipo in (TarefaIA.Tipo.EDITAR, TarefaIA.Tipo.REFAZER_SLIDE, TarefaIA.Tipo.NOVO_SLIDE):
        MensagemIA.objects.create(apresentacao=apresentacao, papel=MensagemIA.Papel.USUARIO, texto=instrucao,
                                  tarefa=tarefa)
    return JsonResponse({'ok': True, 'tarefa': tarefa.como_json()})


def _tarefa_visivel(request, pk):
    tarefa = get_object_or_404(TarefaIA.objects.select_related('apresentacao', 'template'), pk=pk)
    if tarefa.usuario_id == request.user.pk or e_superadmin(request.user):
        return tarefa
    return None


@modulo_liberado
@require_GET
def tarefa(request, pk):
    item = _tarefa_visivel(request, pk)
    if item is None:
        return _erro('Tarefa não encontrada.', 404)
    tarefas.conferir_parada(item)
    return JsonResponse({'ok': True, 'tarefa': item.como_json()})


@modulo_liberado
@require_POST
def responder(request, pk):
    item = _tarefa_visivel(request, pk)
    if item is None:
        return _erro('Tarefa não encontrada.', 404)
    try:
        dados = _corpo_json(request)
        tarefas.responder(item, dados.get('respostas') if isinstance(dados.get('respostas'), dict) else {})
    except (ValueError, tarefas.TarefaRecusada) as exc:
        return _erro(str(exc))
    return JsonResponse({'ok': True, 'tarefa': item.como_json()})


@modulo_liberado
@require_POST
def iniciar(request, pk):
    """Começa a geração depois que o navegador terminou de capturar as telas (módulos do portal)."""
    apresentacao = _apresentacao_editavel(request, pk)
    if apresentacao is None:
        return _erro('Sem permissão.', 403)
    if TarefaIA.objects.filter(apresentacao=apresentacao, tipo=TarefaIA.Tipo.GERAR,
                               status__in=[TarefaIA.Status.PENDENTE, TarefaIA.Status.RODANDO,
                                           TarefaIA.Status.PERGUNTAS]).exists():
        return JsonResponse({'ok': True, 'editor': reverse('apresentacoes:editor', args=[apresentacao.pk])})
    try:
        dados = _corpo_json(request)
    except ValueError as exc:
        return _erro(str(exc))
    ids = [int(i) for i in (dados.get('midias') or []) if str(i).isdigit()][:MAX_ANEXOS]
    anexos = list(Midia.objects.filter(pk__in=ids, apresentacao=apresentacao, tipo=Midia.Tipo.IMAGEM)
                  .values_list('pk', flat=True))
    ordenados = [i for i in ids if i in set(anexos)]
    try:
        tarefas.criar(TarefaIA.Tipo.GERAR, request.user, apresentacao=apresentacao, parametros={'anexos': ordenados})
    except tarefas.TarefaRecusada as exc:
        return _erro(str(exc), 429)
    Apresentacao.objects.filter(pk=apresentacao.pk).update(status=Apresentacao.Status.GERANDO)
    return JsonResponse({'ok': True, 'editor': reverse('apresentacoes:editor', args=[apresentacao.pk])})


# ---------------------------------------------------------------------------
# Exportação
# ---------------------------------------------------------------------------
@modulo_liberado
@require_GET
def exportar_pptx(request, pk):
    apresentacao = _apresentacao_editavel(request, pk)
    if apresentacao is None:
        messages.error(request, 'Sem permissão.')
        return redirect('apresentacoes:inicio')
    from . import exportar_pptx as exportador
    try:
        conteudo = exportador.gerar_pptx(apresentacao.documento or formato.documento_vazio(), apresentacao.titulo,
                                         ler_midia=tarefas.leitor_de_midia(request.user, apresentacao))
    except Exception:                                           # noqa: BLE001
        logger.exception('Exportação .pptx da apresentação %s falhou', apresentacao.pk)
        messages.error(request, 'Não foi possível gerar o PowerPoint agora.')
        return redirect('apresentacoes:editor', pk=apresentacao.pk)
    nome = exportador.nome_do_arquivo(apresentacao.titulo)
    resposta = HttpResponse(conteudo, content_type='application/vnd.openxmlformats-officedocument.presentationml.presentation')
    resposta['Content-Disposition'] = f'attachment; filename="{nome}"'
    return resposta


@modulo_liberado
@require_POST
def exportar_canva(request, pk):
    apresentacao = _apresentacao_editavel(request, pk)
    if apresentacao is None:
        return _erro('Sem permissão.', 403)
    cfg = configuracao()
    if not cfg.canva_configurado:
        return _erro('O Canva ainda não foi configurado pelo SUPERADMIN. Baixe o PowerPoint e importe no Canva '
                     '(Criar design → Importar arquivo).', 400, pptx=reverse('apresentacoes:exportar_pptx',
                                                                              args=[apresentacao.pk]))
    conexao = ConexaoCanva.objects.filter(user=request.user).first()
    if not (conexao and conexao.conectado):
        volta = reverse('apresentacoes:editor', args=[apresentacao.pk])
        return JsonResponse({'ok': False, 'conectar': f"{reverse('apresentacoes:canva_conectar')}?volta={volta}"})
    try:
        item = tarefas.criar(TarefaIA.Tipo.CANVA, request.user, apresentacao=apresentacao)
    except tarefas.TarefaRecusada as exc:
        return _erro(str(exc), 429)
    return JsonResponse({'ok': True, 'tarefa': item.como_json()})


@modulo_liberado
@require_POST
def video(request, pk):
    """Recebe o vídeo narrado gravado no navegador (webm) e entrega em mp4 quando há ffmpeg."""
    apresentacao = _apresentacao_editavel(request, pk)
    if apresentacao is None:
        return _erro('Sem permissão.', 403)
    arquivo = request.FILES.get('arquivo')
    if not arquivo:
        return _erro('Nenhum vídeo enviado.')
    if arquivo.size > LIMITES_UPLOAD[Midia.Tipo.VIDEO][1] * 2:
        return _erro('Vídeo grande demais.')
    conteudo = arquivo.read()
    nome_base = re.sub(r'[^A-Za-z0-9_-]+', '-', apresentacao.titulo)[:60].strip('-') or 'apresentacao'
    mp4 = _converter_para_mp4(conteudo)
    if mp4:
        midia_video = tarefas.salvar_midia(mp4, f'{nome_base}.mp4', dono=request.user, tipo=Midia.Tipo.VIDEO,
                                           origem=Midia.Origem.EXPORTACAO, apresentacao=apresentacao,
                                           mime='video/mp4')
    else:
        midia_video = tarefas.salvar_midia(conteudo, f'{nome_base}.webm', dono=request.user, tipo=Midia.Tipo.VIDEO,
                                           origem=Midia.Origem.EXPORTACAO, apresentacao=apresentacao,
                                           mime='video/webm')
    dados = midia_video.como_json()
    dados['download'] = f'{midia_video.url}?download=1'
    return JsonResponse({'ok': True, 'midia': dados})


def _converter_para_mp4(conteudo):
    if not shutil.which('ffmpeg'):
        return None
    with tempfile.TemporaryDirectory() as pasta:
        entrada, saida = os.path.join(pasta, 'entrada.webm'), os.path.join(pasta, 'saida.mp4')
        with open(entrada, 'wb') as arquivo:
            arquivo.write(conteudo)
        try:
            resultado = subprocess.run(
                ['ffmpeg', '-y', '-loglevel', 'error', '-i', entrada, '-c:v', 'libx264', '-preset', 'veryfast',
                 '-crf', '23', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '160k', '-movflags', '+faststart', saida],
                capture_output=True, timeout=900)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning('Conversão do vídeo para mp4 falhou: %s', exc)
            return None
        if resultado.returncode != 0 or not os.path.exists(saida):
            logger.warning('ffmpeg recusou o vídeo: %s', resultado.stderr[-500:])
            return None
        with open(saida, 'rb') as arquivo:
            return arquivo.read()


# ---------------------------------------------------------------------------
# Canva (OAuth)
# ---------------------------------------------------------------------------
def _volta_segura(request, volta):
    if volta and url_has_allowed_host_and_scheme(volta, allowed_hosts={request.get_host()},
                                                  require_https=request.is_secure()):
        return volta
    return reverse('apresentacoes:inicio')


@modulo_liberado
@require_GET
def canva_conectar(request):
    from . import canva
    volta = _volta_segura(request, request.GET.get('volta'))
    try:
        url = canva.url_de_autorizacao(request.user, request.build_absolute_uri(reverse('apresentacoes:canva_retorno')),
                                       volta)
    except Exception as exc:                                    # noqa: BLE001
        messages.error(request, str(exc) if exc.__class__.__name__.startswith('Canva') else
                       'Não foi possível iniciar a conexão com o Canva.')
        return redirect(volta)
    return HttpResponseRedirect(url)


@modulo_liberado
@require_GET
def canva_retorno(request):
    from . import canva
    conexao = ConexaoCanva.objects.filter(user=request.user).first()
    volta = _volta_segura(request, conexao.volta if conexao else '')
    if request.GET.get('error'):
        messages.error(request, 'A conexão com o Canva foi cancelada.')
        return redirect(volta)
    try:
        canva.concluir_autorizacao(request.user, request.GET.get('code') or '', request.GET.get('state') or '',
                                   request.build_absolute_uri(reverse('apresentacoes:canva_retorno')))
    except Exception as exc:                                    # noqa: BLE001
        messages.error(request, str(exc) if exc.__class__.__name__.startswith('Canva') else
                       'Não foi possível concluir a conexão com o Canva.')
        return redirect(volta)
    messages.success(request, 'Canva conectado. Clique de novo em "Abrir no Canva".')
    return redirect(volta)


@modulo_liberado
@require_POST
def canva_desconectar(request):
    from . import canva
    canva.desconectar(request.user)
    messages.success(request, 'Conta do Canva desconectada.')
    return redirect(_volta_segura(request, request.POST.get('volta')))


# ---------------------------------------------------------------------------
# Módulos do portal
# ---------------------------------------------------------------------------
@modulo_liberado
def modulos(request):
    from . import modulos as catalogo_modulos
    lista = catalogo_modulos.catalogo(request, usar_cache=request.GET.get('atualizar') != '1')
    existentes = {}
    for ap in (Apresentacao.objects.filter(origem=Apresentacao.Origem.MODULO)
               .select_related('dono').order_by('-atualizado_em')):
        if ap.modulo not in existentes and (e_superadmin(request.user) or ap.dono_id == request.user.pk):
            existentes[ap.modulo] = ap
    for modulo in lista:
        modulo['apresentacao'] = existentes.get(modulo['label'])
    garantir_template_padrao()
    modelos = _com_capas(list(templates_visiveis(request.user).filter(status=TemplateApresentacao.Status.PRONTO)))
    return render(request, 'apresentacoes/modulos.html', _contexto(
        request, 'modulos', modulos=lista, sem_apresentacao=sum(1 for m in lista if not m['apresentacao']),
        modelos=modelos, principal=next((t for t in modelos if t.principal), modelos[0] if modelos else None),
        publicos=OPCOES_PUBLICO))


@modulo_liberado
@require_POST
def modulo_gerar(request, label):
    from . import modulos as catalogo_modulos
    lista = catalogo_modulos.catalogo(request)
    modulo = next((m for m in lista if m['label'] == label), None)
    if modulo is None:
        return _erro('Módulo não encontrado no seu menu.', 404)
    try:
        dados = _corpo_json(request)
    except ValueError as exc:
        return _erro(str(exc))
    modelos = list(templates_visiveis(request.user).filter(status=TemplateApresentacao.Status.PRONTO))
    template = next((t for t in modelos if str(t.pk) == str(dados.get('template'))), None) or next(
        (t for t in modelos if t.principal), modelos[0] if modelos else None)
    tema = (template.tema if template else None) or formato.TEMA_PADRAO
    opcoes = {
        'publico': formato.texto(dados.get('publico'), 200) or 'Colaboradores que vão usar o módulo',
        'tom': 'Didático, passo a passo', 'quantidade': None, 'nome_modulo': modulo['nome'],
        'fonte_titulo': formato.fonte(dados.get('fonte_titulo')) or tema.get('fonte_titulo'),
        'fonte_texto': formato.fonte(dados.get('fonte_texto')) or tema.get('fonte_texto'),
        'imagens_ia': bool(dados.get('imagens_ia')), 'narracao': bool(dados.get('narracao')),
        'video_abertura': bool(dados.get('video_abertura')),
    }
    extra = formato.texto(dados.get('instrucoes'), 2000).strip()
    pedido = (f'Apresentação passo a passo do módulo "{modulo["nome"]}" do portal, explicando a nova entrega com '
              f'todas as funcionalidades, para quem vai usar.' + (f' Orientações: {extra}' if extra else ''))
    apresentacao = Apresentacao.objects.create(
        titulo=f'{modulo["nome"]} — como usar'[:200], pedido=pedido, dono=request.user, template=template,
        status=Apresentacao.Status.RASCUNHO, origem=Apresentacao.Origem.MODULO, modulo=label, opcoes=opcoes,
        documento=formato.documento_vazio(tema))
    MensagemIA.objects.create(apresentacao=apresentacao, papel=MensagemIA.Papel.USUARIO, texto=pedido)
    paginas = catalogo_modulos.paginas_para_capturar(label, modulo['telas']) if dados.get('capturar', True) else []
    return JsonResponse({
        'ok': True, 'id': apresentacao.pk, 'paginas': paginas,
        'upload': reverse('apresentacoes:midias_upload'),
        'iniciar': reverse('apresentacoes:iniciar', args=[apresentacao.pk]),
        'editor': reverse('apresentacoes:editor', args=[apresentacao.pk]),
    })


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
@modulo_liberado
def templates_lista(request):
    garantir_template_padrao()
    lista = _com_capas(list(templates_visiveis(request.user).select_related('criado_por')))
    for template in lista:
        template.pode_editar = pode_editar_template(request.user, template)
    return render(request, 'apresentacoes/templates.html', _contexto(request, 'templates', templates=lista))


@modulo_liberado
def template_novo(request):
    if request.method == 'POST':
        arquivos = request.FILES.getlist('arquivos')[:MAX_ANEXOS]
        if not arquivos:
            messages.error(request, 'Envie as imagens (ou o PDF / PowerPoint) do template.')
            return redirect('apresentacoes:template_novo')
        nome = formato.texto(request.POST.get('nome'), 120).strip() or 'Template importado'
        template = TemplateApresentacao.objects.create(
            nome=nome, descricao=formato.texto(request.POST.get('descricao'), 2000),
            estilo=formato.texto(request.POST.get('estilo'), 3000), origem=TemplateApresentacao.Origem.IMAGENS,
            status=TemplateApresentacao.Status.ANALISANDO, criado_por=request.user,
            padrao_da_rede=e_superadmin(request.user) and request.POST.get('padrao_da_rede') == 'on',
            documento=formato.documento_vazio())
        imagens, pptx, avisos = [], None, []
        for arquivo in arquivos:
            nome_arquivo = os.path.basename(arquivo.name or 'arquivo')[:180]
            extensao = os.path.splitext(nome_arquivo)[1].lower()
            if arquivo.size > LIMITE_MATERIAL:
                avisos.append(f'"{nome_arquivo}" passou de 40 MB.')
                continue
            conteudo = arquivo.read()
            try:
                if extensao == '.pptx':
                    pptx = tarefas.salvar_midia(conteudo, nome_arquivo, dono=request.user, tipo=Midia.Tipo.DOCUMENTO,
                                                origem=Midia.Origem.TEMPLATE, template=template,
                                                mime='application/vnd.openxmlformats-officedocument.presentationml.presentation')
                    template.origem = TemplateApresentacao.Origem.PPTX
                elif extensao == '.pdf':
                    template.origem = TemplateApresentacao.Origem.PDF
                    for numero, pagina in enumerate(importacao.paginas_do_pdf(conteudo), start=1):
                        imagens.append(tarefas.salvar_midia(
                            importacao.normalizar_imagem(pagina), f'{nome_arquivo}-p{numero}.jpg', dono=request.user,
                            tipo=Midia.Tipo.IMAGEM, origem=Midia.Origem.TEMPLATE, template=template,
                            mime='image/jpeg', largura=1920, altura=1080))
                else:
                    imagens.append(tarefas.salvar_midia(
                        importacao.normalizar_imagem(conteudo), f'{os.path.splitext(nome_arquivo)[0]}.jpg',
                        dono=request.user, tipo=Midia.Tipo.IMAGEM, origem=Midia.Origem.TEMPLATE, template=template,
                        mime='image/jpeg', largura=1920, altura=1080))
            except (ValueError, Exception) as exc:              # noqa: BLE001
                logger.warning('Arquivo de template recusado: %s', exc)
                avisos.append(f'"{nome_arquivo}" não pôde ser lido.')
        if not imagens and not pptx:
            template.delete()
            messages.error(request, 'Nenhum arquivo válido. ' + ' '.join(avisos))
            return redirect('apresentacoes:template_novo')
        template.save(update_fields=['origem'])
        parametros = {'pptx': pptx.pk} if pptx else {'imagens': [m.pk for m in imagens]}
        try:
            tarefas.criar(TarefaIA.Tipo.ANALISAR_TEMPLATE, request.user, template=template, parametros=parametros)
        except tarefas.TarefaRecusada as exc:
            messages.error(request, str(exc))
        for aviso in avisos:
            messages.warning(request, aviso)
        return redirect('apresentacoes:template_editor', pk=template.pk)
    return render(request, 'apresentacoes/template_novo.html', _contexto(request, 'templates'))


def _template_editavel(request, pk):
    template = get_object_or_404(TemplateApresentacao, pk=pk)
    return template if pode_editar_template(request.user, template) else None


@modulo_liberado
def template_editor(request, pk):
    template = _template_editavel(request, pk)
    if template is None:
        messages.error(request, 'Você não pode editar este template.')
        return redirect('apresentacoes:templates')
    tarefa = TarefaIA.objects.filter(template=template).order_by('-criado_em').first()
    if tarefa:
        tarefas.conferir_parada(tarefa)
    em_andamento = tarefa if tarefa and tarefa.em_andamento else None
    config = {
        'modo': 'template', 'id': template.pk, 'titulo': template.nome, 'status': template.status,
        'erro': template.erro,
        'urls': {
            'documento': reverse('apresentacoes:template_documento', args=[template.pk]),
            'midias': reverse('apresentacoes:midias_upload'),
            'midias_lista': reverse('apresentacoes:template_midias', args=[template.pk]),
            'ia': '', 'tarefa': reverse('apresentacoes:tarefa', args=[0]),
            'responder': reverse('apresentacoes:responder', args=[0]), 'mensagens': '',
            'pptx': '', 'canva': '', 'video': '', 'versoes': '', 'apresentar': '',
            'voltar': reverse('apresentacoes:templates'),
            'limpar_fundo': reverse('apresentacoes:template_limpar_fundo', args=[template.pk]),
        },
        'csrf': get_token(request), 'static_url': settings.STATIC_URL,
        'fontes_url': static('apresentacoes/fontes.json'), 'icones_url': static('apresentacoes/icones.json'),
        'tema_escuro': getattr(request.user, 'theme', '') == 'dark', 'pode_ia': False, 'canva_configurado': False,
        'tarefa_em_andamento': {'id': em_andamento.pk, 'tipo': em_andamento.tipo} if em_andamento else None,
        'upload_template': template.pk,
    }
    return render(request, 'apresentacoes/editor.html', {'apres_config': config, 'titulo': template.nome})


@modulo_liberado
def template_documento(request, pk):
    template = get_object_or_404(TemplateApresentacao, pk=pk)
    visivel = templates_visiveis(request.user).filter(pk=template.pk).exists()
    if request.method == 'GET':
        if not visivel:
            return _erro('Sem permissão.', 403)
        return JsonResponse({'ok': True, 'titulo': template.nome, 'documento': template.documento or
                             formato.documento_vazio(), 'revisao': template.revisao, 'status': template.status,
                             'pode_editar': pode_editar_template(request.user, template)})
    if request.method != 'POST':
        return _erro('Método não permitido.', 405)
    if not pode_editar_template(request.user, template):
        return _erro('Sem permissão para editar este template.', 403)
    try:
        dados = _corpo_json(request)
    except ValueError as exc:
        return _erro(str(exc))
    if not isinstance(dados.get('documento'), dict):
        return _erro('Documento ausente.')
    limpo = formato.sanear_documento(dados['documento'])
    erro_midia = _midias_permitidas(request.user, limpo, template=template)
    if erro_midia:
        return _erro(erro_midia, 403)
    with transaction.atomic():
        atual = TemplateApresentacao.objects.select_for_update().get(pk=template.pk)
        if dados.get('revisao') != atual.revisao:
            return _erro('Este template foi alterado em outro lugar.', 409, revisao=atual.revisao)
        atual.documento = limpo
        nome = formato.texto(dados.get('titulo'), 120).strip()
        if nome:
            atual.nome = nome
        atual.revisao += 1
        atual.save(update_fields=['documento', 'nome', 'revisao', 'atualizado_em'])
    return JsonResponse({'ok': True, 'revisao': atual.revisao, 'documento': limpo, 'titulo': atual.nome})


@modulo_liberado
@require_GET
def template_midias(request, pk):
    template = get_object_or_404(TemplateApresentacao, pk=pk)
    if not templates_visiveis(request.user).filter(pk=template.pk).exists():
        return _erro('Sem permissão.', 403)
    midias = Midia.objects.filter(template=template).exclude(tipo=Midia.Tipo.DOCUMENTO).order_by('-criado_em')[:300]
    return JsonResponse({'ok': True, 'midias': [m.como_json() for m in midias]})


@modulo_liberado
@require_POST
def template_limpar_fundo(request, pk):
    template = _template_editavel(request, pk)
    if template is None:
        return _erro('Sem permissão.', 403)
    try:
        dados = _corpo_json(request)
    except ValueError as exc:
        return _erro(str(exc))
    origem = formato.src_midia(dados.get('imagem'))
    midia_id = formato.id_midia(origem)
    areas = []
    for area in (dados.get('areas') or [])[:40]:
        if isinstance(area, dict):
            areas.append({k: formato.numero(area.get(k), 0, -2000, 4000) for k in ('x', 'y', 'w', 'h')})
    if not areas:
        return _erro('Marque ao menos uma área para apagar.')
    if midia_id:
        base = get_object_or_404(Midia, pk=midia_id)
        if not pode_ver_midia(request.user, base):
            return _erro('Sem permissão para esta imagem.', 403)
        with base.arquivo.open('rb') as arquivo:
            conteudo = arquivo.read()
    elif origem.startswith('static:'):
        from django.contrib.staticfiles import finders
        caminho = finders.find(origem[len('static:'):])
        if not caminho:
            return _erro('Imagem de fundo não encontrada.', 404)
        with open(caminho, 'rb') as arquivo:
            conteudo = arquivo.read()
    else:
        return _erro('Este layout não tem imagem de fundo.')
    try:
        from . import limpeza
        normalizada = importacao.normalizar_imagem(conteudo)
        limpo = limpeza.limpar_bytes(normalizada, areas, escala_do_documento=(1920, 1080))
    except Exception:                                           # noqa: BLE001
        logger.exception('Limpeza do fundo do template %s falhou', template.pk)
        return _erro('Não foi possível limpar essa imagem.')
    nova = tarefas.salvar_midia(limpo, 'fundo-limpo.jpg', dono=request.user, tipo=Midia.Tipo.IMAGEM,
                                origem=Midia.Origem.TEMPLATE, template=template, mime='image/jpeg', largura=1920,
                                altura=1080)
    return JsonResponse({'ok': True, 'midia': nova.como_json()})


@modulo_liberado
@require_POST
def template_excluir(request, pk):
    template = _template_editavel(request, pk)
    if template is None or template.origem == TemplateApresentacao.Origem.SISTEMA:
        messages.error(request, 'Este template não pode ser excluído.')
        return redirect('apresentacoes:templates')
    template.ativo = False
    template.principal = False
    template.save(update_fields=['ativo', 'principal', 'atualizado_em'])
    messages.success(request, f'Template "{template.nome}" removido.')
    return redirect('apresentacoes:templates')


@modulo_liberado
@require_POST
def template_opcoes(request, pk):
    """SUPERADMIN: disponível para todos e template principal."""
    template = get_object_or_404(TemplateApresentacao, pk=pk, ativo=True)
    if not e_superadmin(request.user):
        messages.error(request, 'Só o SUPERADMIN muda isso.')
        return redirect('apresentacoes:templates')
    acao = request.POST.get('acao')
    if acao == 'principal':
        TemplateApresentacao.objects.filter(principal=True).update(principal=False)
        template.principal = True
        template.padrao_da_rede = True
    elif acao == 'rede':
        template.padrao_da_rede = not template.padrao_da_rede
    template.save(update_fields=['principal', 'padrao_da_rede', 'atualizado_em'])
    messages.success(request, 'Template atualizado.')
    return redirect('apresentacoes:templates')


# ---------------------------------------------------------------------------
# Configurações (SUPERADMIN)
# ---------------------------------------------------------------------------
@login_required
def configuracoes(request):
    if not e_superadmin(request.user):
        messages.error(request, 'Só o SUPERADMIN configura o Assistente de Apresentações.')
        return redirect('apresentacoes:inicio' if pode_usar(request.user) else 'dashboard')
    cfg = ConfiguracaoApresentacoes.get()
    if request.method == 'POST':
        secao = request.POST.get('secao')
        if secao == 'acesso':
            antes = set(cfg.liberados.values_list('pk', flat=True))
            ids = {int(x) for x in request.POST.getlist('liberados') if str(x).isdigit()}
            liberados = list(User.objects.filter(pk__in=ids, is_active=True))
            cfg.liberados.set(liberados)
            limpar_cache_do_menu(antes ^ {u.pk for u in liberados})
            messages.success(request, f'{len(liberados)} pessoa(s) podem usar o Assistente de Apresentações.')
        elif secao == 'ia':
            for campo, opcoes in (('modelo_texto', None), ('modelo_imagem', None), ('modelo_video', None),
                                  ('modelo_voz', None), ('voz', VOZES)):
                valor = formato.texto(request.POST.get(campo), 60).strip()
                if valor and re.fullmatch(r'[A-Za-z0-9._\-]{2,60}', valor) and (opcoes is None or valor in opcoes):
                    setattr(cfg, campo, valor)
            messages.success(request, 'Modelos de IA salvos.')
        elif secao == 'canva':
            cfg.canva_client_id = formato.texto(request.POST.get('canva_client_id'), 120).strip()
            segredo = (request.POST.get('canva_client_secret') or '').strip()
            if request.POST.get('limpar_segredo') == 'on':
                cfg.set_canva_secret('')
            elif segredo:
                cfg.set_canva_secret(segredo[:500])
            messages.success(request, 'Integração com o Canva salva.')
        cfg.atualizado_por = request.user
        cfg.save()
        return redirect(f"{reverse('apresentacoes:configuracoes')}#{secao or 'acesso'}")
    return render(request, 'apresentacoes/configuracoes.html', _contexto(
        request, 'configuracoes', cfg=cfg,
        pessoas=User.objects.filter(is_active=True).select_related('sector').order_by('first_name', 'last_name'),
        liberados=set(cfg.liberados.values_list('pk', flat=True)), modelos_texto=MODELOS_TEXTO,
        modelos_imagem=MODELOS_IMAGEM, modelos_video=MODELOS_VIDEO, modelos_voz=MODELOS_VOZ, vozes=VOZES,
        canva_redirect=request.build_absolute_uri(reverse('apresentacoes:canva_retorno')),
        tem_chave_openai=bool(getattr(settings, 'OPENAI_API_KEY', '')),
        uso=Apresentacao.objects.aggregate(total=Count('id'), modulos=Count('id', filter=Q(origem='MODULO'))),
        tarefas_recentes=TarefaIA.objects.select_related('usuario').order_by('-criado_em')[:15]))
