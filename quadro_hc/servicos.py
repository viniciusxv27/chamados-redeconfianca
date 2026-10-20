"""Regras do Quadro de HC: montar o quadro, simular, aplicar, agendar e lembrar."""
from __future__ import annotations

import logging
import unicodedata
from collections import Counter, defaultdict
from datetime import date, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from users.models import Sector, User, UserChangeLog

from .models import AjusteHC, Carteira, HCPrevisto, MovimentacaoHC, ObservacaoHC, VagaHC

logger = logging.getLogger(__name__)

# Janela de experiência: 45 e 90 dias contando o 1º dia (igual à planilha do quadro).
EXPERIENCIA_1, EXPERIENCIA_2 = 44, 89
LEMBRETE_DIAS_ANTES = 2
CAMPOS_HISTORICO = ('sector', 'job_title', 'first_name', 'last_name', 'demission_date', 'is_active')


def pode_acessar(user) -> bool:
    return bool(user and user.is_authenticated
                and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def _norm(texto) -> str:
    texto = unicodedata.normalize('NFKD', str(texto or ''))
    return ' '.join(''.join(c for c in texto if not unicodedata.combining(c)).upper().split())


# ---------------------------------------------------------------------------
# Cargos
# ---------------------------------------------------------------------------
# (ordem, grupo) — a ordem da planilha do quadro: gerente, operacional, adm,
# recepção, consultor pleno, consultor; o resto vem depois.
def grupo_do_cargo(cargo) -> tuple[int, str]:
    n = _norm(cargo)
    if not n:
        return 9, 'Sem cargo'
    if 'GERENTE' in n and 'OPERACIONAL' in n:
        return 2, 'Gerente operacional'
    if 'GERENTE' in n:
        return 1, 'Gerente'
    if 'COORDENADOR' in n or 'SUPERVISOR' in n:
        return 0, 'Coordenação'
    if 'ADMINISTRATIV' in n or n == 'ADM' or 'BKO' in n:
        return 3, 'Administrativo'
    if 'RECEPCION' in n:
        return 4, 'Recepção'
    if 'CONSULTOR' in n and (' PL' in f' {n}' or 'PLENO' in n or 'SENIOR' in n or n.endswith(' PL')):
        return 5, 'Consultor pleno'
    if 'CONSULTOR' in n:
        return 6, 'Consultor'
    if 'SNIPER' in n or 'ITINERANTE' in n:
        return 7, 'Sniper / itinerante'
    return 8, 'Outros'


def cargos_disponiveis() -> list[str]:
    """Os cargos que existem no cadastro (e nas vagas), sem repetir por espaço ou caixa."""
    vistos = {}
    fontes = list(User.objects.filter(is_active=True).exclude(job_title='').values_list('job_title', flat=True))
    fontes += list(VagaHC.objects.filter(fechada_em__isnull=True).values_list('cargo', flat=True))
    for c in fontes:
        limpo = ' '.join(str(c).split())
        if limpo:
            vistos.setdefault(_norm(limpo), limpo)
    return sorted(vistos.values(), key=lambda c: (grupo_do_cargo(c)[0], _norm(c)))


# ---------------------------------------------------------------------------
# Quem está no quadro
# ---------------------------------------------------------------------------
# Grupos de comunicação que o portal usa para "gerente da loja" (o mesmo GERENTES
# de comissão, contestação e caixa; "Gerente / ADM" é o das lojas com gerente-adm).
GRUPOS_DE_GERENTE = ('GERENTES', 'GERENTE / ADM')
STATUS_TRABALHANDO = (User.STATUS_ATIVO, User.STATUS_AFASTADO, User.STATUS_FERIAS)


def ativos(hoje=None):
    """Quem está no quadro: login ativo e não desligado.

    Desligado é quem tem demissão até hoje **e** status que não é de quem
    trabalha. Em 20/10/2026 eram 20 pessoas com login ativo, status ATIVO,
    entrando no portal na semana — e demissão antiga no cadastro (quase todas
    01/11/2024, algumas antes da própria admissão). O quadro as escondia, 7
    delas gerentes: daí as lojas "sem gerente". Agora elas aparecem, marcadas
    para conferir a demissão.
    """
    hoje = hoje or timezone.localdate()
    return (User.objects.filter(is_active=True)
            .filter(Q(demission_date__isnull=True) | Q(demission_date__gt=hoje)
                    | Q(status__in=STATUS_TRABALHANDO)))


def ids_gerentes_por_grupo() -> set:
    nomes = Q()
    for n in GRUPOS_DE_GERENTE:
        nomes |= Q(communication_groups__name__iexact=n)
    return set(User.objects.filter(is_active=True).filter(nomes).values_list('id', flat=True))


def funcao_da_pessoa(u, ajuste=None, gerentes=frozenset()) -> tuple[int, str, str]:
    """(ordem, grupo, de onde veio): o ajuste manual manda; depois o grupo GERENTES na loja; depois o cargo."""
    if ajuste is not None and ajuste.funcao:
        return _ORDEM_POR_GRUPO.get(ajuste.funcao, 8), ajuste.funcao, 'manual'
    ordem, grupo = grupo_do_cargo(u.job_title)
    if u.id in gerentes and ordem not in (1, 2) and u.sector_id and e_loja(u.sector):
        # Está no grupo de gerentes e lotado numa loja: é o gerente dela, seja qual for o cargo escrito.
        return 1, 'Gerente', 'grupo'
    return ordem, grupo, 'cargo'


def _login(valor) -> str:
    """O login veio de planilha como número: "81079439.0" vira "81079439"."""
    texto = str(valor or '').strip()
    if texto.endswith('.0') and texto[:-2].isdigit():
        return texto[:-2]
    return texto


def e_loja(setor) -> bool:
    return bool(setor.adabas) or _norm(setor.name).startswith('LOJA')


def nome_completo(u) -> str:
    return (u.get_full_name() or u.username or '').strip()


def _quem(u) -> str:
    return nome_completo(u) if u else 'sistema'


def historico_por_pessoa(ids, limite=6) -> dict:
    """{user_id: [frases do mais novo para o mais velho]} — movimentações do quadro e alterações do cadastro."""
    eventos = defaultdict(list)
    for m in (MovimentacaoHC.objects.filter(colaborador_id__in=ids, situacao=MovimentacaoHC.APLICADA)
              .select_related('aplicado_por', 'setor_origem', 'novo_setor').order_by('-aplicado_em')):
        eventos[m.colaborador_id].append((m.aplicado_em, f'{m.resumo} — por {_quem(m.aplicado_por)}', 'quadro'))
    rotulos = {'sector': 'Loja', 'job_title': 'Cargo', 'first_name': 'Nome', 'last_name': 'Sobrenome',
               'demission_date': 'Demissão', 'is_active': 'Login ativo'}
    for log in (UserChangeLog.objects.filter(target_id__in=ids, field__in=CAMPOS_HISTORICO)
                .select_related('changed_by').order_by('-created_at')):
        frase = f'{rotulos.get(log.field, log.field_label)}: {log.old_value} → {log.new_value} — por {_quem(log.changed_by)}'
        eventos[log.target_id].append((log.created_at, frase, 'cadastro'))
    saida = {}
    for uid, lista in eventos.items():
        lista.sort(key=lambda e: e[0], reverse=True)
        # A movimentação do quadro também gera linhas no cadastro (mesmo instante): fica só a do quadro.
        filtrada, instantes_quadro = [], [e[0] for e in lista if e[2] == 'quadro']
        for quando, frase, origem in lista:
            if origem == 'cadastro' and any(abs((quando - q).total_seconds()) < 5 for q in instantes_quadro):
                continue
            filtrada.append({'quando': timezone.localtime(quando).strftime('%d/%m/%Y %H:%M'), 'frase': frase,
                             'origem': origem})
        saida[uid] = filtrada[:limite]
    return saida


def _pessoa(u, obs, hist, agendadas, hoje, ajuste=None, gerentes=frozenset()):
    adm = u.admission_date
    exp1 = adm + timedelta(days=EXPERIENCIA_1) if adm else None
    exp2 = adm + timedelta(days=EXPERIENCIA_2) if adm else None

    def alerta(d):
        if not d:
            return ''
        if hoje <= d <= hoje + timedelta(days=7):
            return 'proxima'
        return 'passou' if d < hoje else ''
    ordem, grupo, origem_funcao = funcao_da_pessoa(u, ajuste, gerentes)
    demissao_antiga = bool(u.demission_date and u.demission_date <= hoje)
    return {
        'id': u.id, 'nome': nome_completo(u), 'cargo': ' '.join((u.job_title or '').split()), 'grupo': grupo,
        'ordem': ordem, 'cidade': ' - '.join(x for x in (u.neighborhood, u.city) if x), 'login': _login(u.login_code),
        'nascimento': u.birth_date, 'contratacao': adm, 'exp1': exp1, 'exp2': exp2,
        'exp1_alerta': alerta(exp1), 'exp2_alerta': alerta(exp2), 'telefone': u.phone,
        'observacao': obs.get(u.id, ''), 'historico': hist.get(u.id, []), 'agendadas': agendadas.get(u.id, []),
        'sai_em': u.demission_date if not demissao_antiga else None, 'demissao_antiga': u.demission_date if demissao_antiga else None,
        'origem_funcao': origem_funcao, 'cargo_grupo': grupo_do_cargo(u.job_title)[1],
        'ajuste': ({'funcao': ajuste.funcao, 'setor': ajuste.setor_id, 'setor_nome': ajuste.setor.name if ajuste.setor else '',
                    'situacao': ajuste.situacao, 'motivo': ajuste.motivo} if ajuste else None),
        'status': u.get_status_display() if u.status != User.STATUS_ATIVO else '',
        'novo': bool(adm and adm >= hoje - timedelta(days=30)),
        'setor_id': (ajuste.setor_id if ajuste and ajuste.setor_id else u.sector_id), 'setor_cadastro': u.sector_id,
        'iniciais': ''.join(x[:1] for x in nome_completo(u).split()[:2]).upper() or '?',
    }


def montar_quadro(hoje=None) -> dict:
    """Carteira → loja → pessoas (ordenadas por cargo), com vagas, observações e histórico."""
    hoje = hoje or timezone.localdate()
    ajustes = {a.colaborador_id: a for a in AjusteHC.objects.select_related('setor')}
    incluir = [cid for cid, a in ajustes.items() if a.situacao == AjusteHC.INCLUIR]
    pessoas = list(User.objects.filter(Q(pk__in=ativos(hoje).values('pk')) | Q(pk__in=incluir))
                   .select_related('sector').order_by('first_name', 'last_name'))
    fora = [u for u in pessoas if getattr(ajustes.get(u.id), 'situacao', '') == AjusteHC.EXCLUIR]
    pessoas = [u for u in pessoas if u not in fora]
    gerentes = ids_gerentes_por_grupo()
    ids = [p.id for p in pessoas]
    obs = dict(ObservacaoHC.objects.filter(colaborador_id__in=ids).values_list('colaborador_id', 'texto'))
    hist = historico_por_pessoa(ids)
    agendadas = defaultdict(list)
    for m in MovimentacaoHC.objects.filter(situacao=MovimentacaoHC.AGENDADA, colaborador_id__in=ids):
        agendadas[m.colaborador_id].append({'id': m.id, 'data': m.data, 'resumo': m.resumo})
    vagas = defaultdict(list)
    for v in VagaHC.objects.filter(fechada_em__isnull=True).select_related('criado_por'):
        vagas[v.setor_id].append(v)

    por_setor = defaultdict(list)
    sem_loja = []
    for u in pessoas:
        linha = _pessoa(u, obs, hist, agendadas, hoje, ajustes.get(u.id), gerentes)
        (por_setor[linha['setor_id']] if linha['setor_id'] else sem_loja).append(linha)
    previsto = defaultdict(dict)
    for h in HCPrevisto.objects.filter(quantidade__gt=0):
        previsto[h.setor_id][h.funcao] = h.quantidade

    def bloco(setor):
        gente = sorted(por_setor.get(setor.id, []), key=lambda p: (p['ordem'], _norm(p['cargo']), _norm(p['nome'])))
        contagem = Counter(p['grupo'] for p in gente)
        abertas = vagas.get(setor.id, [])
        prev = previsto.get(setor.id, {})
        comparacao = [{'grupo': g, 'real': contagem.get(g, 0), 'previsto': prev[g], 'falta': prev[g] - contagem.get(g, 0)}
                      for g in sorted(prev, key=grupo_ordem)]
        return {'setor': setor, 'pessoas': gente, 'vagas': abertas, 'total': len(gente),
                'resumo': sorted(contagem.items(), key=lambda kv: grupo_ordem(kv[0])),
                # Gerente de vendas ou operacional — pelo cargo, pelo grupo GERENTES ou por ajuste manual.
                'sem_gerente': e_loja(setor) and not any(p['ordem'] in (1, 2) for p in gente),
                'previsto': prev, 'comparacao': comparacao, 'total_previsto': sum(prev.values()),
                'falta': sum(max(c['falta'], 0) for c in comparacao)}

    carteiras = []
    usados = set()
    for c in Carteira.objects.prefetch_related('lojas').select_related('coordenador'):
        lojas = sorted(c.lojas.all(), key=lambda s: _norm(s.name))
        usados.update(s.id for s in lojas)
        blocos = [bloco(s) for s in lojas]
        carteiras.append({'id': c.id, 'nome': c.nome, 'coordenacao': c.coordenacao, 'lojas': blocos,
                          'total': sum(b['total'] for b in blocos), 'vagas': sum(len(b['vagas']) for b in blocos)})
    restantes = [s for s in Sector.objects.order_by('name') if s.id not in usados]
    lojas_soltas = [bloco(s) for s in restantes if e_loja(s) and (por_setor.get(s.id) or vagas.get(s.id))]
    apoio = [bloco(s) for s in restantes if not e_loja(s) and (por_setor.get(s.id) or vagas.get(s.id))]
    if lojas_soltas:
        carteiras.append({'id': 0, 'nome': 'Lojas sem carteira', 'coordenacao': '', 'lojas': lojas_soltas,
                          'total': sum(b['total'] for b in lojas_soltas),
                          'vagas': sum(len(b['vagas']) for b in lojas_soltas)})
    if apoio:
        carteiras.append({'id': -1, 'nome': 'Escritório e áreas de apoio', 'coordenacao': '', 'lojas': apoio,
                          'total': sum(b['total'] for b in apoio), 'vagas': sum(len(b['vagas']) for b in apoio)})
    todas = [b for c in carteiras for b in c['lojas']]
    return {'carteiras': carteiras, 'sem_loja': sem_loja, 'total': len(pessoas),
            'fora': [{'id': u.id, 'nome': nome_completo(u), 'cargo': u.job_title or '', 'loja': _loja(u.sector),
                      'motivo': ajustes[u.id].motivo} for u in fora],
            'demissao_antiga': sum(1 for b in todas for p in b['pessoas'] if p['demissao_antiga'])
            + sum(1 for p in sem_loja if p['demissao_antiga']),
            'ajustes': sum(1 for a in ajustes.values() if a.funcao or a.setor_id or a.situacao != AjusteHC.AUTO),
            'sem_gerente': sum(1 for b in todas if b['sem_gerente']),
            'exp_proximas': sum(1 for b in todas for p in b['pessoas']
                                if 'proxima' in (p['exp1_alerta'], p['exp2_alerta'])),
            'vagas': sum(len(v) for v in vagas.values()),
            'lojas': sum(1 for c in carteiras for b in c['lojas'] if e_loja(b['setor']))}


_ORDEM_GRUPOS = ['Coordenação', 'Gerente', 'Gerente operacional', 'Administrativo', 'Recepção', 'Consultor pleno',
                 'Consultor', 'Sniper / itinerante', 'Outros', 'Sem cargo']


def grupo_ordem(grupo):
    return _ORDEM_GRUPOS.index(grupo) if grupo in _ORDEM_GRUPOS else 99


# Ordem da tabela (a do grupo_do_cargo) para a função escolhida à mão.
_ORDEM_POR_GRUPO = {'Coordenação': 0, 'Gerente': 1, 'Gerente operacional': 2, 'Administrativo': 3, 'Recepção': 4,
                    'Consultor pleno': 5, 'Consultor': 6, 'Sniper / itinerante': 7, 'Outros': 8, 'Sem cargo': 9}


# ---------------------------------------------------------------------------
# Ajuste manual
# ---------------------------------------------------------------------------
def salvar_ajuste(dados: dict, autor) -> AjusteHC | None:
    pessoa = User.objects.filter(pk=dados.get('colaborador') or 0).first()
    if not pessoa:
        raise ErroQuadro('Colaborador não encontrado.')
    funcao = str(dados.get('funcao') or '').strip()
    if funcao and funcao not in _ORDEM_POR_GRUPO:
        raise ErroQuadro('Função inválida.')
    situacao = str(dados.get('situacao') or AjusteHC.AUTO)
    if situacao not in dict(AjusteHC.SITUACOES):
        raise ErroQuadro('Situação inválida.')
    setor = Sector.objects.filter(pk=dados.get('setor')).first() if str(dados.get('setor') or '').isdigit() else None
    if setor and setor.id == pessoa.sector_id:
        setor = None
    motivo = str(dados.get('motivo') or '').strip()[:300]
    if not funcao and not setor and situacao == AjusteHC.AUTO:
        AjusteHC.objects.filter(colaborador=pessoa).delete()
        return None
    if not motivo:
        raise ErroQuadro('Diga o motivo do ajuste (fica registrado no quadro).')
    ajuste, _ = AjusteHC.objects.update_or_create(colaborador=pessoa, defaults={
        'funcao': funcao, 'setor': setor, 'situacao': situacao, 'motivo': motivo, 'atualizado_por': autor})
    return ajuste


def salvar_previsto(setor_id, quantidades: dict, autor) -> int:
    setor = Sector.objects.filter(pk=setor_id or 0).first()
    if not setor:
        raise ErroQuadro('Loja não encontrada.')
    total = 0
    for funcao, valor in (quantidades or {}).items():
        if funcao not in _ORDEM_POR_GRUPO:
            continue
        try:
            n = max(0, min(int(valor or 0), 99))
        except (TypeError, ValueError):
            raise ErroQuadro(f'Quantidade inválida para {funcao}.')
        if n:
            HCPrevisto.objects.update_or_create(setor=setor, funcao=funcao, defaults={'quantidade': n, 'atualizado_por': autor})
        else:
            HCPrevisto.objects.filter(setor=setor, funcao=funcao).delete()
        total += n
    return total


# ---------------------------------------------------------------------------
# Simulação
# ---------------------------------------------------------------------------
class ErroQuadro(Exception):
    """Mensagem para a tela."""


def ler_movimentacao(dados: dict, autor=None) -> MovimentacaoHC:
    """Monta (sem gravar) a movimentação a partir do que veio da tela."""
    hoje = timezone.localdate()
    try:
        data = date.fromisoformat(str(dados.get('data') or hoje.isoformat())[:10])
    except ValueError:
        raise ErroQuadro('Data inválida.')
    colaborador = None
    if dados.get('colaborador'):
        colaborador = User.objects.filter(pk=dados.get('colaborador')).select_related('sector').first()
        if not colaborador:
            raise ErroQuadro('Colaborador não encontrado.')
    novo_setor = None
    if dados.get('novo_setor'):
        novo_setor = Sector.objects.filter(pk=dados.get('novo_setor')).first()
        if not novo_setor:
            raise ErroQuadro('Loja não encontrada.')
    m = MovimentacaoHC(
        colaborador=colaborador, data=data, novo_setor=novo_setor,
        novo_cargo=' '.join(str(dados.get('novo_cargo') or '').split())[:100],
        novo_nome=' '.join(str(dados.get('novo_nome') or '').split())[:150],
        desligar=bool(dados.get('desligar')), lembrete=str(dados.get('lembrete') or '').strip()[:200],
        observacao=str(dados.get('observacao') or '').strip()[:500], criado_por=autor)
    if colaborador:
        m.setor_origem = colaborador.sector
        m.cargo_origem = colaborador.job_title or ''
        m.nome_origem = nome_completo(colaborador)
        # O que não muda não entra.
        if m.novo_setor and m.novo_setor.id == colaborador.sector_id:
            m.novo_setor = None
        if m.novo_cargo and _norm(m.novo_cargo) == _norm(colaborador.job_title):
            m.novo_cargo = ''
        if m.novo_nome and _norm(m.novo_nome) == _norm(nome_completo(colaborador)):
            m.novo_nome = ''
    if not colaborador and not m.lembrete:
        raise ErroQuadro('Escolha o colaborador (ou escreva um lembrete).')
    if colaborador and not (m.novo_setor or m.novo_cargo or m.novo_nome or m.desligar or m.lembrete):
        raise ErroQuadro('Nada muda: escolha a nova loja, o novo cargo, o nome, o desligamento ou um lembrete.')
    if m.desligar and (m.novo_setor or m.novo_cargo):
        raise ErroQuadro('Desligamento não se combina com mudança de loja ou de cargo.')
    m.resumo = descrever(m)
    return m


def _loja(s):
    return s.name if s else 'sem loja'


def descrever(m: MovimentacaoHC) -> str:
    """A frase do histórico: "João veio como CONSULTOR da Loja Montserrat"."""
    quem = m.nome_origem or 'Lembrete'
    if m.desligar:
        return f'{quem} desligado(a) da {_loja(m.setor_origem)} ({m.cargo_origem or "sem cargo"})'
    partes = []
    if m.novo_setor and m.novo_cargo:
        partes.append(f'foi para a {_loja(m.novo_setor)} como {m.novo_cargo} (era {m.cargo_origem or "sem cargo"} '
                      f'da {_loja(m.setor_origem)})')
    elif m.novo_setor:
        partes.append(f'veio como {m.cargo_origem or "sem cargo"} da {_loja(m.setor_origem)} para a {_loja(m.novo_setor)}')
    elif m.novo_cargo:
        partes.append(f'passou de {m.cargo_origem or "sem cargo"} a {m.novo_cargo} na {_loja(m.setor_origem)}')
    if m.novo_nome:
        partes.append(f'nome corrigido para {m.novo_nome}')
    if m.lembrete:
        partes.append(m.lembrete)
    return (f'{quem} ' + '; '.join(partes))[:300] if m.colaborador_id or m.nome_origem else (m.lembrete or '')[:300]


def _contagem_por_loja(data, ignorar_id=None) -> dict:
    """{setor_id: Counter(grupo)} como o quadro estará na data, com as agendadas até lá."""
    contagem = defaultdict(Counter)
    estado = {u.id: (u.sector_id, grupo_do_cargo(u.job_title)[1]) for u in ativos(timezone.localdate())}
    agendadas = MovimentacaoHC.objects.filter(situacao=MovimentacaoHC.AGENDADA, data__lte=data,
                                              colaborador__isnull=False).order_by('data', 'id')
    if ignorar_id:
        agendadas = agendadas.exclude(pk=ignorar_id)
    for m in agendadas:
        if m.colaborador_id not in estado:
            continue
        setor, grupo = estado[m.colaborador_id]
        if m.desligar:
            estado.pop(m.colaborador_id)
            continue
        estado[m.colaborador_id] = (m.novo_setor_id or setor, grupo_do_cargo(m.novo_cargo)[1] if m.novo_cargo else grupo)
    for setor, grupo in estado.values():
        contagem[setor][grupo] += 1
    return contagem, estado


def simular(m: MovimentacaoHC) -> dict:
    """Antes x depois: o que muda no cadastro e o efeito no quadro das lojas envolvidas."""
    hoje = timezone.localdate()
    agendar = m.data > hoje
    mudancas = []
    c = m.colaborador
    if c:
        if m.novo_setor:
            mudancas.append({'campo': 'Loja', 'antes': _loja(c.sector), 'depois': m.novo_setor.name})
        if m.novo_cargo:
            mudancas.append({'campo': 'Cargo', 'antes': c.job_title or '—', 'depois': m.novo_cargo})
        if m.novo_nome:
            mudancas.append({'campo': 'Nome', 'antes': nome_completo(c), 'depois': m.novo_nome})
        if m.desligar:
            mudancas.append({'campo': 'Situação', 'antes': 'Ativo', 'depois': f'Desligado em {m.data:%d/%m/%Y}'})
    impacto, avisos = [], []
    if c and (m.novo_setor or m.novo_cargo or m.desligar):
        contagem, estado = _contagem_por_loja(m.data, ignorar_id=m.pk)
        setor_atual, grupo_atual = estado.get(c.id, (c.sector_id, grupo_do_cargo(c.job_title)[1]))
        depois = {k: Counter(v) for k, v in contagem.items()}
        if c.id in estado:
            depois.setdefault(setor_atual, Counter())[grupo_atual] -= 1
        if not m.desligar:
            destino = m.novo_setor.id if m.novo_setor else setor_atual
            grupo_novo = grupo_do_cargo(m.novo_cargo)[1] if m.novo_cargo else grupo_atual
            depois.setdefault(destino, Counter())[grupo_novo] += 1
        envolvidos = [s for s in dict.fromkeys([setor_atual, m.novo_setor.id if m.novo_setor else None]) if s]
        setores = {s.id: s for s in Sector.objects.filter(id__in=envolvidos)}
        for sid in envolvidos:
            antes_c, depois_c = contagem.get(sid, Counter()), depois.get(sid, Counter())
            grupos = sorted(set(antes_c) | set(depois_c), key=grupo_ordem)
            impacto.append({'loja': setores[sid].name if sid in setores else 'sem loja',
                            'linhas': [{'grupo': g, 'antes': antes_c.get(g, 0), 'depois': depois_c.get(g, 0)}
                                       for g in grupos if antes_c.get(g, 0) or depois_c.get(g, 0)],
                            'total_antes': sum(antes_c.values()), 'total_depois': sum(depois_c.values())})
            s = setores.get(sid)
            if s and e_loja(s) and antes_c.get('Gerente', 0) and not depois_c.get('Gerente', 0):
                avisos.append(f'A {s.name} fica sem gerente.')
        if m.novo_setor and grupo_do_cargo(m.novo_cargo or c.job_title)[1] == 'Gerente' \
                and contagem.get(m.novo_setor.id, Counter()).get('Gerente', 0):
            avisos.append(f'A {m.novo_setor.name} já tem gerente.')
    if c:
        outras = MovimentacaoHC.objects.filter(colaborador=c, situacao=MovimentacaoHC.AGENDADA).exclude(pk=m.pk)
        for o in outras:
            avisos.append(f'Já existe movimentação agendada para {o.data:%d/%m/%Y}: {o.resumo}')
        if c.demission_date and not m.desligar:
            avisos.append(f'Esta pessoa já tem demissão marcada para {c.demission_date:%d/%m/%Y}.')
    if m.novo_cargo and _norm(m.novo_cargo) not in {_norm(x) for x in cargos_disponiveis()}:
        avisos.append(f'"{m.novo_cargo}" é um cargo novo: ainda não existe no cadastro.')
    return {'resumo': m.resumo, 'agendar': agendar, 'data': m.data.isoformat(),
            'quando': (f'Agendada para {m.data:%d/%m/%Y} (lembrete em {m.data - timedelta(days=LEMBRETE_DIAS_ANTES):%d/%m})'
                       if agendar else 'Aplicada agora no cadastro'),
            'mudancas': mudancas, 'impacto': impacto, 'avisos': avisos}


# ---------------------------------------------------------------------------
# Aplicar / agendar / cancelar
# ---------------------------------------------------------------------------
def aplicar(m: MovimentacaoHC, autor, ip=None) -> MovimentacaoHC:
    """Grava no cadastro (com o histórico de alterações) e marca a movimentação como aplicada."""
    from users.auditoria import fotografar, registrar

    with transaction.atomic():
        c = m.colaborador
        if c and (m.novo_setor or m.novo_cargo or m.novo_nome or m.desligar):
            c = User.objects.select_for_update().get(pk=c.pk)
            antes = fotografar(c)
            anterior = c.sector
            if m.novo_setor:
                c.sector = m.novo_setor
            if m.novo_cargo:
                c.job_title = m.novo_cargo
            if m.novo_nome:
                partes = m.novo_nome.split(' ', 1)
                c.first_name, c.last_name = partes[0][:150], (partes[1] if len(partes) > 1 else '')[:150]
            if m.desligar:
                c.demission_date = m.data
                c.is_active = False
                c.status = User.STATUS_INATIVO
                c.inactivation_reason = ('Desligamento pelo Quadro de HC'
                                         + (f': {m.observacao}' if m.observacao else '')).strip()
            c.save()
            if m.novo_setor:
                if anterior and anterior.id != m.novo_setor.id:
                    c.sectors.remove(anterior)
                c.sectors.add(m.novo_setor)
            registrar(c, antes, autor, ip=ip)
            if m.desligar:
                MovimentacaoHC.objects.filter(colaborador=c, situacao=MovimentacaoHC.AGENDADA).exclude(pk=m.pk) \
                    .update(situacao=MovimentacaoHC.CANCELADA, cancelado_em=timezone.now(), cancelado_por=autor)
        m.situacao = MovimentacaoHC.APLICADA
        m.aplicado_por = autor
        m.aplicado_em = timezone.now()
        m.save()
    return m


def registrar_movimentacao(dados: dict, autor, ip=None) -> tuple[MovimentacaoHC, dict]:
    """Confirma o que foi simulado: aplica hoje (ou data passada) ou agenda."""
    m = ler_movimentacao(dados, autor)
    sim = simular(m)
    m.simulacao = sim
    if sim['agendar']:
        m.situacao = MovimentacaoHC.AGENDADA
        m.save()
    else:
        m.save()
        aplicar(m, autor, ip)
    return m, sim


def aplicar_agendada(m: MovimentacaoHC, autor, ip=None):
    if m.situacao != MovimentacaoHC.AGENDADA:
        raise ErroQuadro('Esta movimentação não está agendada.')
    return aplicar(m, autor, ip)


def cancelar(m: MovimentacaoHC, autor):
    if m.situacao != MovimentacaoHC.AGENDADA:
        raise ErroQuadro('Só dá para cancelar movimentação agendada.')
    m.situacao = MovimentacaoHC.CANCELADA
    m.cancelado_por = autor
    m.cancelado_em = timezone.now()
    m.save(update_fields=['situacao', 'cancelado_por', 'cancelado_em'])


# ---------------------------------------------------------------------------
# Lembretes (agendador diário, 08:00)
# ---------------------------------------------------------------------------
def _destinos(m):
    gente = list(User.objects.filter(is_active=True).filter(Q(hierarchy='SUPERADMIN') | Q(is_superuser=True)))
    if m.criado_por and m.criado_por.is_active and m.criado_por not in gente:
        gente.append(m.criado_por)
    return gente


def _avisar(m, titulo, mensagem):
    try:
        from notifications.services import NotificationChannel, NotificationType, notification_service
        canais = [NotificationChannel.IN_APP, NotificationChannel.PUSH]
        if notification_service.onesignal_enabled:
            canais.append(NotificationChannel.ONESIGNAL)
        notification_service.send_notification(
            _destinos(m), titulo, mensagem, notification_type=NotificationType.SYSTEM, channels=canais,
            action_url='/quadro-hc/?aba=futuras', icon='fas fa-people-arrows', extra_data={'movimentacao_hc': m.pk})
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning('Lembrete do Quadro de HC (%s) não foi enviado: %s', m.pk, exc)
        return False


def enviar_lembretes(hoje=None):
    """2 dias antes e no dia: avisa SUPERADMIN (e quem agendou) das movimentações agendadas."""
    hoje = hoje or timezone.localdate()
    agora = timezone.now()
    antes = dia = 0
    pendentes = MovimentacaoHC.objects.filter(situacao=MovimentacaoHC.AGENDADA).select_related('criado_por')
    for m in pendentes.filter(lembrete_2d_em__isnull=True, data__gt=hoje,
                              data__lte=hoje + timedelta(days=LEMBRETE_DIAS_ANTES)):
        faltam = (m.data - hoje).days
        if _avisar(m, f'Quadro de HC: movimentação em {faltam} dia(s)', f'{m.data:%d/%m/%Y}: {m.resumo}'
                   + (f' Obs.: {m.observacao}' if m.observacao else '')):
            MovimentacaoHC.objects.filter(pk=m.pk).update(lembrete_2d_em=agora)
            antes += 1
    for m in pendentes.filter(lembrete_dia_em__isnull=True, data__lte=hoje):
        if _avisar(m, 'Quadro de HC: movimentação para aplicar hoje',
                   f'{m.data:%d/%m/%Y}: {m.resumo}. Abra o Quadro de HC para aplicar.'):
            MovimentacaoHC.objects.filter(pk=m.pk).update(lembrete_dia_em=agora)
            dia += 1
    return f'{antes} lembrete(s) de 2 dias e {dia} do dia enviados.'
