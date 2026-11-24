"""Requisito "Resultado" do Progresso: a média dos últimos 6 meses nos 7 pilares, puxada do comissionamento
pós-contestação (26/11/2026).

De onde vem: as versões **Pós Contestação** liberadas em /users/manage/system-config/ (uma planilha por mês). A aba
``REMUNERAÇÃO CN`` traz, por pessoa e pilar, o atingimento do mês da versão (``pct_1``) e dos dois anteriores
(``pct_2``, ``pct_3``). Cada mês usa a própria versão; mês sem versão "pós" (ex.: 06/2026) sai da versão seguinte
(o ``pct_2``/``pct_3`` dela). São as mesmas contas da tela de comissionamento (``process_commission_data``).

A média é a de todos os atingimentos (pilar x mês) dos últimos 6 meses que têm número; pilar vazio ou zerado no mês
é "sem dado", não 0%. Ler 6 planilhas leva tempo, então o resultado fica gravado no perfil de cada consultor: a tarefa
diária (core/agendador_diario.py) e o botão do painel do SUPERADMIN atualizam todo mundo de uma vez.
"""
import logging
import threading
import time
from decimal import Decimal

from django.db import close_old_connections
from django.utils import timezone

logger = logging.getLogger(__name__)

MESES = 6
TENTATIVAS = 3
PILARES = ('Móvel', 'Fixa', 'Smartphone', 'Eletrônicos', 'Essenciais', 'Seguro', 'SVA')
NOMES_DOS_MESES = ('jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez')
_rodando = threading.Lock()


# A mesma coluna que a tela de comissionamento lê (``process_commission_data``), mas só pelo nome EXATO:
# a busca parcial de lá, com o mês zerado, cai no mês vizinho ("%ATING_SVA" acha "%ATING_SVA_2") e misturaria meses.
PILARES_DA_PLANILHA = (('Móvel', 'MOVEL'), ('Fixa', 'FIXA'), ('Smartphone', 'SMART'), ('Eletrônicos', 'ELETRO'),
                       ('Essenciais', 'ESSEN'), ('Seguro', 'SEG'), ('SVA', 'SVA'))


def _atingimento(colunas, chave, n, cv):
    """Atingimento (em %) do pilar no mês n (1 = o da versão) — 0 quando a célula está vazia ou zerada."""
    nomes = [f'%ATING_{chave}_{n}', f'%ATING_{chave} {n}', f'ATING_{chave}_{n}', f'%ATING {chave}_{n}',
             f'% ATING_{chave}_{n}', f'%ATING_{chave}_M{n}', f'ATING_{chave}_M{n}']
    if n == 1:
        nomes.append(f'%ATING_{chave}')
    for nome in nomes:
        valor = colunas.get(cv.normalize_col_name(nome))
        if valor is not None and cv.safe_float(valor) != 0:
            return float(cv.convert_percentage(cv.safe_float(valor)))
    return 0


def _mes_antes(ano, mes, quantos):
    total = ano * 12 + (mes - 1) - quantos
    return total // 12, total % 12 + 1


def atingimentos_por_pessoa(versoes=None, falhas=None):
    """{nome normalizado: {(ano, mes): {pilar: pct}}} das versões Pós Contestação liberadas."""
    from users import commission_views as cv
    from users.models import CommissionSpreadsheetVersion as Versao

    if versoes is None:
        versoes = list(Versao.objects.filter(contestacao_phase='pos', status=Versao.STATUS_RELEASED)
                       .exclude(excel_comissao_url='').order_by('-year', '-month')[:MESES])
    com_versao = {(v.year, v.month) for v in versoes}
    pessoas = {}
    for versao in versoes:
        urls = cv.get_excel_urls(versao.year, versao.month, 'pos')
        for tentativa in range(TENTATIVAS):
            # O OneDrive às vezes responde com uma página em vez da planilha: tenta de novo.
            dados = cv.fetch_all_users_from_sheet(cv.SHEET_CN, excel_url=urls['comissao'],
                                                  cache_suffix=f'carreira_{versao.pk}')
            if dados.get('success'):
                break
            time.sleep(2 * (tentativa + 1))
        if not dados.get('success'):
            logger.warning('Progresso: comissionamento %02d/%d não lido: %s', versao.month, versao.year,
                           dados.get('error'))
            if falhas is not None:
                falhas.append(f'{versao.month:02d}/{versao.year}')
            continue
        for linha in dados['users']:
            meses = pessoas.setdefault(cv.normalize_person_name(linha['nome']), {})
            colunas = {cv.normalize_col_name(c): v for c, v in linha['data'].items()}
            for atras in (0, 1, 2):
                mes = _mes_antes(versao.year, versao.month, atras)
                if atras and mes in com_versao:
                    continue                  # o mês tem a própria versão: ela manda
                if atras and mes in meses:
                    continue                  # já veio de uma versão mais nova
                valores = {}
                for pilar, chave in PILARES_DA_PLANILHA:
                    pct = _atingimento(colunas, chave, atras + 1, cv)
                    if pct > 0:
                        valores[pilar] = round(pct, 2)
                if valores:
                    meses[mes] = valores
    return pessoas


def janela(versoes):
    """Os 6 meses que contam: do mês da versão pós-contestação mais nova para trás (o mesmo para todo mundo)."""
    if not versoes:
        return set()
    ano, mes = max((v.year, v.month) for v in versoes)
    return {_mes_antes(ano, mes, i) for i in range(MESES)}


def media_da_pessoa(meses, meses_validos=None):
    """Dos meses de uma pessoa: (média geral, rótulo dos meses, média por pilar, quantos meses), ou None.

    ``meses_validos`` é a janela dos últimos 6 meses do comissionamento: mês antigo (de quem saiu e voltou,
    por exemplo) não entra na média.
    """
    ultimos = sorted((m for m in meses if meses_validos is None or m in meses_validos), reverse=True)[:MESES]
    if not ultimos:
        return None
    todos, por_pilar = [], {}
    for mes in ultimos:
        for pilar, pct in meses[mes].items():
            todos.append(pct)
            por_pilar.setdefault(pilar, []).append(pct)
    if not todos:
        return None
    inicio, fim = min(ultimos), max(ultimos)
    rotulo = f'{NOMES_DOS_MESES[inicio[1] - 1]}–{NOMES_DOS_MESES[fim[1] - 1]}/{str(fim[0])[2:]}'
    pilares = [{'nome': p, 'media': round(sum(por_pilar[p]) / len(por_pilar[p]), 1), 'meses': len(por_pilar[p])}
               for p in PILARES if p in por_pilar]
    return Decimal(str(round(sum(todos) / len(todos), 2))), rotulo, pilares, len(ultimos)


def atualizar_todos():
    """Grava em cada consultor do comercial a média do comissionamento. Devolve (atualizados, sem dado).

    Se alguma planilha da janela não baixar (mesmo tentando de novo), não mexe em nada e devolve None.
    """
    from users import commission_views as cv

    from .models import PerfilCarreira
    from .views import _comercial

    from users.models import CommissionSpreadsheetVersion as Versao
    versoes = list(Versao.objects.filter(contestacao_phase='pos', status=Versao.STATUS_RELEASED)
                   .exclude(excel_comissao_url='').order_by('-year', '-month')[:MESES])
    falhas = []
    pessoas = atingimentos_por_pessoa(versoes, falhas)
    if falhas:
        # Média sem um dos meses seria outra média: fica a última leitura completa.
        logger.warning('Progresso: resultado não atualizado — planilha(s) %s não baixaram', ', '.join(falhas))
        return None
    validos = janela(versoes)
    agora = timezone.now()
    atualizados = sem_dado = 0
    for user in _comercial():
        perfil, _ = PerfilCarreira.objects.get_or_create(user=user)
        meses = pessoas.get(cv.normalize_person_name(user.get_full_name() or user.first_name)) or {}
        calculo = media_da_pessoa(meses, validos)
        if calculo is None:
            campos = {'resultado_auto': None, 'resultado_auto_meses': '', 'resultado_auto_pilares': None}
            sem_dado += 1
        else:
            media, rotulo, pilares, quantos = calculo
            campos = {'resultado_auto': media, 'resultado_auto_meses': f'{rotulo} · {quantos} mês(es)',
                      'resultado_auto_pilares': pilares}
            atualizados += 1
        PerfilCarreira.objects.filter(pk=perfil.pk).update(resultado_auto_em=agora, **campos)
    logger.info('Progresso: resultado do comissionamento — %s com média, %s sem dado', atualizados, sem_dado)
    return atualizados, sem_dado


def tarefa_diaria():
    """Chamada pelo agendador diário (core/agendador_diario.py)."""
    return atualizar_todos()


def atualizar_em_segundo_plano():
    """O botão do painel: lê as planilhas numa thread (leva cerca de um minuto). False se já está rodando."""
    if not _rodando.acquire(blocking=False):
        return False

    def _rodar():
        try:
            close_old_connections()
            atualizar_todos()
        except Exception:  # noqa: BLE001
            logger.exception('Progresso: atualização do resultado falhou')
        finally:
            close_old_connections()
            _rodando.release()

    threading.Thread(target=_rodar, daemon=True, name='progresso-resultado').start()
    return True
