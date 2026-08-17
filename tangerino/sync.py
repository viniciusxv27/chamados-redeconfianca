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
import re
import unicodedata

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from .client import (MOTIVO_FERIAS_ID, de_millis, listar_ferias, listar_funcionarios,
                     listar_marcacoes)
from .models import FeriasLancamento, MarcacaoPonto

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


def _gravar_em_lote(modelo, registros, campos):
    """Grava uma leva de registros com o mínimo de idas ao banco.

    `update_or_create` por item custa duas viagens cada; com ~3 mil marcações
    contra o Postgres remoto isso passava de dez minutos. Aqui são poucas
    consultas: uma para saber o que já existe, um bulk_create e um bulk_update.
    """
    if not registros:
        return {'criados': 0, 'atualizados': 0}

    por_id = {r.tangerino_id: r for r in registros}          # dedup na própria leva
    existentes = dict(modelo.objects
                      .filter(tangerino_id__in=list(por_id))
                      .values_list('tangerino_id', 'id'))

    novos = [r for tid, r in por_id.items() if tid not in existentes]
    alterados = []
    for tid, r in por_id.items():
        if tid in existentes:
            r.id = existentes[tid]
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
    registros = []
    for par in pares:
        tid = par.get('id')
        entrada = de_millis(par.get('dateIn'))
        if not tid or not entrada:
            continue
        eid = par.get('employeeId')
        registros.append(MarcacaoPonto(
            tangerino_id=tid,
            employee_id=eid,
            usuario=usuarios.get(eid),
            nome_funcionario=(par.get('employeeName') or '')[:200],
            dia=entrada.date(),
            entrada=entrada,
            saida=de_millis(par.get('dateOut')),
            nsr_entrada=par.get('nsrIn'),
            nsr_saida=par.get('nsrOut'),
            status=(par.get('status') or '')[:20],
            plataforma=(par.get('plataform') or '')[:30],
            editado=bool(par.get('edited')),
            ajuste=bool(par.get('adjust')),
            observacao=(par.get('comments') or '')[:2000],
            sincronizado_em=agora,     # auto_now não vale em bulk_update
        ))

    resultado = _gravar_em_lote(MarcacaoPonto, registros, [
        'employee_id', 'usuario', 'nome_funcionario', 'dia', 'entrada', 'saida',
        'nsr_entrada', 'nsr_saida', 'status', 'plataforma', 'editado', 'ajuste',
        'observacao', 'sincronizado_em'])
    resultado['lidos'] = len(pares)
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
            nome_funcionario=(emp.get('name') or '')[:200],
            inicio=inicio.date(),
            fim=(fim or inicio).date(),
            status=(item.get('status') or '')[:20],
            observacao=(item.get('observation') or '')[:2000],
            origem=(item.get('origem') or '')[:40],
            dia_inteiro=bool(item.get('fullDay', True)),
            sincronizado_em=agora,     # auto_now não vale em bulk_update
        ))

    resultado = _gravar_em_lote(FeriasLancamento, registros, [
        'employee_id', 'usuario', 'nome_funcionario', 'inicio', 'fim', 'status',
        'observacao', 'origem', 'dia_inteiro', 'sincronizado_em'])
    resultado['lidos'] = len(itens)
    return resultado
