"""Visão Renova: toda venda com Renova no SAP precisa de chamado no portal.

Pedido (09/11/2026): "Na aba Visão Renova (Allied e Assurant), teriam a relação de
todas as vendas criadas com Renova no SAP e, de acordo com o CPF do cliente,
identificaria se existe um chamado aberto para o Renova realizado; se não existir,
enviaria uma mensagem às 15h, fora do horário de pico das mensagens nos grupos,
solicitando a criação."

- **Fonte:** ``zs103_final`` do MySQL do SAP, tipo de faturamento ``ZVNB`` (uma
  linha por ordem). Lida junto com a auditoria (a cada 3 h) e de novo às 15h.
- **Casamento pelo CPF:** o chamado de Renova não traz CPF — traz a venda do Vivo Go
  ("Venda no Vivo Go: 2751333"). A ``vw_conferencia_vendas_produtos`` liga CPF ↔
  venda do Vivo Go ↔ nota do SAP, então a ordem ZVNB casa com o chamado que cita
  uma venda daquele CPF. Também vale o CPF escrito no texto do chamado e a ficha do
  app Renova (``renova.Renova.cliente_cpf``) ligada ao chamado. Só conta chamado de
  categoria com "RENOVA" no nome (Allied e Vini), aberto de 15 dias antes a 60 dias
  depois da fatura.
- **Loja:** o escritório de vendas do SAP (``escr_vend``, ex. E047). Cada código é
  aprendido dos próprios dados — a loja das vendas do Vivo Go com aquela nota que
  mais aparece —, sem tabela fixa no código.
- **Cobrança:** às 15h (``core/agendador_diario.py``), uma mensagem por loja no grupo
  de gestão dela (o mesmo da cobrança dos cursos, ``cursos.GrupoWhatsappLoja``),
  com as vendas dos últimos ``janela_dias`` sem chamado. Repete todo dia até o
  chamado aparecer ou alguém marcar "não precisa". Nasce desligada.
"""
import logging
import re
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import mysql
from .models import CobrancaRenova, ConfiguracaoRenovaSap, VendaRenova
from .permissions import normalizar_loja

logger = logging.getLogger(__name__)

DIAS_NO_ESPELHO = 120                  # quanto do SAP fica na tela
CHAMADO_ANTES = timedelta(days=15)     # chamado aberto antes da fatura (venda no Vivo Go antes do SAP)
CHAMADO_DEPOIS = timedelta(days=60)
INTERVALO_MINIMO = timedelta(minutes=30)   # a mesma loja não é cobrada duas vezes seguidas
LOJA_ADMINISTRATIVA = 'SEDE ADMINISTRATIVA'
MAX_NA_MENSAGEM = 25


def digitos(valor):
    return re.sub(r'\D', '', str(valor or ''))


def venda_base(id_venda):
    """'2751393-1' → '2751393' (o item da venda não importa para o chamado)."""
    return digitos(str(id_venda or '').split('-')[0])


def _decimal(valor):
    try:
        return Decimal(str(valor)).quantize(Decimal('0.01')) if valor not in (None, '') else None
    except (InvalidOperation, ValueError):
        return None


def _sem_html(texto):
    texto = re.sub(r'<[^>]+>', ' ', texto or '')
    return texto.replace('&nbsp;', ' ')


RE_VENDA = re.compile(r'vivo\s*go\D{0,40}?(\d{5,9})', re.I)
RE_CPF = re.compile(r'(?<!\d)(\d{3}\.?\d{3}\.?\d{3}-?\d{2})(?!\d)')


def chaves_do_chamado(texto):
    """(vendas do Vivo Go citadas, CPFs escritos) no texto do chamado."""
    limpo = _sem_html(texto)
    vendas = {m.group(1) for m in RE_VENDA.finditer(limpo)}
    cpfs = {digitos(m.group(1)) for m in RE_CPF.finditer(limpo)}
    return vendas, {c for c in cpfs if len(c) == 11}


# ---------------------------------------------------------------------------
# Leitura do SAP
# ---------------------------------------------------------------------------
def _setores_por_loja():
    from users.models import Sector
    return {normalizar_loja(s.name): s for s in Sector.objects.all()}


def ler_do_sap(hoje=None):
    """Relê as ordens ZVNB e as vendas do Vivo Go e atualiza o espelho. Devolve o resumo."""
    hoje = hoje or timezone.localdate()
    desde = hoje - timedelta(days=DIAS_NO_ESPELHO)
    ordens = mysql.ler_renovas(desde)
    vendas = mysql.ler_vendas_vivogo(desde - timedelta(days=30))

    por_cpf, por_nota, loja_da_nota, nome_por_cpf = defaultdict(set), defaultdict(set), {}, {}
    for v in vendas:
        base = venda_base(v.get('ID_VENDA'))
        nota = str(v.get('NUM_FAT_SAP') or '').strip()
        for coluna in ('CPF_VIVOGO_NORMALIZADO', 'CPF_SAP_NORMALIZADO'):
            cpf = digitos(v.get(coluna))
            if cpf and base:
                por_cpf[cpf].add(base)
            if cpf and (v.get('NOME_CLIENTE') or '').strip():
                nome_por_cpf.setdefault(cpf, str(v['NOME_CLIENTE']).strip())
        if nota and base:
            por_nota[nota].add(base)
        if nota and (v.get('PDV') or '').strip():
            loja_da_nota[nota] = str(v['PDV']).strip()

    # Loja de cada escritório de vendas: a que mais aparece nas notas dele.
    contagem = defaultdict(Counter)
    for o in ordens:
        loja = loja_da_nota.get(str(o.get('num_fat') or '').strip())
        if loja and normalizar_loja(loja) != LOJA_ADMINISTRATIVA:
            contagem[str(o.get('escr_vend') or '').strip()][loja] += 1
    loja_do_escritorio = {e: c.most_common(1)[0][0] for e, c in contagem.items() if e and c}

    setores = _setores_por_loja()
    agora = timezone.now()
    existentes = {v.ordem: v for v in VendaRenova.objects.all()}
    novas, mudaram, vistas = [], [], set()
    campos = ['num_fat', 'data_fatura', 'cpf', 'cliente_nome', 'escritorio', 'loja', 'setor',
              'material', 'imei', 'valor_faturado', 'desconto', 'vendas_vivogo', 'ativa', 'visto_em']
    for o in ordens:
        ordem = str(o['doc_vendas']).strip()
        vistas.add(ordem)
        nota = str(o.get('num_fat') or '').strip()
        cpf = digitos(o.get('cnpj_cpf'))
        escritorio = str(o.get('escr_vend') or '').strip()
        loja = loja_do_escritorio.get(escritorio) or loja_da_nota.get(nota, '')
        valores = {
            'num_fat': nota[:40],
            'data_fatura': o.get('dt_fatura'),
            'cpf': cpf[:14],
            'cliente_nome': nome_por_cpf.get(cpf, '')[:180],
            'escritorio': escritorio[:10],
            'loja': loja[:120],
            'setor': setores.get(normalizar_loja(loja)) if loja else None,
            'material': str(o.get('desc_material') or '').strip()[:255],
            'imei': str(o.get('hexadecimal') or '').strip()[:40],
            'valor_faturado': _decimal(o.get('vl_faturado')),
            'desconto': _decimal(o.get('desconto')),
            'vendas_vivogo': sorted(por_cpf.get(cpf, set()) | por_nota.get(nota, set())),
            'ativa': True,
            'visto_em': agora,
        }
        venda = existentes.get(ordem)
        if venda is None:
            novas.append(VendaRenova(ordem=ordem[:30], **valores))
            continue
        for campo, valor in valores.items():
            setattr(venda, campo, valor)
        mudaram.append(venda)

    if novas:
        VendaRenova.objects.bulk_create(novas, batch_size=500)
    if mudaram:
        VendaRenova.objects.bulk_update(mudaram, campos, batch_size=500)
    # Saiu do SAP (estorno, ordem trocada) dentro do período lido: fica na tela como "saiu".
    sairam = (VendaRenova.objects.filter(ativa=True, data_fatura__gte=desde)
              .exclude(ordem__in=vistas).update(ativa=False))
    casados = casar_chamados()
    resumo = {'ordens': len(vistas), 'novas': len(novas), 'sairam': sairam, 'chamados_achados': casados}
    logger.info('Visão Renova: %s', resumo)
    return resumo


# ---------------------------------------------------------------------------
# Casamento com os chamados
# ---------------------------------------------------------------------------
def _chamados_de_renova(desde):
    from tickets.models import Ticket
    return list(Ticket.objects.filter(category__name__icontains='renova', created_at__gte=desde)
                .only('id', 'description', 'title', 'created_at'))


def _indice(chamados):
    """Por venda do Vivo Go e por CPF: [(criado_em, chamado, como)]."""
    from renova.models import Renova

    por_venda, por_cpf = defaultdict(list), defaultdict(list)
    for t in chamados:
        vendas, cpfs = chaves_do_chamado(f'{t.title}\n{t.description}')
        for v in vendas:
            por_venda[v].append((t.created_at, t, VendaRenova.COMO_VENDA))
        for c in cpfs:
            por_cpf[c].append((t.created_at, t, VendaRenova.COMO_CPF))
    ids = {t.id for t in chamados}
    for ficha in Renova.objects.filter(chamado_id__in=ids).exclude(cliente_cpf='').select_related('chamado'):
        por_cpf[digitos(ficha.cliente_cpf)].append((ficha.chamado.created_at, ficha.chamado, VendaRenova.COMO_FICHA))
    return por_venda, por_cpf


def achar_chamado(venda, por_venda, por_cpf):
    """(chamado, como) do chamado mais perto da fatura, ou (None, '')."""
    candidatos = []
    for v in venda.vendas_vivogo or []:
        candidatos += por_venda.get(str(v), [])
    if venda.cpf:
        candidatos += por_cpf.get(venda.cpf, [])
    if venda.data_fatura:
        de = venda.data_fatura - CHAMADO_ANTES
        ate = venda.data_fatura + CHAMADO_DEPOIS
        candidatos = [c for c in candidatos if de <= timezone.localdate(c[0]) <= ate]
        alvo = venda.data_fatura
        candidatos.sort(key=lambda c: abs((timezone.localdate(c[0]) - alvo).days))
    if not candidatos:
        return None, ''
    _, chamado, como = candidatos[0]
    return chamado, como


def casar_chamados(vendas=None):
    """Procura chamado para as vendas ainda sem chamado. Devolve quantas acharam agora."""
    qs = vendas if vendas is not None else VendaRenova.objects.filter(chamado__isnull=True)
    pendentes = list(qs)
    if not pendentes:
        return 0
    datas = [v.data_fatura for v in pendentes if v.data_fatura]
    desde = (min(datas) if datas else timezone.localdate() - timedelta(days=DIAS_NO_ESPELHO)) - CHAMADO_ANTES
    inicio = timezone.make_aware(timezone.datetime.combine(desde, timezone.datetime.min.time()))
    por_venda, por_cpf = _indice(_chamados_de_renova(inicio))
    agora, achadas = timezone.now(), 0
    for venda in pendentes:
        chamado, como = achar_chamado(venda, por_venda, por_cpf)
        if chamado is None:
            continue
        achadas += VendaRenova.objects.filter(pk=venda.pk, chamado__isnull=True).update(
            chamado=chamado, chamado_como=como, chamado_achado_em=agora)
    return achadas


# ---------------------------------------------------------------------------
# Cobrança no grupo da loja
# ---------------------------------------------------------------------------
def a_cobrar(cfg=None, hoje=None):
    cfg = cfg or ConfiguracaoRenovaSap.get()
    hoje = hoje or timezone.localdate()
    return (VendaRenova.objects.filter(ativa=True, chamado__isnull=True, dispensada=False,
                                       data_fatura__gte=hoje - timedelta(days=max(1, cfg.janela_dias)),
                                       data_fatura__lte=hoje)
            .select_related('setor').order_by('data_fatura', 'ordem'))


def _primeiro_nome(texto):
    partes = str(texto or '').split()
    return ' '.join(partes[:2]).title() if partes else ''


def _aparelho(material):
    texto = ' '.join(str(material or '').split())
    return texto.split('/')[0].strip() or 'aparelho'


def montar_texto(setor, vendas, hoje=None):
    hoje = hoje or timezone.localdate()
    loja = (setor.name or '').strip()
    n = len(vendas)
    linhas = [
        '🔄 *RENOVA SEM CHAMADO*',
        f'_{loja}_',
        '',
        f'Olá, gestão! O SAP mostra {"1 venda" if n == 1 else f"{n} vendas"} com *Renova* '
        f'sem chamado aberto no portal:',
        '',
    ]
    for v in vendas[:MAX_NA_MENSAGEM]:
        cliente = _primeiro_nome(v.cliente_nome) or 'Cliente'
        data = v.data_fatura.strftime('%d/%m') if v.data_fatura else '--/--'
        linha = f'• *{data}* · {cliente} (CPF {v.cpf_mascarado}) · {_aparelho(v.material)}'
        if v.vendas_vivogo:
            linha += f' · Vivo Go {v.vendas_vivogo[-1]}'
        linhas.append(linha)
    if n > MAX_NA_MENSAGEM:
        linhas.append(f'_+ {n - MAX_NA_MENSAGEM} venda(s) — lista completa no portal (Visão SAP › Renova)._')
    base = (getattr(settings, 'BASE_URL', '') or '').rstrip('/')
    linhas += [
        '',
        '👉 Por favor, abram o chamado de *Renova* (Allied ou Vini) com o número da venda do Vivo Go'
        + (f': {base}/tickets/create/' if base else '.'),
        '',
        '_Este lembrete sai todo dia às 15h até o chamado aparecer no portal._',
    ]
    return '\n'.join(linhas)


def previa(cfg=None, hoje=None):
    """[{setor, grupo, vendas, texto}] por loja + o que ficou sem loja/grupo."""
    from cursos.models import GrupoWhatsappLoja

    grupos = {g.setor_id: g for g in GrupoWhatsappLoja.objects.filter(ativo=True)}
    por_setor, sem_loja = defaultdict(list), []
    for v in a_cobrar(cfg, hoje):
        if v.setor_id:
            por_setor[v.setor].append(v)
        else:
            sem_loja.append(v)
    lojas = []
    for setor, vendas in sorted(por_setor.items(), key=lambda x: x[0].name):
        lojas.append({'setor': setor, 'grupo': grupos.get(setor.id), 'vendas': vendas,
                      'texto': montar_texto(setor, vendas, hoje)})
    return {'lojas': lojas, 'sem_loja': sem_loja}


def faltando_no_canal():
    from cursos.cobranca import faltando_no_canal as _faltando
    return _faltando()


def _reivindicar(loja, origem, user):
    from cursos.models import GrupoWhatsappLoja
    with transaction.atomic():
        GrupoWhatsappLoja.objects.select_for_update().filter(pk=loja['grupo'].pk).first()
        recente = (CobrancaRenova.objects
                   .filter(setor=loja['setor'], criado_em__gte=timezone.now() - INTERVALO_MINIMO)
                   .exclude(enviado=False))
        if recente.exists():
            return None
        cobranca = CobrancaRenova.objects.create(
            setor=loja['setor'], grupo=loja['grupo'].grupo, texto=loja['texto'], origem=origem,
            disparado_por=user if getattr(user, 'pk', None) else None)
        cobranca.vendas.set(loja['vendas'])
    return cobranca


def mandar(cobrancas):
    from core import evolution
    from cursos.models import jid_do_grupo

    resumo = {'enviados': 0, 'falhas': 0}
    for cobranca in cobrancas:
        try:
            ok, detalhe = evolution.enviar_texto(jid_do_grupo(cobranca.grupo), cobranca.texto, timeout=20)
        except Exception as exc:                                    # noqa: BLE001
            ok, detalhe = False, f'Erro inesperado: {exc}'
        CobrancaRenova.objects.filter(pk=cobranca.pk).update(enviado=bool(ok), detalhe=str(detalhe or '')[:255])
        if ok:
            from django.db.models import F
            cobranca.vendas.update(cobrancas=F('cobrancas') + 1, ultima_cobranca_em=timezone.now())
            resumo['enviados'] += 1
        else:
            resumo['falhas'] += 1
            logger.warning('Cobrança de Renova não saiu para %s: %s', cobranca.setor, str(detalhe)[:200])
    return resumo


def cobrar(origem=CobrancaRenova.AUTOMATICA, user=None, setores=None, cfg=None, hoje=None):
    """Grava e manda uma mensagem por loja com venda sem chamado. Devolve o que aconteceu."""
    resultado = {'gravadas': [], 'recentes': [], 'sem_grupo': [], 'sem_loja': 0, 'sem_canal': False,
                 'resumo': {'enviados': 0, 'falhas': 0}}
    if faltando_no_canal():
        resultado['sem_canal'] = True
        return resultado
    dados = previa(cfg, hoje)
    resultado['sem_loja'] = len(dados['sem_loja'])
    for loja in dados['lojas']:
        if setores is not None and loja['setor'].id not in setores:
            continue
        if not loja['grupo']:
            resultado['sem_grupo'].append(loja['setor'])
            continue
        cobranca = _reivindicar(loja, origem, user)
        if cobranca is None:
            resultado['recentes'].append(loja['setor'])
        else:
            resultado['gravadas'].append(cobranca)
    if resultado['gravadas']:
        resultado['resumo'] = mandar(resultado['gravadas'])
    return resultado


def tarefa_diaria():
    """15h (core/agendador_diario.py): relê o SAP, procura os chamados e cobra as lojas."""
    cfg = ConfiguracaoRenovaSap.get()
    if not cfg.cobranca_ativa:
        return 'Cobrança de Renova desligada.'
    try:
        ler_do_sap()
    except mysql.SapIndisponivel as exc:
        # Sem o SAP agora, cobra com o que já está no espelho — mas procura os chamados de novo.
        logger.warning('Visão Renova: SAP indisponível às 15h (%s); cobrando com o espelho.', exc)
        casar_chamados()
    r = cobrar(CobrancaRenova.AUTOMATICA, cfg=cfg)
    if r['sem_canal']:
        return 'WhatsApp (Evolution) não configurado neste servidor.'
    return (f"{len(r['gravadas'])} loja(s) cobradas ({r['resumo']['enviados']} enviadas, "
            f"{r['resumo']['falhas']} falhas); {len(r['recentes'])} cobradas há pouco; "
            f"{len(r['sem_grupo'])} sem grupo; {r['sem_loja']} venda(s) sem loja.")
