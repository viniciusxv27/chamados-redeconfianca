"""API JSON da Rotina Gerencial.

Toda resposta segue o mesmo formato: `{"ok": true, ...}` ou
`{"ok": false, "erro": "mensagem para a pessoa"}`, com o status HTTP certo
(400 dado inválido, 401 sem sessão, 403 sem permissão, 404 não encontrado,
405 método errado). O calendário só desenha: quem pode mexer em quê é decidido
aqui e em `servicos`.

As rotas de escrita são POST com corpo JSON e passam pelo CSRF normal do
Django (cabeçalho X-CSRFToken).
"""
import json
import logging
from functools import wraps

from django.db import transaction
from django.http import HttpResponse, JsonResponse

from . import servicos
from .models import AtividadeModelo, AtividadeRotina, ModeloRotina, RotinaGerencial, TipoAviso
from .permissoes import e_superadmin
from .servicos import ErroValidacao, NaoEncontrado, SemPermissao

logger = logging.getLogger(__name__)


def resposta_erro(mensagem, status):
    return JsonResponse({'ok': False, 'erro': mensagem}, status=status)


def api(*metodos):
    """Sessão, método e tradução das exceções de `servicos` para o formato da API."""
    metodos = metodos or ('GET',)

    def decorador(view):
        @wraps(view)
        def envolta(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return resposta_erro('Sua sessão expirou. Entre de novo no portal.', 401)
            if request.method not in metodos:
                resposta = resposta_erro('Método não permitido.', 405)
                resposta['Allow'] = ', '.join(metodos)
                return resposta
            try:
                resposta = view(request, *args, **kwargs)
            except ErroValidacao as exc:
                return resposta_erro(str(exc), 400)
            except SemPermissao as exc:
                return resposta_erro(str(exc), 403)
            except NaoEncontrado as exc:
                return resposta_erro(str(exc), 404)
            except Exception:                                   # noqa: BLE001
                logger.exception('Erro inesperado na API da rotina gerencial (%s)', request.path)
                return resposta_erro('Não foi possível concluir agora. Tente de novo em instantes.', 500)
            resposta['Cache-Control'] = 'no-store'
            return resposta
        return envolta
    return decorador


def ler_json(request):
    try:
        corpo = json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        raise ErroValidacao('JSON inválido.') from None
    if not isinstance(corpo, dict):
        raise ErroValidacao('JSON inválido.')
    return corpo


def exigir_superadmin(request):
    if not e_superadmin(request.user):
        raise SemPermissao('Só o SUPERADMIN pode fazer isso.')


def _id(valor):
    """Id vindo da URL ou do JSON; None se não for um número positivo."""
    if isinstance(valor, bool):
        return None
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return None
    return numero if numero > 0 else None


def rotina_do_alvo(request, valor):
    """A rotina que o pedido quer ver ou mudar.

    Sem `usuario` (ou com o próprio id) é a rotina de quem pede; a de outra
    pessoa, só o SUPERADMIN alcança.
    """
    alvo_id = request.user.id if valor in (None, '') else _id(valor)
    if alvo_id is None:
        raise ErroValidacao('Pessoa inválida.')
    if alvo_id != request.user.id and not e_superadmin(request.user):
        raise SemPermissao('Só o SUPERADMIN mexe na rotina de outra pessoa.')
    rotina = RotinaGerencial.objects.select_related('user').filter(user_id=alvo_id).first()
    if rotina is None:
        if alvo_id == request.user.id:
            raise NaoEncontrado('Você ainda não tem rotina gerencial.')
        raise NaoEncontrado('Esta pessoa ainda não tem rotina gerencial.')
    return rotina


def atividade_da_rotina(request, atividade_id):
    """Atividade que quem pede alcança: da própria rotina, ou qualquer uma para o SUPERADMIN.

    A de outra pessoa responde 404, e não 403, para não confirmar que existe.
    """
    qs = AtividadeRotina.objects.select_related('rotina', 'rotina__user')
    if not e_superadmin(request.user):
        qs = qs.filter(rotina__user=request.user)
    atividade = qs.filter(pk=atividade_id).first()
    if atividade is None:
        raise NaoEncontrado('Atividade não encontrada.')
    return atividade


# ---------------------------------------------------------------------------
# Rotina de uma pessoa
# ---------------------------------------------------------------------------
@api('GET')
def rotina_semana(request):
    rotina = rotina_do_alvo(request, request.GET.get('usuario'))
    if not e_superadmin(request.user):
        servicos.conferir_rotina_ativa(rotina)
    return JsonResponse(servicos.payload_rotina(rotina, request.user))


@api('POST')
def rotina_concluir(request, atividade_id):
    """Conclui a atividade daquele dia. Com comprovante, quando a atividade exige.

    Vem como multipart (o arquivo), não JSON: `data` (AAAA-MM-DD), `observacao`
    e `comprovante`. Só a dona da rotina conclui — o SUPERADMIN acompanha e
    pode desfazer, mas não conclui no lugar dela.
    """
    atividade = atividade_da_rotina(request, atividade_id)
    if atividade.rotina.user_id != request.user.id:
        raise SemPermissao('Só a pessoa da rotina conclui a atividade dela.')
    servicos.conferir_rotina_ativa(atividade.rotina)
    conclusao = servicos.concluir_atividade(
        atividade, request.user,
        request.POST.get('data'),
        arquivo=request.FILES.get('comprovante'),
        observacao=request.POST.get('observacao', ''))
    return JsonResponse({'ok': True, 'conclusao': servicos.serializar_conclusao(conclusao)})


@api('POST')
def rotina_desfazer(request, atividade_id):
    """Tira a conclusão daquele dia — a pessoa, para refazer; o SUPERADMIN, para corrigir."""
    atividade = atividade_da_rotina(request, atividade_id)
    dona = atividade.rotina.user_id == request.user.id
    if not dona and not e_superadmin(request.user):
        raise SemPermissao('Só a pessoa da rotina ou o SUPERADMIN desfaz.')
    if dona:
        servicos.conferir_rotina_ativa(atividade.rotina)
    servicos.desfazer_conclusao(atividade, request.POST.get('data'))
    return JsonResponse({'ok': True, 'conclusao': None})


@api('POST')
def rotina_criar(request):
    corpo = ler_json(request)
    rotina = rotina_do_alvo(request, corpo.get('usuario'))
    admin = e_superadmin(request.user)
    if not admin:
        servicos.conferir_rotina_ativa(rotina)
        if not rotina.pode_criar:
            raise SemPermissao('Sua rotina não permite criar atividades. Fale com a gestão.')
        if corpo.get('bloqueada'):
            raise SemPermissao('Só a gestão pode travar uma atividade.')
    limpos = servicos.ler_dados_atividade(corpo, com_domingo=rotina.com_domingo)
    criadas = servicos.criar_atividades_rotina(rotina, limpos, request.user, criada_pela_pessoa=not admin)
    return JsonResponse({
        'ok': True,
        'atividades': [servicos.serializar_atividade(a, servicos.permissoes_da_atividade(request.user, a, rotina))
                       for a in criadas],
    }, status=201)


@api('POST')
def rotina_atualizar(request, atividade_id):
    atividade = atividade_da_rotina(request, atividade_id)
    admin = e_superadmin(request.user)
    if not admin:
        # Antes de validar: quem tenta mexer no que está travado ouve "travada".
        servicos.conferir_edicao_da_pessoa(atividade)
    limpos = servicos.ler_dados_atividade(ler_json(request), atual=atividade,
                                          com_domingo=atividade.rotina.com_domingo)
    if not admin:
        servicos.conferir_edicao_da_pessoa(atividade, limpos)
    if limpos:
        for campo, valor in limpos.items():
            setattr(atividade, campo, valor)
        with transaction.atomic():
            atividade.save()
            servicos.marcar_atualizacao(atividade.rotina, request.user)
    return JsonResponse({
        'ok': True,
        'atividade': servicos.serializar_atividade(
            atividade, servicos.permissoes_da_atividade(request.user, atividade)),
    })


@api('POST')
def rotina_excluir(request, atividade_id):
    atividade = atividade_da_rotina(request, atividade_id)
    rotina = atividade.rotina
    if not e_superadmin(request.user):
        servicos.conferir_edicao_da_pessoa(atividade)
        if not (atividade.criada_pela_pessoa and rotina.pode_criar):
            raise SemPermissao('Só dá para excluir atividades criadas por você.')
    with transaction.atomic():
        atividade.delete()
        servicos.marcar_atualizacao(rotina, request.user)
    return JsonResponse({'ok': True})


OPCOES_DA_ROTINA = ('ativa', 'pode_criar', 'avisar_whatsapp', 'com_domingo')


@api('POST')
def gestao_opcoes(request, user_id):
    """Os interruptores da rotina de uma pessoa, salvos na hora (lista de pessoas e editor)."""
    exigir_superadmin(request)
    rotina = RotinaGerencial.objects.select_related('user').filter(user_id=user_id).first()
    if rotina is None:
        raise NaoEncontrado('Esta pessoa não tem rotina gerencial.')
    corpo = ler_json(request)
    campos = []
    for campo in OPCOES_DA_ROTINA:
        if campo in corpo:
            if not isinstance(corpo[campo], bool):
                raise ErroValidacao('Valor inválido.')
            campos.append(campo)
    if not campos:
        raise ErroValidacao('Nada para mudar.')
    if 'com_domingo' in campos and not corpo['com_domingo'] and rotina.com_domingo:
        servicos.conferir_pode_tirar_domingo(rotina)
    for campo in campos:
        setattr(rotina, campo, corpo[campo])
    rotina.atualizado_por = request.user
    rotina.save(update_fields=campos + ['atualizado_por', 'atualizado_em'])
    return JsonResponse({'ok': True, 'rotina': servicos.dados_da_rotina(rotina)})


# ---------------------------------------------------------------------------
# Modelos (só SUPERADMIN)
# ---------------------------------------------------------------------------
def _modelo(modelo_id):
    modelo = ModeloRotina.objects.filter(pk=modelo_id).first()
    if modelo is None:
        raise NaoEncontrado('Modelo não encontrado.')
    return modelo


def _atividade_modelo(atividade_id):
    atividade = AtividadeModelo.objects.select_related('modelo').filter(pk=atividade_id).first()
    if atividade is None:
        raise NaoEncontrado('Atividade não encontrada.')
    return atividade


@api('GET', 'POST')
def modelo_atividades(request, modelo_id):
    exigir_superadmin(request)
    modelo = _modelo(modelo_id)
    if request.method == 'GET':
        return JsonResponse(servicos.payload_modelo(modelo))
    limpos = servicos.ler_dados_atividade(ler_json(request), com_domingo=modelo.com_domingo)
    criadas = servicos.criar_atividades_modelo(modelo, limpos)
    return JsonResponse({'ok': True, 'atividades': [servicos.serializar_atividade(a) for a in criadas]},
                        status=201)


@api('POST')
def modelo_atualizar(request, atividade_id):
    exigir_superadmin(request)
    atividade = _atividade_modelo(atividade_id)
    limpos = servicos.ler_dados_atividade(ler_json(request), atual=atividade,
                                          com_domingo=atividade.modelo.com_domingo)
    if limpos:
        for campo, valor in limpos.items():
            setattr(atividade, campo, valor)
        with transaction.atomic():
            atividade.save()
            atividade.modelo.save(update_fields=['atualizado_em'])
    return JsonResponse({'ok': True, 'atividade': servicos.serializar_atividade(atividade)})


@api('POST')
def modelo_excluir(request, atividade_id):
    exigir_superadmin(request)
    atividade = _atividade_modelo(atividade_id)
    modelo = atividade.modelo
    with transaction.atomic():
        atividade.delete()
        modelo.save(update_fields=['atualizado_em'])
    return JsonResponse({'ok': True})


# ---------------------------------------------------------------------------
# Notificador (lembrete e início) e cartão da home
# ---------------------------------------------------------------------------
@api('GET')
def hoje(request):
    """Atividades de hoje de quem está logado, com o relógio do servidor.

    O notificador chama isto a cada 10 minutos: de carona, confere se a
    varredura do WhatsApp deste worker está viva (e religa se tiver morrido).
    """
    try:
        from . import whatsapp
        whatsapp.garantir_varredura()
    except Exception:                                               # noqa: BLE001
        logger.exception('Não foi possível conferir a varredura de WhatsApp da rotina')
    return JsonResponse(servicos.atividades_de_hoje(request.user))


def _registrar_aviso(request, atividade_id, tipo):
    atividade = (AtividadeRotina.objects.select_related('rotina')
                 .filter(pk=atividade_id, rotina__user=request.user).first())
    if atividade is None:
        raise NaoEncontrado('Atividade não encontrada.')
    servicos.conferir_rotina_ativa(atividade.rotina)
    _, novo = servicos.registrar_aviso(request.user, atividade, tipo=tipo)
    return JsonResponse({'ok': True, 'novo': novo, 'url': servicos.url_da_atividade(atividade)})


@api('POST')
def aviso(request, atividade_id):
    """Registra que o aviso de início foi dado; o sino recebe na primeira vez (`novo`)."""
    return _registrar_aviso(request, atividade_id, TipoAviso.INICIO)


@api('POST')
def lembrete(request, atividade_id):
    """Registra o lembrete de minutos antes; o sino recebe na primeira vez (`novo`)."""
    return _registrar_aviso(request, atividade_id, TipoAviso.LEMBRETE)


@api('GET')
def cartao_home(request):
    """O cartão da home já renderizado (HTML), para ele se atualizar quando o dia muda de atividade.

    Responde 204 quando o cartão não vale mais para a pessoa (rotina pausada,
    removida ou sem atividades): a home tira o cartão.
    """
    from .context_processors import rotina_liberada
    from .home import html_do_cartao

    if not rotina_liberada(request.user):
        return HttpResponse(status=204)
    return HttpResponse(html_do_cartao(request.user), content_type='text/html; charset=utf-8')
