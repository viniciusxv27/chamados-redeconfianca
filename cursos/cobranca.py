"""Cobrança dos cursos no grupo de gestão de cada loja (WhatsApp).

Quem ainda não mandou o comprovante — ou teve o comprovante recusado — sai numa
mensagem só por loja, no grupo de gestão dela (``GrupoWhatsappLoja``). A loja é
o setor principal da pessoa: é o campo que separa as lojas de verdade (o M2M
``sectors`` do escritório tem todas).

Dois jeitos de disparar:

- **pelo quadro** (/cursos/gestao/): o gestor confere a prévia do curso
  escolhido e manda;
- **automático**: nos dias e hora da configuração, todos os cursos em aberto
  numa mensagem por loja. Nasce desligado. Sem cron em produção, quem acorda é
  a primeira requisição depois da hora (o mesmo arranjo de
  ``tangerino/agendador.py``): throttle por processo, corrida decidida por
  UPDATE condicional no banco e o trabalho numa thread.

O canal é a Evolution (``core.evolution``), o mesmo dos lembretes da rotina e
da análise de ponto; em processo de teste ela já se recusa a mandar.
"""
import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone

from .models import (
    CobrancaWhatsapp, Comprovante, ConfiguracaoCursos, Curso, GrupoWhatsappLoja, jid_do_grupo,
)

logger = logging.getLogger(__name__)

# Curso vencido continua sendo cobrado por um tempo — é quando mais importa —,
# mas não para sempre: curso de três meses atrás vira ruído no grupo.
DIAS_DEPOIS_DO_PRAZO = 30
# A mesma loja não recebe duas cobranças em menos que isso (clique duplo, dois
# gestores ao mesmo tempo, o automático em cima de um manual).
INTERVALO_MINIMO = timedelta(minutes=30)

_ultima_checagem = None
_intervalo_checagem = 60          # segundos
_trava_local = threading.Lock()


def faltando_no_canal():
    """O que falta configurar neste processo para o WhatsApp sair (lista vazia: pronto)."""
    return [nome for nome in ('EVOLUTION_API_URL', 'EVOLUTION_API_KEY', 'EVOLUTION_INSTANCE')
            if not (getattr(settings, nome, '') or '')]


def cursos_em_aberto(hoje=None):
    """Cursos publicados que ainda valem cobrança."""
    hoje = hoje or timezone.localdate()
    return list(Curso.objects
                .filter(publicado=True, prazo__gte=hoje - timedelta(days=DIAS_DEPOIS_DO_PRAZO))
                .order_by('prazo', 'id'))


# ---------------------------------------------------------------------------
# Quem falta, loja por loja
# ---------------------------------------------------------------------------
def pendentes_por_loja(cursos, cfg=None):
    """{setor_id ou None: {curso_id: [(pessoa, recusado), ...]}} de quem não comprovou."""
    from .views import _pessoas_do_curso

    cfg = cfg or ConfiguracaoCursos.get()
    lojas = {}
    for curso in cursos:
        pessoas = list(_pessoas_do_curso(curso, cfg).select_related('sector'))
        if not pessoas:
            continue
        ultimo = {}
        for c in (Comprovante.objects
                  .filter(curso=curso, colaborador__in=pessoas)
                  .order_by('colaborador_id', '-enviado_em')
                  .only('colaborador_id', 'status')):
            ultimo.setdefault(c.colaborador_id, c)
        for p in pessoas:
            envio = ultimo.get(p.id)
            if envio and envio.vale_como_entregue:
                continue
            recusado = bool(envio and envio.status == Comprovante.RECUSADO)
            lojas.setdefault(p.sector_id, {}).setdefault(curso.id, []).append((p, recusado))
    return lojas


def _prazo(curso, hoje):
    dias = (curso.prazo - hoje).days
    data = curso.prazo.strftime('%d/%m')
    if dias > 1:
        return f'{data} (faltam {dias} dias)'
    if dias == 1:
        return f'{data} (amanhã)'
    if dias == 0:
        return f'{data} (vence hoje)'
    return f'{data} (venceu há {-dias} dia{"s" if dias < -1 else ""})'


PARTICULAS = {'da', 'das', 'de', 'do', 'dos', 'e'}


def _nome(pessoa):
    """O cadastro tem nome em CAIXA ALTA; no grupo fica mais legível assim."""
    palavras = (pessoa.get_full_name() or pessoa.username).split()
    return ' '.join(p.lower() if i and p.lower() in PARTICULAS else p.capitalize()
                    for i, p in enumerate(palavras))


def montar_texto(setor, por_curso, cursos, hoje=None):
    """A mensagem de uma loja. ``por_curso`` é o pedaço dela em ``pendentes_por_loja``."""
    hoje = hoje or timezone.localdate()
    base = (getattr(settings, 'BASE_URL', '') or '').rstrip('/')
    linhas = [f'📚 *Cursos obrigatórios — {setor.name}*', '',
              'Ainda falta enviar o comprovante no portal:']
    for curso in cursos:
        itens = por_curso.get(curso.id)
        if not itens:
            continue
        linhas += ['', f'*{curso.titulo}*', f'Prazo: {_prazo(curso, hoje)}']
        for pessoa, recusado in sorted(itens, key=lambda i: _nome(i[0])):
            extra = ' _(comprovante recusado — enviar de novo)_' if recusado else ''
            linhas.append(f'• {_nome(pessoa)}{extra}')
    linhas += ['', f'👉 Envie em: {base}/cursos/']
    return '\n'.join(linhas)


def previa(cursos, cfg=None, hoje=None):
    """O que sairia agora: uma entrada por loja com pendência, com ou sem grupo."""
    from users.models import Sector

    hoje = hoje or timezone.localdate()
    pendentes = pendentes_por_loja(cursos, cfg)
    grupos = {g.setor_id: g for g in GrupoWhatsappLoja.objects.select_related('setor')}
    setores = Sector.objects.in_bulk([s for s in pendentes if s])
    lojas, sem_loja = [], 0
    for setor_id, por_curso in pendentes.items():
        pessoas = {p.id for itens in por_curso.values() for p, _ in itens}
        if setor_id is None:
            sem_loja += len(pessoas)
            continue
        setor = setores[setor_id]
        grupo = grupos.get(setor_id)
        lojas.append({
            'setor': setor,
            'grupo': grupo if grupo and grupo.ativo and grupo.grupo.strip() else None,
            'pessoas': len(pessoas),
            'texto': montar_texto(setor, por_curso, cursos, hoje),
        })
    lojas.sort(key=lambda l: (l['grupo'] is None, l['setor'].name))
    return {'lojas': lojas, 'sem_loja': sem_loja,
            'com_grupo': sum(1 for l in lojas if l['grupo']),
            'sem_grupo': [l for l in lojas if not l['grupo']]}


# ---------------------------------------------------------------------------
# Envio
# ---------------------------------------------------------------------------
def reivindicar(loja, cursos, origem, user=None):
    """Grava a cobrança ANTES do envio. None se a loja foi cobrada há pouco."""
    grupo = loja['grupo']
    with transaction.atomic():
        # Trava a linha do grupo: dois cliques (ou dois workers) fazem fila aqui,
        # e o segundo já encontra a cobrança do primeiro.
        GrupoWhatsappLoja.objects.select_for_update().filter(pk=grupo.pk).first()
        recente = (CobrancaWhatsapp.objects
                   .filter(setor=loja['setor'], criado_em__gte=timezone.now() - INTERVALO_MINIMO)
                   .exclude(enviado=False))
        if recente.exists():
            return None
        cobranca = CobrancaWhatsapp.objects.create(
            setor=loja['setor'], grupo=grupo.grupo, pessoas=loja['pessoas'],
            texto=loja['texto'], origem=origem, disparado_por=user)
        cobranca.cursos.set(cursos)
    return cobranca


def mandar(cobrancas):
    """Manda as cobranças já gravadas. Roda numa thread: são até 20 grupos."""
    # Pelo módulo: o dublê dos testes troca `core.evolution.enviar_texto`.
    from core import evolution

    resumo = {'enviados': 0, 'falhas': 0}
    for cobranca in cobrancas:
        try:
            ok, detalhe = evolution.enviar_texto(jid_do_grupo(cobranca.grupo), cobranca.texto, timeout=20)
        except Exception as exc:                                    # noqa: BLE001 — o contrato é não levantar
            ok, detalhe = False, f'Erro inesperado: {exc}'
        CobrancaWhatsapp.objects.filter(pk=cobranca.pk).update(
            enviado=bool(ok), detalhe=str(detalhe or '')[:255])
        resumo['enviados' if ok else 'falhas'] += 1
        if not ok:
            logger.warning('Cobrança de curso não saiu para %s: %s', cobranca.setor, str(detalhe)[:200])
    return resumo


def cobrar(cursos, origem, user=None, setores=None, cfg=None, em_segundo_plano=True):
    """Grava e manda a cobrança de cada loja com grupo. Devolve o que foi gravado e o que ficou de fora."""
    resultado = {'gravadas': [], 'recentes': [], 'sem_grupo': [], 'sem_canal': False}
    if faltando_no_canal():
        resultado['sem_canal'] = True
        return resultado

    dados = previa(cursos, cfg)
    for loja in dados['lojas']:
        if setores is not None and loja['setor'].id not in setores:
            continue
        if not loja['grupo']:
            resultado['sem_grupo'].append(loja['setor'])
            continue
        cobranca = reivindicar(loja, cursos, origem, user)
        if cobranca is None:
            resultado['recentes'].append(loja['setor'])
        else:
            resultado['gravadas'].append(cobranca)

    if resultado['gravadas']:
        if em_segundo_plano:
            gravadas = list(resultado['gravadas'])

            def _rodar():
                try:
                    logger.info('Cobrança de cursos no WhatsApp: %s', mandar(gravadas))
                except Exception:                                   # noqa: BLE001
                    logger.exception('Cobrança de cursos no WhatsApp quebrou')
                finally:
                    close_old_connections()

            transaction.on_commit(lambda: threading.Thread(
                target=_rodar, name='cursos-cobranca', daemon=True).start())
        else:
            resultado['resumo'] = mandar(resultado['gravadas'])
    return resultado


# ---------------------------------------------------------------------------
# Automático
# ---------------------------------------------------------------------------
def _hoje_na_hora(cfg, agora):
    return timezone.make_aware(
        timezone.datetime.combine(timezone.localdate(agora), cfg.cobranca_hora),
        timezone.get_current_timezone())


def esta_na_hora(cfg, agora=None):
    """Dia marcado, já passou da hora e ainda não rodou hoje?"""
    if not cfg.cobranca_automatica:
        return False
    agora = agora or timezone.now()
    if timezone.localdate(agora).weekday() not in cfg.dias_da_cobranca:
        return False
    alvo = _hoje_na_hora(cfg, agora)
    if agora < alvo:
        return False
    anterior = cfg.ultima_cobranca_automatica
    return anterior is None or anterior < alvo


def disparar_se_esta_na_hora():
    """Chamado pelo middleware. True se ESTA chamada disparou a cobrança automática."""
    global _ultima_checagem

    # Teste não reivindica o dia: ele não manda nada e gastaria a vez do portal.
    from core.utils import processo_de_teste
    if processo_de_teste():
        return False

    agora = timezone.now()
    with _trava_local:
        if _ultima_checagem is not None and (agora - _ultima_checagem).total_seconds() < _intervalo_checagem:
            return False
        _ultima_checagem = agora

    try:
        from django.db.models import Q

        cfg = ConfiguracaoCursos.get()
        if not esta_na_hora(cfg, agora):
            return False
        # Processo sem o canal não reivindica: gastaria a vez de quem consegue mandar.
        if faltando_no_canal():
            return False

        alvo = _hoje_na_hora(cfg, agora)
        ganhou = (ConfiguracaoCursos.objects
                  .filter(pk=cfg.pk)
                  .filter(Q(ultima_cobranca_automatica__lt=alvo) | Q(ultima_cobranca_automatica__isnull=True))
                  .update(ultima_cobranca_automatica=agora))
        if not ganhou:
            return False

        def _rodar():
            try:
                cursos = cursos_em_aberto()
                if cursos:
                    resultado = cobrar(cursos, CobrancaWhatsapp.AUTOMATICA, em_segundo_plano=False)
                    logger.info('Cobrança automática de cursos: %s gravadas, %s recentes, %s sem grupo, %s',
                                len(resultado['gravadas']), len(resultado['recentes']),
                                len(resultado['sem_grupo']), resultado.get('resumo'))
            except Exception:                                       # noqa: BLE001
                logger.exception('Cobrança automática de cursos quebrou')
            finally:
                close_old_connections()

        threading.Thread(target=_rodar, name='cursos-cobranca-automatica', daemon=True).start()
        return True
    except Exception as exc:                                        # jamais quebra a página
        logger.warning('Agendador da cobrança de cursos ignorado por erro: %s', exc)
        return False
