"""Telas do Assistente Claude: conexão (em /users/settings/) e o chat."""
import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import claude_client, colaborador
from .models import (MODELO_PADRAO, MODELOS, MODELOS_VALIDOS, AssistenteConexao, AssistenteDocumento,
                     AssistenteMensagem)

logger = logging.getLogger(__name__)

HISTORICO_TURNOS = 10   # quantas falas anteriores mandar como contexto


@login_required
@require_POST
def conectar(request):
    """Salva/atualiza a chave do Claude do usuário — testando antes de gravar."""
    conx = claude_client.conexao(request.user)
    chave = (request.POST.get('api_key') or '').strip()
    modelo = (request.POST.get('modelo') or conx.modelo or MODELO_PADRAO).strip()
    if modelo not in MODELOS_VALIDOS:
        modelo = MODELO_PADRAO

    # Sem chave nova: pode ser só troca de modelo de quem já está conectado.
    if not chave:
        if conx.conectado:
            conx.modelo = modelo
            conx.save(update_fields=['modelo', 'atualizado_em'])
            messages.success(request, 'Modelo do assistente atualizado.')
        else:
            messages.error(request, 'Cole a chave da Anthropic (começa com "sk-ant-").')
        return redirect(request.POST.get('voltar') or 'settings')

    ok, msg = claude_client.testar_chave(chave, modelo)
    if not ok:
        messages.error(request, f'Não conectou: {msg}')
        return redirect(request.POST.get('voltar') or 'settings')

    conx.set_api_key(chave)
    conx.modelo = modelo
    conx.ultimo_ok_em = timezone.now()
    conx.save()
    messages.success(request, 'Claude conectado com sucesso! O assistente já está disponível.')
    return redirect(request.POST.get('voltar') or 'settings')


@login_required
@require_POST
def desconectar(request):
    conx = claude_client.conexao(request.user)
    conx.set_api_key('')
    conx.ultimo_ok_em = None
    conx.save()
    messages.success(request, 'Claude desconectado. A chave foi removida.')
    return redirect(request.POST.get('voltar') or 'settings')


@login_required
@require_POST
def testar(request):
    conx = claude_client.conexao(request.user)
    if not conx.conectado:
        messages.error(request, 'Nenhuma chave conectada.')
        return redirect(request.POST.get('voltar') or 'settings')
    ok, msg = claude_client.testar_chave(conx.get_api_key(), conx.modelo)
    if ok:
        conx.ultimo_ok_em = timezone.now()
        conx.save(update_fields=['ultimo_ok_em', 'atualizado_em'])
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect(request.POST.get('voltar') or 'settings')


def tem_modo_claude(user):
    """Quem tem o Assistente Claude: o SUPERADMIN (menu), quem conectou a própria chave ou —
    com a chave da empresa configurada — todo mundo."""
    if user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN':
        return True
    return claude_client.pode_usar(user)


def _e_superadmin(user):
    return bool(user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN')


@login_required
@require_POST
def empresa_salvar(request):
    """SUPERADMIN: chave da empresa (para todos). Testa antes de gravar; em branco só troca o modelo."""
    from .models import ChaveClaudeEmpresa
    voltar = request.POST.get('voltar') or 'settings'
    if not _e_superadmin(request.user):
        messages.error(request, 'Só o SUPERADMIN configura a chave da empresa.')
        return redirect(voltar)
    empresa = ChaveClaudeEmpresa.get()
    chave = (request.POST.get('api_key') or '').strip()
    modelo = (request.POST.get('modelo') or empresa.modelo or MODELO_PADRAO).strip()
    if modelo not in MODELOS_VALIDOS:
        modelo = MODELO_PADRAO
    if not chave:
        if not empresa.configurada:
            messages.error(request, 'Cole a chave da Anthropic (começa com "sk-ant-").')
            return redirect(voltar)
        empresa.modelo = modelo
        empresa.save(update_fields=['modelo'])
        messages.success(request, 'Modelo da chave da empresa atualizado.')
        return redirect(voltar)
    ok, msg = claude_client.testar_chave(chave, modelo)
    if not ok:
        messages.error(request, f'A chave da empresa não foi salva: {msg}')
        return redirect(voltar)
    empresa.set_api_key(chave, request.user)
    empresa.modelo = modelo
    empresa.ultimo_ok_em = timezone.now()
    empresa.save()
    messages.success(request, 'Chave da empresa salva: a partir de agora todos usam esta no Assistente Claude.')
    return redirect(voltar)


@login_required
@require_POST
def empresa_remover(request):
    from .models import ChaveClaudeEmpresa
    voltar = request.POST.get('voltar') or 'settings'
    if not _e_superadmin(request.user):
        messages.error(request, 'Só o SUPERADMIN configura a chave da empresa.')
        return redirect(voltar)
    empresa = ChaveClaudeEmpresa.get()
    empresa.set_api_key('')
    empresa.save()
    messages.success(request, 'Chave da empresa removida. Cada um volta a usar a própria chave.')
    return redirect(voltar)


@login_required
@require_POST
def empresa_testar(request):
    from .models import ChaveClaudeEmpresa
    voltar = request.POST.get('voltar') or 'settings'
    if not _e_superadmin(request.user):
        return redirect(voltar)
    empresa = ChaveClaudeEmpresa.get()
    if not empresa.configurada:
        messages.error(request, 'Nenhuma chave da empresa configurada.')
        return redirect(voltar)
    ok, msg = claude_client.testar_chave(empresa.get_api_key(), empresa.modelo)
    if ok:
        empresa.ultimo_ok_em = timezone.now()
        empresa.save(update_fields=['ultimo_ok_em'])
        messages.success(request, f'Chave da empresa: {msg}')
    else:
        messages.error(request, f'Chave da empresa: {msg}')
    return redirect(voltar)


@login_required
def chat(request):
    # Para os outros, a página é o modo colaborador: sem ferramenta e sem dado de terceiros.
    if not tem_modo_claude(request.user):
        return render(request, 'assistente/colaborador.html')
    conx = claude_client.conexao(request.user)
    historico = list(AssistenteMensagem.objects.filter(user=request.user).order_by('criado_em'))
    return render(request, 'assistente/chat.html', {
        'conexao': conx,
        'pode_usar': claude_client.pode_usar(request.user),
        'modelos': MODELOS,
        'historico': historico[-60:],
    })


@login_required
@require_POST
def enviar(request):
    """Recebe a mensagem, roda o assistente e devolve a resposta (JSON)."""
    if not claude_client.pode_usar(request.user):
        return JsonResponse({'erro': 'Conecte o seu Claude em Configurações primeiro.',
                             'precisa_conectar': True}, status=400)

    try:
        corpo = json.loads(request.body or '{}')
    except ValueError:
        corpo = {}
    mensagem = (corpo.get('mensagem') or '').strip()
    if not mensagem:
        return JsonResponse({'erro': 'Escreva uma mensagem.'}, status=400)
    if len(mensagem) > 4000:
        return JsonResponse({'erro': 'Mensagem muito longa.'}, status=400)

    # Histórico (as falas anteriores) como contexto — só texto, papel a papel.
    anteriores = list(AssistenteMensagem.objects.filter(user=request.user).order_by('-criado_em')[:HISTORICO_TURNOS])
    historico = [{'role': m.papel, 'content': m.conteudo} for m in reversed(anteriores)]

    AssistenteMensagem.objects.create(user=request.user, papel='user', conteudo=mensagem)
    try:
        resposta = claude_client.responder(request.user, mensagem, historico)
    except claude_client.NaoConectado as exc:
        return JsonResponse({'erro': str(exc), 'precisa_conectar': True}, status=400)
    except RuntimeError as exc:
        return JsonResponse({'erro': str(exc)}, status=502)
    except Exception as exc:  # noqa: BLE001
        logger.exception('Assistente falhou: %s', exc)
        return JsonResponse({'erro': 'Erro inesperado no assistente.'}, status=500)

    AssistenteMensagem.objects.create(user=request.user, papel='assistant', conteudo=resposta)
    return JsonResponse({'resposta': resposta})


@login_required
def colaborador_estado(request):
    """O que a janelinha precisa saber. Só é pedido ao abrir: a página não paga por isto."""
    conectado = claude_client.pode_usar(request.user)
    return JsonResponse({'claude': conectado, 'chave_empresa': claude_client.empresa_configurada(),
                         'pagina_claude': reverse('assistente:chat') if tem_modo_claude(request.user) else ''})


@login_required
@require_POST
def colaborador_perguntar(request):
    """A pergunta do modo colaborador: busca o que a pessoa pode abrir e orienta."""
    try:
        corpo = json.loads(request.body or '{}')
    except ValueError:
        corpo = {}
    if not isinstance(corpo, dict):
        corpo = {}
    pergunta = corpo.get('mensagem')
    pergunta = pergunta.strip() if isinstance(pergunta, str) else ''
    if not pergunta:
        return JsonResponse({'erro': 'Escreva a sua dúvida.'}, status=400)
    if len(pergunta) > 2000:
        return JsonResponse({'erro': 'A mensagem está muito longa. Resuma em até 2.000 caracteres.'}, status=400)
    try:
        return JsonResponse(colaborador.orientar(request, pergunta, colaborador.historico_valido(corpo.get('historico'))))
    except Exception as exc:  # noqa: BLE001
        logger.exception('Assistente colaborador falhou: %s', exc)
        return JsonResponse({'erro': 'Não consegui responder agora. Tente de novo em instantes.'}, status=500)


@login_required
@require_POST
def perguntar(request):
    """A janela do canto: Orientação e Claude numa conversa só (09/11/2026).

    Quem tem Claude (chave própria ou da empresa) recebe a resposta do Claude — com
    as ferramentas de sempre — já alimentada pela busca da orientação, e a tela
    mostra os cartões de POPs/páginas e o atalho do chamado. Sem Claude, ou se ele
    falhar, a resposta é a orientação de antes: a pessoa nunca fica sem nada.
    """
    try:
        corpo = json.loads(request.body or '{}')
    except ValueError:
        corpo = {}
    if not isinstance(corpo, dict):
        corpo = {}
    pergunta = corpo.get('mensagem')
    pergunta = pergunta.strip() if isinstance(pergunta, str) else ''
    if not pergunta:
        return JsonResponse({'erro': 'Escreva a sua dúvida.'}, status=400)
    if len(pergunta) > 2000:
        return JsonResponse({'erro': 'A mensagem está muito longa. Resuma em até 2.000 caracteres.'}, status=400)
    historico = colaborador.historico_valido(corpo.get('historico'))

    def so_orientacao(aviso=''):
        dados = colaborador.orientar(request, pergunta, historico)
        dados['claude'] = False
        if aviso:
            dados['resposta'] = f'{aviso}\n\n{dados.get("resposta") or ""}'.strip()
        return JsonResponse(dados)

    try:
        if not claude_client.pode_usar(request.user):
            return so_orientacao()
        try:
            bloco, tela = colaborador.material_para_claude(request, pergunta, historico)
        except Exception as exc:  # noqa: BLE001 — sem o material, o Claude ainda responde
            logger.warning('Assistente: busca da orientação falhou: %s', exc)
            bloco, tela = '', {'fontes': [], 'paginas': [], 'chamado': None}
        anteriores = [{'role': t['papel'], 'content': t['texto']} for t in historico]
        try:
            resposta = claude_client.responder(request.user, pergunta + bloco, anteriores)
        except claude_client.NaoConectado:
            return so_orientacao()
        except Exception as exc:  # noqa: BLE001 — Claude fora: cai na orientação
            logger.warning('Assistente: Claude falhou na janela, caindo na orientação: %s', exc)
            return so_orientacao('O Claude não respondeu agora; segue a orientação do portal.')
        # A conversa da janela também aparece na página do assistente.
        AssistenteMensagem.objects.create(user=request.user, papel='user', conteudo=pergunta)
        AssistenteMensagem.objects.create(user=request.user, papel='assistant', conteudo=resposta)
        return JsonResponse({'claude': True, 'resposta': resposta, **colaborador.filtrar_cartoes(pergunta, resposta, tela)})
    except Exception as exc:  # noqa: BLE001
        logger.exception('Assistente (janela) falhou: %s', exc)
        return JsonResponse({'erro': 'Não consegui responder agora. Tente de novo em instantes.'}, status=500)


@login_required
@require_POST
def limpar(request):
    AssistenteMensagem.objects.filter(user=request.user).delete()
    return redirect('assistente:chat')


# ---------------------------------------------------------------------------
# Documentos criados pelo assistente
# ---------------------------------------------------------------------------
@login_required
def documentos(request):
    return render(request, 'assistente/documentos.html', {
        'documentos': AssistenteDocumento.objects.filter(user=request.user)[:200]})


@login_required
def documento(request, pk):
    from django.shortcuts import get_object_or_404
    doc = get_object_or_404(AssistenteDocumento, pk=pk, user=request.user)
    return render(request, 'assistente/documento.html', {'doc': doc, 'imprimir': request.GET.get('imprimir') == '1'})


FORMATOS = {
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'md': 'text/markdown; charset=utf-8',
}


@login_required
def documento_baixar(request, pk, formato):
    from django.http import Http404, HttpResponse
    from django.shortcuts import get_object_or_404

    from . import documentos as gerador
    if formato not in FORMATOS:
        raise Http404
    doc = get_object_or_404(AssistenteDocumento, pk=pk, user=request.user)
    conteudo = {'docx': gerador.gerar_docx, 'xlsx': gerador.gerar_xlsx, 'md': gerador.gerar_md}[formato](doc)
    resposta = HttpResponse(conteudo, content_type=FORMATOS[formato])
    resposta['Content-Disposition'] = f'attachment; filename="{gerador.nome_de_arquivo(doc, formato)}"'
    return resposta


@login_required
@require_POST
def documento_excluir(request, pk):
    AssistenteDocumento.objects.filter(pk=pk, user=request.user).delete()
    messages.success(request, 'Documento excluído.')
    return redirect('assistente:documentos')
