"""Arquivo protegido aberto pelo app (PDF de contracheque, documento…).

O navegador do celular não leva o token do app, então a API devolve um link
assinado que vale poucos minutos e é só daquela pessoa. Ao abrir, o resolvedor
do módulo confere a permissão DE NOVO com o dono do link — o link não é
passe-livre, só evita mandar o token pela URL.

No módulo:

    from api_mobile.arquivos import registrar, link

    @registrar('contracheque')
    def _pdf(user, obj_id):
        holerite = ...           # 404/PermissionDenied se a pessoa não pode
        return holerite.arquivo, 'holerite.pdf', 'application/pdf'

    # na resposta da API:  'pdf': link(request, 'contracheque', holerite.pk)
"""
from django.contrib.auth import get_user_model
from django.core import signing
from django.http import FileResponse, Http404
from django.views.decorators.http import require_GET

VALIDADE_SEGUNDOS = 300
_SAL = 'api_mobile.arquivo'
RESOLVEDORES = {}


def registrar(tipo):
    def decorar(funcao):
        RESOLVEDORES[tipo] = funcao
        return funcao
    return decorar


def link(request, tipo, obj_id):
    assinatura = signing.dumps({'u': request.user.pk, 't': tipo, 'i': str(obj_id)}, salt=_SAL, compress=True)
    from .dados import absoluta
    return absoluta(request, f'/api/mobile/v1/arquivo/{assinatura}/')


def _carregar_modulos():
    from .modulos import CHAVES, carregar
    for chave in CHAVES:
        carregar(chave)


@require_GET
def baixar(request, assinatura):
    from .permissoes import bloqueio_do_usuario

    try:
        dados = signing.loads(assinatura, salt=_SAL, max_age=VALIDADE_SEGUNDOS)
    except signing.SignatureExpired:
        raise Http404('Link expirado. Abra o arquivo de novo pelo app.')
    except signing.BadSignature:
        raise Http404('Link inválido.')
    user = get_user_model().objects.filter(pk=dados.get('u'), is_active=True).first()
    if user is None or bloqueio_do_usuario(user):
        raise Http404('Link inválido.')
    _carregar_modulos()
    resolvedor = RESOLVEDORES.get(dados.get('t'))
    if resolvedor is None:
        raise Http404('Link inválido.')
    try:
        arquivo, nome, tipo = resolvedor(user, dados.get('i'))
    except Exception as exc:  # PermissionDenied, DoesNotExist… → 404 sem detalhe
        raise Http404('Arquivo indisponível.') from exc
    if hasattr(arquivo, 'open'):
        arquivo.open('rb')
    resposta = FileResponse(arquivo, filename=nome, as_attachment=False, content_type=tipo or None)
    resposta['Cache-Control'] = 'private, no-store'
    return resposta
