"""Rastro completo de um dia (/activity-history/rastro/): tudo o que a pessoa fez no portal — 24/11/2026.

Junta três fontes, todas lidas só do banco:

* **páginas** — ``core.AcessoPagina``: cada página aberta e cada envio. Só existe a partir de
  24/11/2026 (antes o portal não gravava navegação, e o log do gunicorn não diz quem é o
  usuário — só o IP);
* **ações** — ``core.SystemLog``: as ações que o código registra (login, chamados, C$...);
* **banco** — a parte retroativa: varre TODA tabela que tem um vínculo com usuário e uma
  data/hora, e devolve cada linha daquela pessoa cuja data cai no período (chamado criado,
  comentário, comunicado lido, meta movida, sessão aberta, venda lançada...). O papel da
  pessoa na linha vem do nome do campo ("Criado por", "Responsável", "Usuário"), porque a
  varredura não sabe se ela fez ou só foi citada — por isso a coluna "vínculo".

Na varredura, cada tabela é uma consulta com tempo-limite (Postgres): tabela lenta é pulada e
avisada, nunca trava a tela. Só datas que contam um acontecimento entram (criado, alterado,
lido, enviado, concluído...); prazo, vencimento e início/fim agendado ficam de fora.
"""
import logging
import re
from datetime import date, datetime, time, timedelta

from django.apps import apps
from django.conf import settings
from django.db import connection, models
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

FONTES = [
    ('paginas', 'Páginas e envios'),
    ('acoes', 'Ações registradas'),
    ('banco', 'Registros no banco'),
]
MAX_DIAS = 31
LIMITE_POR_TABELA = 5000
TEMPO_POR_TABELA_MS = 6000

# Tabelas que não são "o que a pessoa fez" (ou que já são fontes próprias).
FORA_DA_VARREDURA = {
    'core.systemlog', 'core.acessopagina', 'sessions.session', 'contenttypes.contenttype',
    'auth.permission', 'auth.group', 'core.tarefadiaria',
}
APPS_FORA = {'contenttypes', 'sessions', 'auth'}

_DATA_EVENTO = re.compile(
    r'(criad|created|atualizad|updated|modified|alterad|editad|lid[oa]|read|vist[oa]|viewed|seen|enviad|sent|'
    r'submitted|respondid|answered|conclu|complet|finaliz|finished|aprovad|approved|recusad|rejected|reprovad|'
    r'cancelad|cancelled|canceled|assinad|signed|acess|access|activity|login|logout|decidid|publicad|published|'
    r'pag[oa]|paid|recebid|received|registrad|resgat|redeem|confirmad|confirmed|baixad|download|upload|'
    r'arquivad|archived|excluid|deleted|resolvid|resolved|fechad|closed|abert|opened|assumid|assigned|'
    r'iniciad|started_at|ended_at|marcad|batid|punch|entreg|delivered|avaliad|rated|feito|done|action_time|'
    r'timestamp|^data_hora$|^momento$|^quando$|^em$)', re.I)
_DATA_NAO_EVENTO = re.compile(
    r'(prazo|due|deadline|expir|validade|vencimento|agendad|scheduled|previst|planned|nascimento|birth|'
    r'admiss|demiss|^inicio$|^fim$|^start$|^end$|start_time|end_time|^data_inicio$|^data_fim$|proxim|next_)', re.I)
# Linhas que o sistema grava sozinho para a pessoa (ela não fez nada): entram com tipo
# "automático" e a tela tem um botão para escondê-las. (tabela, campo de data) ou (tabela, '*').
AUTOMATICOS = {
    ('simulator.projecaosimulador', '*'), ('notifications.usernotification', 'created_at'),
    ('core.notification', 'created_at'), ('tangerino.envioanaliseponto', '*'), ('rotina.avisorotina', '*'),
    ('maps.posicaoregistrada', '*'), ('notifications.truepushsubscriber', '*'),
}


def automatico(label, campo):
    return (label, '*') in AUTOMATICOS or (label, campo) in AUTOMATICOS


_CAMPO_TITULO = ('titulo', 'title', 'nome', 'name', 'assunto', 'subject', 'descricao', 'description', 'texto',
                 'text', 'conteudo', 'content', 'mensagem', 'message', 'comentario', 'comment', 'acao',
                 'action', 'status', 'tipo', 'type', 'codigo', 'code', 'numero', 'number', 'url', 'caminho')


# ─── Período e pessoas ───────────────────────────────────────────────────────
def _dia(texto):
    try:
        return datetime.strptime((texto or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        return None


def periodo(parametros):
    """(de, até) a partir de ?dia= ou ?de=&ate= (até MAX_DIAS dias). Padrão: hoje."""
    hoje = timezone.localdate()
    dia = _dia(parametros.get('dia'))
    de, ate = _dia(parametros.get('de')), _dia(parametros.get('ate'))
    if dia:
        de = ate = dia
    de = de or ate or hoje
    ate = ate or de
    if ate < de:
        de, ate = ate, de
    if (ate - de).days >= MAX_DIAS:
        ate = de + timedelta(days=MAX_DIAS - 1)
    return de, ate


def _limites(de, ate):
    tz = timezone.get_current_timezone()
    return (timezone.make_aware(datetime.combine(de, time.min), tz),
            timezone.make_aware(datetime.combine(ate + timedelta(days=1), time.min), tz))


def ids_de_usuarios(parametros):
    ids = []
    for valor in parametros.getlist('u') if hasattr(parametros, 'getlist') else (parametros.get('u') or []):
        for parte in str(valor).split(','):
            if parte.strip().isdigit():
                ids.append(int(parte))
    return sorted(set(ids))


# ─── Catálogo das tabelas (montado uma vez por processo) ─────────────────────
_CATALOGO = None


def _e_data_de_evento(campo):
    if getattr(campo, 'auto_now', False) or getattr(campo, 'auto_now_add', False):
        return True
    nome = campo.name
    if _DATA_NAO_EVENTO.search(nome):
        return False
    return bool(_DATA_EVENTO.search(nome))


def catalogo():
    """[(modelo, [(attname, rótulo do vínculo)], [(campo de data, rótulo, auto_now)], [campos de título])]."""
    global _CATALOGO
    if _CATALOGO is not None:
        return _CATALOGO
    usuario = settings.AUTH_USER_MODEL.lower()
    itens = []
    for modelo in apps.get_models():
        meta = modelo._meta
        if meta.label_lower in FORA_DA_VARREDURA or meta.app_label in APPS_FORA or meta.proxy or not meta.managed:
            continue
        vinculos = [(f.attname, str(f.verbose_name)) for f in meta.concrete_fields
                    if isinstance(f, models.ForeignKey) and f.related_model
                    and f.related_model._meta.label_lower == usuario]
        datas = [(f.name, str(f.verbose_name), bool(getattr(f, 'auto_now', False)))
                 for f in meta.concrete_fields if isinstance(f, models.DateTimeField) and _e_data_de_evento(f)]
        if not vinculos or not datas:
            continue
        nomes = {f.name: f for f in meta.concrete_fields}
        titulos = [n for n in _CAMPO_TITULO if n in nomes and isinstance(nomes[n], (models.CharField, models.TextField))][:3]
        if not titulos:
            titulos = [f.name for f in meta.concrete_fields if isinstance(f, models.CharField)
                       and not f.choices and f.name not in ('password', 'session_key', 'token', 'chave')][:1]
        itens.append((modelo, vinculos, datas, titulos))
    _CATALOGO = itens
    return itens


# ─── Fontes ───────────────────────────────────────────────────────────────────
def _evento(em, user_id, fonte, modulo, o_que, detalhe='', ip='', vinculo='', endereco='', auto=False):
    return {'em': em, 'user_id': user_id, 'fonte': fonte, 'modulo': modulo, 'o_que': o_que,
            'detalhe': detalhe, 'ip': ip or '', 'vinculo': vinculo, 'endereco': endereco, 'automatico': auto}


def _modulo_do_caminho(caminho):
    primeiro = (caminho or '/').strip('/').split('/', 1)[0]
    return primeiro.replace('-', ' ').capitalize() if primeiro else 'Início'


def de_paginas(ini, fim, ids):
    from .models import AcessoPagina
    qs = AcessoPagina.objects.filter(criado_em__gte=ini, criado_em__lt=fim, user__isnull=False)
    if ids:
        qs = qs.filter(user_id__in=ids)
    saida = []
    for a in qs.order_by('criado_em'):
        envio = a.metodo != 'GET'
        endereco = a.caminho + (('?' + a.consulta) if a.consulta else '')
        o_que = (f'Enviou ({a.metodo})' if envio else 'Abriu a página')
        detalhe = endereco + (f' · resposta {a.status}' if a.status and a.status >= 400 else '') \
            + (f' · {a.duracao_ms} ms' if a.duracao_ms is not None else '')
        saida.append(_evento(a.criado_em, a.user_id, 'paginas', _modulo_do_caminho(a.caminho), o_que, detalhe,
                             a.ip, endereco=endereco))
    return saida


def de_acoes(ini, fim, ids):
    from . import historico
    from .models import SystemLog
    qs = SystemLog.objects.filter(created_at__gte=ini, created_at__lt=fim, user__isnull=False)
    if ids:
        qs = qs.filter(user_id__in=ids)
    saida = []
    for log in qs.order_by('created_at'):
        acao = historico.dados_da_acao(log.action_type)
        saida.append(_evento(log.created_at, log.user_id, 'acoes', acao['modulo_rotulo'], acao['rotulo'],
                             log.description or '', log.ip_address))
    return saida


def _texto(valor, limite=160):
    texto = ' '.join(str(valor or '').split())
    return texto if len(texto) <= limite else texto[:limite - 1] + '…'


def de_banco(ini, fim, ids, avisos):
    """A varredura retroativa. Uma consulta por tabela, com tempo-limite."""
    saida = []
    postgres = connection.vendor == 'postgresql'
    ids_set = set(ids)
    try:
        if postgres:
            with connection.cursor() as cur:
                cur.execute(f'SET statement_timeout = {int(TEMPO_POR_TABELA_MS)}')
        for modelo, vinculos, datas, titulos in catalogo():
            meta = modelo._meta
            q_user = Q()
            for attname, _r in vinculos:
                q_user |= Q(**({f'{attname}__in': ids} if ids else {f'{attname}__isnull': False}))
            q_data = Q()
            for nome, _r, _auto in datas:
                q_data |= Q(**{f'{nome}__gte': ini, f'{nome}__lt': fim})
            colunas = ['pk'] + [a for a, _r in vinculos] + [n for n, _r, _a in datas] + titulos
            try:
                linhas = list(modelo._base_manager.filter(q_user & q_data).values(*colunas)[:LIMITE_POR_TABELA + 1])
            except Exception as exc:  # noqa: BLE001 — tabela lenta/sem coluna no banco: pula e avisa
                avisos.append(f'{meta.verbose_name_plural} ({meta.label}): não consultada — {type(exc).__name__}')
                continue
            if len(linhas) > LIMITE_POR_TABELA:
                avisos.append(f'{meta.verbose_name_plural}: mais de {LIMITE_POR_TABELA} registros no período; '
                              f'mostrando os {LIMITE_POR_TABELA} primeiros.')
                linhas = linhas[:LIMITE_POR_TABELA]
            modulo = str(apps.get_app_config(meta.app_label).verbose_name)
            nome_modelo = str(meta.verbose_name).capitalize()
            for linha in linhas:
                rotulo_titulo = next((_texto(linha.get(n)) for n in titulos if (linha.get(n) or '').strip()), '')
                detalhe = f'#{linha["pk"]}' + (f' {rotulo_titulo}' if rotulo_titulo else '')
                vistos = []
                for nome, rotulo, _auto in datas:
                    quando = linha.get(nome)
                    if not quando or not (ini <= quando < fim):
                        continue
                    # Duas datas da mesma linha coladas ("Quando" e "Criado em", "Criado" e
                    # "Atualizado" no mesmo salvamento) são o mesmo acontecimento.
                    if any(abs((quando - v).total_seconds()) < 5 for v in vistos):
                        continue
                    vistos.append(quando)
                    auto = automatico(meta.label_lower, nome)
                    for attname, vinculo in vinculos:
                        uid = linha.get(attname)
                        if uid is None or (ids_set and uid not in ids_set):
                            continue
                        saida.append(_evento(quando, uid, 'banco', modulo, f'{nome_modelo} — {rotulo}', detalhe,
                                             vinculo=vinculo, auto=auto))
    finally:
        if postgres:
            try:
                with connection.cursor() as cur:
                    cur.execute('SET statement_timeout = 0')
            except Exception:  # noqa: BLE001
                logger.warning('Rastro: não voltou o statement_timeout', exc_info=True)
    return saida


# ─── Junta tudo ───────────────────────────────────────────────────────────────
def montar(de, ate, ids=None, fontes=None):
    """Eventos do período (mais antigos primeiro), pessoas envolvidas e avisos."""
    from django.contrib.auth import get_user_model

    fontes = [f for f in (fontes or [c for c, _r in FONTES]) if f in dict(FONTES)]
    ids = list(ids or [])
    ini, fim = _limites(de, ate)
    avisos = []
    eventos = []
    if 'paginas' in fontes:
        eventos += de_paginas(ini, fim, ids)
    if 'acoes' in fontes:
        eventos += de_acoes(ini, fim, ids)
    if 'banco' in fontes:
        eventos += de_banco(ini, fim, ids, avisos)
    eventos.sort(key=lambda e: (e['em'], e['user_id'] or 0))

    usuarios = {u.pk: u for u in get_user_model().objects.select_related('sector')
                .filter(pk__in={e['user_id'] for e in eventos} | set(ids))}
    for e in eventos:
        u = usuarios.get(e['user_id'])
        e['pessoa'] = (u.get_full_name() or u.email) if u else f'Usuário #{e["user_id"]}'
        e['setor'] = str(u.sector) if u and getattr(u, 'sector', None) else ''
        e['local'] = timezone.localtime(e['em'])
    inicio_paginas = primeiro_acesso_registrado()
    if 'paginas' in fontes and (inicio_paginas is None or inicio_paginas.date() > de):
        avisos.insert(0, 'Páginas acessadas só existem a partir de '
                         f'{timezone.localtime(inicio_paginas):%d/%m/%Y %H:%M}' if inicio_paginas else
                         'Páginas acessadas ainda não têm registro: o portal passou a gravar a navegação agora. '
                         'Para trás, o que existe é o rastro no banco e as ações registradas.')
    return eventos, usuarios, avisos


def primeiro_acesso_registrado():
    from .models import AcessoPagina
    return AcessoPagina.objects.order_by('criado_em').values_list('criado_em', flat=True).first()


def resumo_por_pessoa(eventos, usuarios):
    por = {}
    for e in eventos:
        p = por.setdefault(e['user_id'], {'user_id': e['user_id'], 'pessoa': e['pessoa'], 'setor': e['setor'],
                                          'total': 0, 'paginas': 0, 'acoes': 0, 'banco': 0,
                                          'primeiro': e['local'], 'ultimo': e['local']})
        p['total'] += 1
        p[e['fonte']] += 1
        p['ultimo'] = e['local']
    return sorted(por.values(), key=lambda p: -p['total'])


COLUNAS_EXPORTACAO = ['Data', 'Hora', 'Pessoa', 'Setor', 'Fonte', 'Tipo', 'Módulo', 'O que', 'Detalhe',
                      'Vínculo da pessoa', 'IP']


def linhas_para_exportar(eventos):
    rotulos = dict(FONTES)
    for e in eventos:
        yield [e['local'].strftime('%d/%m/%Y'), e['local'].strftime('%H:%M:%S'), e['pessoa'], e['setor'],
               rotulos.get(e['fonte'], e['fonte']), 'Automático' if e['automatico'] else 'Feito pela pessoa',
               e['modulo'], e['o_que'], e['detalhe'], e['vinculo'], e['ip']]


def exportar_xlsx(eventos, resumo, de, ate, avisos):
    """Planilha: aba Movimentações (tudo, em ordem de hora), aba Resumo por pessoa e aba Observações."""
    import io

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = 'Movimentações'
    cabecalho = Font(bold=True, color='FFFFFF')
    fundo = PatternFill('solid', fgColor='FF6B35')
    ws.append(COLUNAS_EXPORTACAO)
    for linha in linhas_para_exportar(eventos):
        ws.append(linha)
    larguras = [11, 9, 28, 22, 18, 17, 18, 38, 60, 20, 16]
    for i, largura in enumerate(larguras, 1):
        ws.column_dimensions[get_column_letter(i)].width = largura
        ws.cell(row=1, column=i).font = cabecalho
        ws.cell(row=1, column=i).fill = fundo
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = ws.dimensions

    r = wb.create_sheet('Resumo por pessoa')
    r.append(['Pessoa', 'Setor', 'Total', 'Páginas e envios', 'Ações registradas', 'Registros no banco',
              'Primeiro', 'Último'])
    for p in resumo:
        r.append([p['pessoa'], p['setor'], p['total'], p['paginas'], p['acoes'], p['banco'],
                  p['primeiro'].strftime('%d/%m %H:%M:%S'), p['ultimo'].strftime('%d/%m %H:%M:%S')])
    for i, largura in enumerate([30, 24, 8, 16, 16, 18, 15, 15], 1):
        r.column_dimensions[get_column_letter(i)].width = largura
        r.cell(row=1, column=i).font = cabecalho
        r.cell(row=1, column=i).fill = fundo
    r.freeze_panes = 'A2'

    o = wb.create_sheet('Observações')
    periodo_txt = de.strftime('%d/%m/%Y') + ('' if de == ate else ' a ' + ate.strftime('%d/%m/%Y'))
    o.append(['Período', periodo_txt])
    o.append(['Gerado em', timezone.localtime().strftime('%d/%m/%Y %H:%M')])
    o.append(['Movimentações', len(eventos)])
    o.append([])
    o.append(['Fontes'])
    o.append(['Páginas e envios', 'Cada página aberta e cada formulário enviado (registro desde que o portal passou a gravar).'])
    o.append(['Ações registradas', 'Ações que o portal grava no histórico (login, chamados, C$, usuários...).'])
    o.append(['Registros no banco', 'Varredura de todas as tabelas com vínculo à pessoa e data no período. '
              '"Vínculo da pessoa" diz em que campo ela aparece (autor, responsável, destinatário...).'])
    o.append(['Tipo "Automático"', 'Gravado pelo sistema para a pessoa (notificação recebida, posição do app, projeção diária).'])
    if avisos:
        o.append([])
        o.append(['Avisos'])
        for a in avisos:
            o.append(['', a])
    o.column_dimensions['A'].width = 22
    o.column_dimensions['B'].width = 110
    for linha in o.iter_rows():
        for c in linha:
            c.alignment = Alignment(wrap_text=True, vertical='top')
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
