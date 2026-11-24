"""Regras do módulo de bugs: quem pode o quê, fila de análise, aprovação e publicação.

A análise e a publicação rodam em thread (como as apresentações): a tela acompanha por
polling em /bugs/<id>/estado/. Até 2 análises ao mesmo tempo; o resto espera na fila e é
puxado quando uma termina (ou quando alguém abre /bugs/). Análise sem sinal de vida há
mais de 20 minutos (servidor reiniciou no meio) vira ERRO e pode ser refeita.
"""
import logging
import threading
from datetime import timedelta

from django.db import close_old_connections, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .models import RelatoBug

logger = logging.getLogger(__name__)

S = RelatoBug.Status
MAX_SIMULTANEAS = 2
SEM_SINAL = timedelta(minutes=20)
LIMITE_DIA = 10            # relatos por pessoa por dia (o SUPERADMIN não tem limite)


def e_superadmin(user):
    return bool(user and user.is_authenticated and user.is_active
                and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def visiveis(user):
    qs = RelatoBug.objects.select_related('autor', 'decidido_por')
    return qs if e_superadmin(user) else qs.filter(autor=user)


def pode_relatar_hoje(user):
    if e_superadmin(user):
        return True
    inicio = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return RelatoBug.objects.filter(autor=user, criado_em__gte=inicio).count() < LIMITE_DIA


# ------------------------------------------------------------------ avisos
def _avisar(usuarios, titulo, mensagem, relato):
    from core.models import Notification
    url = reverse('bugs:detalhe', args=[relato.pk])
    for u in usuarios:
        try:
            Notification.objects.create(user=u, title=titulo[:200], message=mensagem, notification_type='SYSTEM',
                                        related_object_id=relato.pk, related_url=url)
        except Exception:  # noqa: BLE001 — aviso que falha não pode travar o fluxo do bug
            logger.warning('Bugs: aviso para %s falhou', u.pk, exc_info=True)


def _superadmins():
    from django.contrib.auth import get_user_model
    return get_user_model().objects.filter(is_active=True).filter(Q(is_superuser=True) | Q(hierarchy='SUPERADMIN'))


# ------------------------------------------------------------------ fila de análise
def conferir_parados():
    limite = timezone.now() - SEM_SINAL
    for relato in RelatoBug.objects.filter(status__in=[S.ANALISANDO, S.PUBLICANDO], atualizado_em__lt=limite):
        relato.status = S.ERRO if relato.status == S.ANALISANDO else S.FALHA_PUBLICACAO
        relato.erro = 'Parou no meio, sem sinal de vida (o servidor provavelmente reiniciou). Peça de novo.'
        relato.save(update_fields=['status', 'erro', 'atualizado_em'])


def puxar_fila():
    """Começa as análises que cabem agora. Chamado ao relatar, ao terminar uma análise e ao abrir /bugs/."""
    conferir_parados()
    vagas = MAX_SIMULTANEAS - RelatoBug.objects.filter(status=S.ANALISANDO).count()
    for pk in RelatoBug.objects.filter(status=S.NA_FILA).order_by('criado_em').values_list('pk', flat=True)[:max(0, vagas)]:
        # Pega o relato só se ainda estiver na fila (outro processo/servidor pode ter pegado).
        if RelatoBug.objects.filter(pk=pk, status=S.NA_FILA).update(
                status=S.ANALISANDO, analise_iniciada_em=timezone.now(), atualizado_em=timezone.now(), erro=''):
            transaction.on_commit(lambda pk=pk: _thread(_analisar_em_thread, pk, f'bugs-analise-{pk}'))


def _thread(alvo, pk, nome):
    threading.Thread(target=alvo, args=(pk,), daemon=True, name=nome).start()


def _analisar_em_thread(pk):
    close_old_connections()
    try:
        executar_analise(pk)
    finally:
        close_old_connections()
        try:
            puxar_fila()
        except Exception:  # noqa: BLE001
            logger.exception('Bugs: falha ao puxar a fila')
        close_old_connections()


def executar_analise(pk):
    from assistente.claude_client import NaoConectado

    from . import analise

    relato = RelatoBug.objects.select_related('autor').get(pk=pk)
    relato.analises += 1
    relato.passos = []
    relato.tokens_entrada = relato.tokens_saida = 0
    relato.save(update_fields=['analises', 'passos', 'tokens_entrada', 'tokens_saida', 'atualizado_em'])
    relato.passo('Claude começou a análise')
    try:
        sessao = analise.analisar(relato)
    except NaoConectado:
        return _falhar(relato, 'Sem chave do Claude: o SUPERADMIN precisa configurar a chave da empresa em '
                               'Configurações (/users/settings/), na parte do Assistente Claude.')
    except RuntimeError as exc:
        return _falhar(relato, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception('Bugs: análise do relato %s quebrou', pk)
        return _falhar(relato, f'Erro inesperado na análise: {type(exc).__name__}: {exc}')

    fim = sessao.fim
    relato.titulo = fim['titulo'][:200]
    relato.diagnostico = fim['diagnostico']
    relato.explicacao_usuario = fim['explicacao_usuario']
    relato.analise_concluida_em = timezone.now()
    relato.teste_script = sessao.teste.get('script', '')
    relato.teste_saida = sessao.teste.get('saida', '')
    if fim['tipo'] == 'correcao':
        relato.solucao = fim['solucao']
        relato.alteracoes = sessao.rascunho.alteracoes()
        relato.diff = sessao.rascunho.diff()
        relato.testes_ok = True
        relato.status = S.AGUARDANDO
        relato.passo('Correção pronta e testada: aguardando aprovação do SUPERADMIN', salvar=False)
        relato.save()
        _avisar(_superadmins(), f'Bug #{relato.pk}: correção para aprovar',
                f'{relato.titulo}. O Claude corrigiu e testou — revise e aprove para publicar.', relato)
    else:
        relato.solucao = ''
        relato.alteracoes = []
        relato.diff = ''
        relato.testes_ok = None
        relato.status = S.SEM_CORRECAO
        relato.passo('Concluído sem mudança no código', salvar=False)
        relato.save()
        _avisar([relato.autor], f'Bug #{relato.pk}: análise concluída', relato.explicacao_usuario[:500], relato)
    return relato


def _falhar(relato, mensagem):
    relato.status = S.ERRO
    relato.erro = mensagem
    relato.analise_concluida_em = timezone.now()
    relato.passo('A análise parou: ' + mensagem[:200], salvar=False)
    relato.save()
    return relato


# ------------------------------------------------------------------ ações
def relatar(user, url, descricao, print_tela=None):
    relato = RelatoBug.objects.create(autor=user, url=url[:500], descricao=descricao, print_tela=print_tela)
    puxar_fila()
    return relato


def pedir_nova_analise(relato, user, orientacao):
    """O SUPERADMIN manda o Claude de novo, com uma orientação (ajuste, outro caminho, mais contexto)."""
    if relato.status in (S.NA_FILA, S.ANALISANDO, S.PUBLICANDO, S.PUBLICADO):
        return False
    relato.orientacao = (orientacao or '').strip()
    relato.status = S.NA_FILA
    relato.erro = ''
    relato.motivo_recusa = ''
    relato.decidido_por = None
    relato.decidido_em = None
    relato.save()
    puxar_fila()
    return True


def recusar(relato, user, motivo):
    if relato.status != S.AGUARDANDO:
        return False
    relato.status = S.REJEITADO
    relato.motivo_recusa = (motivo or '').strip()
    relato.decidido_por = user
    relato.decidido_em = timezone.now()
    relato.save()
    _avisar([relato.autor], f'Bug #{relato.pk}: correção não aprovada',
            'A correção proposta não foi aprovada' + (f': {relato.motivo_recusa[:300]}' if relato.motivo_recusa else '.'),
            relato)
    return True


def aprovar(relato, user):
    """Aprova e publica (commit + push) em thread. Também serve para tentar de novo após falha."""
    atualizados = RelatoBug.objects.filter(pk=relato.pk, status__in=[S.AGUARDANDO, S.FALHA_PUBLICACAO]).update(
        status=S.PUBLICANDO, decidido_por=user, decidido_em=timezone.now(), erro='', atualizado_em=timezone.now())
    if not atualizados:
        return False
    transaction.on_commit(lambda: _thread(_publicar_em_thread, relato.pk, f'bugs-publica-{relato.pk}'))
    return True


def _publicar_em_thread(pk):
    close_old_connections()
    try:
        publicar(pk)
    finally:
        close_old_connections()


def mensagem_do_commit(relato):
    linhas = [f'fix: {relato.titulo or "correção do bug #%d" % relato.pk} (bug #{relato.pk})', '']
    if relato.diagnostico:
        linhas += ['Causa:', relato.diagnostico.strip(), '']
    if relato.solucao:
        linhas += ['Correção:', relato.solucao.strip(), '']
    linhas.append(f'Relatado em {relato.url} e corrigido pelo Claude no portal (/bugs/{relato.pk}/); '
                  f'testado em cópia isolada e aprovado por {relato.decidido_por.get_full_name() if relato.decidido_por else "SUPERADMIN"}.')
    linhas += ['', 'Co-Authored-By: Claude <noreply@anthropic.com>']
    return '\n'.join(linhas)


def publicar(pk):
    from . import github

    relato = RelatoBug.objects.select_related('autor', 'decidido_por').get(pk=pk)
    relato.passo('Publicando no GitHub (commit + push)…')
    try:
        sha, url = github.publicar(relato.alteracoes, mensagem_do_commit(relato))
    except github.ErroPublicacao as exc:
        relato.status = S.FALHA_PUBLICACAO
        relato.erro = str(exc)
        relato.passo('A publicação falhou: ' + str(exc)[:200], salvar=False)
        relato.save()
        return relato
    except Exception as exc:  # noqa: BLE001
        logger.exception('Bugs: publicação do relato %s quebrou', pk)
        relato.status = S.FALHA_PUBLICACAO
        relato.erro = f'Erro inesperado: {type(exc).__name__}: {exc}'
        relato.save()
        return relato
    relato.status = S.PUBLICADO
    relato.commit_sha = sha
    relato.commit_url = url
    relato.publicado_em = timezone.now()
    relato.passo(f'Publicado: commit {sha[:7]}', salvar=False)
    relato.save()
    _avisar([relato.autor], f'Bug #{relato.pk} corrigido',
            (relato.explicacao_usuario or relato.titulo)[:400] + ' A correção entra no ar na próxima atualização do portal.',
            relato)
    return relato
