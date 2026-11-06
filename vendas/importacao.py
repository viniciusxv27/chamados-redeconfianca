"""Uma importação da tabela de preços, do arquivo ao aviso — a mesma pela tela e pelo e-mail.

A tela (/vendas/precos/importar/) e a busca diária no e-mail (vendas/tabela_email.py)
passam por aqui: grava a tabela, registra a ImportacaoPrecos e, já com a tabela
gravada, manda o aviso no WhatsApp e publica o popup. Falha no aviso nunca desfaz a
importação.
"""
import logging

from django.utils import timezone

from . import aviso_precos
from .models import ImportacaoPrecos
from .services import importar_tabela_precos

logger = logging.getLogger(__name__)

VARIACAO_VAZIA = {'subiram': [], 'diminuiram': [], 'novos': [], 'removidos': [], 'outros': 0}


def registrar_importacao(arquivo, nome_arquivo, *, usuario=None, origem='manual',
                         destinatarios=(), grupos=(), popup=True):
    """Importa e avisa. Devolve {'ok', 'registro', 'resumo', 'erro', 'resultado'}.

    ``resultado`` é o que a tela mostra depois (vai para a sessão: só texto).
    """
    registro = ImportacaoPrecos(usuario=usuario if getattr(usuario, 'pk', None) else None,
                                arquivo=(nome_arquivo or '')[:255], origem=origem)
    try:
        resumo = importar_tabela_precos(arquivo)
    except Exception as exc:  # noqa: BLE001 — [Confiabilidade.NF003] a tabela vigente fica como estava
        registro.erro = str(exc)[:2000]
        registro.save()
        return {'ok': False, 'registro': registro, 'resumo': None, 'erro': str(exc), 'resultado': None}

    registro.incluidos, registro.alterados = resumo['incluidos'], resumo['alterados']
    registro.sem_mudanca, registro.rejeitados = resumo['sem_mudanca'], resumo['rejeitados'][:500]
    registro.removidos = resumo['removidos'] + resumo['inativados']
    registro.save()

    # A tabela já está gravada: daqui para baixo nada desfaz a importação.
    resultado = {'arquivo': registro.arquivo, 'envios': None, 'sku': resumo.get('sku'),
                 'mantidos': resumo.get('mantidos') or []}
    variacao = resumo.get('variacao') or VARIACAO_VAZIA
    try:
        resultado['variacao'] = aviso_precos.para_tela(variacao)
        if destinatarios or grupos:
            quando = timezone.localtime(registro.quando)
            resultado['envios'] = (aviso_precos.avisar(list(destinatarios), variacao, quando=quando)
                                   + aviso_precos.avisar_grupos(list(grupos), variacao, quando=quando))
    except Exception as exc:  # noqa: BLE001
        resultado['erro_aviso'] = f'O aviso no WhatsApp não saiu: {exc}'[:300]
    if popup:
        try:
            aviso_precos.criar_popup(registro, variacao, por=usuario)
            resultado['popup'] = True
        except Exception as exc:  # noqa: BLE001 — a tabela já foi gravada
            logger.exception('Aviso de preços: popup não criado')
            resultado['erro_popup'] = f'O aviso no portal não saiu: {exc}'[:300]
    return {'ok': True, 'registro': registro, 'resumo': resumo, 'erro': '', 'resultado': resultado}
