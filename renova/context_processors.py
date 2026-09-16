"""Menu do Vini Renova: quem vê o item e quantos aparelhos esperam chegada."""
import logging

from django.core.cache import caches

logger = logging.getLogger(__name__)

CACHE_SEGUNDOS = 60      # roda em toda página: configuração mudada aparece em até 1 minuto


def renova_menu(request):
    user = getattr(request, 'user', None)
    vazio = {'renova_liberado': False, 'renova_aguardando': 0}
    if not (user and user.is_authenticated):
        return vazio
    chave = f'renova:menu:{user.pk}'
    try:
        guardado = caches['local'].get(chave)
        if guardado is not None:
            return guardado
        from . import checklist
        from .models import Renova
        from .permissoes import configuracao, e_gerente, pode_fazer, pode_receber, pode_ver_gestao

        cfg = configuracao()
        receber = pode_receber(user, cfg)
        gerente = e_gerente(user)
        # Pendências de quem olha: para quem recebe, aparelhos a caminho; para o gerente, trocas a aprovar.
        a_caminho = (Renova.objects.filter(aprovacao=Renova.APROVADA, recebimento=Renova.PENDENTE)
                     .exclude(parecer=checklist.NAO_APROVADO).count() if receber else 0)
        a_aprovar = (Renova.objects.filter(aprovacao=Renova.AGUARDANDO_GERENTE, loja_id=user.sector_id).count()
                     if gerente and user.sector_id else 0)
        dados = {
            'renova_liberado': receber or gerente or pode_fazer(user, cfg) or pode_ver_gestao(user, cfg),
            'renova_aguardando': a_caminho + a_aprovar,
        }
        caches['local'].set(chave, dados, CACHE_SEGUNDOS)
        return dados
    except Exception as exc:                                    # noqa: BLE001 — o menu nunca derruba a página
        logger.warning('Menu do Renova não carregou: %s', exc)
        return vazio


def limpar_cache_do_menu(user_ids):
    """Chamado ao mudar habilitados ou marcar chegada, para o menu não esperar o minuto."""
    try:
        caches['local'].delete_many([f'renova:menu:{pk}' for pk in user_ids])
    except Exception:                                           # noqa: BLE001
        pass
