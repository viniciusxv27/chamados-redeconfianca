"""Impulso no app — como cada coisa do Impulso vira JSON (cards, meta, pontuação, ranking).

Só formata: quem vê o quê e o que cada ação faz moram no app ``impulso`` e são
chamados por ``api_mobile/modulos/impulso.py``.
"""
from decimal import Decimal

from django.utils import timezone

from impulso.models import Meta
from impulso.utils import FAIXAS, faixa_info

from .. import dados

ROTULO_COLUNA = {
    Meta.Status.A_FAZER: 'A fazer',
    Meta.Status.EM_ANDAMENTO: 'Em andamento',
    Meta.Status.ENTREGUE: 'Entregue',
    Meta.Status.CONCLUIDA: 'Concluída',
}
MESES = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho',
         'agosto', 'setembro', 'outubro', 'novembro', 'dezembro']


def numero(valor):
    if valor is None:
        return None
    return float(valor) if isinstance(valor, Decimal) else valor


def nome_do_mes(dia):
    return f'{MESES[dia.month - 1]} de {dia.year}'


def pessoa(request, user):
    """Pessoa enxuta (sem setor): os cards trazem muitas e o setor custaria uma consulta por pessoa."""
    if user is None:
        return None
    nome = user.get_full_name() or user.email
    return {'id': user.pk, 'nome': nome, 'primeiro_nome': user.first_name or nome.split(' ')[0],
            'foto': dados.url_absoluta(request, getattr(user, 'profile_picture', None))}


def faixa(nome):
    f = faixa_info(nome)
    return {'nome': f['nome'], 'rotulo': f['label'], 'regra': f['regra'], 'cor': f['cor'], 'premiada': f['premiada']}


def faixas():
    return [faixa(f['nome']) for f in FAIXAS]


def card(request, m, hoje=None):
    """Um card do Kanban. Espera ``m`` preparado por ``_preparar_cards`` (novidades) e com itens no prefetch."""
    hoje = hoje or timezone.localdate()
    itens = list(m.itens.all())
    novidades = getattr(m, 'novidades', None) or {}
    return {
        'id': m.pk,
        'titulo': m.titulo,
        'status': m.status,
        'status_nome': ROTULO_COLUNA.get(m.status, m.get_status_display()),
        'prioridade': m.prioridade,
        'prioridade_nome': m.get_prioridade_display(),
        'prazo': m.prazo.isoformat() if m.prazo else None,
        'dias_para_o_prazo': (m.prazo - hoje).days if m.prazo else None,
        'atrasado': m.is_overdue,
        'recorrente': m.recorrencia != Meta.Recorrencia.UNICA,
        'colaborador': pessoa(request, m.colaborador),
        'gestor': pessoa(request, m.gestor),
        'em_parceria': bool(getattr(m, 'em_parceria', False)),
        'feitos': sum(1 for i in itens if i.concluido),
        'total_itens': len(itens),
        'novidades': novidades,
        'tem_novidade': any(novidades.values()),
        'nota': numero(m.media_avaliacao),
        # O card da coluna Concluída não arrasta no portal (templates/impulso/_card_meta.html:9).
        'pode_mover': m.vale_pontos and m.status != Meta.Status.CONCLUIDA,
    }


def meta_detalhe(request, meta, *, itens, anexos, comentarios, participantes, parcerias, regras):
    user = request.user
    return {
        'id': meta.pk,
        'titulo': meta.titulo,
        'descricao': meta.descricao,
        'descricao_foto': dados.url_absoluta(request, meta.descricao_foto),
        'descricao_audio': dados.url_absoluta(request, meta.descricao_audio),
        'status': meta.status,
        'status_nome': ROTULO_COLUNA.get(meta.status, meta.get_status_display()),
        'prioridade': meta.prioridade,
        'prioridade_nome': meta.get_prioridade_display(),
        'prazo': meta.prazo.isoformat() if meta.prazo else None,
        'atrasado': meta.is_overdue,
        'recorrencia': meta.recorrencia,
        'recorrencia_nome': meta.get_recorrencia_display(),
        'apenas_dias_uteis': meta.apenas_dias_uteis,
        'aprovacao': meta.aprovacao,
        'aprovacao_nome': meta.get_aprovacao_display(),
        'motivo_recusa': meta.motivo_recusa or None,
        'colaborador': pessoa(request, meta.colaborador),
        'gestor': pessoa(request, meta.gestor),
        'criado_por': pessoa(request, meta.created_by),
        'criado_em': dados.iso(meta.created_at),
        'participantes': [pessoa(request, p) for p in participantes if p.id != meta.colaborador_id],
        'parcerias': [{
            'id': p.pk, 'convidado': pessoa(request, p.convidado), 'convidado_por': pessoa(request, p.convidado_por),
            'status': p.status, 'status_nome': p.get_status_display(), 'mensagem': p.mensagem or None,
        } for p in parcerias],
        'meu_convite': next(({'id': p.pk, 'convidado_por': pessoa(request, p.convidado_por), 'mensagem': p.mensagem or None}
                             for p in parcerias if p.convidado_id == user.id and p.esta_pendente), None),
        'itens': [{
            'id': i.pk, 'texto': i.texto, 'concluido': i.concluido,
            'concluido_por': pessoa(request, i.concluido_por) if i.concluido else None,
            'concluido_em': dados.iso(i.concluido_em) if i.concluido else None,
        } for i in itens],
        'feitos': sum(1 for i in itens if i.concluido),
        'anexos': [{
            'id': a.pk, 'nome': a.nome_exibicao, 'tipo': a.tipo,
            'url': a.url if a.tipo == a.Tipo.LINK else dados.url_absoluta(request, a.arquivo),
            'enviado_por': pessoa(request, a.enviado_por), 'enviado_em': dados.iso(a.enviado_em),
        } for a in anexos],
        'comentarios': [comentario(request, c) for c in comentarios],
        'entrega_link': meta.entrega_link or None,
        'entregue_em': dados.iso(meta.entregue_em),
        'avaliacao': ({
            'nota_qualidade': meta.nota_qualidade, 'nota_prazo': meta.nota_prazo,
            'media': numero(meta.media_avaliacao), 'comentario': meta.avaliacao_comentario or None,
            'avaliado_por': pessoa(request, meta.avaliado_por), 'avaliado_em': dados.iso(meta.avaliado_em),
        } if meta.is_avaliada else None),
        **regras,
        'url_web': f'/impulso/metas/{meta.pk}/',
    }


def comentario(request, c):
    return {'id': c.pk, 'autor': pessoa(request, c.autor), 'texto': c.mensagem,
            'criado_em': dados.iso(c.criado_em), 'sou_eu': c.autor_id == request.user.id}


def pontuacao(dados_faixa):
    """Saída de ``impulso.scoring.calcular_pontuacao`` → JSON (o que a tela "Minha pontuação" mostra)."""
    from impulso.scoring import blocos_resumo, linhas_detalhadas

    return {
        'percentual': numero(dados_faixa['percentual']),
        'total': numero(dados_faixa['total']),
        'maximo': numero(dados_faixa['aplicavel']),
        'pontos_sem_oportunidade': numero(dados_faixa['pontos_sem_oportunidade']),
        'faixa': faixa(dados_faixa['faixa']),
        'blocos': [{'nome': b['nome'], 'cor': b['cor'], 'valor': numero(b['valor']), 'maximo': numero(b['max']),
                    'pct': numero(b['pct'])} for b in blocos_resumo(dados_faixa)],
        'linhas': [{'bloco': l['bloco'], 'item': l['item'], 'pontos': numero(l['pontos']),
                    'maximo': numero(l['max']), 'info': l['info']} for l in linhas_detalhadas(dados_faixa)],
        'desempate': dados_faixa.get('desempate'),
    }
