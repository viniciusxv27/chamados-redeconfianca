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
from django.utils import timezone

from .client import (MOTIVO_FERIAS_ID, de_millis, listar_ferias, listar_funcionarios,
                     listar_marcacoes, listar_saldo_horas)
from .models import FeriasLancamento, MarcacaoPonto, SaldoHoras

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

        registros.append(MarcacaoPonto(
            employee_id=eid,
            usuario=usuarios.get(eid),
            nome=(do_dia[0].get('employeeName') or '')[:200],
            data=dia,
            total_segundos=total,
            em_aberto=aberto,
            plataforma=(do_dia[0].get('plataform') or '')[:30],
            editado=any(bool(p.get('edited')) for p in do_dia),
            marcacoes_extras=extras,
            tangerino_ids=[p['id'] for p in do_dia],
            sincronizado_em=agora,
            **campos))

    resultado = _gravar_em_lote(MarcacaoPonto, registros, [
        'employee_id', 'usuario', 'nome', 'entrada1', 'saida1', 'entrada2', 'saida2',
        'entrada3', 'saida3', 'marcacoes_extras', 'total_segundos', 'em_aberto',
        'plataforma', 'editado', 'tangerino_ids', 'sincronizado_em'],
        chave=('employee_id', 'data'),
        escopo=MarcacaoPonto.objects.filter(data__gte=inicio, data__lte=hoje))
    resultado['lidos'] = len(pares)
    resultado['dias'] = len(registros)
    return resultado


def sincronizar_saldos(inicio=None, fim=None):
    """Traz o saldo de banco de horas para a tabela SaldoHoras.

    O padrão é do 1º de janeiro até hoje — "saldo do ano". O saldo do Tangerino
    é sempre relativo ao período consultado, então a janela escolhida vai
    gravada junto: sem ela, o número não se interpreta.
    """
    hoje = timezone.localdate()
    fim = fim or hoje
    inicio = inicio or hoje.replace(month=1, day=1)

    itens = listar_saldo_horas(inicio, fim)
    usuarios = _mapa_usuarios()
    agora = timezone.now()

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
            periodo_inicio=inicio,
            periodo_fim=fim,
            sincronizado_em=agora,
        ))

    resultado = _gravar_em_lote(SaldoHoras, registros, [
        'usuario', 'nome', 'email', 'saldo_minutos', 'periodo_inicio',
        'periodo_fim', 'sincronizado_em'], chave=('employee_id',))
    resultado['lidos'] = len(itens)
    resultado['periodo'] = (inicio, fim)
    return resultado


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
