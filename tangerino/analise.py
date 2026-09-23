"""A análise das divergências do ponto que sai pelo WhatsApp.

Três cadências, com o mesmo conteúdo e recortes diferentes:

- **diária**, todo dia, sobre o dia anterior;
- **semanal**, toda segunda-feira, sobre a semana passada (segunda a domingo);
- **mensal**, no último dia útil do mês, sobre o mês corrente.

Quem recebe é quem o SUPERADMIN escolher em ``AnalisePontoConfig`` — a
configuração nasce desligada, porque isto manda mensagem para o WhatsApp de
gente de verdade.

As divergências são as mesmas do relatório (``tangerino/pendencias.py``): um
lugar só decide o que é pendência. A mensagem vai agrupada por loja, com o nome
do colaborador e o que faltou em cada dia.

Cuidados que valem a pena conhecer:

- a linha de ``EnvioAnalisePonto`` é gravada ANTES do envio e tem trava única
  por (pessoa, cadência, fim do período): três workers do gunicorn não mandam a
  mesma análise três vezes, e uma rodada repetida no mesmo dia não reenvia;
- processo sem a Evolution configurada não reivindica nada — senão ele gastaria
  a vez de quem consegue mandar (a mesma lição dos lembretes da rotina);
- em processo de teste o envio já é recusado pelo ``core.evolution``, mas o
  dublê dos testes troca a função mesmo assim.
"""
import calendar
import logging
from datetime import date, timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from . import pendencias as pendencias_svc
from .models import AnalisePontoConfig, EnvioAnalisePonto

logger = logging.getLogger(__name__)

# A mensagem do WhatsApp tem limite; acima disso o resto vira "veja o relatório".
LINHAS_NA_MENSAGEM = 60


def faltando_no_canal():
    """O que falta configurar neste processo para o WhatsApp sair (lista vazia: pronto)."""
    return [nome for nome in ('EVOLUTION_API_URL', 'EVOLUTION_API_KEY', 'EVOLUTION_INSTANCE')
            if not (getattr(settings, nome, '') or '')]


# ---------------------------------------------------------------------------
# Os períodos de cada cadência
# ---------------------------------------------------------------------------
def e_dia_util(dia):
    """Segunda a sexta. Feriado não entra: o portal não tem calendário de feriados."""
    return dia.weekday() < 5


def e_ultimo_dia_util_do_mes(dia):
    """Não existe outro dia útil depois deste, dentro do mesmo mês."""
    if not e_dia_util(dia):
        return False
    ultimo = date(dia.year, dia.month, calendar.monthrange(dia.year, dia.month)[1])
    seguinte = dia + timedelta(days=1)
    while seguinte <= ultimo:
        if e_dia_util(seguinte):
            return False
        seguinte += timedelta(days=1)
    return True


def periodo(tipo, hoje):
    """(início, fim) da cadência, olhando de ``hoje``."""
    if tipo == EnvioAnalisePonto.Tipo.DIARIO:
        ontem = hoje - timedelta(days=1)
        return ontem, ontem
    if tipo == EnvioAnalisePonto.Tipo.SEMANAL:
        segunda = hoje - timedelta(days=hoje.weekday() + 7)
        return segunda, segunda + timedelta(days=6)
    primeiro = hoje.replace(day=1)
    return primeiro, hoje


def cadencias_do_dia(config, hoje):
    """Quais análises vencem hoje, na ordem em que são mandadas."""
    tipos = []
    if config.diario:
        tipos.append(EnvioAnalisePonto.Tipo.DIARIO)
    if config.semanal and hoje.weekday() == 0:
        tipos.append(EnvioAnalisePonto.Tipo.SEMANAL)
    if config.mensal and e_ultimo_dia_util_do_mes(hoje):
        tipos.append(EnvioAnalisePonto.Tipo.MENSAL)
    return tipos


# ---------------------------------------------------------------------------
# A mensagem
# ---------------------------------------------------------------------------
TITULOS = {
    EnvioAnalisePonto.Tipo.DIARIO: 'Ponto de ontem',
    EnvioAnalisePonto.Tipo.SEMANAL: 'Ponto da semana passada',
    EnvioAnalisePonto.Tipo.MENSAL: 'Ponto do mês',
}


def _link(inicio, fim):
    base = (getattr(settings, 'BASE_URL', '') or '').rstrip('/')
    if not base:
        return ''
    return f'{base}{reverse("tangerino:relatorio")}?de={inicio:%Y-%m-%d}&ate={fim:%Y-%m-%d}&pendencias=1'


def _quando(tipo, inicio, fim):
    if inicio == fim:
        return f'{inicio:%d/%m/%Y}'
    return f'{inicio:%d/%m} a {fim:%d/%m/%Y}'


def texto_da_analise(tipo, inicio, fim, linhas):
    """A mensagem pronta: divergências agrupadas por loja, pessoa e dia."""
    cabeca = f'*{TITULOS[tipo]} — {_quando(tipo, inicio, fim)}*'
    com_pendencia = [l for l in linhas if l['tem_pendencia']]
    if not com_pendencia:
        return f'{cabeca}\n\n✅ Nenhuma divergência de ponto no período.'

    pessoas = len({l["usuario"].id for l in com_pendencia})
    partes = [cabeca,
              f'{len(com_pendencia)} dia(s) com divergência, de {pessoas} colaborador(es).']
    escritas = 0
    cortou = False
    for loja, da_loja in pendencias_svc.por_loja(com_pendencia).items():
        if escritas >= LINHAS_NA_MENSAGEM:
            cortou = True
            break
        partes.append(f'\n*{loja}*')
        por_pessoa = {}
        for linha in da_loja:
            por_pessoa.setdefault(linha['nome'], []).append(linha)
        for nome, dias in por_pessoa.items():
            if escritas >= LINHAS_NA_MENSAGEM:
                cortou = True
                break
            if inicio == fim:
                partes.append(f'• {nome}: {dias[0]["pendencia"].lower()}')
                escritas += 1
                continue
            partes.append(f'• {nome}')
            for dia in dias:
                if escritas >= LINHAS_NA_MENSAGEM:
                    cortou = True
                    break
                partes.append(f'   – {dia["data"]:%d/%m} ({_dia_da_semana(dia["data"])}): '
                              f'{dia["pendencia"].lower()}')
                escritas += 1
    if cortou:
        partes.append(f'\n… e mais divergências além das {LINHAS_NA_MENSAGEM} primeiras linhas.')
    link = _link(inicio, fim)
    if link:
        partes.append(f'\nRelatório completo: {link}')
    return '\n'.join(partes)


def _dia_da_semana(dia):
    return ('seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom')[dia.weekday()]


# ---------------------------------------------------------------------------
# O envio
# ---------------------------------------------------------------------------
def sincronizacao_de_hoje(hoje=None):
    """A sincronização de marcações já rodou hoje?

    A análise fala do dia anterior, e a marcação de ontem só entra na tabela na
    sincronização de hoje: mandar antes dela seria mandar dado pela metade.
    Sem sincronização automática ligada não há o que esperar — quem sincroniza
    na mão decide a hora.
    """
    from .models import ConfiguracaoTangerino, SincronizacaoTangerino

    if not ConfiguracaoTangerino.get().sincronizar_automatico:
        return True
    hoje = hoje or timezone.localdate()
    return SincronizacaoTangerino.objects.filter(
        tipo=SincronizacaoTangerino.Tipo.PONTO, sucesso=True, executada_em__date=hoje).exists()


def destinatarios(config):
    """Quem recebe e tem telefone utilizável no cadastro."""
    from core.evolution import normalizar_numero

    pessoas = []
    for user in config.destinatarios.filter(is_active=True).order_by('first_name', 'last_name'):
        numero = normalizar_numero(getattr(user, 'phone', '') or '')
        if numero:
            pessoas.append((user, numero))
    return pessoas


def reivindicar(user, tipo, inicio, fim):
    """Grava o envio antes de mandar. None se outro worker (ou outra rodada) já gravou."""
    try:
        with transaction.atomic():
            return EnvioAnalisePonto.objects.create(
                user=user, tipo=tipo, periodo_inicio=inicio, periodo_fim=fim)
    except IntegrityError:
        return None


def enviar(tipos=None, hoje=None, config=None):
    """Manda as análises que vencem hoje. Devolve um resumo do que aconteceu."""
    from core import evolution

    config = config or AnalisePontoConfig.get()
    hoje = hoje or timezone.localdate()
    resumo = {'enviados': 0, 'falhas': 0, 'sem_divergencia': 0, 'ja_enviados': 0, 'sem_telefone': 0}

    if not config.ativo:
        resumo['desligada'] = True
        return resumo
    if faltando_no_canal():
        # Sem o canal, reivindicar só gastaria a vez do servidor que consegue mandar.
        resumo['sem_canal'] = True
        return resumo

    pessoas = destinatarios(config)
    resumo['sem_telefone'] = config.destinatarios.filter(is_active=True).count() - len(pessoas)
    if not pessoas:
        return resumo

    for tipo in (tipos if tipos is not None else cadencias_do_dia(config, hoje)):
        inicio, fim = periodo(tipo, hoje)
        linhas = pendencias_svc.linhas_do_periodo(inicio, fim, apenas_com_pendencia=True)
        if not linhas and config.somente_com_pendencia:
            resumo['sem_divergencia'] += 1
            continue
        texto = texto_da_analise(tipo, inicio, fim, linhas)
        dias = len(linhas)
        gente = len({l['usuario'].id for l in linhas})
        for user, numero in pessoas:
            envio = reivindicar(user, tipo, inicio, fim)
            if envio is None:
                resumo['ja_enviados'] += 1
                continue
            try:
                ok, detalhe = evolution.enviar_texto(numero, texto)
            except Exception as exc:                                # noqa: BLE001 — o contrato é não levantar
                ok, detalhe = False, f'Erro inesperado: {exc}'
            EnvioAnalisePonto.objects.filter(pk=envio.pk).update(
                enviado=bool(ok), detalhe=str(detalhe or '')[:255], pessoas=gente, dias=dias)
            if ok:
                resumo['enviados'] += 1
            else:
                resumo['falhas'] += 1
                logger.warning('Análise de ponto (%s) não saiu para %s: %s', tipo, user, str(detalhe)[:200])
    return resumo


def previa(tipo, hoje=None):
    """O que sairia agora nessa cadência — para a tela de configuração conferir antes de ligar."""
    hoje = hoje or timezone.localdate()
    inicio, fim = periodo(tipo, hoje)
    linhas = pendencias_svc.linhas_do_periodo(inicio, fim, apenas_com_pendencia=True)
    return {'tipo': tipo, 'inicio': inicio, 'fim': fim, 'linhas': linhas,
            'texto': texto_da_analise(tipo, inicio, fim, linhas)}
