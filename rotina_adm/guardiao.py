"""Missão Guardião (26/11/2026): a gamificação do ADM de loja (o "Guardião") dentro da Rotina ADM.

Especificação: "Projeto Guardião Gamificação.pdf". Ciclo mensal, 9 missões em 3 pilares, até 100 pontos:

PREVENIR (20)
  1. Renova — Conformidade SAP (5): vendas com Renova no SAP (/sap/renova/, ``VendaRenova``) da loja no mês
     que têm chamado (ou foram dispensadas). Proporcional; sem venda no mês = 5.
  2. CheckLojas Mensal (5): a loja registra aqui que fez o CheckLojas do mês (``LancamentoGuardiao``).
     Prazo: o último dia do ciclo. Feito = 5; senão 0.
  3. Melhoria Contínua e Boas Práticas (5): ideias do Inovar (Impulso) que a pessoa assinou (autora ou
     participante), criadas no mês e APROVADAS. 3 ou mais = 5; menos = 0.
  4. Evolução do Guardião (5): cursos (/cursos/) que alcançam a pessoa com prazo no mês — Curso Foco do mês
     (se ela está no escopo dos cursos) e capacitação atribuída a ela. Todos com comprovante enviado até o
     prazo (e não recusado) = 5. Sem curso no ciclo = 5 (nada a cumprir).

PROTEGER (40)
  5. Contagem LVUT (20): 4 categorias × 4 contagens no mês (``ContagemEstoque``). O mês vira 4 janelas
     (1–7, 8–14, 15–21, 22–fim); cada categoria contada dentro da janela vale 1,25. Com divergência fica
     bloqueada até a loja regularizar (até o fim do ciclo); fora da janela ou sem contagem = 0.
  6. Protocolo de Recuperação LVUT (10): divergências das contagens do mês regularizadas até o fim do ciclo
     ÷ divergências encontradas (peso = número de divergências). Sem divergência = 10 se houve contagem.
  7. Proteção Fiscal (10): vendas "Sem NF" da Visão SAP da loja com venda no mês que saíram do SAP ou foram
     dadas como resolvidas até o fim do ciclo (a marcada como resolvida que o SAP releu e continua lá não
     vale). Proporcional; sem pendência no mês = 10.

GARANTIR (40)
  8. Gestão da Agenda Semanal (20): itens do check-list diário da loja feitos até o horário + atividades da
     Rotina Gerencial da pessoa concluídas no próprio dia ÷ previstos. Dia de hoje só entra o que já foi
     feito (não perde ponto pelo que ainda dá tempo). Missões incluídas à mão na Agenda de Missões ficam
     fora da nota até a validação delas ser definida (pendência do próprio PDF).
  9. Segurança e Confiabilidade do Estoque Total (20): quem gere lança o % de conformidade do inventário
     mensal da loja; 80% ou mais = 20; abaixo ou sem lançamento = 0.

Proporcional é linear (90% de 10 = 9), com duas casas. Níveis pelo total: 0 Desenvolvimento, 60 Evolução,
70 Avançada, 90 Cumprida, 100 Excelência. Os pontos não acumulam entre meses; o mês passado se consulta
recalculando (``?mes=AAAA-MM``).

O Guardião é a pessoa com a Rotina ADM liberada e setor principal de loja (a loja é ``user.sector``).
"""
import logging
from collections import defaultdict
from datetime import date, timedelta

from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from . import ranking, servicos

logger = logging.getLogger(__name__)

PILARES = (
    ('PREVENIR', 'Prevenir', 20, 'Antecipar para proteger.'),
    ('PROTEGER', 'Proteger', 40, 'Cuidar do que construímos.'),
    ('GARANTIR', 'Garantir', 40, 'Transformar atitude em excelência.'),
)
# (chave, número, pilar, nome, descrição curta, máximo, ícone)
MISSOES = (
    ('renova', 1, 'PREVENIR', 'Renova — Conformidade SAP', 'Operações Renova com chamado no SAP', 5, 'fa-recycle'),
    ('checklojas', 2, 'PREVENIR', 'CheckLojas Mensal', 'Checklist mensal concluído no prazo', 5, 'fa-clipboard-check'),
    ('melhoria', 3, 'PREVENIR', 'Melhoria Contínua e Boas Práticas', 'Contribuições validadas no ciclo', 5, 'fa-lightbulb'),
    ('evolucao', 4, 'PREVENIR', 'Evolução do Guardião', 'Curso atribuído concluído no prazo', 5, 'fa-graduation-cap'),
    ('lvut', 5, 'PROTEGER', 'Contagem LVUT', 'Quatro pilares de contagem no detalhe', 20, 'fa-boxes'),
    ('recuperacao', 6, 'PROTEGER', 'Protocolo de Recuperação LVUT', 'Divergências regularizadas no prazo', 10, 'fa-sync-alt'),
    ('fiscal', 7, 'PROTEGER', 'Proteção Fiscal', 'Pendências fiscais regularizadas no prazo', 10, 'fa-file-invoice'),
    ('agenda', 8, 'GARANTIR', 'Gestão da Agenda Semanal', 'Atividades elegíveis concluídas no prazo', 20, 'fa-calendar-check'),
    ('estoque_total', 9, 'GARANTIR', 'Segurança e Confiabilidade do Estoque Total', 'Conformidade do inventário mensal', 20, 'fa-warehouse'),
)
NIVEIS = (
    (0, 'Missão em Desenvolvimento', 'desenvolvimento', 'fa-flag'),
    (60, 'Missão em Evolução', 'evolucao', 'fa-chart-line'),
    (70, 'Missão Avançada', 'avancada', 'fa-bullseye'),
    (90, 'Missão Cumprida', 'cumprida', 'fa-check-circle'),
    (100, 'Missão de Excelência', 'excelencia', 'fa-star'),
)
LVUT_POR_CONTAGEM = 1.25
ESTOQUE_TOTAL_MINIMO = 80
MELHORIA_MINIMO = 3
JANELAS = ((1, 7), (8, 14), (15, 21), (22, 31))
MESES = ranking.MESES


def fmt(n):
    """12.5 → '12,5'; 6.666 → '6,67'; 20.0 → '20' (como o print)."""
    if n is None:
        return '—'
    texto = f'{round(float(n), 2):.2f}'.rstrip('0').rstrip('.')
    return texto.replace('.', ',')


def proporcional(maximo, feitos, total):
    if not total:
        return 0.0
    return round(maximo * min(feitos, total) / total, 2)


def nivel(total):
    atual = NIVEIS[0]
    for n in NIVEIS:
        if total >= n[0] - 1e-9:
            atual = n
    return {'minimo': atual[0], 'nome': atual[1], 'classe': atual[2], 'icone': atual[3]}


def faixas(total):
    """As faixas do Código de Conquista (NIVEIS) com a situação de quem tem ``total`` pontos no ciclo.

    Cada faixa: numero, minimo, ate (o mínimo da seguinte − 1 ou 100), nome, classe, icone e
    estado = 'alcancada' | 'atual' | 'futura'. ``proxima`` é a faixa seguinte (None na última) e
    ``falta`` quantos pontos faltam para ela; ``pct_na_faixa`` mostra o avanço dentro da atual.
    """
    atual = nivel(total)
    lista = []
    for i, (minimo, nome, classe, icone) in enumerate(NIVEIS):
        seguinte = NIVEIS[i + 1][0] if i + 1 < len(NIVEIS) else None
        estado = 'atual' if minimo == atual['minimo'] else ('alcancada' if minimo < atual['minimo'] else 'futura')
        lista.append({'numero': i + 1, 'minimo': minimo, 'ate': (seguinte - 1) if seguinte is not None else 100,
                      'nome': nome, 'classe': classe, 'icone': icone, 'estado': estado, 'ultima': seguinte is None})
    i_atual = next(i for i, f in enumerate(lista) if f['estado'] == 'atual')
    proxima = lista[i_atual + 1] if i_atual + 1 < len(lista) else None
    falta = max(proxima['minimo'] - total, 0) if proxima else 0
    if proxima:
        largura = proxima['minimo'] - lista[i_atual]['minimo']
        pct = round(min(max((total - lista[i_atual]['minimo']) * 100 / largura, 0), 100), 1) if largura else 100
    else:
        pct = 100
    return {'lista': lista, 'atual': lista[i_atual], 'proxima': proxima, 'falta': falta, 'falta_txt': fmt(falta),
            'pct_na_faixa': pct, 'total_txt': fmt(total), 'pct_total': round(min(max(total, 0), 100), 1)}


def periodo(mes_texto=None, hoje=None):
    """(início, fim, hoje) do ciclo. ``mes_texto`` 'AAAA-MM'; inválido ou futuro = o mês de hoje."""
    hoje = hoje or timezone.localdate()
    inicio, fim, _ate = ranking.periodo(mes_texto, hoje)
    return inicio, fim, hoje


def janelas(inicio, fim):
    """As 4 janelas de contagem LVUT do mês: [(n, de, até)]."""
    return [(n, inicio.replace(day=a), min(inicio.replace(day=min(b, fim.day)), fim))
            for n, (a, b) in enumerate(JANELAS, start=1)]


def _dia(dt):
    return timezone.localtime(dt).date() if dt else None


# ── Os dados do ciclo. Para uma pessoa, cada coisa se busca na hora; o ranking chama ``preparar``
#    com todos os Guardiões e cada busca vira uma consulta só para todo mundo (≈12 no total). ──
class Base:
    def __init__(self, inicio, fim, hoje):
        self.inicio, self.fim, self.hoje = inicio, fim, hoje
        self.ate = min(fim, hoje)
        self._pdvs = None
        self._corte = False
        self._cfg_cursos = None
        self._lancamentos = None
        self._focos = None
        self._lote = None

    @property
    def aberto(self):
        return self.hoje <= self.fim

    # Visão SAP
    def _mapa_pdvs(self):
        from auditoria_sap.models import LinhaAuditoria
        from auditoria_sap.permissions import normalizar_loja
        if self._pdvs is None:
            self._pdvs = defaultdict(list)
            for p in LinhaAuditoria.objects.exclude(pdv='').order_by().values_list('pdv', flat=True).distinct():
                self._pdvs[normalizar_loja(p)].append(p)
        return self._pdvs

    def pdvs(self, loja):
        """PDVs do SAP que casam com a loja (mesma normalização da Visão SAP); None se a Visão SAP está fora."""
        try:
            from auditoria_sap.permissions import normalizar_loja
            return self._mapa_pdvs().get(normalizar_loja(loja.name), [])
        except Exception:  # noqa: BLE001
            logger.warning('Missão Guardião: PDVs do SAP fora', exc_info=True)
            return None

    def corte(self):
        if self._corte is False:
            try:
                from auditoria_sap.models import inicio_da_ultima_leitura
                self._corte = inicio_da_ultima_leitura()
            except Exception:  # noqa: BLE001
                self._corte = None
        return self._corte

    def _q_fiscal(self, pdvs):
        from auditoria_sap.models import LinhaAuditoria, q_da_tela, q_valor_desprezivel
        return (LinhaAuditoria.objects.filter(q_da_tela('sem_nf'), pdv__in=list(pdvs),
                                              data_venda__range=(self.inicio, self.fim))
                .exclude(q_valor_desprezivel()))

    def linhas_fiscais(self, loja, pdvs):
        campos = ('ativa', 'sumiu_em', 'resolvida', 'resolvida_em')
        if self._lote is not None and 'fiscal' in self._lote:
            return [l[1:] for p in pdvs for l in self._lote['fiscal'].get(p, [])]
        return list(self._q_fiscal(pdvs).values_list(*campos))

    def renova(self, loja):
        from auditoria_sap.models import VendaRenova
        if self._lote is not None and 'renova' in self._lote:
            return self._lote['renova'].get(loja.id, [])
        return list(VendaRenova.objects.filter(setor=loja, ativa=True, data_fatura__range=(self.inicio, self.fim))
                    .values_list('chamado_id', 'dispensada'))

    # Rotina ADM
    def lancamento(self, loja, missao):
        from .models import LancamentoGuardiao
        if self._lancamentos is None:
            self._lancamentos = {(l.loja_id, l.missao): l for l in
                                 LancamentoGuardiao.objects.filter(competencia=self.inicio).select_related('lancado_por')}
        return self._lancamentos.get((loja.id, missao))

    def contagens(self, loja):
        from .models import ContagemEstoque
        if self._lote is not None:
            return self._lote['contagens'].get(loja.id, [])
        return list(ContagemEstoque.objects.filter(loja=loja, data__range=(self.inicio, self.fim))
                    .select_related('feito_por', 'regularizada_por').order_by('data', 'feito_em'))

    def checklist(self, loja):
        """(Previstos, [(item, horário, dia, feito_em)]) do check-list da loja no ciclo, até hoje."""
        from .models import MarcacaoChecklist
        if self._lote is not None:
            return self._lote['previstos'], self._lote['marcacoes'].get(loja.id, [])
        marc = list(MarcacaoChecklist.objects.filter(item__loja=loja, data__range=(self.inicio, self.ate))
                    .values_list('item_id', 'item__horario', 'data', 'feito_em'))
        return ranking.Previstos({loja.id}), marc

    def rotina(self, pessoa):
        """(rotina ou None, atividades, {(atividade, dia): dia em que foi concluída})."""
        from rotina.models import ConclusaoAtividade, RotinaGerencial
        if self._lote is not None:
            return self._lote['rotinas'].get(pessoa.id, (None, [], {}))
        rotina = RotinaGerencial.objects.filter(user=pessoa, ativa=True).first()
        if rotina is None:
            return None, [], {}
        concl = {(a, d): _dia(c) for a, d, c in ConclusaoAtividade.objects.filter(
            atividade__rotina=rotina, data__range=(self.inicio, self.ate)).values_list('atividade_id', 'data', 'criado_em')}
        return rotina, list(rotina.atividades.all()), concl

    # Impulso (Inovar)
    def ideias(self, pessoa):
        from impulso.models import Ideia
        if self._lote is not None and 'ideias' in self._lote:
            return self._lote['ideias'].get(pessoa.id, [])
        return list(Ideia.objects.filter(Q(autor=pessoa) | Q(participantes=pessoa),
                                         criado_em__date__gte=self.inicio, criado_em__date__lte=self.fim)
                    .distinct().values_list('status', flat=True))

    # Cursos
    def cfg_cursos(self):
        if self._cfg_cursos is None:
            from cursos.models import ConfiguracaoCursos
            self._cfg_cursos = ConfiguracaoCursos.get()
        return self._cfg_cursos

    def focos(self):
        from cursos.models import Curso
        if self._focos is None:
            self._focos = list(Curso.objects.filter(publicado=True, tipo=Curso.FOCO, competencia=self.inicio))
        return self._focos

    def capacitacoes(self, pessoa):
        from cursos.models import Curso
        if self._lote is not None and 'capacitacoes' in self._lote:
            return self._lote['capacitacoes'].get(pessoa.id, [])
        return list(Curso.objects.filter(publicado=True, tipo=Curso.CAPACITACAO, atribuicoes__colaborador=pessoa,
                                         prazo__range=(self.inicio, self.fim)).distinct())

    def comprovantes(self, pessoa, cursos):
        from cursos.models import Comprovante
        if self._lote is not None and 'comprovantes' in self._lote:
            return [c for c in self._lote['comprovantes'].get(pessoa.id, []) if c.curso_id in {x.id for x in cursos}]
        return list(Comprovante.objects.filter(curso__in=cursos, colaborador=pessoa).only('curso_id', 'status', 'enviado_em'))

    def preparar(self, pessoas):
        """Busca de uma vez o que o cálculo de várias pessoas vai pedir (ranking)."""
        from cursos.models import AtribuicaoCurso, Comprovante, Curso
        from rotina.models import AtividadeRotina, ConclusaoAtividade, RotinaGerencial

        from .models import ContagemEstoque, MarcacaoChecklist
        pids = [p.id for p in pessoas]
        lojas = {p.sector_id for p in pessoas if servicos.e_loja(p.sector)}
        lote = {'contagens': defaultdict(list), 'marcacoes': defaultdict(list), 'rotinas': {}}
        for c in (ContagemEstoque.objects.filter(loja_id__in=lojas, data__range=(self.inicio, self.fim))
                  .select_related('feito_por', 'regularizada_por').order_by('data', 'feito_em')):
            lote['contagens'][c.loja_id].append(c)
        lote['previstos'] = ranking.Previstos(lojas)
        for loja_id, *resto in (MarcacaoChecklist.objects.filter(item__loja_id__in=lojas, data__range=(self.inicio, self.ate))
                                .values_list('item__loja_id', 'item_id', 'item__horario', 'data', 'feito_em')):
            lote['marcacoes'][loja_id].append(tuple(resto))
        rotinas = {r.id: r for r in RotinaGerencial.objects.filter(user_id__in=pids, ativa=True)}
        ativs = defaultdict(list)
        for a in AtividadeRotina.objects.filter(rotina_id__in=list(rotinas)):
            ativs[a.rotina_id].append(a)
        concl = defaultdict(dict)
        for rot, a, d, c in ConclusaoAtividade.objects.filter(atividade__rotina_id__in=list(rotinas),
                                                              data__range=(self.inicio, self.ate)) \
                .values_list('atividade__rotina_id', 'atividade_id', 'data', 'criado_em'):
            concl[rot][(a, d)] = _dia(c)
        for r in rotinas.values():
            lote['rotinas'][r.user_id] = (r, ativs.get(r.id, []), concl.get(r.id, {}))
        try:
            from auditoria_sap.models import VendaRenova
            renova = defaultdict(list)
            for setor, chamado, dispensada in (VendaRenova.objects.filter(setor_id__in=lojas, ativa=True,
                                                                          data_fatura__range=(self.inicio, self.fim))
                                               .values_list('setor_id', 'chamado_id', 'dispensada')):
                renova[setor].append((chamado, dispensada))
            lote['renova'] = renova
        except Exception:  # noqa: BLE001 — cada missão busca sozinha e mostra o aviso
            pass
        try:
            todos = [pdv for lista in self._mapa_pdvs().values() for pdv in lista]
            fiscal = defaultdict(list)
            for linha in self._q_fiscal(todos).values_list('pdv', 'ativa', 'sumiu_em', 'resolvida', 'resolvida_em'):
                fiscal[linha[0]].append(linha)
            lote['fiscal'] = fiscal
        except Exception:  # noqa: BLE001
            pass
        try:
            from impulso.models import Ideia
            ideias = defaultdict(list)
            base_i = Ideia.objects.filter(criado_em__date__gte=self.inicio, criado_em__date__lte=self.fim)
            vistos = set()
            for i, autor, status in base_i.filter(autor_id__in=pids).values_list('id', 'autor_id', 'status'):
                vistos.add((i, autor))
                ideias[autor].append(status)
            for i, quem, status in base_i.filter(participantes__in=pids).values_list('id', 'participantes', 'status'):
                if (i, quem) not in vistos:
                    vistos.add((i, quem))
                    ideias[quem].append(status)
            lote['ideias'] = ideias
        except Exception:  # noqa: BLE001
            pass
        try:
            cap = defaultdict(list)
            cursos_cap = {}
            for a in (AtribuicaoCurso.objects.filter(colaborador_id__in=pids, curso__publicado=True,
                                                     curso__tipo=Curso.CAPACITACAO,
                                                     curso__prazo__range=(self.inicio, self.fim))
                      .select_related('curso')):
                cap[a.colaborador_id].append(a.curso)
                cursos_cap[a.curso_id] = a.curso
            lote['capacitacoes'] = cap
            ids_cursos = set(cursos_cap) | {c.id for c in self.focos()}
            comp = defaultdict(list)
            for c in Comprovante.objects.filter(curso_id__in=ids_cursos, colaborador_id__in=pids) \
                    .only('curso_id', 'colaborador_id', 'status', 'enviado_em'):
                comp[c.colaborador_id].append(c)
            lote['comprovantes'] = comp
        except Exception:  # noqa: BLE001
            pass
        self._lote = lote
        return self


def _resultado(chave, pontos, **extra):
    _c, numero, pilar, nome, descricao, maximo, icone = next(m for m in MISSOES if m[0] == chave)
    pontos = round(max(0.0, min(float(pontos), maximo)), 2)
    saida = {'chave': chave, 'numero': numero, 'pilar': pilar, 'nome': nome, 'descricao': descricao,
             'maximo': maximo, 'icone': icone, 'pontos': pontos, 'pontos_txt': fmt(pontos),
             'pct_barra': round(pontos * 100 / maximo, 1) if maximo else 0,
             'fonte': 'auto', 'aviso': '', 'resumo': '', 'status': 'cumprida' if pontos >= maximo else
             ('parcial' if pontos > 0 else 'pendente')}
    saida.update(extra)
    return saida


# ── PREVENIR ────────────────────────────────────────────────────────────────
def m_renova(base, loja):
    try:
        vendas = base.renova(loja)
    except Exception:  # noqa: BLE001
        return _resultado('renova', 0, aviso='A Visão Renova do SAP está fora do ar agora.', status='pendente')
    total = len(vendas)
    ok = sum(1 for chamado, dispensada in vendas if chamado or dispensada)
    if not total:
        return _resultado('renova', 5, resumo='Nenhuma operação Renova no ciclo: validada pelo sistema.',
                          pct=None, link='/sap/renova/')
    pct = round(ok * 100 / total, 1)
    return _resultado('renova', proporcional(5, ok, total), pct=pct, link='/sap/renova/',
                      resumo=f'{ok} de {total} operações com chamado ({fmt(pct)}% de conformidade).')


def m_checklojas(base, loja):
    from .models import LancamentoGuardiao
    lanc = base.lancamento(loja, LancamentoGuardiao.CHECKLOJAS)
    if lanc and lanc.data <= base.fim:
        quem = lanc.lancado_por.get_full_name() if lanc.lancado_por else ''
        return _resultado('checklojas', 5, fonte='registro', lancamento=lanc,
                          resumo=f'Registrado em {lanc.data:%d/%m}' + (f' por {quem}.' if quem else '.'))
    return _resultado('checklojas', 0, fonte='registro', lancamento=None,
                      status='pendente' if base.aberto else 'perdida',
                      aviso='Sem fonte automática: a loja registra aqui que fez o CheckLojas do mês.',
                      resumo=f'Prazo: {base.fim:%d/%m}.' if base.aberto else 'Não registrado no ciclo.')


def m_melhoria(base, pessoa):
    try:
        ideias = base.ideias(pessoa)
    except Exception:  # noqa: BLE001
        return _resultado('melhoria', 0, aviso='O Inovar está fora do ar agora.', status='pendente')
    aprovadas = sum(1 for s in ideias if s == 'APROVADA')
    analise = sum(1 for s in ideias if s in ('NOVA', 'EM_ANALISE'))
    pontos = 5 if aprovadas >= MELHORIA_MINIMO else 0
    resumo = f'{min(aprovadas, MELHORIA_MINIMO)} de {MELHORIA_MINIMO} contribuições validadas'
    if analise:
        resumo += f' · {analise} aguardando avaliação'
    return _resultado('melhoria', pontos, resumo=resumo + '.', link='/impulso/inovar/',
                      aprovadas=aprovadas, em_analise=analise,
                      status='cumprida' if pontos else ('parcial' if aprovadas or analise else 'pendente'))


def cursos_do_ciclo(base, pessoa):
    """[(curso, situação)] dos cursos com prazo no ciclo que alcançam a pessoa.

    situação: 'no_prazo', 'fora_prazo', 'recusado', 'pendente' (sem comprovante, prazo não venceu) ou 'perdido'.
    """
    from cursos.models import Comprovante
    cursos = []
    if base.focos() and base.cfg_cursos().no_escopo(pessoa):
        cursos += base.focos()
    cursos += base.capacitacoes(pessoa)
    if not cursos:
        return []
    comprovantes = defaultdict(list)
    for c in base.comprovantes(pessoa, cursos):
        comprovantes[c.curso_id].append(c)
    saida = []
    for curso in sorted(set(cursos), key=lambda c: (c.prazo, c.id)):
        validos = [c for c in comprovantes.get(curso.id, []) if c.status != Comprovante.RECUSADO]
        if any(_dia(c.enviado_em) <= curso.prazo for c in validos):
            situacao = 'no_prazo'
        elif validos:
            situacao = 'fora_prazo'
        elif comprovantes.get(curso.id):
            situacao = 'recusado'
        else:
            situacao = 'pendente' if base.hoje <= curso.prazo else 'perdido'
        saida.append((curso, situacao))
    return saida


def m_evolucao(base, pessoa):
    try:
        cursos = cursos_do_ciclo(base, pessoa)
    except Exception:  # noqa: BLE001
        logger.warning('Missão Guardião: cursos fora', exc_info=True)
        return _resultado('evolucao', 0, aviso='Os cursos estão fora do ar agora.', status='pendente', cursos=[])
    if not cursos:
        return _resultado('evolucao', 5, cursos=[], link='/cursos/',
                          resumo='Nenhum curso atribuído neste ciclo: missão sem pendência.')
    feitos = sum(1 for _c, s in cursos if s == 'no_prazo')
    pontos = 5 if feitos == len(cursos) else 0
    nomes = {'no_prazo': 'concluído no prazo', 'fora_prazo': 'concluído fora do prazo', 'recusado':
             'comprovante recusado', 'pendente': 'aguardando conclusão', 'perdido': 'não realizado'}
    lista = [{'curso': c, 'situacao': s, 'rotulo': nomes[s]} for c, s in cursos]
    em_aberto = any(s in ('pendente', 'recusado') for _c, s in cursos)
    return _resultado('evolucao', pontos, cursos=lista, link='/cursos/',
                      status='cumprida' if pontos else ('pendente' if em_aberto else 'perdida'),
                      resumo=f'{feitos} de {len(cursos)} curso(s) concluído(s) no prazo.')


# ── PROTEGER ────────────────────────────────────────────────────────────────
def _regularizada_no_prazo(base, c):
    return bool(c.regularizada_em and _dia(c.regularizada_em) <= base.fim)


def m_lvut(base, loja, contagens):
    """As 16 contagens do mês (4 categorias × 4 janelas) e a situação de cada uma."""
    from .models import ContagemEstoque
    js = janelas(base.inicio, base.fim)
    grade, conquistadas, bloqueadas, pendentes, perdidas = [], 0, 0, 0, 0
    for chave, nome in ContagemEstoque.CATEGORIAS:
        icone, _cor = servicos.VISUAL_ESTOQUE[chave]
        celulas = []
        for n, de, ate in js:
            daqui = [c for c in contagens if c.categoria == chave and de <= c.data <= ate]
            limpa = next((c for c in daqui if not c.divergencias), None)
            recuperada = next((c for c in daqui if c.divergencias and _regularizada_no_prazo(base, c)), None)
            presa = next((c for c in daqui if c.divergencias and not c.regularizada_em), None)
            if limpa or recuperada:
                situacao, ref = ('conquistada', limpa) if limpa else ('recuperada', recuperada)
                conquistadas += 1
            elif presa and base.aberto:
                situacao, ref = 'bloqueada', presa
                bloqueadas += 1
            elif daqui:
                situacao, ref = 'perdida', daqui[-1]      # divergência não regularizada até o fechamento
                perdidas += 1
            elif base.hoje <= ate:
                situacao, ref = 'pendente', None
                pendentes += 1
            else:
                situacao, ref = 'nao_realizada', None
                perdidas += 1
            celulas.append({'janela': n, 'de': de, 'ate': ate, 'situacao': situacao, 'contagem': ref,
                            'atual': de <= base.hoje <= ate})
        grade.append({'chave': chave, 'nome': nome, 'icone': icone, 'celulas': celulas,
                      'pontos': fmt(LVUT_POR_CONTAGEM * sum(1 for c in celulas
                                                            if c['situacao'] in ('conquistada', 'recuperada')))})
    pontos = conquistadas * LVUT_POR_CONTAGEM
    return _resultado('lvut', pontos, grade=grade, janelas=js, conquistadas=conquistadas, bloqueadas=bloqueadas,
                      pendentes=pendentes, perdidas=perdidas, total_contagens=len(js) * 4,
                      resumo=f'{conquistadas} de {len(js) * 4} contagens conquistadas'
                             + (f' · {bloqueadas} bloqueada(s) por divergência' if bloqueadas else '') + '.')


def m_recuperacao(base, contagens):
    com_div = [c for c in contagens if c.divergencias]
    total = sum(c.divergencias for c in com_div)
    feitas = sum(c.divergencias for c in com_div if _regularizada_no_prazo(base, c))
    abertas = [c for c in com_div if not c.regularizada_em]
    if not total:
        contou = any(c.data <= base.hoje for c in contagens)
        return _resultado('recuperacao', 10 if contou else 0, pct=None, abertas=[],
                          status='cumprida' if contou else 'pendente',
                          resumo='Nenhuma divergência nas contagens do ciclo.' if contou else
                          'Aguardando as contagens do ciclo para validar.')
    pct = round(feitas * 100 / total, 1)
    return _resultado('recuperacao', proporcional(10, feitas, total), pct=pct, abertas=abertas,
                      resumo=f'{feitas} de {total} divergência(s) regularizada(s) ({fmt(pct)}%).')


def m_fiscal(base, loja):
    pdvs = base.pdvs(loja)
    if pdvs is None:
        return _resultado('fiscal', 0, aviso='A Visão SAP está fora do ar agora.', status='pendente')
    try:
        linhas = base.linhas_fiscais(loja, pdvs)
    except Exception:  # noqa: BLE001
        return _resultado('fiscal', 0, aviso='A Visão SAP está fora do ar agora.', status='pendente')
    corte = base.corte()

    def regularizada(ativa, sumiu_em, resolvida, resolvida_em):
        if not ativa:
            return bool(sumiu_em is None or _dia(sumiu_em) <= base.fim)
        if resolvida and resolvida_em and _dia(resolvida_em) <= base.fim:
            return not (corte and resolvida_em < corte)   # o SAP releu e a venda continua lá: não saiu
        return False

    total = len(linhas)
    ok = sum(1 for l in linhas if regularizada(*l))
    if not total:
        return _resultado('fiscal', 10, pct=None, link='/sap/?grupo=sem_nf',
                          resumo='Nenhuma pendência fiscal no ciclo: validada pelo sistema.')
    pct = round(ok * 100 / total, 1)
    return _resultado('fiscal', proporcional(10, ok, total), pct=pct, link='/sap/?grupo=sem_nf', abertas=total - ok,
                      resumo=f'{ok} de {total} pendência(s) "Sem NF" regularizada(s) ({fmt(pct)}%).')


# ── GARANTIR ────────────────────────────────────────────────────────────────
def m_agenda(base, pessoa, loja):
    """Check-list diário da loja (até o horário) + Rotina Gerencial da pessoa (no próprio dia)."""
    ate = base.ate
    dias = [base.inicio + timedelta(days=n) for n in range(max((ate - base.inicio).days + 1, 0))]
    previstos = check_prazo = 0
    if loja is not None:
        prev, lista = base.checklist(loja)
        marcadas = defaultdict(dict)
        for item_id, horario, dia, feito_em in lista:
            marcadas[dia][item_id] = horario is None or timezone.localtime(feito_em).time() <= horario
        for d in dias:
            feitos = marcadas.get(d, {})
            if d.weekday() == 6 and not feitos:
                continue                                  # domingo sem nada marcado: loja fechada
            do_dia = prev.do_dia(loja.id, d, feitos.keys())
            previstos += len(do_dia) if d < base.hoje else len(feitos)
            check_prazo += sum(1 for ok in feitos.values() if ok)
    rot_prev = rot_prazo = 0
    rotina, atividades, concl = base.rotina(pessoa)
    for d in dias:
        for a in atividades:
            if a.dia_semana != d.weekday() or (_dia(a.criada_em) or d) > d:
                continue
            feita = concl.get((a.id, d))
            if d < base.hoje or feita:
                rot_prev += 1
                rot_prazo += 1 if (feita and feita <= d) else 0
    total = previstos + rot_prev
    ok = check_prazo + rot_prazo
    if not total:
        return _resultado('agenda', 0, pct=None, status='pendente',
                          aviso='Nenhuma atividade prevista: monte o check-list da loja e a Rotina Gerencial.',
                          detalhe={'check_prev': 0, 'check_ok': 0, 'rot_prev': 0, 'rot_ok': 0, 'tem_rotina': bool(rotina)})
    pct = round(ok * 100 / total, 2)
    return _resultado('agenda', proporcional(20, ok, total), pct=pct,
                      detalhe={'check_prev': previstos, 'check_ok': check_prazo, 'rot_prev': rot_prev,
                               'rot_ok': rot_prazo, 'tem_rotina': bool(rotina)},
                      resumo=f'{ok} de {total} atividades no prazo ({fmt(pct)}%).')


def m_estoque_total(base, loja):
    from .models import LancamentoGuardiao
    lanc = base.lancamento(loja, LancamentoGuardiao.ESTOQUE_TOTAL)
    aviso = 'Sem fonte automática: quem gere lança o resultado do inventário mensal da loja.'
    if lanc is None or lanc.valor is None:
        return _resultado('estoque_total', 0, fonte='manual', aviso=aviso, lancamento=None, pct=None,
                          status='pendente' if base.aberto else 'perdida',
                          resumo=f'Alvo mínimo: {ESTOQUE_TOTAL_MINIMO}% · aguardando o resultado do inventário.')
    pct = float(lanc.valor)
    pontos = 20 if pct >= ESTOQUE_TOTAL_MINIMO else 0
    return _resultado('estoque_total', pontos, fonte='manual', aviso=aviso, lancamento=lanc, pct=pct,
                      status='cumprida' if pontos else 'perdida',
                      resumo=f'Alvo mínimo: {ESTOQUE_TOTAL_MINIMO}% · {fmt(pct)}% de conformidade.')


# ── Agenda de Missões (quadro) ───────────────────────────────────────────────
def cartoes_da_agenda(base, pessoa, loja, missoes, contagens):
    """Os cartões do ciclo: as missões com entrega (CheckLojas, curso, contagens, divergências) + as manuais."""
    from .models import ContagemEstoque, MissaoAgenda
    cartoes = []

    def cartao(titulo, tipo, prazo, status, link='', obj=None):
        cartoes.append({'titulo': titulo, 'tipo': tipo, 'prazo': prazo, 'status': status, 'link': link,
                        'manual': obj is not None, 'obj': obj,
                        'atrasada': status != 'CUMPRIDA' and prazo is not None and prazo < base.hoje})

    m = {x['chave']: x for x in missoes}
    if loja is not None:
        cartao('CheckLojas Mensal', 'Mensal', base.fim,
               'CUMPRIDA' if m['checklojas']['pontos'] else 'ATRIBUIDA', 'missoes#checklojas')
    for linha in m['evolucao'].get('cursos', []):
        status = {'no_prazo': 'CUMPRIDA', 'fora_prazo': 'CUMPRIDA', 'recusado': 'EM_ACAO'}.get(linha['situacao'], 'ATRIBUIDA')
        cartao(f'Evolução do Guardião — {linha["curso"].titulo}', 'Curso do mês', linha['curso'].prazo, status, '/cursos/')
    if loja is not None:
        nomes = dict(ContagemEstoque.CATEGORIAS)
        for n, de, ate in janelas(base.inicio, base.fim):
            if de > base.hoje + timedelta(days=6):
                continue                                  # janela que ainda vai começar daqui a mais de uma semana
            feitas = {c.categoria for c in contagens if de <= c.data <= ate}
            status = 'CUMPRIDA' if len(feitas) == len(nomes) else ('EM_ACAO' if feitas else 'ATRIBUIDA')
            cartao(f'Contagem LVUT — janela {n} ({de:%d/%m} a {ate:%d/%m})', 'Semanal', ate, status, 'lvut')
        for c in contagens:
            if c.divergencias:
                cartao(f'Tratamento de divergências — {nomes.get(c.categoria, c.categoria)} ({c.data:%d/%m})',
                       'Operacional', base.fim, 'CUMPRIDA' if c.regularizada_em else 'EM_ACAO', 'lvut')
    for obj in MissaoAgenda.objects.filter(pessoa=pessoa).filter(
            Q(prazo__range=(base.inicio, base.fim)) | Q(prazo__lt=base.inicio, status__in=['ATRIBUIDA', 'EM_ACAO'],
                                                        criado_em__date__lte=base.fim)):
        cartao(obj.titulo, obj.get_origem_display(), obj.prazo, obj.status, obj=obj)
    cartoes.sort(key=lambda c: (c['prazo'] or date.max, c['titulo']))
    return cartoes


# ── Junta tudo ──────────────────────────────────────────────────────────────
def calcular(pessoa, mes_texto=None, hoje=None, base=None, com_cartoes=True):
    """A Missão Guardião da pessoa no mês: as 9 missões, pilares, total, nível e o quadro da agenda."""
    if base is None:
        base = Base(*periodo(mes_texto, hoje))
    loja = pessoa.sector if servicos.e_loja(pessoa.sector) else None
    contagens = base.contagens(loja) if loja is not None else []
    sem_loja = 'Sem loja no setor principal: a missão da loja não se aplica.'
    missoes = []
    for chave in [m[0] for m in MISSOES]:
        try:
            if chave == 'renova':
                r = m_renova(base, loja) if loja else _resultado('renova', 0, aviso=sem_loja, status='pendente')
            elif chave == 'checklojas':
                r = m_checklojas(base, loja) if loja else _resultado('checklojas', 0, aviso=sem_loja, status='pendente')
            elif chave == 'melhoria':
                r = m_melhoria(base, pessoa)
            elif chave == 'evolucao':
                r = m_evolucao(base, pessoa)
            elif chave == 'lvut':
                r = m_lvut(base, loja, contagens) if loja else _resultado('lvut', 0, aviso=sem_loja, status='pendente')
            elif chave == 'recuperacao':
                r = m_recuperacao(base, contagens) if loja else _resultado('recuperacao', 0, aviso=sem_loja, status='pendente')
            elif chave == 'fiscal':
                r = m_fiscal(base, loja) if loja else _resultado('fiscal', 0, aviso=sem_loja, status='pendente')
            elif chave == 'agenda':
                r = m_agenda(base, pessoa, loja)
            else:
                r = m_estoque_total(base, loja) if loja else _resultado('estoque_total', 0, aviso=sem_loja, status='pendente')
        except Exception:  # noqa: BLE001 — uma missão com erro não derruba a central
            logger.exception('Missão Guardião: erro na missão %s', chave)
            r = _resultado(chave, 0, aviso='Não deu para calcular esta missão agora.', status='pendente')
        missoes.append(r)

    pilares = []
    for chave, nome, maximo, lema in PILARES:
        pts = round(sum(m['pontos'] for m in missoes if m['pilar'] == chave), 2)
        pilares.append({'chave': chave, 'nome': nome, 'maximo': maximo, 'lema': lema, 'pontos': pts,
                        'pontos_txt': fmt(pts), 'pct': round(pts * 100 / maximo, 1),
                        'missoes': [m for m in missoes if m['pilar'] == chave]})
    total = round(sum(m['pontos'] for m in missoes), 2)
    saida = {
        'pessoa': pessoa, 'loja': loja, 'inicio': base.inicio, 'fim': base.fim, 'hoje': base.hoje,
        'aberto': base.aberto, 'missoes': missoes, 'por_chave': {m['chave']: m for m in missoes},
        'pilares': pilares, 'total': total, 'total_txt': fmt(total), 'nivel': nivel(total),
        'faltam_txt': fmt(round(100 - total, 2)),
        'ciclo': f'{base.inicio.month:02d}/{base.inicio.year}',
        'mes_extenso': f'{MESES[base.inicio.month - 1]} de {base.inicio.year}',
    }
    if com_cartoes:
        cartoes = cartoes_da_agenda(base, pessoa, loja, missoes, contagens)
        abertas = [c for c in cartoes if c['status'] != 'CUMPRIDA']
        saida.update({
            'cartoes': cartoes,
            'colunas': [{'status': s, 'nome': n, 'cartoes': [c for c in cartoes if c['status'] == s]}
                        for s, n in (('ATRIBUIDA', 'Missão Atribuída'), ('EM_ACAO', 'Missão em Ação'),
                                     ('CUMPRIDA', 'Missão Cumprida'))],
            'abertas': len(abertas), 'concluidas': len(cartoes) - len(abertas),
            'atrasadas': [c for c in abertas if c['atrasada']],
            'proximas': [c for c in abertas if not c['atrasada']][:6],
        })
    return saida


def guardioes():
    """Quem joga: Rotina ADM liberada (pessoa ou grupo) e setor principal de loja."""
    return ranking.analistas_com_acesso()


def ranking_do_mes(mes_texto=None, hoje=None):
    """Guardiões em Destaque: todos os Guardiões do mês, do maior total para o menor (guardado 5 min)."""
    inicio, fim, hoje = periodo(mes_texto, hoje)
    pessoas = list(guardioes().order_by('id'))
    chave = f'rotina_adm:guardiao:ranking:v1:{inicio:%Y%m}:{hoje:%Y%m%d}:' + ','.join(str(p.id) for p in pessoas)[:180]
    guardado = cache.get(chave)
    if guardado is None:
        base = Base(inicio, fim, hoje).preparar(pessoas)
        guardado = []
        for p in pessoas:
            r = calcular(p, base=base, com_cartoes=False)
            guardado.append({'id': p.id, 'total': r['total'],
                             'pilares': {pl['chave']: pl['pontos'] for pl in r['pilares']}})
        cache.set(chave, guardado, 300)
    por_id = {p.id: p for p in pessoas}
    linhas = []
    for g in guardado:
        p = por_id.get(g['id'])
        if p is None:
            continue
        linhas.append({'pessoa': p, 'nome': p.get_full_name() or p.username, 'loja': p.sector.name if p.sector else '',
                       'total': g['total'], 'total_txt': fmt(g['total']), 'nivel': nivel(g['total']),
                       'pilares': [{'chave': c, 'pontos_txt': fmt(g['pilares'].get(c, 0)), 'maximo': m,
                                    'pct': round(g['pilares'].get(c, 0) * 100 / m, 1)} for c, _n, m, _l in PILARES]})
    linhas.sort(key=lambda l: (-l['total'], l['nome'].lower()))
    for n, l in enumerate(linhas, start=1):
        l['posicao'] = n
    return {'inicio': inicio, 'fim': fim, 'linhas': linhas}
