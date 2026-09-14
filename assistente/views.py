"""Telas do Assistente Claude: conexão (em /users/settings/) e o chat."""
import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import claude_client
from .models import AssistenteConexao, AssistenteMensagem, MODELOS, MODELOS_VALIDOS

logger = logging.getLogger(__name__)

HISTORICO_TURNOS = 10   # quantas falas anteriores mandar como contexto


@login_required
@require_POST
def conectar(request):
    """Salva/atualiza a chave do Claude do usuário — testando antes de gravar."""
    conx = claude_client.conexao(request.user)
    chave = (request.POST.get('api_key') or '').strip()
    modelo = (request.POST.get('modelo') or conx.modelo or 'claude-sonnet-5').strip()
    if modelo not in MODELOS_VALIDOS:
        modelo = 'claude-sonnet-5'

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


@login_required
def chat(request):
    conx = claude_client.conexao(request.user)
    historico = list(AssistenteMensagem.objects.filter(user=request.user).order_by('criado_em'))
    return render(request, 'assistente/chat.html', {
        'conexao': conx,
        'modelos': MODELOS,
        'historico': historico[-60:],
    })


@login_required
@require_POST
def enviar(request):
    """Recebe a mensagem, roda o assistente e devolve a resposta (JSON)."""
    conx = claude_client.conexao(request.user)
    if not conx.conectado:
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
@require_POST
def limpar(request):
    AssistenteMensagem.objects.filter(user=request.user).delete()
    return redirect('assistente:chat')
