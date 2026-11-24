"""Aba "Automação SAP" da Visão SAP e a API que o app da loja consulta para se atualizar sozinho."""
import logging

from django.contrib import messages
from django.core import signing
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.db.models import F
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from . import automacao
from .espelho import ultima_sincronizacao
from .models import ChaveAutomacaoSAP, InstaladorPonteSAP, VersaoAutomacaoSAP, versao_em_tupla
from .permissions import CHAVE_GESTOR, e_gestor, pode_escolher_gestores, pode_ver
from .views import so_auditoria

logger = logging.getLogger(__name__)


def e_superadmin(user):
    return bool(user.is_authenticated and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def pode_enviar_versao(user):
    """Envia, publica e despublica versões do app (25/11/2026): o SUPERADMIN e os gestores marcados em /sap/gestores/.

    A chave do app e o instalador da ponte continuam só com o SUPERADMIN.
    """
    if e_superadmin(user):
        return True
    from users.module_access import user_has_module
    return bool(user.is_authenticated and user_has_module(user, CHAVE_GESTOR))


def _so_superadmin(request):
    if e_superadmin(request.user):
        return None
    messages.error(request, 'Só o SUPERADMIN mexe na chave do app e na ponte.')
    return redirect('auditoria_sap:automacao')


def _so_quem_envia(request):
    if pode_enviar_versao(request.user):
        return None
    messages.error(request, 'Só o SUPERADMIN e os gestores da Visão SAP enviam versões da Automação SAP.')
    return redirect('auditoria_sap:automacao')


def _log(request, texto):
    try:
        from core.models import SystemLog
        SystemLog.objects.create(user=request.user, action_type='ADMIN_ACTION', description=texto[:1000])
    except Exception:  # noqa: BLE001 — o log não segura a ação
        logger.warning('Log da Automação SAP não foi gravado', exc_info=True)


def _entregar(versao):
    if automacao.em_disco():
        return FileResponse(versao.arquivo.open('rb'), as_attachment=True,
                            filename=f'AutomacaoSAP_v{versao.versao}.zip')
    return redirect(automacao.link_temporario(versao))


@login_required
@so_auditoria
def tela(request):
    superadmin = e_superadmin(request.user)
    pode_enviar = pode_enviar_versao(request.user)
    versoes = list(VersaoAutomacaoSAP.objects.select_related('enviada_por'))
    versoes.sort(key=lambda v: v.tupla, reverse=True)
    contexto = {
        'aba': 'automacao', 'ultima': ultima_sincronizacao(), 'e_gestor': e_gestor(request.user),
        'pode_escolher_gestores': pode_escolher_gestores(request.user),
        'atual': VersaoAutomacaoSAP.atual(), 'versoes': versoes if pode_enviar else [v for v in versoes if v.publicada],
        'superadmin': superadmin, 'pode_enviar': pode_enviar, 'ponte': automacao.ponte_oferecida(), 'ponte_enviada': InstaladorPonteSAP.atual(),
    }
    if superadmin:
        contexto['chave'] = ChaveAutomacaoSAP.get()
        contexto['url_api'] = request.build_absolute_uri(reverse('auditoria_sap:automacao_api_versao'))
    return render(request, 'auditoria_sap/automacao.html', contexto)


@login_required
@require_POST
def enviar(request):
    if (barra := _so_quem_envia(request)) is not None:
        return barra
    arquivo = request.FILES.get('arquivo')
    if not arquivo:
        messages.error(request, 'Escolha o .zip da versão.')
        return redirect('auditoria_sap:automacao')
    versao = (request.POST.get('versao') or '').strip().lstrip('vV') or automacao.versao_do_nome(arquivo.name)
    if not versao_em_tupla(versao):
        messages.error(request, 'Informe a versão no formato 1.2.3 (ou deixe no nome do arquivo, ex.: AutomacaoSAP_v1.2.3.zip).')
        return redirect('auditoria_sap:automacao')
    if VersaoAutomacaoSAP.objects.filter(versao=versao).exists():
        messages.error(request, f'A versão {versao} já foi enviada. Use um número novo.')
        return redirect('auditoria_sap:automacao')
    try:
        sha256, tamanho = automacao.conferir_zip(arquivo)
    except automacao.PacoteInvalido as erro:
        messages.error(request, f'Pacote recusado: {erro}')
        return redirect('auditoria_sap:automacao')
    atual = VersaoAutomacaoSAP.atual()
    try:
        nova = VersaoAutomacaoSAP.objects.create(
            versao=versao, arquivo=arquivo, sha256=sha256, tamanho=tamanho,
            notas=(request.POST.get('notas') or '').strip()[:4000],
            publicada=request.POST.get('publicar', 'on') == 'on', enviada_por=request.user)
    except IntegrityError:
        messages.error(request, f'A versão {versao} já foi enviada. Use um número novo.')
        return redirect('auditoria_sap:automacao')
    _log(request, f'Automação SAP: versão {versao} enviada ({tamanho // 1024} KB, sha256 {sha256[:12]}…)'
                  f'{"" if nova.publicada else " — sem publicar"}')
    if nova.publicada and atual and nova.tupla < atual.tupla:
        messages.warning(request, f'Versão {versao} salva, mas a {atual.versao} é maior e continua sendo a oferecida às lojas.')
    elif nova.publicada:
        messages.success(request, f'Versão {versao} publicada: os apps das lojas se atualizam na próxima vez que abrirem.')
    else:
        messages.success(request, f'Versão {versao} salva sem publicar. Publique quando quiser liberar para as lojas.')
    return redirect('auditoria_sap:automacao')


@login_required
@require_POST
def publicar(request, versao_id):
    if (barra := _so_quem_envia(request)) is not None:
        return barra
    versao = get_object_or_404(VersaoAutomacaoSAP, pk=versao_id)
    versao.publicada = not versao.publicada
    versao.save(update_fields=['publicada'])
    _log(request, f'Automação SAP: versão {versao.versao} {"publicada" if versao.publicada else "despublicada"}')
    atual = VersaoAutomacaoSAP.atual()
    messages.success(request, f'Versão {versao.versao} {"publicada" if versao.publicada else "despublicada"}. '
                              f'As lojas recebem agora a {atual.versao if atual else "— nenhuma —"}.')
    return redirect('auditoria_sap:automacao')


@login_required
@require_POST
def trocar_chave(request):
    if (barra := _so_superadmin(request)) is not None:
        return barra
    chave = ChaveAutomacaoSAP.get()
    chave.chave = ChaveAutomacaoSAP.nova_chave()
    chave.trocada_por = request.user
    chave.save()
    _log(request, 'Automação SAP: chave do app trocada')
    messages.warning(request, 'Chave trocada. Os apps com a chave antiga param de se atualizar até receberem a nova.')
    return redirect('auditoria_sap:automacao')


@login_required
@so_auditoria
def baixar(request, versao_id):
    versao = get_object_or_404(VersaoAutomacaoSAP, pk=versao_id)
    if not versao.publicada and not pode_enviar_versao(request.user):
        raise Http404
    VersaoAutomacaoSAP.objects.filter(pk=versao.pk).update(downloads=F('downloads') + 1)
    return _entregar(versao)


# ── O app dentro do portal (Ponte) ────────────────────────────────────────────
SAL_PONTE = 'auditoria_sap.ponte'
PONTE_TOKEN_SEGUNDOS = 120
PONTE_TAMANHO_MAX = 60 * 1024 * 1024


@login_required
@so_auditoria
@require_GET
def ponte_sessao(request):
    """O que a página entrega à ponte do computador: um token curto (quem é) e a chave para parear."""
    token = signing.dumps({'u': request.user.pk}, salt=SAL_PONTE, compress=True)
    return JsonResponse({'token': token, 'chave': ChaveAutomacaoSAP.get().chave,
                         'portal': request.build_absolute_uri('/').rstrip('/')}, headers={'Cache-Control': 'no-store'})


@require_GET
def api_ponte_conferir(request):
    """A ponte confere o token que recebeu da página: quem é, e se ainda pode usar a Automação SAP."""
    if not automacao.chave_confere(request, ChaveAutomacaoSAP.get().chave):
        return JsonResponse({'ok': False, 'erro': 'Chave inválida: abra a página de novo para parear a ponte.'}, status=403)
    try:
        dados = signing.loads(request.GET.get('token', ''), salt=SAL_PONTE, max_age=PONTE_TOKEN_SEGUNDOS)
    except signing.SignatureExpired:
        return JsonResponse({'ok': False, 'erro': 'A sessão expirou: recarregue a página.'}, status=403)
    except signing.BadSignature:
        return JsonResponse({'ok': False, 'erro': 'Sessão inválida.'}, status=403)
    from django.contrib.auth import get_user_model
    usuario = get_user_model().objects.filter(pk=dados.get('u'), is_active=True).first()
    if usuario is None or not pode_ver(usuario):
        return JsonResponse({'ok': False, 'erro': 'Sem acesso à Automação SAP.'}, status=403)
    pc = (request.GET.get('pc') or '')[:60]
    try:
        from core.models import SystemLog
        SystemLog.objects.create(user=usuario, action_type='ADMIN_ACTION',
                                 description=f'Automação SAP aberta no portal — PC {pc or "?"}, ponte {request.GET.get("ponte", "")[:20]}')
    except Exception:  # noqa: BLE001
        logger.warning('Log da ponte não gravado', exc_info=True)
    return JsonResponse({'ok': True, 'usuario': usuario.get_full_name() or usuario.email})


@login_required
@require_POST
def ponte_enviar(request):
    if (barra := _so_superadmin(request)) is not None:
        return barra
    arquivo = request.FILES.get('arquivo')
    if not arquivo or not arquivo.name.lower().endswith('.exe') or arquivo.size > PONTE_TAMANHO_MAX:
        messages.error(request, 'Envie o PonteAutomacaoSAP.exe gerado pelo construtor (até 60 MB).')
        return redirect('auditoria_sap:automacao')
    cabeca = arquivo.read(2)
    arquivo.seek(0)
    if cabeca != b'MZ':
        messages.error(request, 'Isso não é um executável do Windows.')
        return redirect('auditoria_sap:automacao')
    versao = (request.POST.get('versao') or '').strip().lstrip('vV') or automacao.versao_do_nome(arquivo.name) or '1.0.0'
    import hashlib
    resumo = hashlib.sha256()
    for pedaco in arquivo.chunks():
        resumo.update(pedaco)
    arquivo.seek(0)
    InstaladorPonteSAP.objects.create(versao=versao[:20], arquivo=arquivo, sha256=resumo.hexdigest(), tamanho=arquivo.size,
                                      enviado_por=request.user)
    _log(request, f'Automação SAP: instalador da ponte {versao} enviado')
    messages.success(request, f'Instalador da ponte {versao} publicado: é ele que o portal oferece a quem ainda não tem a ponte.')
    return redirect('auditoria_sap:automacao')


@login_required
@so_auditoria
def ponte_baixar(request):
    oferta = automacao.ponte_oferecida()
    if oferta is None:
        raise Http404
    nome = f'PonteAutomacaoSAP_v{oferta["versao"]}.exe'
    if oferta['origem'] == 'imagem':
        _log(request, f'Automação SAP: ponte {oferta["versao"]} baixada (instalador da imagem)')
        return FileResponse(open(oferta['caminho'], 'rb'), as_attachment=True, filename=nome)
    item = oferta['item']
    InstaladorPonteSAP.objects.filter(pk=item.pk).update(downloads=F('downloads') + 1)
    if automacao.em_disco():
        return FileResponse(item.arquivo.open('rb'), as_attachment=True, filename=nome)
    from cameras.servicos import link_do_instalador
    return redirect(link_do_instalador(item.arquivo.name, nome))


# ── API do app (sem sessão; vale a chave) ──────────────────────────────────────

@require_GET
def api_versao(request):
    """O app pergunta, ao abrir: GET ?atual=1.2.1 com o cabeçalho X-Chave-Automacao."""
    if not automacao.chave_confere(request, ChaveAutomacaoSAP.get().chave):
        return JsonResponse({'erro': 'Chave inválida.'}, status=403)
    atual = VersaoAutomacaoSAP.atual()
    if atual is None:
        return JsonResponse({'versao': None, 'atualizar': False})
    instalada = versao_em_tupla(request.GET.get('atual'))
    return JsonResponse({
        'versao': atual.versao,
        'atualizar': bool(instalada) and instalada < atual.tupla,
        'url': request.build_absolute_uri(reverse('auditoria_sap:automacao_api_baixar', args=[atual.versao])),
        'sha256': atual.sha256, 'tamanho': atual.tamanho, 'notas': atual.notas,
        'publicada_em': atual.enviada_em.isoformat(),
        # O que o atualizador nunca sobrescreve na pasta da loja.
        'preservar': list(automacao.PASTAS_DA_LOJA),
    })


@require_GET
def api_baixar(request, versao):
    if not automacao.chave_confere(request, ChaveAutomacaoSAP.get().chave):
        return JsonResponse({'erro': 'Chave inválida.'}, status=403)
    item = VersaoAutomacaoSAP.objects.filter(versao=versao, publicada=True).first()
    if item is None:
        return JsonResponse({'erro': 'Versão não publicada.'}, status=404)
    VersaoAutomacaoSAP.objects.filter(pk=item.pk).update(atualizacoes=F('atualizacoes') + 1)
    return _entregar(item)
