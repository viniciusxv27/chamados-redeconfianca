"""Popup "Você tem algum login com erro?" — campanhas do Acess exibidas no portal.

O gate de popups (portal_popups.context_processors) chama ``campanha_pendente`` só na home e só quando
nenhum popup de /popups/ está na vez — os de /popups/ vêm primeiro, um por vez. Custo por carregamento:
a lista de campanhas no ar fica 60 s no cache local do processo (vale para todo mundo) e a situação da
pessoa fica no cache compartilhado até ela responder; no pior caso, uma consulta.

Quem responde "sim" escolhe o sistema (os mesmos da ficha em /acess/colaborador/<id>/). Pool de cobrança
pede telefone e e-mail; os outros, a descrição. A resposta com erro vira um pedido em Solicitações
(motivo "Falha de acesso"), como os da janela de Suporte — só aviso dentro do portal, nada sai para fora.
"""
from datetime import timedelta

from django.core.cache import cache, caches
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from . import servicos as sv
from .models import SISTEMAS, CampanhaLoginErro, ItemLoginErro, RespostaLoginErro

CHAVE_CAMPANHAS = 'acess:popup_login:campanhas'
SEGUNDOS_CAMPANHAS = 60
SEGUNDOS_SITUACAO = 6 * 3600
MOTIVO_SOLICITACAO = 'FALHA'


def e_pool(sistema):
    return sv.norm(sistema) == 'POOL DE COBRANCA'


def chave_situacao(user_id):
    return f'acess:popup_login:u{user_id}'


def _cache_local():
    try:
        return caches['local']
    except Exception:  # noqa: BLE001 — sem cache "local" configurado
        return cache


def limpar_cache_campanhas():
    _cache_local().delete(CHAVE_CAMPANHAS)


def _campanhas_no_ar():
    """As campanhas ativas e não vencidas, como dicionários simples (cabem no cache)."""
    local = _cache_local()
    lista = local.get(CHAVE_CAMPANHAS)
    if lista is None:
        from django.db.models import Q
        agora = timezone.now()
        lista = [{'id': c.id, 'titulo': c.titulo, 'pergunta': c.pergunta, 'inicio': c.inicio, 'fim': c.fim,
                  'setores': {s.id for s in c.setores.all()}, 'hierarquias': list(c.hierarquias or []),
                  'usuarios': {u.id for u in c.usuarios.all()}}
                 for c in CampanhaLoginErro.objects.filter(ativo=True).filter(Q(fim__isnull=True) | Q(fim__gte=agora))
                 .prefetch_related('setores', 'usuarios').order_by('id')]
        local.set(CHAVE_CAMPANHAS, lista, SEGUNDOS_CAMPANHAS)
    return lista


def no_publico(user, setores, hierarquias, usuarios=()):
    """Setor principal (``user.sector``, como os outros filtros por loja) e hierarquia; vazio = todos.

    Pessoas escolhidas uma a uma entram sempre. Só com pessoas escolhidas (sem setor nem
    hierarquia), o público é só elas — e não "todos".
    """
    if usuarios and getattr(user, 'pk', None) in usuarios:
        return True
    if usuarios and not setores and not hierarquias:
        return False
    if setores and getattr(user, 'sector_id', None) not in setores:
        return False
    if hierarquias and getattr(user, 'hierarchy', '') not in hierarquias:
        return False
    return True


def alvo(campanha, user):
    return no_publico(user, {s.id for s in campanha.setores.all()}, campanha.hierarquias or [],
                      {u.id for u in campanha.usuarios.all()})


def _situacao(user):
    """{campanha_id: (situacao, adiado_ate)} da pessoa — no cache compartilhado até ela responder."""
    chave = chave_situacao(user.pk)
    dados = cache.get(chave)
    if dados is None:
        dados = {cid: (sit, ate) for cid, sit, ate in RespostaLoginErro.objects.filter(usuario=user)
                 .values_list('campanha_id', 'situacao', 'adiado_ate')}
        cache.set(chave, dados, SEGUNDOS_SITUACAO)
    return dados


def campanha_pendente(user):
    """A primeira campanha no ar, para o público da pessoa, ainda sem resposta (ou com o adiamento vencido)."""
    if not (user and getattr(user, 'is_authenticated', False)):
        return None
    agora = timezone.now()
    candidatas = [c for c in _campanhas_no_ar()
                  if not (c['inicio'] and agora < c['inicio']) and not (c['fim'] and agora > c['fim'])
                  and no_publico(user, c['setores'], c['hierarquias'], c.get('usuarios') or ())]
    if not candidatas:
        return None
    situacao = _situacao(user)
    hoje = timezone.localdate()
    for c in candidatas:
        sit, ate = situacao.get(c['id'], (None, None))
        if sit in (RespostaLoginErro.SEM_ERRO, RespostaLoginErro.COM_ERRO):
            continue
        if sit == RespostaLoginErro.ADIADO and ate and ate > hoje:
            continue
        return dict(c, sistemas=[{'nome': s, 'pool': e_pool(s)} for s in SISTEMAS])
    return None


# ── resposta ────────────────────────────────────────────────────────────────
def _telefone(valor):
    d = sv.so_digitos(valor)
    if len(d) in (12, 13) and d.startswith('55'):
        d = d[2:]
    if len(d) not in (10, 11) or d[:2].startswith('0'):
        raise sv.RegraAcess('Informe o telefone com DDD (10 ou 11 números).')
    return d


def telefone_formatado(d):
    d = sv.so_digitos(d)
    if len(d) == 11:
        return f'({d[:2]}) {d[2:7]}-{d[7:]}'
    if len(d) == 10:
        return f'({d[:2]}) {d[2:6]}-{d[6:]}'
    return d


def validar_itens(itens):
    """Lista de {sistema, telefone, email, descricao} → itens limpos. Levanta RegraAcess com a mensagem."""
    if not isinstance(itens, list) or not itens:
        raise sv.RegraAcess('Escolha o sistema com erro.')
    if len(itens) > len(SISTEMAS):
        raise sv.RegraAcess('Itens demais.')
    limpos, vistos = [], set()
    for bruto in itens:
        if not isinstance(bruto, dict):
            raise sv.RegraAcess('Envio inválido.')
        sistema = str(bruto.get('sistema') or '').strip()
        if sistema not in SISTEMAS:
            raise sv.RegraAcess('Escolha o sistema com erro.' if not sistema else f'Sistema fora da lista: {sistema}.')
        if sistema in vistos:
            raise sv.RegraAcess(f'{sistema} apareceu duas vezes.')
        vistos.add(sistema)
        item = {'sistema': sistema, 'telefone': '', 'email': '', 'descricao': ''}
        if e_pool(sistema):
            item['telefone'] = _telefone(bruto.get('telefone'))
            email = str(bruto.get('email') or '').strip()
            try:
                validate_email(email)
            except ValidationError:
                raise sv.RegraAcess(f'Informe um e-mail válido para o {sistema}.')
            item['email'] = email[:254]
        else:
            descricao = str(bruto.get('descricao') or '').strip()
            if len(descricao) < 5:
                raise sv.RegraAcess(f'Conte em poucas palavras o erro no {sistema}.')
            if len(descricao) > 1000:
                raise sv.RegraAcess('A descrição vai até 1.000 caracteres.')
            item['descricao'] = descricao
        limpos.append(item)
    return limpos


def _texto_solicitacao(campanha, itens):
    linhas = [f'Resposta ao popup "{campanha.titulo}": login com erro.']
    for i in itens:
        if i['telefone'] or i['email']:
            linhas.append(f'- {i["sistema"]}: telefone {telefone_formatado(i["telefone"])}, e-mail {i["email"]}')
        else:
            linhas.append(f'- {i["sistema"]}: {i["descricao"]}')
    return '\n'.join(linhas)[:2000]


@transaction.atomic
def responder(user, campanha, resposta, itens=None, request=None):
    """resposta: 'nao' | 'sim' | 'depois'. Devolve a RespostaLoginErro gravada."""
    if not campanha.no_ar() or not alvo(campanha, user):
        raise sv.RegraAcess('Este popup não está mais disponível para você.')
    atual = RespostaLoginErro.objects.select_for_update().filter(campanha=campanha, usuario=user).first()
    if atual and atual.respondida:
        raise sv.RegraAcess('Você já respondeu. Obrigado!')
    if resposta not in ('nao', 'sim', 'depois'):
        raise sv.RegraAcess('Escolha uma resposta.')
    limpos = validar_itens(itens) if resposta == 'sim' else []

    r = atual or RespostaLoginErro(campanha=campanha, usuario=user)
    r.setor_id, r.hierarquia = getattr(user, 'sector_id', None), getattr(user, 'hierarchy', '') or ''
    r.respondido_em = timezone.now()
    if resposta == 'depois':
        r.situacao, r.adiado_ate = RespostaLoginErro.ADIADO, timezone.localdate() + timedelta(days=1)
    else:
        r.situacao = RespostaLoginErro.COM_ERRO if limpos else RespostaLoginErro.SEM_ERRO
        r.adiado_ate = None
    r.save()
    if limpos:
        ItemLoginErro.objects.bulk_create([ItemLoginErro(resposta=r, **i) for i in limpos])
        pool = next((i for i in limpos if i['telefone']), None)
        try:
            # Vira pedido em Solicitações (aviso só dentro do portal para quem trata chamados).
            r.solicitacao = sv.criar_solicitacao(
                user, MOTIVO_SOLICITACAO, [i['sistema'] for i in limpos], _texto_solicitacao(campanha, limpos),
                contato=pool['telefone'] if pool else (getattr(user, 'phone', '') or ''), request=request)
            r.save(update_fields=['solicitacao'])
        except sv.RegraAcess:
            pass  # limite de pedidos abertos: a resposta fica registrada no popup mesmo assim
    if resposta != 'depois':
        sv.registrar(request, 'POPUP_LOGIN_RESPOSTA', 'CHAMADO',
                     f'{user.get_full_name() or user.email} respondeu ao popup "{campanha.titulo}": '
                     + (('erro em ' + ', '.join(i['sistema'] for i in limpos)) if limpos else 'sem erro'),
                     colaborador=sv.ficha_do_usuario(user), usuario=user)
    transaction.on_commit(lambda: cache.delete(chave_situacao(user.pk)))
    return r


# ── gestão ──────────────────────────────────────────────────────────────────
def publico_qs(campanha):
    from django.contrib.auth import get_user_model
    from django.db.models import Q
    ativos = get_user_model().objects.filter(is_active=True)
    setores = list(campanha.setores.values_list('id', flat=True))
    escolhidos = list(campanha.usuarios.values_list('id', flat=True)) if campanha.pk else []
    if escolhidos and not setores and not campanha.hierarquias:
        return ativos.filter(pk__in=escolhidos)
    filtro = Q()
    if setores:
        filtro &= Q(sector_id__in=setores)
    if campanha.hierarquias:
        filtro &= Q(hierarchy__in=campanha.hierarquias)
    if escolhidos:
        filtro |= Q(pk__in=escolhidos)
    return ativos.filter(filtro)
