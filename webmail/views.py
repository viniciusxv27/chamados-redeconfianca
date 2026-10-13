"""Telas e API do e-mail.

A página é uma só (``caixa``); pastas, lista, leitura e envio vêm por fetch.
Toda chamada usa a conta do próprio usuário logado — não existe parâmetro de
conta, então ninguém lê a caixa de outro trocando um número na URL.
"""
import json
import logging
import mimetypes
import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from . import cliente
from .conteudo import limpar_assinatura
from .models import ContaEmail

logger = logging.getLogger(__name__)

TENTATIVAS = 5              # senhas erradas seguidas antes de esperar
JANELA_TENTATIVAS = 15 * 60


def _dominios():
    return [d.strip().lower() for d in (settings.WEBMAIL_DOMINIOS or '').split(',') if d.strip()]


def _conta(request):
    return ContaEmail.objects.filter(usuario=request.user).select_related('usuario').first()


def _erro(exc, conta=None):
    """Erro do servidor de e-mail → JSON; senha recusada pede para entrar de novo."""
    if isinstance(exc, cliente.SenhaRecusada) and conta is not None:
        ContaEmail.objects.filter(pk=conta.pk).update(senha_recusada=True, ultimo_erro=str(exc)[:300])
        cliente.esquecer(conta.pk)
        return JsonResponse({'ok': False, 'erro': str(exc), 'reconectar': True}, status=401)
    if isinstance(exc, cliente.ServidorFora) and conta is not None:
        ContaEmail.objects.filter(pk=conta.pk).update(ultimo_erro=str(exc)[:300])
    return JsonResponse({'ok': False, 'erro': str(exc)}, status=getattr(exc, 'status', 400))


def _api(funcao):
    """Exige conta conectada, roda a função e transforma erro em JSON."""
    def embrulho(request, *args, **kwargs):
        conta = _conta(request)
        if conta is None or conta.senha_recusada:
            return JsonResponse({'ok': False, 'erro': 'Conecte seu e-mail.', 'reconectar': True}, status=401)
        try:
            resposta = funcao(request, conta, *args, **kwargs)
        except cliente.ErroEmail as exc:
            return _erro(exc, conta)
        agora = timezone.now()
        if not conta.ultimo_acesso or (agora - conta.ultimo_acesso).total_seconds() > 300:
            ContaEmail.objects.filter(pk=conta.pk).update(ultimo_acesso=agora, ultimo_erro='')
        return resposta
    embrulho.__name__ = funcao.__name__
    embrulho.__doc__ = funcao.__doc__
    return login_required(embrulho)


# ── Páginas ───────────────────────────────────────────────────────────────

@login_required
def caixa(request):
    conta = _conta(request)
    if conta is None or conta.senha_recusada:
        return render(request, 'webmail/conectar.html', {
            'conta': conta,
            'dominio': (_dominios() or [''])[0],
            'servidor': settings.WEBMAIL_IMAP_HOST,
        })
    nome = conta.remetente or conta.email
    return render(request, 'webmail/caixa.html', {
        'conta': conta,
        'iniciais': ''.join(p[0] for p in nome.split()[:2]).upper() or conta.email[:1].upper(),
        'assinatura_padrao': _assinatura_padrao(request.user, conta),
    })


def _assinatura_padrao(user, conta):
    nome = user.get_full_name() or user.username
    linhas = [f'<strong>{nome}</strong>']
    cargo = ' · '.join(x for x in (getattr(user, 'job_title', ''), getattr(user.sector, 'name', '') if user.sector else '') if x)
    if cargo:
        linhas.append(cargo)
    linhas.append('Rede Confiança')
    linhas.append(conta.email)
    return '<p style="color:#6b7280;font-size:13px;line-height:1.5">' + '<br>'.join(linhas) + '</p>'


@login_required
@require_POST
def conectar(request):
    email = (request.POST.get('email') or '').strip().lower()
    senha = request.POST.get('senha') or ''
    dominios = _dominios()
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        messages.error(request, 'Digite o e-mail completo, como nome@' + (dominios[0] if dominios else 'empresa.com.br') + '.')
        return redirect('webmail:caixa')
    if dominios and email.split('@')[-1] not in dominios:
        messages.error(request, 'Use o seu e-mail institucional (@' + ', @'.join(dominios) + ').')
        return redirect('webmail:caixa')
    if not senha:
        messages.error(request, 'Digite a senha do e-mail.')
        return redirect('webmail:caixa')

    chave = f'webmail:tentativas:{request.user.pk}'
    tentativas = cache.get(chave, 0)
    if tentativas >= TENTATIVAS:
        messages.error(request, 'Muitas tentativas com senha errada. Espere 15 minutos e tente de novo.')
        return redirect('webmail:caixa')
    try:
        cliente.testar_login(email, senha)
    except cliente.SenhaRecusada:
        cache.set(chave, tentativas + 1, JANELA_TENTATIVAS)
        messages.error(request, 'E-mail ou senha não conferem no servidor de e-mail.')
        return redirect('webmail:caixa')
    except cliente.ErroEmail as exc:
        messages.error(request, f'Não consegui falar com o servidor de e-mail: {exc}')
        return redirect('webmail:caixa')
    cache.delete(chave)

    conta = ContaEmail.objects.filter(usuario=request.user).first() or ContaEmail(usuario=request.user)
    trocou = conta.pk and conta.email != email
    conta.email = email
    conta.definir_senha(senha)
    if trocou or not conta.pk:
        conta.nome_exibicao = request.user.get_full_name() or ''
    conta.ultimo_acesso = timezone.now()
    conta.save()
    cliente.esquecer(conta.pk)
    messages.success(request, f'Pronto! {email} está ligado ao seu usuário do portal.')
    return redirect('webmail:caixa')


@login_required
@require_POST
def desconectar(request):
    conta = _conta(request)
    if conta is not None:
        cliente.esquecer(conta.pk)
        conta.delete()
        messages.success(request, 'E-mail desconectado. A senha foi apagada do portal.')
    return redirect('webmail:caixa')


@login_required
@require_POST
def preferencias(request):
    conta = _conta(request)
    if conta is None:
        return JsonResponse({'ok': False, 'erro': 'Conecte seu e-mail.'}, status=401)
    conta.nome_exibicao = (request.POST.get('nome_exibicao') or '').strip()[:150]
    conta.assinatura = limpar_assinatura(request.POST.get('assinatura') or '')[:20000]
    conta.save(update_fields=['nome_exibicao', 'assinatura', 'atualizada_em'])
    return JsonResponse({'ok': True, 'assinatura': conta.assinatura, 'nome_exibicao': conta.nome_exibicao})


# ── API ───────────────────────────────────────────────────────────────────

@_api
@require_GET
def api_pastas(request, conta):
    return JsonResponse({'ok': True, 'pastas': cliente.executar(conta, cliente.pastas)})


@_api
@require_POST
def api_criar_pasta(request, conta):
    nome = cliente.executar(conta, lambda s: cliente.criar_pasta(s, request.POST.get('nome')))
    return JsonResponse({'ok': True, 'id': nome})


def _int(valor, padrao=1):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return padrao


@_api
@require_GET
def api_lista(request, conta):
    pasta = request.GET.get('pasta') or 'INBOX'
    dados = cliente.executar(conta, lambda s: cliente.listar(
        s, pasta, _int(request.GET.get('pagina')), request.GET.get('busca') or '',
        request.GET.get('filtro') or ''))
    return JsonResponse({'ok': True, **dados})


@_api
@require_GET
def api_mensagem(request, conta):
    pasta = request.GET.get('pasta') or 'INBOX'
    uid = _int(request.GET.get('uid'), 0)
    if not uid:
        return JsonResponse({'ok': False, 'erro': 'Mensagem inválida.'}, status=400)
    dados = cliente.executar(conta, lambda s: cliente.ler(
        s, pasta, uid, imagens=request.GET.get('imagens') == '1'))
    return JsonResponse({'ok': True, 'mensagem': dados})


@_api
@require_POST
def api_acao(request, conta):
    try:
        corpo = json.loads(request.body or b'{}')
    except ValueError:
        corpo = {}
    pasta = corpo.get('pasta') or 'INBOX'
    resultado = cliente.executar(conta, lambda s: cliente.acao(
        s, pasta, corpo.get('uids') or [], corpo.get('acao') or '', corpo.get('destino') or ''))
    return JsonResponse({'ok': True, 'resultado': resultado})


@_api
@require_GET
def api_resumo(request, conta):
    return JsonResponse({'ok': True, **cliente.executar(conta, cliente.resumo)})


@_api
@require_GET
def api_novidades(request, conta):
    return JsonResponse({'ok': True, **cliente.executar(conta, cliente.novidades)})


@_api
@require_GET
def api_contatos(request, conta):
    """Quem já tem e-mail institucional ligado no portal — para o campo Para."""
    termo = (request.GET.get('q') or '').strip()
    if len(termo) < 2:
        return JsonResponse({'ok': True, 'contatos': []})
    from django.db.models import Q
    contas = (ContaEmail.objects.select_related('usuario', 'usuario__sector')
              .filter(Q(email__icontains=termo) | Q(nome_exibicao__icontains=termo)
                      | Q(usuario__first_name__icontains=termo) | Q(usuario__last_name__icontains=termo))
              .exclude(pk=conta.pk)[:8])
    return JsonResponse({'ok': True, 'contatos': [{
        'nome': c.remetente or c.usuario.get_full_name(),
        'email': c.email,
        'setor': c.usuario.sector.name if c.usuario.sector else '',
    } for c in contas]})


def _baixar(nome, mime, dado, inline):
    seguro_inline = inline and (mime.startswith('image/') and mime != 'image/svg+xml' or mime == 'application/pdf')
    resposta = HttpResponse(dado, content_type=mime if seguro_inline else (mime or 'application/octet-stream'))
    nome_ascii = re.sub(r'[^\w.\- ]', '_', nome.encode('ascii', 'replace').decode())[:150] or 'anexo'
    from urllib.parse import quote
    resposta['Content-Disposition'] = (
        f'{"inline" if seguro_inline else "attachment"}; filename="{nome_ascii}"; '
        f"filename*=UTF-8''{quote(nome)}")
    resposta['X-Content-Type-Options'] = 'nosniff'
    if mime != 'application/pdf':          # o leitor de PDF do navegador não abre com sandbox
        resposta['Content-Security-Policy'] = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"
    resposta['Cache-Control'] = 'private, max-age=300'
    return resposta


@_api
@require_GET
def anexo(request, conta):
    pasta = request.GET.get('pasta') or 'INBOX'
    nome, mime, dado = cliente.executar(conta, lambda s: cliente.anexo(
        s, pasta, _int(request.GET.get('uid'), 0), request.GET.get('secao') or ''))
    return _baixar(nome, mime, dado, request.GET.get('inline') == '1')


@_api
@require_GET
def original(request, conta):
    pasta = request.GET.get('pasta') or 'INBOX'
    uid = _int(request.GET.get('uid'), 0)
    dado = cliente.executar(conta, lambda s: cliente.original(s, pasta, uid))
    return _baixar(f'mensagem-{uid}.eml', 'message/rfc822', dado, False)


def _ler_envio(request, conta, rascunho):
    anexos = []
    for arquivo in request.FILES.getlist('anexos'):
        mime = arquivo.content_type or mimetypes.guess_type(arquivo.name)[0] or 'application/octet-stream'
        anexos.append((arquivo.name, mime, arquivo.read()))
    p = request.POST
    encaminhar = p.get('encaminhar_pasta'), _int(p.get('encaminhar_uid'), 0)
    if encaminhar[1]:
        manter = set(p.getlist('encaminhar_anexo'))
        anexos += [a for a in cliente.executar(conta, lambda s: cliente.anexos_de(s, encaminhar[0], encaminhar[1]))
                   if not manter or a[0] in manter]
    return cliente.montar(
        conta, para=p.get('para', ''), cc=p.get('cc', ''), cco=p.get('cco', ''),
        assunto=p.get('assunto', ''), html_corpo=p.get('corpo', ''), anexos=anexos,
        responde_a=p.get('responde_a', ''), referencias=p.get('referencias', ''), rascunho=rascunho)


@_api
@require_POST
def api_enviar(request, conta):
    msg, destinos = _ler_envio(request, conta, rascunho=False)
    recusados = cliente.enviar(conta, msg, destinos)
    p = request.POST
    respondida = (p.get('respondida_pasta'), p.get('respondida_uid')) if p.get('respondida_uid') else None
    try:
        cliente.executar(conta, lambda s: cliente.depois_do_envio(
            s, msg, destinos, rascunho_uid=p.get('rascunho_uid') or None, respondida=respondida))
    except cliente.ErroEmail as exc:
        logger.warning('Envio feito; arrumação depois falhou: %s', exc)
    return JsonResponse({'ok': True, 'recusados': list(recusados)})


@_api
@require_POST
def api_rascunho(request, conta):
    msg, _ = _ler_envio(request, conta, rascunho=True)
    uid = cliente.executar(conta, lambda s: cliente.salvar_rascunho(s, msg, request.POST.get('rascunho_uid') or None))
    return JsonResponse({'ok': True, 'uid': uid})
