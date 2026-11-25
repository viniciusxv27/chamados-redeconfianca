"""Alerta semanal de faltas — o DP recebe o geral e o gestor de cada loja recebe a equipe dele.

Pedido do DP (25/11/2026): hoje a cobrança das faltas é manual e, com a semana corrida,
fica para trás. Uma vez por semana (segunda, sexta ou domingo, à escolha) sai pelo WhatsApp:

- **para o DP** (``AlertaFaltasConfig.dp``): todas as lojas numa mensagem só;
- **para o gestor de cada loja** (regra do Quadro de HC: cargo, grupo GERENTES ou ajuste
  manual — ``quadro_hc.servicos.gerentes_por_loja``): só as faltas da equipe dele, com o
  pedido para procurar o DP e verificarem juntos cada caso;
- opcionalmente no grupo de gestão da loja (o mesmo da cobrança dos cursos).

**Falta** é o dia previsto sem batida nenhuma — a mesma regra do relatório
(``tangerino/pendencias.py``): folga, feriado, férias e atestado não têm horas previstas e
não entram; quem não bate ponto, ainda não tinha sido admitido ou tem o dia fora do que foi
buscado no Tangerino também não. Esquecer uma batida não é falta (fica no relatório).

O período é sempre a última semana fechada, de segunda a domingo. A loja é o setor
principal da pessoa (``user.sector``); setor que não é loja (escritório) só aparece no geral.

Nasce desligado, como tudo que manda mensagem para gente de verdade. Cada destino é
gravado em ``EnvioAlertaFaltas`` ANTES do envio, com chave única por semana.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from . import pendencias as pendencias_svc
from .models import AlertaFaltasConfig, EnvioAlertaFaltas

logger = logging.getLogger(__name__)

FALTA = 'Não houve nenhuma batida no dia'
LINHAS_NO_GERAL = 80          # o WhatsApp corta mensagem longa; o resto fica no relatório
DIAS_DA_SEMANA = ('seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom')


# ---------------------------------------------------------------------------
# Período
# ---------------------------------------------------------------------------
def semana_fechada(hoje):
    """(segunda, domingo) da última semana que já terminou, olhando de ``hoje``."""
    segunda = hoje - timedelta(days=hoje.weekday() + 7)
    return segunda, segunda + timedelta(days=6)


def semana_do_dia(dia):
    """(segunda, domingo) da semana que contém ``dia``."""
    segunda = dia - timedelta(days=dia.weekday())
    return segunda, segunda + timedelta(days=6)


# ---------------------------------------------------------------------------
# As faltas da semana, por loja
# ---------------------------------------------------------------------------
def _nome(user):
    return (user.get_full_name() or user.get_username() or '').strip()


def _e_loja(setor):
    from quadro_hc.servicos import e_loja
    return bool(setor) and e_loja(setor)


def lojas_com_faltas(inicio, fim, buscar=True, aviso=None):
    """Lista de lojas (ordem alfabética) com as faltas da semana, quem avisar e o total.

    Cada item: setor, nome, e_loja, pessoas [{user, nome, dias}], faltas, gerentes
    [{user, nome, numero}], grupo (JID ou '').
    """
    from core.evolution import normalizar_numero
    from cursos.models import GrupoWhatsappLoja, jid_do_grupo
    from quadro_hc.servicos import gerentes_por_loja

    linhas = pendencias_svc.linhas_do_periodo(inicio, fim, apenas_com_pendencia=True, buscar=buscar,
                                              aviso=aviso)
    faltas = [l for l in linhas if FALTA in l['pendencias']]
    por_setor = {}
    for l in faltas:
        u = l['usuario']
        loja = por_setor.setdefault(u.sector_id, {'setor': u.sector if u.sector_id else None, 'pessoas': {}})
        loja['pessoas'].setdefault(u.pk, {'user': u, 'nome': l['nome'], 'dias': []})['dias'].append(l['data'])

    gerentes = gerentes_por_loja()
    grupos = {g.setor_id: jid_do_grupo(g.grupo) for g in GrupoWhatsappLoja.objects.filter(ativo=True)}
    saida = []
    for setor_id, loja in por_setor.items():
        setor = loja['setor']
        pessoas = sorted(loja['pessoas'].values(), key=lambda p: p['nome'].upper())
        for p in pessoas:
            p['dias'].sort()
        e_loja = _e_loja(setor)
        saida.append({
            'setor': setor,
            'setor_id': setor_id,
            'nome': setor.name if setor else 'Sem loja',
            'e_loja': e_loja,
            'pessoas': pessoas,
            'faltas': sum(len(p['dias']) for p in pessoas),
            'gerentes': [{'user': g, 'nome': _nome(g), 'numero': normalizar_numero(getattr(g, 'phone', '') or '')}
                         for g in (gerentes.get(setor_id, []) if e_loja else [])],
            'grupo': grupos.get(setor_id, '') if e_loja else '',
        })
    saida.sort(key=lambda l: (not l['e_loja'], l['nome'].upper()))
    return saida


# ---------------------------------------------------------------------------
# As mensagens
# ---------------------------------------------------------------------------
def _periodo(inicio, fim):
    return f'{inicio:%d/%m} a {fim:%d/%m/%Y}'


def _dias(dias):
    return ', '.join(f'{d:%d/%m} ({DIAS_DA_SEMANA[d.weekday()]})' for d in dias)


def _link(inicio, fim, setor_id=None):
    base = (getattr(settings, 'BASE_URL', '') or '').rstrip('/')
    if not base:
        return ''
    url = f'{base}{reverse("tangerino:relatorio")}?de={inicio:%Y-%m-%d}&ate={fim:%Y-%m-%d}&pendencias=1'
    return url + (f'&setor={setor_id}' if setor_id else '')


def texto_do_gestor(loja, inicio, fim, orientacao=''):
    """A mensagem de uma loja: as faltas da equipe e o pedido para procurar o DP."""
    gente = len(loja['pessoas'])
    partes = [f'*Faltas da sua equipe — {loja["nome"]}*',
              f'Semana de {_periodo(inicio, fim)}',
              '',
              f'{loja["faltas"]} falta(s) de {gente} colaborador(es), dias sem nenhuma batida de ponto:']
    for p in loja['pessoas']:
        partes.append(f'• {p["nome"]}: {_dias(p["dias"])}')
    orientacao = (orientacao or '').strip()
    if orientacao:
        partes += ['', orientacao]
    return '\n'.join(partes)


def _destino_da_loja(loja, config):
    """Como a loja foi (ou seria) avisada, em poucas palavras, para o geral do DP."""
    if not loja['e_loja']:
        return 'fora das lojas: só no geral'
    partes = []
    if config.gerentes:
        com = [g['nome'].split()[0].title() for g in loja['gerentes'] if g['numero']]
        sem = [g for g in loja['gerentes'] if not g['numero']]
        if com:
            partes.append('gestor: ' + ', '.join(com))
        if sem:
            partes.append(f'{len(sem)} gestor(es) sem telefone')
        if not loja['gerentes']:
            partes.append('loja sem gestor no Quadro de HC')
    if config.grupo_da_loja:
        partes.append('grupo da loja' if loja['grupo'] else 'sem grupo cadastrado')
    return '; '.join(partes) or 'gestor não é avisado'


def texto_do_dp(lojas, inicio, fim, config):
    """O geral: todas as lojas, quem faltou e quem foi avisado."""
    cabeca = f'*Faltas da semana — {_periodo(inicio, fim)}*'
    if not lojas:
        return f'{cabeca}\n\n✅ Nenhuma falta na semana (dia previsto sem batida nenhuma).'
    total = sum(l['faltas'] for l in lojas)
    pessoas = sum(len(l['pessoas']) for l in lojas)
    partes = [cabeca, f'{total} falta(s) de {pessoas} colaborador(es) em {len(lojas)} loja(s)/setor(es).']
    escritas, cortou = 0, False
    for loja in lojas:
        if escritas >= LINHAS_NO_GERAL:
            cortou = True
            break
        partes.append(f'\n*{loja["nome"]}* — {loja["faltas"]} falta(s) · {_destino_da_loja(loja, config)}')
        for p in loja['pessoas']:
            if escritas >= LINHAS_NO_GERAL:
                cortou = True
                break
            partes.append(f'• {p["nome"]}: {_dias(p["dias"])}')
            escritas += 1
    if cortou:
        partes.append(f'\n… e mais faltas além das {LINHAS_NO_GERAL} primeiras linhas.')
    link = _link(inicio, fim)
    if link:
        partes.append(f'\nRelatório completo: {link}')
    return '\n'.join(partes)


# ---------------------------------------------------------------------------
# Prévia e envio
# ---------------------------------------------------------------------------
def previa(inicio, fim, config=None, buscar=True):
    config = config or AlertaFaltasConfig.get()
    aviso = {}
    lojas = lojas_com_faltas(inicio, fim, buscar=buscar, aviso=aviso)
    for loja in lojas:
        loja['destino'] = _destino_da_loja(loja, config)
        loja['texto'] = texto_do_gestor(loja, inicio, fim, config.orientacao) if loja['e_loja'] else ''
    return {'inicio': inicio, 'fim': fim, 'lojas': lojas, 'aviso': aviso,
            'faltas': sum(l['faltas'] for l in lojas),
            'pessoas': sum(len(l['pessoas']) for l in lojas),
            'texto_dp': texto_do_dp(lojas, inicio, fim, config)}


def destinatarios_dp(config):
    from core.evolution import normalizar_numero
    saida = []
    for u in config.dp.filter(is_active=True).order_by('first_name', 'last_name'):
        numero = normalizar_numero(getattr(u, 'phone', '') or '')
        if numero:
            saida.append((u, numero))
    return saida


def _reivindicar(chave, reenviar, **campos):
    """Grava o envio antes de mandar. None se outro worker/rodada já mandou (e não é reenvio)."""
    try:
        with transaction.atomic():
            return EnvioAlertaFaltas.objects.create(chave=chave, **campos)
    except IntegrityError:
        if not reenviar:
            return None
        envio = EnvioAlertaFaltas.objects.filter(chave=chave).first()
        if envio is not None:
            EnvioAlertaFaltas.objects.filter(pk=envio.pk).update(manual=campos.get('manual', False))
        return envio


def _mandar(envio, numero, texto, resumo):
    from core import evolution
    try:
        ok, detalhe = evolution.enviar_texto(numero, texto, timeout=20)
    except Exception as exc:                                        # noqa: BLE001 — o contrato é não levantar
        ok, detalhe = False, f'Erro inesperado: {exc}'
    EnvioAlertaFaltas.objects.filter(pk=envio.pk).update(
        enviado=bool(ok), detalhe=str(detalhe or '')[:255])
    if ok:
        resumo['enviados'] += 1
    else:
        resumo['falhas'] += 1
        logger.warning('Alerta de faltas não saiu (%s): %s', envio.chave, str(detalhe)[:200])


def enviar(inicio=None, fim=None, config=None, hoje=None, manual=False, reenviar=False, so_para=None):
    """Manda o alerta da semana. ``so_para`` = [(user, número)]: só o geral, para teste.

    Devolve um resumo: enviados, falhas, ja_enviados, sem_telefone, lojas, faltas.
    """
    from .analise import faltando_no_canal

    config = config or AlertaFaltasConfig.get()
    if inicio is None:
        inicio, fim = semana_fechada(hoje or timezone.localdate())
    resumo = {'enviados': 0, 'falhas': 0, 'ja_enviados': 0, 'sem_telefone': 0, 'inicio': inicio, 'fim': fim}
    if faltando_no_canal():
        resumo['sem_canal'] = True
        return resumo

    lojas = lojas_com_faltas(inicio, fim)
    resumo['lojas'] = len(lojas)
    resumo['faltas'] = sum(l['faltas'] for l in lojas)
    gente = sum(len(l['pessoas']) for l in lojas)
    texto_geral = texto_do_dp(lojas, inicio, fim, config)
    marca = f'{fim:%Y%m%d}'

    if so_para is not None:
        for user, numero in so_para:
            envio = EnvioAlertaFaltas.objects.create(
                chave=f'TESTE:{user.pk}:{timezone.now():%Y%m%d%H%M%S%f}', destino=EnvioAlertaFaltas.DP,
                user=user, periodo_inicio=inicio, periodo_fim=fim, faltas=resumo['faltas'], pessoas=gente,
                manual=True)
            _mandar(envio, numero, '🧪 Teste — ' + texto_geral, resumo)
        return resumo

    dp = destinatarios_dp(config)
    resumo['sem_telefone'] += config.dp.filter(is_active=True).count() - len(dp)
    for user, numero in dp:
        envio = _reivindicar(f'DP:{user.pk}:{marca}', reenviar, destino=EnvioAlertaFaltas.DP, user=user,
                             periodo_inicio=inicio, periodo_fim=fim, faltas=resumo['faltas'], pessoas=gente,
                             manual=manual)
        if envio is None:
            resumo['ja_enviados'] += 1
            continue
        _mandar(envio, numero, texto_geral, resumo)

    for loja in lojas:
        if not loja['e_loja']:
            continue
        texto = texto_do_gestor(loja, inicio, fim, config.orientacao)
        comum = {'loja': loja['setor'], 'periodo_inicio': inicio, 'periodo_fim': fim, 'faltas': loja['faltas'],
                 'pessoas': len(loja['pessoas']), 'manual': manual}
        if config.gerentes:
            for g in loja['gerentes']:
                if not g['numero']:
                    resumo['sem_telefone'] += 1
                    continue
                envio = _reivindicar(f'GERENTE:{loja["setor_id"]}:{g["user"].pk}:{marca}', reenviar,
                                     destino=EnvioAlertaFaltas.GERENTE, user=g['user'], **comum)
                if envio is None:
                    resumo['ja_enviados'] += 1
                    continue
                _mandar(envio, g['numero'], texto, resumo)
        if config.grupo_da_loja and loja['grupo']:
            envio = _reivindicar(f'GRUPO:{loja["setor_id"]}:{marca}', reenviar,
                                 destino=EnvioAlertaFaltas.GRUPO, **comum)
            if envio is None:
                resumo['ja_enviados'] += 1
                continue
            _mandar(envio, loja['grupo'], texto, resumo)
    return resumo


# ---------------------------------------------------------------------------
# Agendamento (chamado pelo middleware do ponto, como a análise)
# ---------------------------------------------------------------------------
def _alvo_de_hoje(config, agora):
    return timezone.make_aware(timezone.datetime.combine(timezone.localdate(agora), config.hora_envio),
                               timezone.get_current_timezone())


def esta_na_hora(config, agora=None):
    """Ligado, hoje é o dia escolhido, passou do horário e ainda não rodou hoje."""
    if not config.ativo:
        return False
    agora = agora or timezone.now()
    if timezone.localdate(agora).weekday() != config.dia_semana:
        return False
    alvo = _alvo_de_hoje(config, agora)
    if agora < alvo:
        return False
    return config.ultimo_envio is None or config.ultimo_envio < alvo
