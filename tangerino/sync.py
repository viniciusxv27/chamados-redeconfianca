"""Vínculo entre os usuários do portal e os funcionários do Tangerino.

Estratégia, nesta ordem:

1. **CPF** — chave real, imune a apelido, acento e nome de casada. Cobre a
   quase totalidade da base (na primeira medição: 164 de 181).
2. **Nome normalizado** — só para quem não tem CPF dos dois lados. Sem acento,
   sem pontuação, espaços colapsados, tudo em caixa alta. Um nome que aparece
   duas vezes no Tangerino é considerado **ambíguo e nunca casado sozinho**:
   vincular a pessoa errada num sistema de ponto é pior do que não vincular.

O que sobrar vai para a tela de vínculo manual.
"""
import logging
import re
import unicodedata

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Max, Min, Sum
from django.utils import timezone

from . import jornada as jornada_svc
from .client import (MOTIVO_FERIAS_ID, de_millis, listar_ferias, listar_funcionarios,
                     listar_marcacoes, listar_saldo_horas)
from .models import FeriasLancamento, JornadaTrabalho, MarcacaoPonto, SaldoHoras

logger = logging.getLogger(__name__)
User = get_user_model()


def so_digitos(valor):
    return re.sub(r'\D', '', valor or '')


def chave_nome(valor):
    """Nome comparável: sem acento, sem pontuação, espaços colapsados, caixa alta."""
    sem_acento = unicodedata.normalize('NFKD', valor or '').encode('ascii', 'ignore').decode()
    limpo = re.sub(r'[^A-Za-z ]', ' ', sem_acento)
    return re.sub(r'\s+', ' ', limpo).strip().upper()


def _indices(funcionarios):
    por_cpf, por_nome = {}, {}
    for f in funcionarios:
        cpf = so_digitos(f.get('cpf'))
        if cpf:
            por_cpf[cpf] = f
        por_nome.setdefault(chave_nome(f.get('name')), []).append(f)
    return por_cpf, por_nome


def sincronizar_vinculos(revincular=False, aplicar=True):
    """Casa usuários do portal com funcionários do Tangerino.

    ``revincular=False`` (padrão) não mexe em quem já tem ID — vínculo ajustado
    à mão não é sobrescrito por uma rodada automática.
    ``aplicar=False`` só simula, para a tela mostrar o que aconteceria.
    """
    funcionarios = listar_funcionarios(usar_cache=False)
    por_cpf, por_nome = _indices(funcionarios)
    tomados = set(User.objects.exclude(tangerino_employee_id__isnull=True)
                  .values_list('tangerino_employee_id', flat=True))

    resultado = {
        'casados_cpf': 0, 'casados_nome': 0, 'ja_vinculados': 0,
        'sem_correspondencia': 0, 'ambiguos': 0,
        'total_tangerino': len(funcionarios), 'pendentes': [],
    }

    for usuario in User.objects.filter(is_active=True).order_by('first_name', 'last_name'):
        if usuario.tangerino_employee_id and not revincular:
            resultado['ja_vinculados'] += 1
            continue

        achado, via = None, None
        cpf = so_digitos(usuario.cpf)
        if cpf and cpf in por_cpf:
            achado, via = por_cpf[cpf], 'cpf'
        else:
            candidatos = por_nome.get(chave_nome(usuario.full_name), [])
            if len(candidatos) == 1:
                achado, via = candidatos[0], 'nome'
            elif len(candidatos) > 1:
                resultado['ambiguos'] += 1

        # Um mesmo funcionário não pode ser vinculado a dois usuários.
        if achado and achado['id'] in tomados and usuario.tangerino_employee_id != achado['id']:
            achado = None

        if not achado:
            resultado['sem_correspondencia'] += 1
            resultado['pendentes'].append({
                'user_id': usuario.id, 'nome': usuario.full_name,
                'email': usuario.email, 'cpf': cpf,
            })
            continue

        if aplicar:
            usuario.tangerino_employee_id = achado['id']
            usuario.tangerino_synced_at = timezone.now()
            usuario.save(update_fields=['tangerino_employee_id', 'tangerino_synced_at'])
        tomados.add(achado['id'])
        resultado['casados_cpf' if via == 'cpf' else 'casados_nome'] += 1

    return resultado


def funcionarios_disponiveis():
    """Funcionários do Tangerino ainda não vinculados a ninguém, para o select
    da tela de vínculo manual."""
    tomados = set(User.objects.exclude(tangerino_employee_id__isnull=True)
                  .values_list('tangerino_employee_id', flat=True))
    livres = [f for f in listar_funcionarios() if f['id'] not in tomados]
    return sorted(livres, key=lambda f: (f.get('name') or '').upper())


# ---------------------------------------------------------------------------
# Espelho local: grava no banco o que a API devolve
# ---------------------------------------------------------------------------
def _mapa_usuarios():
    return {u.tangerino_employee_id: u for u in
            User.objects.exclude(tangerino_employee_id__isnull=True)}


def _gravar_em_lote(modelo, registros, campos, chave=('tangerino_id',), escopo=None):
    """Grava uma leva de registros com o mínimo de idas ao banco.

    `update_or_create` por item custa duas viagens cada; com milhares de linhas
    contra o Postgres remoto isso passava de dez minutos. Aqui são poucas
    consultas: uma para saber o que já existe, um bulk_create e um bulk_update.

    ``chave`` são os campos que identificam a linha (para o ponto é o par
    funcionário+data) e ``escopo`` limita a busca do que já existe ao período
    sincronizado, para não varrer a tabela inteira a cada rodada.
    """
    if not registros:
        return {'criados': 0, 'atualizados': 0}

    def chave_de(obj):
        return tuple(getattr(obj, c) for c in chave)

    por_chave = {chave_de(r): r for r in registros}          # dedup na própria leva
    qs = modelo.objects.all() if escopo is None else escopo
    existentes = {}
    for valores in qs.values_list(*chave, 'id'):
        k = tuple(valores[:-1])
        if k in por_chave:
            existentes[k] = valores[-1]

    novos = [r for k, r in por_chave.items() if k not in existentes]
    alterados = []
    for k, r in por_chave.items():
        if k in existentes:
            r.id = existentes[k]
            alterados.append(r)

    if novos:
        modelo.objects.bulk_create(novos, batch_size=500)
    if alterados:
        modelo.objects.bulk_update(alterados, campos, batch_size=500)
    return {'criados': len(novos), 'atualizados': len(alterados)}


def sincronizar_jornadas():
    """Traz as escalas contratadas para a tabela JornadaTrabalho.

    São poucas escalas (~30) para muita gente, então a sincronização é barata
    e o resultado serve de base para todo cálculo de "horas previstas".
    """
    carga = jornada_svc.carregar_jornadas()
    agora = timezone.now()

    registros = []
    for escala in carga['grades'].values():
        grade = escala['grade']
        registros.append(JornadaTrabalho(
            tangerino_id=escala['id'],
            nome=escala['nome'],
            # JSON só aceita chave string; a leitura trata os dois formatos.
            horas_por_dia={str(dia): seg for dia, seg in grade.items()},
            segundos_semana=sum(grade.values()),
            sincronizado_em=agora,
        ))

    resultado = _gravar_em_lote(JornadaTrabalho, registros, [
        'nome', 'horas_por_dia', 'segundos_semana', 'sincronizado_em'])
    resultado['escalas'] = len(registros)
    resultado['sem_jornada'] = sum(1 for r in registros if not r.segundos_semana)
    return resultado


def _grades_por_funcionario():
    """{employee_id: {dia: segundos}} lendo do banco, sem ir na API.

    Cai para a API só quando a tabela de jornadas ainda não foi sincronizada,
    para a primeira execução não sair com previsto zerado.
    """
    ligacao = {f.get('id'): (f.get('currentWorkSchedule') or {}).get('id')
               for f in listar_funcionarios()}

    guardadas = {j.tangerino_id: j for j in JornadaTrabalho.objects.all()}
    if not guardadas:
        sincronizar_jornadas()
        guardadas = {j.tangerino_id: j for j in JornadaTrabalho.objects.all()}

    grades = {}
    for eid, escala in ligacao.items():
        registro = guardadas.get(escala)
        if not registro:
            continue
        grades[eid] = {int(dia): seg for dia, seg in (registro.horas_por_dia or {}).items()}
    return grades


def sincronizar_marcacoes(dias=30, employee_id=None):
    """Traz as marcações dos últimos N dias para a tabela MarcacaoPonto.

    A janela padrão volta 30 dias porque marcação antiga ainda pode mudar
    (ajuste do gestor, marcação retroativa). Como a chave é o id do par no
    Tangerino, reprocessar o mesmo período só atualiza — nunca duplica.
    """
    hoje = timezone.localdate()
    inicio = hoje - timedelta(days=dias)
    pares = listar_marcacoes(inicio, hoje, employee_id=employee_id, usar_cache=False)
    usuarios = _mapa_usuarios()

    agora = timezone.now()

    # Junta os pares do Tangerino por (funcionário, dia) para virar uma linha só.
    #
    # O `vistos` não é zelo excessivo: a paginação da API repete registros entre
    # páginas (4400 itens lidos para 4197 pares reais). Enquanto a chave da
    # tabela era o id do par, a duplicata se resolvia sozinha; agrupando por dia
    # ela viraria um par fantasma — entrada1 e entrada2 idênticas, e o total de
    # horas dobrado. Por isso o id é conferido antes de entrar no grupo.
    vistos = set()
    por_dia = {}
    for par in pares:
        tid = par.get('id')
        entrada = de_millis(par.get('dateIn'))
        if not tid or not entrada or tid in vistos:
            continue
        vistos.add(tid)
        por_dia.setdefault((par.get('employeeId'), entrada.date()), []).append(par)

    # Quanto cada um devia ter trabalhado nesses dias, já sem feriado e abono.
    grades = _grades_por_funcionario()
    abonos = jornada_svc.carregar_abonos(inicio, hoje)

    registros = []
    for (eid, dia), do_dia in por_dia.items():
        do_dia.sort(key=lambda p: p['dateIn'])
        campos = {}
        total = 0
        extras = []
        aberto = False

        for i, par in enumerate(do_dia, start=1):
            entrada, saida = de_millis(par.get('dateIn')), de_millis(par.get('dateOut'))
            if saida and entrada:
                total += max(0, int((saida - entrada).total_seconds()))
            if not saida:
                aberto = True
            if i <= 3:
                campos[f'entrada{i}'] = entrada
                campos[f'saida{i}'] = saida
            else:
                # Além do 3º par: guarda o horário em vez de jogar fora.
                extras.append(entrada.strftime('%H:%M'))
                if saida:
                    extras.append(saida.strftime('%H:%M'))

        if extras:
            logger.warning('%s em %s teve %d pares de ponto; o excedente foi para '
                           'marcacoes_extras.', do_dia[0].get('employeeName'), dia, len(do_dia))

        previsto = jornada_svc.previsto_liquido(
            grades.get(eid), dia, abonos.get((eid, dia), 0))

        registros.append(MarcacaoPonto(
            employee_id=eid,
            usuario=usuarios.get(eid),
            nome=(do_dia[0].get('employeeName') or '')[:200],
            data=dia,
            total_segundos=total,
            previsto_segundos=previsto,
            em_aberto=aberto,
            plataforma=(do_dia[0].get('plataform') or '')[:30],
            editado=any(bool(p.get('edited')) for p in do_dia),
            marcacoes_extras=extras,
            tangerino_ids=[p['id'] for p in do_dia],
            sincronizado_em=agora,
            **campos))

    resultado = _gravar_em_lote(MarcacaoPonto, registros, [
        'employee_id', 'usuario', 'nome', 'entrada1', 'saida1', 'entrada2', 'saida2',
        'entrada3', 'saida3', 'marcacoes_extras', 'total_segundos', 'previsto_segundos',
        'em_aberto', 'plataforma', 'editado', 'tangerino_ids', 'sincronizado_em'],
        chave=('employee_id', 'data'),
        escopo=MarcacaoPonto.objects.filter(data__gte=inicio, data__lte=hoje))
    resultado['lidos'] = len(pares)
    resultado['dias'] = len(registros)
    # Linhas de sincronizações anteriores ficaram fora da janela e sem previsto.
    # O cálculo é local e barato, então elas são acertadas junto.
    resultado['previsto_recalculado'] = recalcular_previsto(
        grades=grades, ate=inicio - timedelta(days=1))
    return resultado


def recalcular_previsto(grades=None, de=None, ate=None):
    """Refaz o previsto das marcações já gravadas.

    Serve para dois casos: linhas antigas, gravadas antes de existir o campo, e
    mudança de escala, que muda o previsto de tudo dali para frente. Não toca
    na API além do que já está em cache — a conta é feita aqui.
    """
    alvo = MarcacaoPonto.objects.all()
    if de:
        alvo = alvo.filter(data__gte=de)
    if ate:
        alvo = alvo.filter(data__lte=ate)
    linhas = list(alvo)
    if not linhas:
        return 0

    grades = grades if grades is not None else _grades_por_funcionario()
    limites = alvo.aggregate(ini=Min('data'), fim=Max('data'))
    abonos = jornada_svc.carregar_abonos(limites['ini'], limites['fim'])

    mudaram = []
    for linha in linhas:
        novo = jornada_svc.previsto_liquido(
            grades.get(linha.employee_id), linha.data,
            abonos.get((linha.employee_id, linha.data), 0))
        if linha.previsto_segundos != novo:
            linha.previsto_segundos = novo
            mudaram.append(linha)

    if mudaram:
        MarcacaoPonto.objects.bulk_update(mudaram, ['previsto_segundos'], batch_size=500)
    return len(mudaram)


def inicio_do_historico():
    """Data de admissão mais antiga da empresa — o começo real do histórico.

    Sem isso o saldo sairia cortado: consultando só o ano corrente vinham 127
    pessoas e -5.726h; desde a primeira admissão vêm 130 pessoas e -4.995h. A
    diferença é o saldo acumulado de anos anteriores, que ficava de fora.
    """
    admissoes = [de_millis(f.get('admissionDate')).date()
                 for f in listar_funcionarios() if f.get('admissionDate')]
    return min(admissoes) if admissoes else timezone.localdate().replace(month=1, day=1)


def sincronizar_saldos(inicio=None, fim=None):
    """Traz o saldo de banco de horas para a tabela SaldoHoras.

    O padrão cobre o **período todo**: da admissão mais antiga até hoje. O saldo
    do Tangerino é sempre relativo à janela consultada, então ela vai gravada
    junto — sem o período, o número não se interpreta.
    """
    hoje = timezone.localdate()
    fim = fim or hoje
    inicio = inicio or inicio_do_historico()

    itens = listar_saldo_horas(inicio, fim)
    usuarios = _mapa_usuarios()
    agora = timezone.now()

    previsto, trabalhado, janela = _previsto_e_trabalhado()

    registros = []
    vistos = set()
    for item in itens:
        eid = item.get('employeeId')
        if not eid or eid in vistos:
            continue
        vistos.add(eid)
        registros.append(SaldoHoras(
            employee_id=eid,
            usuario=usuarios.get(eid),
            nome=(item.get('name') or '')[:200],
            email=(item.get('email') or '')[:254],
            saldo_minutos=int(item.get('hoursBalanceInMinutes') or 0),
            previsto_minutos=previsto.get(eid, 0),
            trabalhado_minutos=trabalhado.get(eid, 0),
            analise_inicio=janela[0],
            analise_fim=janela[1],
            periodo_inicio=inicio,
            periodo_fim=fim,
            sincronizado_em=agora,
        ))

    resultado = _gravar_em_lote(SaldoHoras, registros, [
        'usuario', 'nome', 'email', 'saldo_minutos', 'previsto_minutos',
        'trabalhado_minutos', 'analise_inicio', 'analise_fim', 'periodo_inicio',
        'periodo_fim', 'sincronizado_em'], chave=('employee_id',))
    resultado['lidos'] = len(itens)
    resultado['periodo'] = (inicio, fim)
    resultado['janela_analise'] = janela
    return resultado


def _previsto_e_trabalhado():
    """Previsto e trabalhado por pessoa, lidos das marcações já espelhadas.

    Sai do banco, não da API: o histórico inteiro não cabe numa consulta (a
    paginação corta em 8.000 registros e leva minutos), e as marcações locais
    já trazem o previsto calculado dia a dia. A janela devolvida é exatamente
    a que existe na tabela — quem quiser mais fundo roda
    ``sincronizar_marcacoes`` com mais dias.
    """
    agregado = (MarcacaoPonto.objects
                .values('employee_id')
                .annotate(previsto=Sum('previsto_segundos'),
                          feito=Sum('total_segundos')))
    previsto = {l['employee_id']: int((l['previsto'] or 0) / 60) for l in agregado}
    trabalhado = {l['employee_id']: int((l['feito'] or 0) / 60) for l in agregado}

    limites = MarcacaoPonto.objects.aggregate(ini=Min('data'), fim=Max('data'))
    return previsto, trabalhado, (limites['ini'], limites['fim'])


def sincronizar_ferias():
    """Traz todos os lançamentos de FÉRIAS para a tabela FeriasLancamento."""
    itens = listar_ferias(usar_cache=False)
    usuarios = _mapa_usuarios()

    agora = timezone.now()
    registros = []
    for item in itens:
        tid = item.get('id')
        inicio, fim = de_millis(item.get('startDate')), de_millis(item.get('endDate'))
        if not tid or not inicio:
            continue
        emp = item.get('employeeDTO') or {}
        eid = emp.get('id')
        registros.append(FeriasLancamento(
            tangerino_id=tid,
            employee_id=eid,
            usuario=usuarios.get(eid),
            nome=(emp.get('name') or '')[:200],
            inicio=inicio.date(),
            fim=(fim or inicio).date(),
            status=(item.get('status') or '')[:20],
            observacao=(item.get('observation') or '')[:2000],
            origem=(item.get('origem') or '')[:40],
            dia_inteiro=bool(item.get('fullDay', True)),
            sincronizado_em=agora,     # auto_now não vale em bulk_update
        ))

    resultado = _gravar_em_lote(FeriasLancamento, registros, [
        'employee_id', 'usuario', 'nome', 'inicio', 'fim', 'status',
        'observacao', 'origem', 'dia_inteiro', 'sincronizado_em'])
    resultado['lidos'] = len(itens)
    return resultado
