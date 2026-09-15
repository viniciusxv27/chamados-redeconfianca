"""Impulso (/impulso/) no assistente — acesso completo, com as regras do módulo.

Leitura (impulso_consultar): o mesmo recorte de cada tela — painel, Kanban,
solicitações, atividades, ranking e pontuação, feedbacks, assiduidade, Conectar,
projetos foco, ideias e ciclos — pelos helpers de permissão das próprias views,
sem renderizar template nem tocar em sessão. Leitura nunca chama a OpenAI: o
feedback mostra a análise que já existe, e abrir a meta não a marca como vista.

Escrita: cada ação tem a prévia, que valida com as mesmas regras da view e do
modelo, resolve nomes e diz o que acontece junto (quem é avisado, o que some, a
próxima ocorrência), e a execução, que chama a view do Impulso como se a pessoa
tivesse clicado (chamar_view) e relê o banco para devolver ids e links. Nada roda
sem a confirmação do usuário numa nova mensagem (ferramentas.executar).

Fora do alcance, de propósito — só pela tela: a régua de pesos (pesos_editar),
encerrar ciclo (credita C$ de verdade e não volta), qualquer envio de arquivo
(anexo, certificado, vídeo, material do Conectar) e o progresso do vídeo, que é
a trava para ninguém concluir vídeo obrigatório sem assistir.
"""
import logging
import re
from datetime import date

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import QueryDict
from django.utils import timezone

from .comum import (Invalido, _acao, _bool, _cortar, _data, _dh, _dia, _falha, _ids, _int, _inteiro, _limite,
                    _lista_ids, _lista_txt, _nome, _nomes, _normal, _obj, _ok_json, _sem_erro, _sim, _titulo,
                    _txt, _url, chamar_view)

logger = logging.getLogger(__name__)
User = get_user_model()

SEM_ARQUIVO = 'Enviar arquivo (anexo, certificado, vídeo ou material) só pela tela.'

# O registro fica no fim do arquivo (TOOLS.update). Existe desde já porque o
# portal inteiro importa este nome (ferramentas.py → TOOLS.update(TOOLS_IMPULSO)).
TOOLS = {}


# ─── Base ────────────────────────────────────────────────────────────────────

def _gestor(user):
    from impulso.utils import is_impulso_manager

    return is_impulso_manager(user)


def _membro(user):
    from impulso.utils import is_impulso_member

    if not is_impulso_member(user):
        raise Invalido('Você não participa do Impulso: o módulo é do Escritório (ADM), dos ADMs das lojas e dos '
                       'gestores do Impulso. Quem libera o acesso é o RH/SUPERADMIN.')


def _d(dia):
    return f'{dia:%d/%m/%Y}' if dia else '—'


def _mm(dia):
    return f'{dia:%m/%Y}' if dia else '—'


def _tela(caminho):
    return f'Tela: {_url(caminho)}'


def _recados(r):
    """Os avisos de sucesso/informação que a tela mostraria depois da ação."""
    return ' '.join(texto for nivel, texto in r['avisos'] if nivel != 'error')


def _motivo(r):
    """Por que a ação não aconteceu, como a tela diria; erro inesperado da view vira orientação."""
    return _falha(r, 'a tela do Impulso deu um erro inesperado — confira nela se algo chegou a ser feito.')


def _id_em(destino, padrao):
    achado = re.search(padrao, destino or '')
    return int(achado.group(1)) if achado else None


def _escolha(valor, opcoes, campo, extras=None):
    """Valor livre ("em andamento", "EM_ANDAMENTO") → a chave do choices do modelo."""
    texto = _normal(valor).replace('-', '_').replace(' ', '_')
    if not texto:
        return ''
    mapa = {_normal(k): k for k in opcoes}
    mapa.update(extras or {})
    if texto in mapa:
        return mapa[texto]
    raise Invalido(f'{campo} inválido ("{valor}"). Use: ' + ', '.join(opcoes) + '.')


STATUS_META_EXTRA = {'fazer': 'A_FAZER', 'andamento': 'EM_ANDAMENTO', 'concluido': 'CONCLUIDA'}
RECORRENCIA_EXTRA = {'unica_vez': 'UNICA', 'nenhuma': 'UNICA', 'nao_repete': 'UNICA', 'diariamente': 'DIARIA',
                     'semanalmente': 'SEMANAL', 'quinzenalmente': 'QUINZENAL', 'mensalmente': 'MENSAL'}


def _link_valido(valor, campo='url'):
    texto = str(valor or '').strip()
    if not texto:
        raise Invalido(f'Informe o {campo}.')
    if not re.match(r'^https?://\S+$', texto, re.IGNORECASE):
        raise Invalido(f'{campo} precisa ser um endereço completo, começando com http:// ou https://.')
    if len(texto) > 200:
        raise Invalido(f'{campo} passa de 200 caracteres (o limite do campo): encurte o link.')
    return texto


def _nota(valor, campo):
    try:
        nota = int(str(valor).strip())
    except (TypeError, ValueError):
        nota = None
    if nota is None or not 0 <= nota <= 5:
        raise Invalido(f'{campo} deve ser um número inteiro de 0 a 5.')
    return nota


def _colaboradores_por_ids(ids, campo):
    """Colaboradores do Impulso pelos ids (a mesma lista que as telas oferecem); id de fora é erro."""
    from impulso.utils import get_colaboradores

    ids = list(dict.fromkeys(ids))
    achados = {u.pk: u for u in get_colaboradores().filter(pk__in=ids)}
    faltando = [str(i) for i in ids if i not in achados]
    if faltando:
        raise Invalido(f'{campo}: id {", ".join(faltando)} não é colaborador do Impulso '
                       '(veja impulso_consultar o_que=pessoas).')
    return [achados[i] for i in ids]


class _Pedido:
    """Só o request.GET que os filtros do Impulso leem (impulso.filtros.ler)."""

    def __init__(self, get):
        self.GET = get


def _filtros(args, mes_padrao=''):
    """Busca por nome, setor e mês com a régua de impulso/filtros.py, como na URL da tela."""
    from impulso import filtros

    q = QueryDict(mutable=True)
    if args.get('busca'):
        q[filtros.PARAM_NOME] = str(args['busca'])
    if args.get('setor_id'):
        q[filtros.PARAM_SETOR] = str(_inteiro(args['setor_id'], 'setor_id'))
    mes = str(args.get('mes') or '').strip()
    if _normal(mes) in ('todos', 'tudo', 'todos_os_meses'):
        q[filtros.PARAM_MES] = ''
    elif mes:
        if not filtros.periodo_do_mes(mes):
            raise Invalido(f'mes inválido ("{mes}"). Use AAAA-MM ou todos.')
        q[filtros.PARAM_MES] = mes
    return filtros.ler(_Pedido(q), mes_padrao=mes_padrao)


def _desc_filtros(f, com_mes=True):
    partes = []
    if com_mes:
        partes.append(f'mês {_mm(f["inicio"])}' if f['inicio'] else 'todos os meses')
    if f['nome']:
        partes.append(f'busca "{f["nome"]}"')
    if f['setor']:
        from users.models import Sector

        setor = Sector.objects.filter(pk=f['setor']).first()
        partes.append(f'setor {setor.name if setor else f["setor"]}')
    return ', '.join(partes)


def _prazo(prazo, hoje, encerrado=False):
    if not prazo:
        return 'sem prazo'
    texto = f'prazo {_d(prazo)}'
    if encerrado:
        return texto
    dias = (prazo - hoje).days
    if dias < 0:
        return texto + f' (ATRASADA há {-dias} dia(s))'
    if dias == 0:
        return texto + ' (vence hoje)'
    if dias == 1:
        return texto + ' (vence amanhã)'
    return texto + (f' (em {dias} dias)' if dias <= 7 else '')


def _meta_por_id(args, campo='meta_id'):
    from impulso.models import Meta

    if not args.get(campo):
        raise Invalido(f'Informe {campo} (os ids vêm em impulso_consultar o_que=metas, solicitacoes ou atividades).')
    meta = (Meta.objects.select_related('colaborador', 'gestor', 'solicitada_por', 'decidida_por', 'avaliado_por',
                                        'created_by', 'duplicada_de')
            .filter(pk=_inteiro(args.get(campo), campo)).first())
    if meta is None:
        raise Invalido(f'Meta #{args.get(campo)} não encontrada.')
    return meta


def _meta_visivel(user, args):
    from impulso import views as iv

    meta = _meta_por_id(args)
    if not iv._pode_ver_meta(user, meta):
        raise Invalido('Meta não encontrada — ou você não tem acesso a ela.')
    return meta


def _mexe_nos_passos(user, meta):
    """_pode_mexer_no_item da view: gestor, dono, participante ou superusuário."""
    return (user.is_superuser or user.pk in (meta.gestor_id, meta.colaborador_id)
            or meta.participantes.filter(pk=user.pk).exists())


# ─── Leitura: metas ──────────────────────────────────────────────────────────

def _linha_meta(m, user, hoje, novidades=False):
    partes = [f'meta #{m.pk}', m.titulo, m.get_status_display()]
    if m.aprovacao != m.Aprovacao.APROVADA:
        partes.append(m.get_aprovacao_display().lower())
    partes.append(_prazo(m.prazo, hoje, encerrado=m.status == m.Status.CONCLUIDA))
    partes.append('sua' if m.colaborador_id == user.pk else f'de {_nome(m.colaborador)} (id {m.colaborador_id})')
    partes.append('gestor: você' if m.gestor_id == user.pk else f'gestor: {_nome(m.gestor)}')
    feitos, total = m.progresso_itens
    if total:
        partes.append(f'to-do {feitos}/{total}')
    if m.recorrencia != m.Recorrencia.UNICA:
        partes.append(m.get_recorrencia_display().lower())
    if m.is_avaliada:
        partes.append(f'notas qualidade {m.nota_qualidade}/5 e prazo {m.nota_prazo}/5')
    if novidades:
        n = m.novidades_para(user)
        novas = [f'{n[chave]} {rotulo}' for chave, rotulo in (('comentarios', 'comentário(s)'), ('anexos', 'anexo(s)'),
                                                              ('itens', 'passo(s) marcado(s)')) if n[chave]]
        if n['entregue']:
            novas.append('entregue')
        if novas:
            partes.append('novidades desde a sua última visita: ' + ', '.join(novas))
    return ' · '.join(partes)


def _pendentes_do_sino(user):
    """O contador de solicitações do Kanban: o gestor vê o que decide; o colaborador, o que espera."""
    from impulso.models import Meta

    pendentes = Meta.objects.filter(aprovacao=Meta.Aprovacao.PENDENTE)
    if _gestor(user):
        return pendentes if user.is_superuser else pendentes.filter(gestor=user)
    return pendentes.filter(solicitada_por=user)


def _ler_metas(user, args):
    from impulso import filtros
    from impulso import views as iv
    from impulso.models import Meta

    hoje = timezone.localdate()
    qs = iv._metas_do_usuario(user).select_related('colaborador', 'gestor').prefetch_related('itens')
    if args.get('colaborador_id'):
        qs = qs.filter(colaborador_id=_inteiro(args['colaborador_id'], 'colaborador_id'))
    # Sem mês pedido, o Kanban abre no mês atual e corta pelo prazo — igual à tela.
    f = _filtros(args, mes_padrao=filtros.mes_atual())
    qs = filtros.por_mes(filtros.por(qs, f, ['colaborador', 'gestor']), f, 'prazo')
    status = _escolha(args.get('status'), Meta.Status.values, 'status', STATUS_META_EXTRA)
    if status:
        qs = qs.filter(status=status)
    metas = list(qs.order_by('prazo', 'titulo'))
    limite = _limite(args, 40, 150)

    if user.is_superuser:
        alcance = 'todas as metas aprovadas da rede (superadmin)'
    elif _gestor(user):
        alcance = 'as suas, as que você gerencia e as da equipe dos seus setores'
    else:
        alcance = 'as suas e as que você divide como responsável'
    linhas = [f'Kanban do Impulso — {_desc_filtros(f)}' + (f', status {status}' if status else '')
              + f' — {len(metas)} meta(s) (você vê {alcance}; pedidos pendentes ficam em o_que=solicitacoes):']
    mostradas = 0
    for coluna in Meta.KANBAN_STATUSES:
        if status and coluna.value != status:
            continue
        da_coluna = [m for m in metas if m.status == coluna.value]
        linhas.append(f'{coluna.label} ({len(da_coluna)}):' if da_coluna else f'{coluna.label}: nenhuma.')
        for m in da_coluna:
            if mostradas >= limite:
                break
            linhas.append('  ' + _linha_meta(m, user, hoje, novidades=True))
            mostradas += 1
    if mostradas < len(metas):
        linhas.append(f'… {len(metas) - mostradas} meta(s) fora da lista: filtre (mes, busca, colaborador_id, status) '
                      'ou aumente o limite.')
    pendentes = _pendentes_do_sino(user).count()
    if pendentes:
        linhas.append(f'Solicitações pendentes: {pendentes} — veja o_que=solicitacoes.')
    linhas.append(_tela('/impulso/metas/'))
    return '\n'.join(linhas)


def _pode_na_meta(user, meta):
    from impulso.utils import is_impulso_manager

    pode = []
    aberta = meta.vale_pontos and meta.status != meta.Status.CONCLUIDA
    if meta.pode_decidir(user):
        pode.append('aprovar ou recusar a solicitação')
    if meta.pode_cancelar_solicitacao(user):
        pode.append('cancelar a solicitação')
    if aberta:
        pode.append('mover no Kanban')
    if aberta and (meta.colaborador_id == user.pk or user.is_superuser):
        pode.append('entregar')
    if meta.vale_pontos and is_impulso_manager(user) and (meta.gestor_id == user.pk or user.is_superuser):
        pode.append('reavaliar (as notas são substituídas)' if meta.is_avaliada else 'avaliar com as notas')
    if meta.pode_editar(user):
        pode.append('editar e duplicar')
    elif meta.pode_solicitar_duplicacao(user):
        pode.append('pedir ao gestor a duplicação')
    if meta.gestor_id == user.pk or user.is_superuser:
        pode.append('trocar os outros responsáveis')
    if meta.pode_excluir(user):
        pode.append('excluir')
    if user.is_superuser or user.pk in (meta.gestor_id, meta.colaborador_id):
        pode.append('acrescentar passos ao to-do')
    if _mexe_nos_passos(user, meta):
        pode.append('marcar, editar e excluir passos')
    pode.append('comentar e anexar link')
    return pode


def _ler_meta(user, args):
    from impulso import views as iv

    meta = _meta_visivel(user, args)
    hoje = timezone.localdate()
    concluida = meta.status == meta.Status.CONCLUIDA
    linhas = [f'Meta #{meta.pk}: {meta.titulo}',
              f'Status: {meta.get_status_display()} · aprovação: {meta.get_aprovacao_display()} · '
              f'{_prazo(meta.prazo, hoje, concluida)}']
    if meta.recorrencia != meta.Recorrencia.UNICA:
        rec = f'Recorrência: {meta.get_recorrencia_display()}' + (' (somente dias úteis)' if meta.apenas_dias_uteis else '')
        if meta.recorrencia_de_id:
            rec += f' · ocorrência nº {meta.numero_da_ocorrencia} (a anterior é a meta #{meta.recorrencia_de_id})'
        proxima = meta.ocorrencias.first()
        if proxima:
            rec += f' · a próxima já existe: meta #{proxima.pk}, prazo {_d(proxima.prazo)}'
        elif meta.repete:
            rec += f' · ao ser avaliada, gera a próxima com prazo {_d(meta.proximo_prazo())}'
        elif not meta.recorrencia_ativa:
            rec += ' · geração automática desligada nesta meta'
        linhas.append(rec)
    participantes = list(meta.participantes.all())
    linhas.append(f'Responsável: {_nome(meta.colaborador)} (id {meta.colaborador_id}) · gestor: {_nome(meta.gestor)} '
                  f'(id {meta.gestor_id})' + (f' · outros responsáveis: {_nomes(participantes)}' if participantes else ''))
    origem = [f'criada por {_nome(meta.created_by)} em {_dh(meta.created_at)}']
    if meta.solicitada_por_id:
        origem.append(f'pedida por {_nome(meta.solicitada_por)}')
    if meta.decidida_por_id:
        origem.append(f'decidida por {_nome(meta.decidida_por)} em {_dh(meta.decidida_em)}')
    if meta.duplicada_de_id:
        origem.append(f'cópia da meta #{meta.duplicada_de_id} "{meta.duplicada_de.titulo}"')
    if meta.tarefa_origem_id:
        origem.append(f'importada da tarefa #{meta.tarefa_origem_id} (ata de reunião)')
    linhas.append('Origem: ' + ' · '.join(origem))
    if meta.recusada and meta.motivo_recusa:
        linhas.append(f'Motivo da recusa: {_cortar(meta.motivo_recusa, 1000)}')
    linhas.append('Descrição: ' + (_cortar(meta.descricao, 3000) or '—'))
    if meta.entregue_em or meta.entrega_link:
        entrega = [f'entregue em {_dh(meta.entregue_em)}' if meta.entregue_em else '',
                   f'link {meta.entrega_link}' if meta.entrega_link else '']
        linhas.append('Entrega: ' + ' · '.join(x for x in entrega if x))
    if meta.is_avaliada:
        linhas.append(f'Avaliação: qualidade {meta.nota_qualidade}/5 · prazo {meta.nota_prazo}/5 · por '
                      f'{_nome(meta.avaliado_por)} em {_dh(meta.avaliado_em)}'
                      + (f' · comentário: {_cortar(meta.avaliacao_comentario, 1000)}' if meta.avaliacao_comentario else ''))

    itens = list(meta.itens.select_related('concluido_por'))
    feitos = sum(1 for i in itens if i.concluido)
    linhas.append(f'To-do ({feitos}/{len(itens)} feitos):' if itens else 'To-do: sem passos.')
    for i in itens:
        marca = (f'[x] {i.texto} · marcado por {_nome(i.concluido_por)} em {_dh(i.concluido_em)}' if i.concluido
                 else f'[ ] {i.texto}')
        linhas.append(f'  item #{i.pk} · {marca}')

    anexos = list(meta.anexos.select_related('enviado_por'))
    linhas.append(f'Anexos ({len(anexos)}):' if anexos else 'Anexos: nenhum.')
    for a in anexos:
        a.meta = meta
        tipo = f'link {a.url}' if a.tipo == a.Tipo.LINK else 'arquivo (abrir pela tela)'
        mexe = ' · você pode renomear ou excluir' if iv._pode_mexer_no_anexo(user, a) else ''
        linhas.append(f'  anexo #{a.pk} · "{a.nome_exibicao}" · {tipo} · por {_nome(a.enviado_por)} em '
                      f'{_dh(a.enviado_em)}{mexe}')

    total = meta.comentarios.count()
    recentes = list(meta.comentarios.select_related('autor').order_by('-criado_em')[:15])[::-1]
    linhas.append((f'Comentários ({total}' + (', os 15 mais recentes' if total > 15 else '') + '):') if total
                  else 'Comentários: nenhum.')
    for c in recentes:
        linhas.append(f'  comentário #{c.pk} · {_dh(c.criado_em)} · {_nome(c.autor)}: {_cortar(c.mensagem, 600)}')
    linhas.append('O que você pode fazer: ' + ', '.join(_pode_na_meta(user, meta)) + '.')
    linhas.append(_tela(f'/impulso/metas/{meta.pk}/'))
    return '\n'.join(linhas)


def _ler_solicitacoes(user, args):
    from impulso import filtros
    from impulso.models import Meta

    hoje = timezone.localdate()
    base = (Meta.objects.select_related('colaborador', 'gestor', 'solicitada_por', 'duplicada_de', 'decidida_por')
            .prefetch_related('itens'))
    if _gestor(user):
        pendentes = base.filter(aprovacao=Meta.Aprovacao.PENDENTE)
        if not user.is_superuser:
            pendentes = pendentes.filter(gestor=user)
        titulo = 'Solicitações para você aprovar' + (' (todas da rede — superadmin)' if user.is_superuser else '')
    else:
        pendentes = base.filter(solicitada_por=user, aprovacao=Meta.Aprovacao.PENDENTE)
        titulo = 'Suas solicitações aguardando aprovação'
    recusadas = base.filter(aprovacao=Meta.Aprovacao.RECUSADA).filter(Q(gestor=user) | Q(solicitada_por=user))

    f = _filtros(args)
    alvos = ['colaborador', 'solicitada_por']
    pendentes = list(filtros.por_mes(filtros.por(pendentes, f, alvos), f, 'prazo'))
    recusadas = list(filtros.por_mes(filtros.por(recusadas, f, alvos), f, 'prazo')[:20])
    limite = _limite(args, 40, 150)

    linhas = [f'{titulo} — {_desc_filtros(f)} ({len(pendentes)}):' if pendentes
              else f'{titulo} — {_desc_filtros(f)}: nenhuma.']
    for m in pendentes[:limite]:
        extra = []
        if m.solicitada_por_id:
            extra.append('pedida por você' if m.solicitada_por_id == user.pk else f'pedida por {_nome(m.solicitada_por)}')
        if m.duplicada_de_id:
            extra.append(f'duplicação da meta #{m.duplicada_de_id} "{m.duplicada_de.titulo}"')
        if m.pode_decidir(user):
            extra.append('você pode aprovar ou recusar')
        if m.pode_cancelar_solicitacao(user):
            extra.append('você pode cancelar')
        linhas.append('  ' + ' · '.join([_linha_meta(m, user, hoje)] + extra))
    if len(pendentes) > limite:
        linhas.append(f'… mais {len(pendentes) - limite}: filtre ou aumente o limite.')
    if recusadas:
        linhas.append(f'Recusadas recentes ({len(recusadas)}):')
        for m in recusadas:
            motivo = f' · motivo: {_cortar(m.motivo_recusa, 200)}' if m.motivo_recusa else ''
            linhas.append(f'  meta #{m.pk} · {m.titulo} · para {_nome(m.colaborador)} · recusada por '
                          f'{_nome(m.decidida_por)} em {_dh(m.decidida_em)}{motivo}')
    linhas.append(_tela('/impulso/metas/solicitacoes/'))
    return '\n'.join(linhas)


def _ler_atividades(user, args):
    from impulso import filtros
    from impulso.models import Meta
    from impulso.utils import get_colaboradores_do_gestor

    hoje = timezone.localdate()
    metas = Meta.objects.exclude(status=Meta.Status.CONCLUIDA)
    if _gestor(user):
        equipe = list(get_colaboradores_do_gestor(user).values_list('id', flat=True))
        metas = metas.filter(Q(colaborador=user) | Q(colaborador_id__in=equipe) | Q(gestor=user)).distinct()
        alcance = 'as suas, as da equipe e as que você gerencia'
    else:
        metas = metas.filter(colaborador=user)
        alcance = 'as suas'
    if args.get('colaborador_id'):
        metas = metas.filter(colaborador_id=_inteiro(args['colaborador_id'], 'colaborador_id'))
    f = _filtros(args)
    metas = filtros.por_mes(filtros.por(metas, f, ['colaborador', 'gestor']), f, 'prazo')
    lista = list(metas.select_related('colaborador', 'gestor').prefetch_related('itens').order_by('prazo'))
    limite = _limite(args, 40, 150)
    atrasadas = sum(1 for m in lista if m.prazo and m.prazo < hoje)
    linhas = [f'Próximas atividades não concluídas ({alcance}) — {_desc_filtros(f)} — {len(lista)} meta(s), '
              f'{atrasadas} atrasada(s):' if lista else f'Nenhuma atividade em aberto ({alcance}) — {_desc_filtros(f)}.']
    linhas += ['  ' + _linha_meta(m, user, hoje) for m in lista[:limite]]
    if len(lista) > limite:
        linhas.append(f'… mais {len(lista) - limite}: filtre ou aumente o limite.')
    linhas.append(_tela('/impulso/atividades/'))
    return '\n'.join(linhas)


# ─── Leitura: painel, ranking e pontuação ────────────────────────────────────

def _linha_pontos(dados):
    from impulso.utils import faixa_info

    return (f'{dados["percentual"]}% · faixa {faixa_info(dados["faixa"])["label"]} · {dados["total"]} de '
            f'{dados["aplicavel"]} pontos (CONFIAR {dados["confiar"]}/{dados["confiar_max"]} · CONECTAR '
            f'{dados["conectar"]}/{dados["conectar_max"]} · INOVAR {dados["inovar"]}/{dados["inovar_max"]})')


def _ciclo_ativo():
    from impulso.models import Ciclo

    ciclo = Ciclo.objects.filter(status=Ciclo.Status.ABERTO).first()
    return f'Ciclo ativo: {ciclo.nome} ({_d(ciclo.inicio)} a {_d(ciclo.fim)}).' if ciclo else 'Nenhum ciclo aberto.'


def _ler_painel(user, args):
    from impulso import views as iv
    from impulso.models import ConclusaoConteudo, Ideia, ImpulsoFeedback, Meta, TarefaProjeto
    from impulso.scoring import calcular_pontuacao
    from impulso.utils import get_colaboradores_do_gestor

    gestor = _gestor(user)
    hoje = timezone.localdate()
    visiveis = iv._metas_do_usuario(user)
    minhas_abertas = Meta.objects.filter(colaborador=user).exclude(status=Meta.Status.CONCLUIDA)
    dados = calcular_pontuacao(user)
    papel = 'superadmin' if user.is_superuser else ('gestor do Impulso' if gestor else 'colaborador')
    linhas = [f'Impulso de {_nome(user)} ({papel})',
              f'Pontuação de {_mm(dados["inicio"])}, ao vivo: ' + _linha_pontos(dados),
              f'Kanban: {visiveis.count()} meta(s) visível(is) · '
              f'{visiveis.exclude(status=Meta.Status.CONCLUIDA).count()} em aberto · '
              f'{visiveis.filter(status=Meta.Status.CONCLUIDA).count()} concluída(s) · suas atrasadas: '
              f'{minhas_abertas.filter(prazo__lt=hoje).count()}']
    proximas = list(minhas_abertas.select_related('colaborador', 'gestor').prefetch_related('itens')
                    .order_by('prazo')[:5])
    if proximas:
        linhas.append('Suas próximas atividades:')
        linhas += ['  ' + _linha_meta(m, user, hoje) for m in proximas]
    abertas = TarefaProjeto.objects.filter(responsavel=user).exclude(status=TarefaProjeto.Status.CONCLUIDA).count()
    linhas.append(f'Feedbacks recebidos: {ImpulsoFeedback.objects.filter(colaborador=user).count()} · ideias suas: '
                  f'{Ideia.objects.filter(autor=user).count()} · tarefas de projeto em aberto: {abertas}')
    pendentes = _pendentes_do_sino(user).count()
    if pendentes:
        linhas.append(f'Solicitações de meta {"para decidir" if gestor else "esperando o gestor"}: {pendentes}')
    if gestor:
        entregues = Meta.objects.filter(gestor=user, status=Meta.Status.ENTREGUE).count()
        linhas.append(f'Metas entregues esperando a sua avaliação: {entregues} · ideias novas para avaliar: '
                      f'{Ideia.objects.filter(status=Ideia.Status.NOVA).count()}')
        conferir = ConclusaoConteudo.objects.filter(concluido=True, aprovacao=ConclusaoConteudo.Aprovacao.PENDENTE)
        if not user.is_superuser:
            conferir = conferir.filter(user_id__in=list(get_colaboradores_do_gestor(user).values_list('id', flat=True)))
        linhas.append(f'Conclusões do Conectar esperando conferência: {conferir.count()}')
    linhas.append(_ciclo_ativo())
    linhas.append('Detalhes: impulso_consultar com o_que=metas, solicitacoes, atividades, pontuacao, ranking, '
                  'feedbacks, assiduidade, conectar, projetos, ideias, ciclos ou pessoas.')
    linhas.append(_tela('/impulso/'))
    return '\n'.join(linhas)


def _ler_ranking(user, args):
    from impulso import filtros
    from impulso.scoring import calcular_pontuacao
    from impulso.utils import faixa_info, get_colaboradores

    # O ranking é sempre o do mês corrente (como na tela): o mês não entra no filtro.
    f = _filtros(dict(args, mes=None))
    ranking = [(c, calcular_pontuacao(c)) for c in filtros.pessoas(get_colaboradores(), f)]
    ranking.sort(key=lambda par: float(par[1]['percentual']), reverse=True)
    posicao = next((i for i, (c, _) in enumerate(ranking, 1) if c.pk == user.pk), None)
    minha = ranking[posicao - 1][1] if posicao else calcular_pontuacao(user)
    limite = _limite(args, 40, 150)
    recorte = _desc_filtros(f, com_mes=False)
    linhas = [f'Ranking do Impulso — {_mm(minha["inicio"])}, ao vivo — {len(ranking)} colaborador(es)'
              + (f' ({recorte})' if recorte else '') + ':']
    for i, (c, d) in enumerate(ranking[:limite], 1):
        linhas.append(f'{i}. {_nome(c)} (id {c.pk}) · {d["percentual"]}% · {faixa_info(d["faixa"])["label"]} · '
                      f'{d["total"]} pts · CONFIAR {d["confiar"]} · CONECTAR {d["conectar"]} · INOVAR {d["inovar"]}'
                      + (' ← você' if c.pk == user.pk else ''))
    if len(ranking) > limite:
        linhas.append(f'… mais {len(ranking) - limite} colaborador(es): aumente o limite ou filtre por setor/busca.')
    linhas.append('Sua pontuação: ' + _linha_pontos(minha)
                  + (f' — posição {posicao} de {len(ranking)}.' if posicao
                     else ' — você não está nesta lista de colaboradores.'))
    linhas.append(_ciclo_ativo())
    linhas.append('Faixas: Impulso 100% · Ouro acima de 90% · Prata acima de 70% · Bronze até 70%. Cada mês Ouro ou '
                  'Impulso reserva 100 C$, pagos quando o ciclo é encerrado.')
    if args.get('mes'):
        linhas.append('O ranking é sempre o do mês corrente, ao vivo; meses já fechados ficam em o_que=ciclos.')
    linhas.append('Detalhe de uma pessoa: o_que=pontuacao com colaborador_id (gestor vê qualquer um; os demais, a própria).')
    linhas.append(_tela('/impulso/acompanhamento/'))
    return '\n'.join(linhas)


def _ler_pontuacao(user, args):
    from impulso import filtros
    from impulso.models import PontuacaoMensal
    from impulso.scoring import calcular_pontuacao, linhas_detalhadas
    from impulso.utils import faixa_info, get_colaboradores

    colaboradores = get_colaboradores()
    alvo = user
    if args.get('colaborador_id') and _inteiro(args['colaborador_id'], 'colaborador_id') != user.pk:
        alvo = colaboradores.filter(pk=_inteiro(args['colaborador_id'], 'colaborador_id')).first()
        if alvo is None:
            raise Invalido('Colaborador não encontrado no Impulso (veja o_que=pessoas).')
        if not _gestor(user):
            raise Invalido('Você só pode ver o seu próprio detalhamento.')
    inicio = fim = None
    mes = str(args.get('mes') or '').strip()
    if mes and _normal(mes) != 'todos':
        periodo = filtros.periodo_do_mes(mes)
        if not periodo:
            raise Invalido(f'mes inválido ("{mes}"). Use AAAA-MM.')
        inicio, fim = periodo
    dados = calcular_pontuacao(alvo, inicio=inicio, fim=fim)
    hoje = timezone.localdate()
    corrente = dados['inicio'] == date(hoje.year, hoje.month, 1)
    quem = 'Sua pontuação' if alvo.pk == user.pk else f'Pontuação de {_nome(alvo)} (id {alvo.pk})'
    linhas = [f'{quem} — {_mm(dados["inicio"])}'
              + (' (mês corrente, ao vivo):' if corrente else ' (calculada agora, com os dados de hoje):'),
              _linha_pontos(dados)]
    if dados['pontos_sem_oportunidade'] > 0:
        linhas.append(f'{dados["pontos_sem_oportunidade"]} ponto(s) sem oportunidade no mês: item que não existiu '
                      'vale zero e continua no total, sem ser falha da pessoa.')
    linhas.append('Item a item:')
    for linha in linhas_detalhadas(dados):
        linhas.append(f'  {linha["bloco"]} · {linha["item"]}: {linha["pontos"]}/{linha["max"]} — {linha["info"]}')
    historico = list(PontuacaoMensal.objects.filter(user=alvo).select_related('mes', 'mes__ciclo')
                     .order_by('-mes__referencia')[:12])
    if historico:
        linhas.append('Meses fechados (valor oficial, congelado no fechamento):')
        for p in historico:
            linhas.append(f'  {_mm(p.mes.referencia)} · ciclo {p.mes.ciclo.nome} · {p.percentual}% · '
                          f'{faixa_info(p.faixa)["label"]} · {p.total} pts'
                          + (f' · {p.confiancas_previstas} C$ reservados' if p.confiancas_previstas else ''))
        fechado = next((p for p in historico if inicio and p.mes.referencia == inicio and p.mes.is_fechado), None)
        if fechado:
            linhas.append(f'Esse mês já foi fechado: o valor oficial é o congelado ({fechado.percentual}%), não o '
                          'calculado agora.')
    na_lista = colaboradores.filter(pk=alvo.pk).exists()
    linhas.append(_tela(f'/impulso/acompanhamento/{alvo.pk}/' if na_lista else '/impulso/'))
    return '\n'.join(linhas)


# ─── Leitura: feedbacks e assiduidade ────────────────────────────────────────

def _nota_ia(fb):
    if fb.nota_ia is None:
        return 'sem análise da IA' if not fb.ai_summary else 'IA sem nota'
    return f'nota IA {fb.nota_ia} ({fb.faixa_da_nota["rotulo"]})'


def _ler_feedbacks(user, args):
    from impulso import filtros
    from impulso.models import ImpulsoFeedback
    from impulso.utils import get_colaboradores_do_gestor

    if user.is_superuser:
        qs, alcance = ImpulsoFeedback.objects.all(), 'todos da empresa (superadmin)'
    elif _gestor(user):
        equipe = list(get_colaboradores_do_gestor(user).values_list('id', flat=True))
        qs = ImpulsoFeedback.objects.filter(Q(gestor=user) | Q(colaborador_id__in=equipe)).distinct()
        alcance = 'os que você aplicou e os da equipe dos seus setores'
    else:
        qs, alcance = ImpulsoFeedback.objects.filter(colaborador=user), 'os que você recebeu'
    qs = qs.select_related('colaborador', 'gestor')
    if args.get('colaborador_id'):
        qs = qs.filter(colaborador_id=_inteiro(args['colaborador_id'], 'colaborador_id'))
    f = _filtros(args)
    if f['inicio']:
        qs = qs.filter(referencia_mes__year=f['inicio'].year, referencia_mes__month=f['inicio'].month)
    situacao = _normal(args.get('status')).replace(' ', '_')
    if situacao == 'sem_analise':
        qs = qs.filter(ai_summary='')
    elif situacao == 'com_analise':
        qs = qs.exclude(ai_summary='')
    elif situacao == 'atencao':
        qs = qs.filter(nota_ia__lt=5)
    elif situacao:
        raise Invalido('status, em feedbacks: sem_analise | com_analise | atencao (nota da IA abaixo de 5).')
    busca = ' '.join(str(args.get('busca') or '').split())
    if busca:
        # Como a tela: procura no nome de quem recebeu e no texto do feedback.
        qs = qs.filter(Q(colaborador__first_name__icontains=busca) | Q(colaborador__last_name__icontains=busca)
                       | Q(colaborador__email__icontains=busca) | Q(pontos_fortes__icontains=busca)
                       | Q(pontos_melhoria__icontains=busca))
    if f['setor']:
        qs = filtros.por(qs, {'nome': '', 'setor': f['setor'], 'ativo': True}, 'colaborador')
    lista = list(qs.order_by('-referencia_mes', '-criado_em'))
    limite = _limite(args, 30, 100)
    notas = [float(x.nota_ia) for x in lista if x.nota_ia is not None]
    media = f'{sum(notas) / len(notas):.1f}' if notas else '—'
    linhas = [f'Feedbacks do Impulso ({alcance}) — {len(lista)} encontrado(s) · com análise da IA: '
              f'{sum(1 for x in lista if x.ai_summary)} · média da nota IA: {media} · abaixo de 5: '
              f'{sum(1 for n in notas if n < 5)}']
    for fb in lista[:limite]:
        linhas.append(f'  feedback #{fb.pk} · {_mm(fb.referencia_mes)} · para {_nome(fb.colaborador)} '
                      f'(id {fb.colaborador_id}) · de {_nome(fb.gestor)} · {_nota_ia(fb)} · fortes: '
                      f'{_cortar(fb.pontos_fortes, 140)} · a melhorar: {_cortar(fb.pontos_melhoria, 140)}')
    if len(lista) > limite:
        linhas.append(f'… mais {len(lista) - limite}: filtre (mes, colaborador_id, busca, status) ou aumente o limite.')
    linhas.append('Detalhe e análise completa: o_que=feedback com feedback_id (abre para quem deu, quem recebeu e o '
                  'superadmin). A nota do formulário formal fica em /feedback/ e aparece no item "Feedback do gestor" '
                  'de o_que=pontuacao.')
    linhas.append(_tela('/impulso/feedbacks/'))
    return '\n'.join(linhas)


def _ler_feedback(user, args):
    from impulso.models import ImpulsoFeedback

    if not args.get('feedback_id'):
        raise Invalido('Informe feedback_id (os ids vêm em o_que=feedbacks).')
    fb = (ImpulsoFeedback.objects.select_related('colaborador', 'gestor')
          .filter(pk=_inteiro(args['feedback_id'], 'feedback_id')).first())
    if fb is None or not (user.is_superuser or user.pk in (fb.gestor_id, fb.colaborador_id)):
        raise Invalido('Feedback não encontrado — ou você não tem acesso a ele (só quem deu, quem recebeu e o superadmin).')
    linhas = [f'Feedback #{fb.pk} de {_mm(fb.referencia_mes)}',
              f'Para {_nome(fb.colaborador)} (id {fb.colaborador_id}) · de {_nome(fb.gestor)} (id {fb.gestor_id}) · '
              f'registrado em {_dh(fb.criado_em)}',
              'Pontos fortes: ' + _cortar(fb.pontos_fortes, 3000),
              'Pontos a melhorar: ' + _cortar(fb.pontos_melhoria, 3000),
              'Comentário geral: ' + (_cortar(fb.comentario, 3000) or '—')]
    if fb.ai_summary:
        linhas.append(f'Análise da IA ({_nota_ia(fb)}, gerada em {_dh(fb.ai_summary_generated_at)}):')
        linhas.append(_cortar(fb.ai_summary, 4000))
    else:
        erro = f' Último erro: {_cortar(fb.ai_summary_error, 300)}.' if fb.ai_summary_error else ''
        linhas.append('Análise da IA: ainda não gerada.' + erro + ' Abrir o feedback na tela tenta gerar; quem deu o '
                      'feedback pode refazer com impulso_feedback (acao=regenerar_ia).')
    linhas.append(_tela(f'/impulso/feedbacks/{fb.pk}/'))
    return '\n'.join(linhas)


def _assiduidade_detalhe(rotulo, resposta):
    pontos, maximo, d = resposta
    if d.get('sem_dias_avaliaveis'):
        return [f'{rotulo}: sem dia útil avaliável ainda neste mês.']
    linhas = [f'{rotulo}: {pontos}/{maximo} — {d.get("motivo") or "—"}']
    extras = [f'{d.get("dias_completos", 0)} de {d.get("dias_uteis", 0)} dia(s) útil(eis) com as 4 batidas',
              f'ajustes usados {d.get("total_ajustes", 0)} de {d.get("limite_ajustes", 3)}']
    if d.get('total_perdoados'):
        extras.append(f'{d["total_perdoados"]} ajuste(s) em dia de exceção, fora da conta')
    if d.get('no_prazo'):
        extras.append('ainda dá para ajustar (prazo de 24 h): '
                      + ', '.join(f'{x["data"]:%d/%m} ({x["batidas"]} batida(s))' for x in d['no_prazo']))
    if d.get('incompletos'):
        extras.append('incompletos fora do prazo: ' + ', '.join(f'{x["data"]:%d/%m}' for x in d['incompletos'][:10]))
    if d.get('faltas'):
        extras.append('falta injustificada: ' + ', '.join(f'{x:%d/%m}' for x in d['faltas']))
    if d.get('faltas_indisponiveis'):
        extras.append('as faltas lançadas no Tangerino não puderam ser consultadas agora')
    linhas.append('  ' + ' · '.join(extras))
    return linhas


def _ler_assiduidade(user, args):
    from impulso import filtros
    from impulso.assiduidade_ponto import LIMITE_AJUSTES_MES, nota_assiduidade_ponto
    from impulso.models import ExcecaoAssiduidade
    from impulso.utils import get_colaboradores_do_gestor

    hoje = timezone.localdate()
    f = _filtros(args)
    ano, mes = (f['inicio'].year, f['inicio'].month) if f['inicio'] else (hoje.year, hoje.month)
    linhas = [f'Assiduidade de {mes:02d}/{ano}, lida do ponto eletrônico: 4 batidas por dia útil, até '
              f'{LIMITE_AJUSTES_MES} ajustes no mês; passar disso, dia incompleto fora do prazo ou falta injustificada '
              'zera os 10 pontos.']
    minha = nota_assiduidade_ponto(user, ano, mes)
    if minha is None:
        linhas.append('Você: sem ponto eletrônico sincronizado neste mês (ou dispensado de bater ponto) — se houver '
                      'nota, ela sai da folha de ponto importada (veja o_que=pontuacao).')
    else:
        linhas += _assiduidade_detalhe('Você', minha)
    if _gestor(user) or user.is_superuser:
        equipe = []
        for pessoa in filtros.pessoas(get_colaboradores_do_gestor(user), f):
            if pessoa.pk == user.pk or not pessoa.tangerino_employee_id:
                continue
            resposta = nota_assiduidade_ponto(pessoa, ano, mes)
            if resposta is not None:
                equipe.append((pessoa, resposta))
        # Quem perdeu os pontos primeiro; depois quem está perto do limite de ajustes.
        equipe.sort(key=lambda x: (x[1][0] > 0, -(x[1][2].get('total_ajustes') or 0), x[0].first_name))
        limite = _limite(args, 40, 150)
        recorte = _desc_filtros(f, com_mes=False)
        linhas.append((f'Equipe ({len(equipe)} com ponto' + (f'; {recorte}' if recorte else '') + '):') if equipe
                      else 'Equipe: ninguém com ponto sincronizado neste recorte.')
        for pessoa, (pontos, maximo, d) in equipe[:limite]:
            motivo = 'sem dia útil avaliável' if d.get('sem_dias_avaliaveis') else (d.get('motivo') or '—')
            linhas.append(f'  {_nome(pessoa)} (id {pessoa.pk}) · {pontos}/{maximo} · ajustes '
                          f'{d.get("total_ajustes", 0)}/{LIMITE_AJUSTES_MES} · {motivo}')
        if len(equipe) > limite:
            linhas.append(f'… mais {len(equipe) - limite}: filtre por setor/busca ou aumente o limite.')
    excecoes = list(ExcecaoAssiduidade.objects.filter(data__year=ano, data__month=mes).select_related('criado_por'))
    if excecoes:
        linhas.append('Dias de exceção do mês (o ajuste desses dias não conta no limite):')
        linhas += [f'  exceção #{e.pk} · {_dia(e.data)} · "{e.motivo}" · por {_nome(e.criado_por)}' for e in excecoes]
    else:
        linhas.append('Nenhum dia de exceção neste mês.')
    if user.is_superuser:
        linhas.append('Como superadmin, você cria e desfaz exceções com impulso_administrar.')
    linhas.append(_tela(f'/impulso/assiduidade/?mes={mes}&ano={ano}'))
    return '\n'.join(linhas)


# ─── Leitura: Conectar, projetos, ideias, ciclos e pessoas ───────────────────

STATUS_TAREFA_EXTRA = {'fazer': 'A_FAZER', 'andamento': 'EM_ANDAMENTO', 'concluido': 'CONCLUIDA',
                       'feita': 'CONCLUIDA', 'feito': 'CONCLUIDA'}
STATUS_IDEIA_EXTRA = {'analise': 'EM_ANALISE', 'aprovar': 'APROVADA', 'aprovado': 'APROVADA',
                      'arquivar': 'ARQUIVADA', 'arquivado': 'ARQUIVADA'}


def _periodo_txt(inicio, fim):
    if not inicio and not fim:
        return 'sem período (vale sempre)'
    return f'período {_d(inicio) if inicio else "…"} a {_d(fim) if fim else "…"}'


def _situacao_conclusao(conc):
    if conc is None or not conc.concluido:
        if conc is not None and conc.video_duracao:
            return (f'não concluído (vídeo assistido até {int(conc.video_assistido_ate // 60)}min'
                    f'{int(conc.video_assistido_ate % 60):02d}s de {int(conc.video_duracao // 60)}min)')
        return 'não concluído'
    texto = f'concluído em {_dh(conc.concluido_em)}'
    if conc.aprovacao == conc.Aprovacao.APROVADA:
        return texto + ' e aprovado (vale ponto)'
    if conc.aprovacao == conc.Aprovacao.RECUSADA:
        return texto + f', mas RECUSADO: "{_cortar(conc.observacao, 200)}" — corrija e marque de novo'
    return texto + ', esperando a conferência do gestor'


def _ler_conectar(user, args):
    from impulso import filtros
    from impulso import views as iv
    from impulso.models import ConclusaoConteudo
    from impulso.utils import get_colaboradores_do_gestor

    gestor = _gestor(user)
    conteudos = iv.conteudos_para(user, gestor).prefetch_related('obrigatorio_para')
    f = _filtros(args)
    # Nome e setor procuram por PESSOA ("o que foi dirigido à Ana"), como na tela.
    if f['nome'] or f['setor']:
        conteudos = filtros.por(conteudos, f, 'obrigatorio_para')
    conteudos = list(filtros.por_periodo(conteudos, f, 'inicio', 'fim'))
    minhas = {c.conteudo_id: c for c in ConclusaoConteudo.objects.filter(user=user)}
    limite = _limite(args, 40, 150)
    alcance = 'todo o catálogo ativo (gestor)' if gestor else 'o que foi direcionado a você ou vale para toda a equipe'
    linhas = [f'Conectar — {alcance} — {_desc_filtros(f)} — {len(conteudos)} conteúdo(s):']
    for c in conteudos[:limite]:
        para = list(c.obrigatorio_para.all())
        partes = [f'conteúdo #{c.pk}', c.rotulo, c.titulo, 'obrigatório' if c.obrigatorio else 'opcional',
                  _periodo_txt(c.inicio, c.fim)]
        if not c.periodo_ativo():
            partes.append('fora do período hoje')
        if not para:
            partes.append('vale para toda a equipe')
        else:
            partes.append(f'direcionado a {_nomes(para, 8)}' if gestor else f'direcionado a {len(para)} pessoa(s)')
        if c.url:
            partes.append(f'link {c.url}')
        if c.video_reproduzivel:
            partes.append('vídeo do portal: assistir até o fim na tela para concluir')
        partes.append('sua conclusão: ' + _situacao_conclusao(minhas.get(c.pk)))
        linhas.append('  ' + ' · '.join(partes))
    if len(conteudos) > limite:
        linhas.append(f'… mais {len(conteudos) - limite}: filtre ou aumente o limite.')
    if gestor:
        equipe = set(get_colaboradores_do_gestor(user).values_list('id', flat=True))
        fila = []
        for c in (ConclusaoConteudo.objects.filter(concluido=True, aprovacao=ConclusaoConteudo.Aprovacao.PENDENTE)
                  .select_related('user', 'conteudo').order_by('concluido_em')):
            if not (user.is_superuser or c.user_id in equipe) or not filtros.combina(c.user, f):
                continue
            if f['inicio'] and not (c.concluido_em and f['inicio'] <= timezone.localtime(c.concluido_em).date() <= f['fim']):
                continue
            fila.append(c)
        linhas.append(f'Esperando a sua conferência ({len(fila)}):' if fila else 'Nada esperando conferência neste recorte.')
        for c in fila[:limite]:
            linhas.append(f'  conclusão #{c.pk} · {_nome(c.user)} (id {c.user_id}) · conteúdo #{c.conteudo_id} '
                          f'"{c.conteudo.titulo}" · concluído em {_dh(c.concluido_em)} · '
                          + ('com certificado (ver na tela)' if c.certificado else 'sem certificado')
                          + ('' if c.pode_decidir(user) else ' · é a sua: outro gestor confere'))
        linhas.append('Conferir: impulso_conectar (acao=aprovar_conclusao ou recusar_conclusao, com conclusao_id).')
    linhas.append(_tela('/impulso/conectar/'))
    return '\n'.join(linhas)


def _ler_projetos(user, args):
    from impulso import filtros
    from impulso.models import ProjetoFoco, TarefaProjeto
    from impulso.utils import get_colaboradores_do_gestor

    gestor = _gestor(user)
    projetos = ProjetoFoco.objects.all() if gestor else ProjetoFoco.objects.filter(membros=user, ativo=True)
    f = _filtros(args)
    projetos = filtros.por(projetos, f, 'membros')
    if f['inicio']:
        # O mês do projeto é o das tarefas dele (ou o da criação), como na tela.
        projetos = projetos.filter(Q(tarefas__prazo__gte=f['inicio'], tarefas__prazo__lte=f['fim'])
                                   | Q(criado_em__date__gte=f['inicio'], criado_em__date__lte=f['fim'])).distinct()
    lista = list(projetos.prefetch_related('membros', 'tarefas').distinct())
    limite = _limite(args, 40, 150)
    linhas = [f'Projetos foco — {"todos (gestor)" if gestor else "os ativos em que você está"} — {_desc_filtros(f)} — '
              f'{len(lista)} projeto(s):']
    for p in lista[:limite]:
        feitas, total = p.progresso_tarefas
        estado = f'concluído em {_dh(p.concluido_em)}' if p.concluido else 'em andamento'
        pronto = ' · todas as tarefas entregues: pronto para concluir' if p.tudo_entregue and not p.concluido else ''
        linhas.append(f'  projeto #{p.pk} · {p.nome} · {"ativo" if p.ativo else "INATIVO"} · {estado} · '
                      f'{len(p.membros.all())} membro(s) · tarefas {feitas}/{total} concluídas{pronto}')

    tarefas = TarefaProjeto.objects.all()
    if gestor:
        equipe = list(get_colaboradores_do_gestor(user).values_list('id', flat=True))
        tarefas = tarefas.filter(Q(responsavel=user) | Q(responsavel_id__in=equipe)).distinct()
    else:
        tarefas = tarefas.filter(responsavel=user)
    tarefas = filtros.por_mes(filtros.por(tarefas, f, 'responsavel'), f, 'prazo')
    status = _escolha(args.get('status'), TarefaProjeto.Status.values, 'status', STATUS_TAREFA_EXTRA)
    tarefas = tarefas.filter(status=status) if status else tarefas.exclude(status=TarefaProjeto.Status.CONCLUIDA)
    tarefas = list(tarefas.select_related('projeto', 'responsavel').order_by('status', 'prazo'))
    rotulo = f'status {status}' if status else 'não concluídas'
    linhas.append((f'Tarefas {"suas e da equipe" if gestor else "suas"} ({rotulo}, {len(tarefas)}):') if tarefas
                  else f'Nenhuma tarefa {"sua ou da equipe" if gestor else "sua"} ({rotulo}).')
    for t in tarefas[:limite]:
        linhas.append(f'  tarefa #{t.pk} · {t.titulo} · projeto #{t.projeto_id} "{t.projeto.nome}" · responsável: '
                      f'{"você" if t.responsavel_id == user.pk else _nome(t.responsavel)} · '
                      f'{_prazo(t.prazo, timezone.localdate(), t.status == t.Status.CONCLUIDA)} · {t.get_status_display()}')
    linhas.append(_tela('/impulso/conectar/projetos/'))
    return '\n'.join(linhas)


def _projeto_visivel(user, args):
    from impulso import views as iv
    from impulso.models import ProjetoFoco

    if not args.get('projeto_id'):
        raise Invalido('Informe projeto_id (os ids vêm em impulso_consultar o_que=projetos).')
    projeto = (ProjetoFoco.objects.select_related('criado_por', 'concluido_por')
               .filter(pk=_inteiro(args['projeto_id'], 'projeto_id')).first())
    if projeto is None or not iv._pode_ver_projeto(user, projeto):
        raise Invalido('Projeto não encontrado — ou você não faz parte dele.')
    return projeto


def _ler_projeto(user, args):
    projeto = _projeto_visivel(user, args)
    gestor = _gestor(user)
    hoje = timezone.localdate()
    membros = list(projeto.membros.all())
    feitas, total = projeto.progresso_tarefas
    estado = (f'concluído em {_dh(projeto.concluido_em)} por {_nome(projeto.concluido_por)}' if projeto.concluido
              else 'em andamento')
    linhas = [f'Projeto foco #{projeto.pk}: {projeto.nome}',
              f'{"Ativo" if projeto.ativo else "INATIVO (as tarefas não pontuam)"} · {estado} · criado por '
              f'{_nome(projeto.criado_por)} em {_dh(projeto.criado_em)}',
              'Descrição: ' + (_cortar(projeto.descricao, 2000) or '—'),
              f'Equipe ({len(membros)}): {_nomes(membros, 40)}' if membros else 'Equipe: ninguém ainda.',
              f'Tarefas concluídas: {feitas} de {total}'
              + (' — todas entregues: pronto para concluir' if projeto.tudo_entregue and not projeto.concluido else '')]
    tarefas = projeto.tarefas.select_related('responsavel')
    if not gestor:
        tarefas = tarefas.filter(responsavel=user)
    tarefas = list(tarefas)
    linhas.append(('Tarefas:' if gestor else 'Suas tarefas neste projeto:') if tarefas
                  else ('Tarefas: nenhuma.' if gestor else 'Nenhuma tarefa sua neste projeto.'))
    for t in tarefas:
        descricao = f' · {_cortar(t.descricao, 200)}' if t.descricao else ''
        linhas.append(f'  tarefa #{t.pk} · {t.titulo} · {t.get_status_display()} · responsável: '
                      f'{"você" if t.responsavel_id == user.pk else _nome(t.responsavel)} · '
                      f'{_prazo(t.prazo, hoje, t.status == t.Status.CONCLUIDA)}{descricao}')
    anexos = list(projeto.anexos.select_related('enviado_por'))
    linhas.append(f'Anexos ({len(anexos)}):' if anexos else 'Anexos: nenhum.')
    for a in anexos:
        a.projeto = projeto
        tipo = f'link {a.url}' if a.tipo == a.Tipo.LINK else 'arquivo (abrir pela tela)'
        linhas.append(f'  anexo #{a.pk} · "{a.nome_exibicao}" · {tipo} · por {_nome(a.enviado_por)} em '
                      f'{_dh(a.enviado_em)}' + (' · você pode excluir' if a.pode_mexer(user) else ''))
    linhas.append('Como gestor, você edita o projeto, cria tarefas, muda status e conclui/reabre (impulso_projeto).'
                  if gestor else 'Você muda o status das suas tarefas e anexa links (impulso_projeto).')
    linhas.append(_tela(f'/impulso/conectar/projetos/{projeto.pk}/'))
    return '\n'.join(linhas)


def _ler_ideias(user, args):
    from impulso import filtros
    from impulso.models import Ideia

    gestor = _gestor(user)
    ideias = Ideia.objects.all() if gestor else Ideia.objects.filter(Q(autor=user) | Q(participantes=user)).distinct()
    # Mês sim, pessoa não: filtrar por nome ou setor entregaria a autoria que o gestor não vê.
    f = _filtros({'mes': args.get('mes')})
    ideias = filtros.por_mes(ideias, f, 'criado_em__date')
    status = _escolha(args.get('status'), Ideia.Status.values, 'status', STATUS_IDEIA_EXTRA)
    if status:
        ideias = ideias.filter(status=status)
    lista = list(ideias.select_related('autor').prefetch_related('participantes').order_by('-criado_em'))
    limite = _limite(args, 30, 100)
    alcance = ('todas, com a autoria oculta de propósito — o gestor avalia a ideia, não quem escreveu' if gestor
               else 'as suas e as em que você participa')
    linhas = [f'Ideias do Inovar ({alcance}) — {_desc_filtros(f)}' + (f', status {status}' if status else '')
              + f' — {len(lista)}:']
    for ideia in lista[:limite]:
        equipe = list(ideia.participantes.all())
        partes = [f'ideia #{ideia.pk}', ideia.get_status_display(), f'impacto: {ideia.setor_impacto}',
                  f'criada em {_dh(ideia.criado_em)}', f'ideia: {_cortar(ideia.descricao, 300)}',
                  f'motivo: {_cortar(ideia.motivo, 200)}']
        if ideia.resposta_gestor:
            partes.append(f'retorno do gestor: {_cortar(ideia.resposta_gestor, 200)}')
        # O único caso em que nomes aparecem: a ideia é de quem está vendo.
        if ideia.autor_id == user.pk:
            partes.append('sua' + (f', com {_nomes(equipe)}' if equipe else '')
                          + (' · você ainda pode editar' if ideia.editavel else ''))
        elif any(p.pk == user.pk for p in equipe):
            partes.append('você participa (autoria oculta)')
        linhas.append('  ' + ' · '.join(partes))
    if len(lista) > limite:
        linhas.append(f'… mais {len(lista) - limite}: filtre por mes ou status, ou aumente o limite.')
    if any(args.get(k) for k in ('busca', 'setor_id', 'colaborador_id')):
        linhas.append('No Inovar não há filtro por pessoa ou setor: ele revelaria quem escreveu cada ideia.')
    if gestor:
        linhas.append('Decidir: impulso_ideia (acao=decidir, com status e resposta ao autor).')
    linhas.append(_tela('/impulso/inovar/'))
    return '\n'.join(linhas)


def _ler_mes_do_ciclo(user, mes, f, limite):
    from impulso import ciclos as servico
    from impulso import filtros
    from impulso.utils import faixa_info

    pontuacoes = list(filtros.por(mes.pontuacoes.select_related('user', 'setor').order_by('-percentual'), f, 'user'))
    estado = (f'fechado em {_dh(mes.fechado_em)} por {_nome(mes.fechado_por)}' if mes.is_fechado
              else 'aberto — a pontuação só é congelada ao fechar')
    recorte = _desc_filtros(f, com_mes=False)
    linhas = [f'Mês #{mes.pk} ({_mm(mes.referencia)}) do ciclo "{mes.ciclo.nome}" · {estado}',
              (f'Pontuação congelada ({len(pontuacoes)}' + (f'; {recorte}' if recorte else '') + '):') if pontuacoes
              else 'Nenhuma pontuação congelada neste recorte.']
    for i, p in enumerate(pontuacoes[:limite], 1):
        linhas.append(f'{i}. {_nome(p.user)} (id {p.user_id}) · {p.percentual}% · {faixa_info(p.faixa)["label"]} · '
                      f'{p.total} pts · setor {p.setor.name if p.setor_id else "sem setor"}'
                      + (f' · {p.confiancas_previstas} C$ reservados' if p.confiancas_previstas else ''))
    if len(pontuacoes) > limite:
        linhas.append(f'… mais {len(pontuacoes) - limite}: filtre ou aumente o limite.')
    setores = servico.setores_do_mes(mes)
    if setores:
        linhas.append('Setor destaque (soma das notas):')
        linhas += [f'  {s["setor"]} · soma {s["soma"]} · média {s["media"]}% · {s["pessoas"]} pessoa(s)'
                   for s in setores[:limite]]
    linhas.append(_tela(f'/impulso/ciclos/mes/{mes.pk}/'))
    return '\n'.join(linhas)


def _ler_ciclo(user, ciclo, f, limite):
    from impulso import ciclos as servico
    from impulso import filtros
    from impulso.utils import faixa_info

    meses = list(ciclo.meses.all())
    resumo = filtros.lista(servico.resumo_ciclo(ciclo), f, lambda linha: linha['user'])
    linhas = [f'Ciclo #{ciclo.pk} "{ciclo.nome}" · {_d(ciclo.inicio)} a {_d(ciclo.fim)} · {ciclo.get_status_display()}'
              + (' · C$ já creditadas' if ciclo.confiancas_creditadas else ''),
              'Meses: ' + (', '.join(f'mês #{m.pk} {_mm(m.referencia)} {m.get_status_display().lower()}' for m in meses)
                           or '—'),
              'Nota do ciclo por colaborador (média dos meses fechados):' if resumo else 'Nenhum mês fechado ainda.']
    for i, linha in enumerate(resumo[:limite], 1):
        medalhas = ', '.join(f'{_mm(p.mes.referencia)} {faixa_info(p.faixa)["label"]}' for p in linha['meses'])
        linhas.append(f'{i}. {_nome(linha["user"])} (id {linha["user"].pk}) · média {linha["media_percentual"]}% · '
                      f'{faixa_info(linha["faixa"])["label"]} · {linha["total"]} pts · medalhas: {medalhas} · '
                      f'C$ {linha["confiancas"]}')
    if any(not m.is_fechado for m in meses):
        linhas.append('Ainda há mês aberto: o ciclo só pode ser encerrado (na tela) com todos os meses fechados.')
    linhas.append(_tela(f'/impulso/ciclos/{ciclo.pk}/'))
    return '\n'.join(linhas)


def _mes_do_ciclo(args):
    """O CicloMes por mes_id, ou pelo mês AAAA-MM (o do ciclo mais recente que o tem)."""
    from impulso import filtros
    from impulso.models import CicloMes

    base = CicloMes.objects.select_related('ciclo', 'fechado_por')
    if args.get('mes_id'):
        mes = base.filter(pk=_inteiro(args['mes_id'], 'mes_id')).first()
        if mes is None:
            raise Invalido('Mês de ciclo não encontrado (os ids vêm em impulso_consultar o_que=ciclos).')
        return mes
    texto = str(args.get('mes') or '').strip()
    if not texto or _normal(texto) == 'todos':
        return None
    periodo = filtros.periodo_do_mes(texto)
    if not periodo:
        raise Invalido(f'mes inválido ("{texto}"). Use AAAA-MM.')
    mes = base.filter(referencia=periodo[0]).order_by('-ciclo__inicio').first()
    if mes is None:
        raise Invalido(f'Nenhum ciclo tem o mês {periodo[0]:%m/%Y} (veja impulso_consultar o_que=ciclos).')
    return mes


def _ler_ciclos(user, args):
    from impulso.models import Ciclo

    f = _filtros(dict(args, mes=None))
    limite = _limite(args, 40, 150)
    mes = _mes_do_ciclo(args)
    if mes is not None:
        return _ler_mes_do_ciclo(user, mes, f, limite)
    if args.get('ciclo_id'):
        ciclo = Ciclo.objects.prefetch_related('meses').filter(pk=_inteiro(args['ciclo_id'], 'ciclo_id')).first()
        if ciclo is None:
            raise Invalido('Ciclo não encontrado (veja impulso_consultar o_que=ciclos).')
        return _ler_ciclo(user, ciclo, f, limite)
    ciclos = list(Ciclo.objects.prefetch_related('meses'))
    linhas = [f'Ciclos do Impulso ({len(ciclos)}):' if ciclos else 'Nenhum ciclo criado ainda.']
    for c in ciclos:
        meses = ', '.join(f'mês #{m.pk} {_mm(m.referencia)} {m.get_status_display().lower()}' for m in c.meses.all())
        linhas.append(f'  ciclo #{c.pk} · {c.nome} · {_d(c.inicio)} a {_d(c.fim)} · {c.get_status_display()}'
                      + (' (C$ já creditadas)' if c.confiancas_creditadas else '') + f' · meses: {meses or "—"}')
    linhas.append('Detalhe: ciclo_id (nota do ciclo por pessoa) ou mes_id / mes=AAAA-MM (pontuação congelada do mês e '
                  'setor destaque).')
    if _gestor(user):
        linhas.append('Fechar ou reabrir um mês: impulso_administrar. Criar e encerrar ciclo (encerrar credita as C$) '
                      'só pela tela.')
    linhas.append(_tela('/impulso/ciclos/'))
    return '\n'.join(linhas)


def _ler_pessoas(user, args):
    from impulso import filtros
    from impulso.utils import get_colaboradores, get_colaboradores_do_gestor, get_gestores, get_gestores_do_setor

    f = _filtros(dict(args, mes=None))
    limite = _limite(args, 30, 100)
    recorte = _desc_filtros(f, com_mes=False)

    def linha(u, extra=''):
        return (f'  id {u.pk} · {_nome(u)} · {getattr(u, "job_title", "") or "sem cargo"} · '
                f'{u.sector.name if u.sector_id else "sem setor"}{extra}')

    if _gestor(user):
        equipe = set(get_colaboradores_do_gestor(user).values_list('id', flat=True))
        colaboradores = list(filtros.pessoas(get_colaboradores(), f).select_related('sector')[:limite + 1])
        linhas = ['Colaboradores do Impulso — para quem você cria meta, dá feedback, põe em projeto ou inclui como '
                  'responsável' + (f' ({recorte})' if recorte else '') + ':']
        for u in colaboradores[:limite]:
            extra = ''
            if u.pk not in equipe:
                area = list(get_gestores_do_setor(u).exclude(pk=user.pk))
                extra = (' · de outra área: meta para ele vai para aprovação de ' + _nomes(area, 5) if area
                         else ' · de outra área e sem gestor do Impulso lá: a tela recusa meta para ele')
            linhas.append(linha(u, extra))
        if len(colaboradores) > limite:
            linhas.append('… há mais: use busca (nome) ou setor_id.')
        gestores = list(filtros.pessoas(get_gestores(), f).select_related('sector')[:limite])
        linhas.append('Gestores do Impulso — quem pode ficar como gestor responsável de uma meta da sua área:')
        linhas += [linha(u) for u in gestores] or ['  nenhum neste recorte.']
    else:
        meus = list(filtros.pessoas(get_gestores_do_setor(user), f).select_related('sector'))
        linhas = ['Gestores do seu setor — a quem você pede meta ou duplicação (e quem avalia):']
        linhas += [linha(u) for u in meus] or ['  nenhum: não há gestor do Impulso num setor seu — fale com o RH.']
        colegas = list(filtros.pessoas(get_colaboradores().exclude(pk=user.pk), f).select_related('sector')[:limite + 1])
        linhas.append('Colegas que você pode incluir numa ideia (até 3)' + (f' ({recorte})' if recorte else '') + ':')
        linhas += [linha(u) for u in colegas[:limite]] or ['  ninguém neste recorte.']
        if len(colegas) > limite:
            linhas.append('… há mais: use busca (nome) ou setor_id.')
    return '\n'.join(linhas)


LEITURAS = {
    'painel': _ler_painel, 'metas': _ler_metas, 'meta': _ler_meta, 'solicitacoes': _ler_solicitacoes,
    'atividades': _ler_atividades, 'ranking': _ler_ranking, 'pontuacao': _ler_pontuacao,
    'feedbacks': _ler_feedbacks, 'feedback': _ler_feedback, 'assiduidade': _ler_assiduidade,
    'conectar': _ler_conectar, 'projetos': _ler_projetos, 'projeto': _ler_projeto, 'ideias': _ler_ideias,
    'ciclos': _ler_ciclos, 'pessoas': _ler_pessoas,
}
APELIDOS = {'dashboard': 'painel', 'kanban': 'metas', 'solicitacao': 'solicitacoes', 'acompanhamento': 'ranking',
            'faixa': 'pontuacao', 'inovar': 'ideias', 'ideia': 'ideias', 'conteudos': 'conectar',
            'tarefas': 'projetos', 'ciclo': 'ciclos'}


def _impulso_consultar(user, args):
    _membro(user)
    o_que = _normal(args.get('o_que') or 'painel').replace(' ', '_').replace('-', '_')
    leitura = LEITURAS.get(APELIDOS.get(o_que, o_que))
    if leitura is None:
        raise Invalido('o_que inválido. Use: ' + ' | '.join(LEITURAS) + '.')
    return leitura(user, args)


# ─── Ações: criar e alterar meta ─────────────────────────────────────────────
# A prévia valida com as regras da view e do modelo e descreve o que vai
# acontecer, sem gravar nada; a execução recebe exatamente os dados
# normalizados e chama a view do Impulso, com os nomes de campo do formulário.

def _meta_link(mid):
    return f'Meta: {_url(f"/impulso/metas/{mid}/")}'


def _infos(r):
    """Só os avisos informativos (ex.: prazo que andou para a segunda), sem o 'feito!' da tela."""
    return ' '.join(texto for nivel, texto in r['avisos'] if nivel in ('info', 'warning'))


def _textos(valor, limite=300):
    if valor in (None, ''):
        return []
    if not isinstance(valor, (list, tuple)):
        valor = [valor]
    return [' '.join(str(t).split())[:limite] for t in valor if str(t).strip()]


def _prazo_pedido(pedido, ajustado, aviso):
    if aviso:
        return (f'prazo {_d(ajustado)} ({_d(pedido)} cai no fim de semana e, com somente dias úteis, anda para a '
                'segunda)')
    return f'prazo {_d(pedido)}'


def _previa_criar_meta(user, args):
    from impulso import views as iv
    from impulso.models import Meta
    from impulso.utils import get_colaboradores, get_colaboradores_do_gestor, get_gestores, get_gestores_do_setor

    _membro(user)
    sou_gestor = _gestor(user)
    titulo = _titulo(args, obrigatorio='Informe o título da meta.')[:200]
    descricao = str(args.get('descricao') or '').strip()
    if not descricao:
        raise Invalido('Informe a descrição da meta (a tela exige título, descrição e prazo).')
    prazo = _data(args.get('prazo'), 'prazo')
    recorrencia = (_escolha(args.get('recorrencia'), Meta.Recorrencia.values, 'recorrencia', RECORRENCIA_EXTRA)
                   or Meta.Recorrencia.UNICA)
    dias_uteis = recorrencia != Meta.Recorrencia.UNICA and _sim(args.get('apenas_dias_uteis'))
    ajustado, aviso = iv._prazo_em_dia_util(prazo, dias_uteis)
    if ajustado < timezone.localdate():
        raise Invalido('O prazo não pode ser anterior a hoje.')
    passos = _textos(args.get('itens'))
    repete = dict(Meta.Recorrencia.choices)[recorrencia].lower() + (', somente dias úteis' if dias_uteis else '')

    if sou_gestor:
        if not args.get('colaborador_id'):
            raise Invalido('Diga para quem é a meta (colaborador_id; veja impulso_consultar o_que=pessoas).')
        colaborador = get_colaboradores().filter(pk=_inteiro(args['colaborador_id'], 'colaborador_id')).first()
        if colaborador is None:
            raise Invalido('Essa pessoa não é colaboradora do Impulso (veja impulso_consultar o_que=pessoas).')
        participantes = [u for u in _colaboradores_por_ids(_ids(args.get('participantes'), 'participantes'),
                                                           'participantes') if u.pk != colaborador.pk]
        da_area = get_colaboradores_do_gestor(user).filter(pk=colaborador.pk).exists()
        if da_area:
            gestor = user
            if args.get('gestor_id'):
                gestor = get_gestores().filter(pk=_inteiro(args['gestor_id'], 'gestor_id')).first()
                if gestor is None:
                    raise Invalido('O gestor escolhido não é gestor do Impulso (veja impulso_consultar o_que=pessoas).')
        else:
            # Demanda para outra área não entra direto: quem fica com ela e aprova é um gestor de lá.
            area = get_gestores_do_setor(colaborador).exclude(pk=user.pk)
            if not area.exists():
                raise Invalido(f'{_nome(colaborador)} é de outra área e não há gestor do Impulso cadastrado nela para '
                               'aprovar a demanda. Fale com o RH para ajustar o cadastro.')
            escolha = args.get('gestor_aprovador_id') or args.get('gestor_id')
            gestor = area.filter(pk=_inteiro(escolha, 'gestor_aprovador_id')).first() if escolha else area.first()
            if gestor is None:
                raise Invalido(f'{_nome(colaborador)} é de outra área: o gestor da meta tem de ser um gestor de lá — '
                               + _nomes(list(area)) + '.')
        para = 'você' if colaborador.pk == user.pk else _nome(colaborador)
        linhas = [f'Criar a meta "{titulo}" para {para}, {_prazo_pedido(prazo, ajustado, aviso)}, recorrência {repete}.']
        if da_area:
            linhas.append(f'Gestor responsável (avalia no fim): {"você" if gestor.pk == user.pk else _nome(gestor)}.')
            linhas.append(f'Entra direto no Kanban; {para} recebe o aviso de meta atribuída.')
            if gestor.pk != user.pk:
                linhas.append(f'{_nome(gestor)} recebe aviso de que a meta foi criada no nome dele.')
        else:
            avisados = list(get_gestores_do_setor(colaborador).exclude(pk=user.pk))
            linhas.append(f'{_nome(colaborador)} é de outra área: a meta nasce PENDENTE, com {_nome(gestor)} como gestor, '
                          f'e só entra no Kanban depois da aprovação. Recebem o pedido: {_nomes(avisados)}.')
        if participantes:
            linhas.append(f'Outros responsáveis: {_nomes(participantes)} — recebem aviso.')
    else:
        if args.get('colaborador_id') and _inteiro(args['colaborador_id'], 'colaborador_id') != user.pk:
            raise Invalido('Só gestor do Impulso cria meta para outra pessoa; você pode pedir uma meta para você mesmo.')
        if args.get('participantes'):
            raise Invalido('Só o gestor inclui outros responsáveis na meta.')
        possiveis = get_gestores_do_setor(user)
        gestor = possiveis.filter(pk=_inteiro(args['gestor_id'], 'gestor_id')).first() if args.get('gestor_id') else None
        if gestor is None:
            raise Invalido('Escolha o gestor do seu setor que acompanha a meta (gestor_id): '
                           + (_nomes(list(possiveis)) or 'não há nenhum cadastrado — fale com o RH') + '.')
        colaborador, participantes, da_area = user, [], True
        precisa = _sim(args.get('precisa_aprovacao'), padrao=True)
        linhas = [f'{"Pedir" if precisa else "Criar"} a meta "{titulo}" para você, '
                  f'{_prazo_pedido(prazo, ajustado, aviso)}, recorrência {repete}, gestor {_nome(gestor)}.',
                  f'Vai para {_nome(gestor)} aprovar (recebe o pedido); entra no seu Kanban depois da aprovação.' if precisa
                  else f'Entra direto no seu Kanban, sem aprovação; {_nome(gestor)} recebe aviso e avalia no fim.']
    if passos:
        linhas.append(f'To-do ({len(passos)}): ' + '; '.join(f'{n}) {p}' for n, p in enumerate(passos, 1)))
    linhas.append('Descrição: ' + _cortar(descricao, 300))

    dados = {'titulo': titulo, 'descricao': descricao, 'prazo': prazo.isoformat(), 'recorrencia': recorrencia,
             'itens': passos}
    if dias_uteis:
        dados['apenas_dias_uteis'] = 'on'
    if sou_gestor:
        dados.update(colaborador=str(colaborador.pk), participantes=[str(u.pk) for u in participantes])
        dados['gestor' if da_area else 'gestor_aprovador'] = str(gestor.pk)
    else:
        dados.update(gestor=str(gestor.pk), precisa_aprovacao='sim' if precisa else 'nao')
    return '\n'.join(linhas), dados


def _exec_criar_meta(user, d):
    from impulso import views as iv
    from impulso.models import Meta

    r = chamar_view(iv.meta_create, user, '/impulso/metas/nova/', dados=d)
    mid = _id_em(r['destino'], r'^/impulso/metas/(\d+)/$')
    if not _sem_erro(r) or not mid:
        return 'A meta não foi criada: ' + _motivo(r)
    meta = Meta.objects.select_related('colaborador', 'gestor').get(pk=mid)
    linhas = [f'Meta criada: #{meta.pk} "{meta.titulo}" para {_nome(meta.colaborador)}, prazo {_d(meta.prazo)}, '
              f'gestor {_nome(meta.gestor)} — {meta.get_aprovacao_display().lower()}.']
    extras = [f'{n} passo(s) no to-do' for n in [meta.itens.count()] if n]
    extras += [f'{n} outro(s) responsável(is)' for n in [meta.participantes.count()] if n]
    if extras:
        linhas.append('Com ' + ' e '.join(extras) + '.')
    if _recados(r):
        linhas.append(_recados(r))
    linhas.append(_meta_link(meta.pk))
    return '\n'.join(linhas)


def _previa_alterar_meta(user, args):
    from impulso import views as iv
    from impulso.models import Meta
    from impulso.utils import get_colaboradores

    _membro(user)
    meta = _meta_por_id(args)
    campos = [k for k in ('titulo', 'descricao', 'prazo', 'recorrencia', 'apenas_dias_uteis') if args.get(k) is not None]
    entra = _ids(args.get('adicionar_participantes'), 'adicionar_participantes')
    sai = _ids(args.get('remover_participantes'), 'remover_participantes')
    if not campos and not entra and not sai:
        raise Invalido('Diga o que mudar: titulo, descricao, prazo, recorrencia, apenas_dias_uteis, '
                       'adicionar_participantes ou remover_participantes.')
    linhas, dados = [f'Alterar a meta #{meta.pk} "{meta.titulo}" (de {_nome(meta.colaborador)}):'], {'meta_id': meta.pk}

    if campos:
        if not meta.pode_editar(user):
            raise Invalido('Você não pode editar esta atividade: edita o gestor do Impulso que responde por ela (criou, '
                           'aprovou, é o gestor dela ou a pessoa é da equipe dele) e o superadmin.')
        # A tela salva o formulário inteiro: o que não muda vai com o valor atual.
        titulo = _titulo(args)[:200] if 'titulo' in campos else meta.titulo
        descricao = str(args['descricao']).strip() if 'descricao' in campos else meta.descricao
        prazo = _data(args['prazo'], 'prazo') if 'prazo' in campos else meta.prazo
        recorrencia = meta.recorrencia
        if 'recorrencia' in campos:
            recorrencia = (_escolha(args['recorrencia'], Meta.Recorrencia.values, 'recorrencia', RECORRENCIA_EXTRA)
                           or meta.recorrencia)
        pede_uteis = _sim(args['apenas_dias_uteis']) if 'apenas_dias_uteis' in campos else meta.apenas_dias_uteis
        uteis = recorrencia != Meta.Recorrencia.UNICA and pede_uteis
        ajustado, aviso = iv._prazo_em_dia_util(prazo, uteis)
        rotulos = dict(Meta.Recorrencia.choices)
        mudancas = []
        if titulo != meta.titulo:
            mudancas.append(f'título: "{meta.titulo}" → "{titulo}"')
        if descricao != meta.descricao:
            mudancas.append('descrição nova: ' + (_cortar(descricao, 300) or '(vazia)'))
        if ajustado != meta.prazo:
            atrasa = ajustado < timezone.localdate() and meta.status != Meta.Status.CONCLUIDA
            mudancas.append(f'prazo: {_d(meta.prazo)} → {_d(ajustado)}' + (' (caía no fim de semana)' if aviso else '')
                            + (' — já passou: a meta fica atrasada' if atrasa else ''))
        if recorrencia != meta.recorrencia:
            mudancas.append(f'recorrência: {rotulos[meta.recorrencia]} → {rotulos[recorrencia]}')
        if uteis != meta.apenas_dias_uteis:
            mudancas.append('passa a valer só dia útil' if uteis else 'deixa de ser só dia útil'
                            + (' (meta de vez única não usa essa opção)' if pede_uteis else ''))
        if mudancas:
            linhas += [f'• {m}' for m in mudancas]
            if titulo != meta.titulo or ajustado != meta.prazo:
                avisados = [u for u in meta.responsaveis if u.pk != user.pk]
                linhas.append('A mudança de título/prazo vira comentário na meta'
                              + (f' e {_nomes(avisados)} recebe(m) aviso.' if avisados else '.'))
            post = {'titulo': titulo, 'descricao': descricao, 'prazo': prazo.isoformat(), 'recorrencia': recorrencia}
            if uteis:
                post['apenas_dias_uteis'] = 'on'
            dados['campos'] = post

    if entra or sai:
        if not (meta.gestor_id == user.pk or user.is_superuser):
            raise Invalido('Apenas o gestor da meta (ou o superadmin) muda os outros responsáveis.')
        if meta.colaborador_id in entra:
            raise Invalido(f'{_nome(meta.colaborador)} já é o responsável principal da meta.')
        pedidos = _colaboradores_por_ids(entra, 'adicionar_participantes')
        atuais = list(meta.participantes.all())
        atuais_ids = {u.pk for u in atuais}
        # A view grava o conjunto inteiro filtrado pelos colaboradores do Impulso: quem saiu do grupo cai junto.
        validos = set(get_colaboradores().filter(pk__in=atuais_ids).values_list('pk', flat=True))
        novos = [u for u in pedidos if u.pk not in atuais_ids]
        tirar = [u for u in atuais if u.pk in sai]
        perdidos = [u for u in atuais if u.pk not in validos and u.pk not in sai]
        final = ((validos - set(sai)) | {u.pk for u in novos}) - {meta.colaborador_id}
        if final != atuais_ids:
            if novos:
                linhas.append(f'• incluir como responsáveis: {_nomes(novos)} — recebem aviso')
            if tirar:
                linhas.append(f'• tirar dos responsáveis: {_nomes(tirar)} — recebem aviso')
            if perdidos:
                linhas.append(f'• {_nomes(perdidos)} não está(ão) mais no Impulso e sai(em) junto')
            linhas.append('A troca de responsáveis vira comentário na meta.')
            dados['participantes'] = [str(i) for i in sorted(final)]
    if 'campos' not in dados and 'participantes' not in dados:
        raise Invalido('Nada muda: o que foi pedido já é o que está na meta.')
    return '\n'.join(linhas), dados


def _exec_alterar_meta(user, d):
    from impulso import views as iv
    from impulso.models import Meta

    mid, feito = d['meta_id'], []
    if 'campos' in d:
        r = chamar_view(iv.meta_editar, user, f'/impulso/metas/{mid}/editar/', dados=d['campos'], meta_id=mid)
        if not _sem_erro(r) or r['destino'] != f'/impulso/metas/{mid}/':
            return 'A meta não foi alterada: ' + _motivo(r)
        feito.append('dados atualizados' + (f' — {_infos(r)}' if _infos(r) else ''))
    if 'participantes' in d:
        r = chamar_view(iv.meta_participantes_editar, user, f'/impulso/metas/{mid}/responsaveis/',
                        dados={'participantes': d['participantes']}, meta_id=mid)
        if not _sem_erro(r):
            inicio = 'Os dados foram atualizados, mas os responsáveis não mudaram: ' if feito else 'Os responsáveis não mudaram: '
            return inicio + _motivo(r)
        feito.append('responsáveis atualizados')
    meta = Meta.objects.select_related('colaborador').get(pk=mid)
    outros = list(meta.participantes.all())
    return (f'Meta #{meta.pk} ({"; ".join(feito)}): "{meta.titulo}" · prazo {_d(meta.prazo)} · '
            f'{meta.get_recorrencia_display().lower()}' + (' (somente dias úteis)' if meta.apenas_dias_uteis else '')
            + f' · responsável {_nome(meta.colaborador)}' + (f' · outros: {_nomes(outros)}' if outros else '')
            + f'\n{_meta_link(meta.pk)}')


# ─── Ações: andamento, decisão, cópia e exclusão da meta ────────────────────

def _previa_mover(user, args):
    from impulso.models import Meta

    meta = _meta_visivel(user, args)
    if not meta.vale_pontos:
        raise Invalido('Esta meta ainda não foi aprovada pelo gestor.')
    if meta.status == Meta.Status.CONCLUIDA:
        raise Invalido('A meta já está concluída (avaliada pelo gestor) e não sai dessa coluna pelo Kanban.')
    novo = _escolha(args.get('status'), Meta.Status.values, 'status', STATUS_META_EXTRA)
    if novo not in (Meta.Status.A_FAZER, Meta.Status.EM_ANDAMENTO, Meta.Status.ENTREGUE):
        raise Invalido('status deve ser A_FAZER, EM_ANDAMENTO ou ENTREGUE — a conclusão acontece na avaliação do gestor.')
    if novo == meta.status:
        raise Invalido(f'A meta já está em {meta.get_status_display()}.')
    linhas = [f'Mover a meta #{meta.pk} "{meta.titulo}" de {meta.get_status_display()} para '
              f'{dict(Meta.Status.choices)[novo]}.']
    if novo != Meta.Status.ENTREGUE:
        linhas.append('Ninguém é avisado.')
    elif meta.entregue_em:
        linhas.append(f'A data de entrega anterior ({_dh(meta.entregue_em)}) fica, e ninguém é avisado de novo.')
    else:
        linhas.append(f'Registra a entrega agora e {_nome(meta.gestor)} (gestor) recebe aviso. Para mandar o link da '
                      'entrega junto, use acao=entregar.')
    return '\n'.join(linhas), {'meta_id': meta.pk, 'status': novo}


def _exec_mover(user, d):
    from impulso import views as iv
    from impulso.models import Meta

    mid = d['meta_id']
    r = chamar_view(iv.meta_update_status, user, f'/impulso/metas/{mid}/status/', dados={'status': d['status']},
                    meta_id=mid)
    if not _ok_json(r):
        return 'A meta não mudou de coluna: ' + _motivo(r)
    meta = Meta.objects.get(pk=mid)
    return f'Meta #{mid} "{meta.titulo}" agora está em {meta.get_status_display()}.\n{_meta_link(mid)}'


def _previa_entregar(user, args):
    from impulso.models import Meta

    meta = _meta_por_id(args)
    if not (meta.colaborador_id == user.pk or user.is_superuser):
        raise Invalido('Apenas o colaborador da meta (o responsável principal) pode entregá-la.')
    if not meta.vale_pontos:
        raise Invalido('Esta meta ainda não foi aprovada pelo gestor.')
    if meta.status == Meta.Status.CONCLUIDA:
        raise Invalido('A meta já foi concluída na avaliação do gestor.')
    link = _link_valido(args['entrega_link'], 'entrega_link') if args.get('entrega_link') else ''
    linhas = [f'Entregar a meta #{meta.pk} "{meta.titulo}": o status passa a Entregue (aguardando avaliação).',
              f'Link da entrega: {link}' if link else ('Sem link novo; fica o atual: ' + meta.entrega_link
                                                       if meta.entrega_link else 'Sem link de entrega.'),
              f'{_nome(meta.gestor)} (gestor) recebe aviso e avalia com as notas de qualidade e prazo.']
    if meta.status == Meta.Status.ENTREGUE:
        linhas.append(f'Ela já estava entregue ({_dh(meta.entregue_em)}): a data de entrega passa a ser agora.')
    return '\n'.join(linhas), {'meta_id': meta.pk, 'entrega_link': link}


def _exec_entregar(user, d):
    from impulso import views as iv
    from impulso.models import Meta

    mid = d['meta_id']
    r = chamar_view(iv.meta_entregar, user, f'/impulso/metas/{mid}/entregar/', dados={'entrega_link': d['entrega_link']},
                    meta_id=mid)
    meta = Meta.objects.get(pk=mid)
    if not _sem_erro(r) or meta.status != Meta.Status.ENTREGUE:
        return 'A meta não foi entregue: ' + _motivo(r)
    return (f'Meta #{mid} "{meta.titulo}" entregue em {_dh(meta.entregue_em)}'
            + (f' com o link {meta.entrega_link}' if meta.entrega_link else '') + f'.\n{_meta_link(mid)}')


def _previa_avaliar(user, args):
    meta = _meta_por_id(args)
    if not (_gestor(user) and (meta.gestor_id == user.pk or user.is_superuser)):
        raise Invalido('Apenas o gestor da meta (sendo gestor do Impulso) ou o superadmin pode avaliá-la.')
    if not meta.vale_pontos:
        raise Invalido('Aprove a solicitação antes de avaliar a meta.')
    qualidade = _nota(args.get('nota_qualidade'), 'nota_qualidade')
    prazo = _nota(args.get('nota_prazo'), 'nota_prazo')
    comentario = str(args.get('comentario') or '').strip()
    linhas = [f'Avaliar a meta #{meta.pk} "{meta.titulo}" de {_nome(meta.colaborador)}: qualidade {qualidade}/5, '
              f'prazo {prazo}/5.']
    if comentario:
        linhas.append('Comentário: ' + _cortar(comentario, 500))
    if meta.is_avaliada:
        linhas.append(f'Ela já tinha sido avaliada (qualidade {meta.nota_qualidade}/5, prazo {meta.nota_prazo}/5, em '
                      f'{_dh(meta.avaliado_em)}): as notas e o comentário são substituídos.')
    elif meta.status != meta.Status.ENTREGUE:
        linhas.append(f'Atenção: a meta ainda não foi entregue (está em {meta.get_status_display()}); avaliar já a conclui.')
    linhas.append(f'A meta passa a Concluída e {_nome(meta.colaborador)} recebe aviso com as notas.')
    proximo = meta.proximo_prazo()
    if proximo and not meta.ocorrencias.exists():
        linhas.append(f'Como é {meta.get_recorrencia_display().lower()}, a próxima ocorrência nasce com prazo '
                      f'{_d(proximo)} e {_nome(meta.colaborador)} recebe aviso.')
    return '\n'.join(linhas), {'meta_id': meta.pk, 'nota_qualidade': str(qualidade), 'nota_prazo': str(prazo),
                               'avaliacao_comentario': comentario}


def _exec_avaliar(user, d):
    from impulso import views as iv
    from impulso.models import Meta

    mid = d['meta_id']
    campos = {k: v for k, v in d.items() if k != 'meta_id'}
    r = chamar_view(iv.meta_avaliar, user, f'/impulso/metas/{mid}/avaliar/', dados=campos, meta_id=mid)
    meta = Meta.objects.get(pk=mid)
    if not _sem_erro(r) or meta.status != Meta.Status.CONCLUIDA:
        return 'A avaliação não foi registrada: ' + _motivo(r)
    linhas = [f'Meta #{mid} "{meta.titulo}" avaliada (qualidade {meta.nota_qualidade}/5, prazo {meta.nota_prazo}/5) '
              'e concluída.']
    proxima = meta.ocorrencias.first()
    if proxima:
        linhas.append(f'Próxima ocorrência: meta #{proxima.pk}, prazo {_d(proxima.prazo)} — '
                      f'{_url(f"/impulso/metas/{proxima.pk}/")}')
    linhas.append(_meta_link(mid))
    return '\n'.join(linhas)


def _previa_decidir(user, args, decisao):
    meta = _meta_por_id(args)
    if not meta.pendente_aprovacao:
        raise Invalido(f'Essa meta não está aguardando aprovação ({meta.get_aprovacao_display()}).')
    if not meta.pode_decidir(user):
        if meta.solicitada_por_id == user.pk:
            raise Invalido('Quem pediu não decide o próprio pedido: decide o gestor escolhido ou um gestor da área do '
                           'colaborador.')
        raise Invalido('Apenas o gestor escolhido (ou um gestor do Impulso da área do colaborador) pode decidir esta '
                       'solicitação.')
    motivo = str(args.get('motivo') or '').strip()
    linhas = [f'{"Aprovar" if decisao == "aprovar" else "Recusar"} a solicitação da meta #{meta.pk} "{meta.titulo}" '
              f'para {_nome(meta.colaborador)} (prazo {_d(meta.prazo)}'
              + (f', pedida por {_nome(meta.solicitada_por)}' if meta.solicitada_por_id else '') + ').']
    if meta.duplicada_de_id:
        linhas.append(f'É um pedido de duplicação da meta #{meta.duplicada_de_id} "{meta.duplicada_de.titulo}" '
                      '(a original não muda).')
    if decisao == 'aprovar':
        linhas.append(f'A meta entra no Kanban de {_nome(meta.colaborador)}, que recebe aviso.')
    else:
        linhas.append((f'Motivo: "{_cortar(motivo, 500)}". ' if motivo else 'Sem motivo informado (vale explicar). ')
                      + f'{_nome(meta.colaborador)} recebe aviso{" com o motivo" if motivo else ""}; a meta fica '
                      'recusada e não vale ponto.')
    return '\n'.join(linhas), {'meta_id': meta.pk, 'decisao': decisao, 'motivo_recusa': motivo}


def _exec_decidir(user, d):
    from impulso import views as iv
    from impulso.models import Meta

    mid = d['meta_id']
    r = chamar_view(iv.meta_decidir, user, f'/impulso/metas/{mid}/decidir/',
                    dados={'decisao': d['decisao'], 'motivo_recusa': d['motivo_recusa']}, meta_id=mid)
    meta = Meta.objects.select_related('colaborador').get(pk=mid)
    esperado = Meta.Aprovacao.APROVADA if d['decisao'] == 'aprovar' else Meta.Aprovacao.RECUSADA
    if not _sem_erro(r) or meta.aprovacao != esperado:
        return 'A solicitação não foi decidida: ' + _motivo(r)
    feito = 'aprovada e já está no Kanban' if esperado == Meta.Aprovacao.APROVADA else 'recusada'
    return (f'Solicitação da meta #{mid} "{meta.titulo}" {feito}; {_nome(meta.colaborador)} foi avisado(a).\n'
            f'{_meta_link(mid)}')


def _previa_cancelar_solicitacao(user, args):
    meta = _meta_por_id(args)
    if not meta.pode_cancelar_solicitacao(user):
        if meta.solicitada_por_id != user.pk:
            raise Invalido('Só quem fez a solicitação pode cancelá-la.')
        raise Invalido('Esta solicitação já foi decidida pelo gestor — fale com ele para remover a meta.')
    extras = [f'{n} {rotulo}' for n, rotulo in ((meta.itens.count(), 'passo(s) do to-do'),
                                                  (meta.anexos.count(), 'anexo(s)'),
                                                  (meta.comentarios.count(), 'comentário(s)')) if n]
    linhas = [f'Cancelar o seu pedido da meta #{meta.pk} "{meta.titulo}" (prazo {_d(meta.prazo)}): a solicitação é '
              'apagada' + (f', com {", ".join(extras)}' if extras else '') + '.',
              f'{_nome(meta.gestor)} recebe aviso do cancelamento.', 'Não dá para desfazer.']
    return '\n'.join(linhas), {'meta_id': meta.pk}


def _exec_cancelar_solicitacao(user, d):
    from impulso import views as iv
    from impulso.models import Meta

    mid = d['meta_id']
    r = chamar_view(iv.meta_solicitacao_cancelar, user, f'/impulso/metas/{mid}/cancelar-solicitacao/', meta_id=mid)
    if not _sem_erro(r) or Meta.objects.filter(pk=mid).exists():
        return 'A solicitação não foi cancelada: ' + _motivo(r)
    return f'Solicitação da meta #{mid} cancelada; o gestor foi avisado. {_url("/impulso/metas/solicitacoes/")}'


def _previa_duplicar(user, args):
    from impulso import views as iv
    from impulso.utils import get_colaboradores, get_colaboradores_do_gestor, get_gestores_do_setor

    original = _meta_por_id(args)
    if not original.pode_editar(user):
        if original.pode_solicitar_duplicacao(user):
            raise Invalido('Você não edita esta atividade, então não duplica direto: use acao=solicitar_duplicacao '
                           '(o gestor aprova a cópia).')
        raise Invalido('Você não pode duplicar esta atividade.')
    colaborador = original.colaborador
    if args.get('colaborador_id') and _inteiro(args['colaborador_id'], 'colaborador_id') != original.colaborador_id:
        colaborador = get_colaboradores().filter(pk=_inteiro(args['colaborador_id'], 'colaborador_id')).first()
        if colaborador is None:
            raise Invalido('Escolha um colaborador do Impulso para receber a cópia.')
    trocou = colaborador.pk != original.colaborador_id
    gestor, fora = original.gestor, False
    if trocou and not get_colaboradores_do_gestor(user).filter(pk=colaborador.pk).exists():
        area = get_gestores_do_setor(colaborador).exclude(pk=user.pk)
        if not area.exists():
            raise Invalido(f'{_nome(colaborador)} é de outra área e não há gestor do Impulso cadastrado nela para '
                           'aprovar a demanda. Fale com o RH para ajustar o cadastro.')
        gestor, fora = area.first(), True
    hoje = timezone.localdate()
    prazo, _aviso = iv._prazo_em_dia_util(original.prazo if original.prazo and original.prazo >= hoje else hoje,
                                          original.apenas_dias_uteis)
    outros = list(original.participantes.exclude(pk=colaborador.pk))
    passos = original.itens.count()
    linhas = [f'Duplicar a meta #{original.pk} "{original.titulo}" como "Cópia de {original.titulo}"'[:260]
              + f' para {_nome(colaborador)}, prazo {_d(prazo)}, gestor {_nome(gestor)}.',
              f'Vai junto: descrição, recorrência, {passos} passo(s) do to-do'
              + (f' e os outros responsáveis ({_nomes(outros)})' if outros else '')
              + '. Não copia status, entrega, notas, comentários nem anexos.']
    if fora:
        avisados = list(get_gestores_do_setor(colaborador).exclude(pk=user.pk))
        linhas.append(f'{_nome(colaborador)} é de outra área: a cópia nasce pendente e {_nomes(avisados)} recebe(m) o '
                      'pedido para aprovar.')
    elif trocou:
        linhas.append(f'{_nome(colaborador)} recebe aviso'
                      + (f', e {_nome(gestor)} também, por ficar como gestor responsável.' if gestor.pk != user.pk else '.'))
    else:
        linhas.append('A cópia entra direto no Kanban e ninguém é avisado.')
    linhas.append('Depois dá para ajustar título e prazo da cópia com impulso_alterar_meta.')
    return '\n'.join(linhas), {'meta_id': original.pk, 'colaborador': str(colaborador.pk)}


def _exec_duplicar(user, d):
    from impulso import views as iv
    from impulso.models import Meta

    mid = d['meta_id']
    r = chamar_view(iv.meta_duplicar, user, f'/impulso/metas/{mid}/duplicar/', dados={'colaborador': d['colaborador']},
                    meta_id=mid)
    copia_id = _id_em(r['destino'], r'^/impulso/metas/(\d+)/editar/$')
    if not _sem_erro(r) or not copia_id:
        return 'A meta não foi duplicada: ' + _motivo(r)
    copia = Meta.objects.select_related('colaborador', 'gestor').get(pk=copia_id)
    return (f'Cópia criada: meta #{copia.pk} "{copia.titulo}" para {_nome(copia.colaborador)}, prazo {_d(copia.prazo)}, '
            f'gestor {_nome(copia.gestor)} — {copia.get_aprovacao_display().lower()}, {copia.itens.count()} passo(s).\n'
            f'{_meta_link(copia.pk)}')


def _previa_solicitar_duplicacao(user, args):
    from impulso import views as iv
    from impulso.models import Meta
    from impulso.utils import get_gestores_do_setor

    original = _meta_por_id(args)
    if original.pode_editar(user):
        raise Invalido('Você edita esta atividade: use acao=duplicar (a cópia sai direto, sem pedido).')
    if not original.pode_solicitar_duplicacao(user):
        raise Invalido('Você não pode duplicar esta atividade.')
    ja = Meta.objects.filter(duplicada_de=original, solicitada_por=user, aprovacao=Meta.Aprovacao.PENDENTE).first()
    if ja:
        raise Invalido(f'Você já pediu a duplicação desta atividade (meta #{ja.pk}) — aguarde o gestor decidir.')
    hoje = timezone.localdate()
    titulo = _titulo(args)[:200] if args.get('titulo') else f'Cópia de {original.titulo}'[:200]
    prazo = (_data(args['prazo'], 'prazo') if args.get('prazo')
             else (original.prazo if original.prazo and original.prazo >= hoje else hoje))
    if prazo < hoje:
        raise Invalido('O prazo não pode ser anterior a hoje.')
    gestores = get_gestores_do_setor(user)
    if args.get('gestor_id'):
        gestor = gestores.filter(pk=_inteiro(args['gestor_id'], 'gestor_id')).first()
        if gestor is None:
            raise Invalido('Escolha um gestor do seu setor: ' + (_nomes(list(gestores)) or 'não há nenhum') + '.')
    else:
        gestor = gestores.filter(pk=original.gestor_id).first() or gestores.first()
        if gestor is None:
            raise Invalido('Não há gestor do Impulso num setor seu para aprovar o pedido — fale com o RH.')
    ajustado, aviso = iv._prazo_em_dia_util(prazo, original.apenas_dias_uteis)
    linhas = [f'Pedir a duplicação da meta #{original.pk} "{original.titulo}" como "{titulo}", para você, '
              f'{_prazo_pedido(prazo, ajustado, aviso)}.',
              f'Vai junto: descrição, recorrência e {original.itens.count()} passo(s) do to-do; os outros responsáveis não.',
              f'{_nome(gestor)} recebe o pedido para aprovar; a cópia entra no seu Kanban depois disso.']
    return '\n'.join(linhas), {'meta_id': original.pk, 'titulo': titulo, 'prazo': prazo.isoformat(),
                               'gestor': str(gestor.pk)}


def _exec_solicitar_duplicacao(user, d):
    from impulso import views as iv
    from impulso.models import Meta

    mid = d['meta_id']
    campos = {k: v for k, v in d.items() if k != 'meta_id'}
    r = chamar_view(iv.meta_duplicar_solicitar, user, f'/impulso/metas/{mid}/duplicar/solicitar/', dados=campos,
                    meta_id=mid)
    copia_id = _id_em(r['destino'], r'^/impulso/metas/(\d+)/$')
    copia = (Meta.objects.select_related('gestor').filter(pk=copia_id, duplicada_de_id=mid, solicitada_por=user).first()
             if copia_id and copia_id != mid else None)
    if not _sem_erro(r) or copia is None:
        return 'O pedido de duplicação não foi feito: ' + _motivo(r)
    return (f'Pedido enviado: meta #{copia.pk} "{copia.titulo}", prazo {_d(copia.prazo)}, esperando '
            f'{_nome(copia.gestor)} aprovar.\n{_meta_link(copia.pk)}')


def _previa_excluir_meta(user, args):
    meta = _meta_por_id(args)
    if not meta.pode_excluir(user):
        if meta.pendente_aprovacao:
            raise Invalido('Solicitação pendente não se exclui: o gestor recusa (impulso_decidir_meta) ou quem pediu '
                           'cancela.')
        raise Invalido('Você não pode excluir esta meta: exclui o gestor do Impulso que responde por ela e o superadmin '
                       '(colaborador nunca exclui).')
    impacto = meta.impacto_da_exclusao
    linhas = [f'Excluir a meta #{meta.pk} "{meta.titulo}" de {_nome(meta.colaborador)} ({meta.get_status_display()}, '
              f'prazo {_d(meta.prazo)}).',
              f'Somem junto: {meta.itens.count()} passo(s) do to-do, {impacto["anexos"]} anexo(s) (arquivos inclusive) e '
              f'{impacto["comentarios"]} comentário(s).']
    if impacto['avaliada']:
        linhas.append(f'Ela já foi avaliada: a pontuação do mês de {_nome(meta.colaborador)} é recalculada sem ela.')
    if impacto['ocorrencias']:
        linhas.append(f'{impacto["ocorrencias"]} ocorrência(s) seguinte(s) continuam existindo, só perdem o vínculo.')
    linhas += [f'{_nome(meta.colaborador)} recebe aviso da remoção.', 'Não dá para desfazer.']
    return '\n'.join(linhas), {'meta_id': meta.pk}


def _exec_excluir_meta(user, d):
    from impulso import views as iv
    from impulso.models import Meta

    mid = d['meta_id']
    r = chamar_view(iv.meta_excluir, user, f'/impulso/metas/{mid}/excluir/', meta_id=mid)
    if not _sem_erro(r) or Meta.objects.filter(pk=mid).exists():
        return 'A meta não foi excluída: ' + _motivo(r)
    return (_recados(r) or f'Meta #{mid} excluída.') + f' Kanban: {_url("/impulso/metas/")}'


# ─── Ações: to-do, comentários e anexos da meta ──────────────────────────────

def _item_por_id(args):
    from impulso.models import MetaItem

    if not args.get('item_id'):
        raise Invalido('Informe item_id (os ids vêm em impulso_consultar o_que=meta).')
    item = (MetaItem.objects.select_related('meta', 'meta__colaborador', 'meta__gestor', 'concluido_por')
            .filter(pk=_inteiro(args['item_id'], 'item_id')).first())
    if item is None:
        raise Invalido(f'Passo #{args["item_id"]} não encontrado.')
    return item


def _previa_item_adicionar(user, args):
    meta = _meta_por_id(args)
    if not (user.is_superuser or user.pk in (meta.gestor_id, meta.colaborador_id)):
        raise Invalido('Só o gestor e o responsável principal da meta (ou o superadmin) acrescentam passos ao to-do.')
    textos = _textos(args.get('textos') or args.get('texto'))[:20]
    if not textos:
        raise Invalido('Informe o texto do passo (texto, ou textos para vários de uma vez).')
    feitos, total = meta.progresso_itens
    linhas = [f'Acrescentar {len(textos)} passo(s) ao to-do da meta #{meta.pk} "{meta.titulo}" (hoje {feitos}/{total} '
              'feitos):']
    linhas += [f'  {n}) {texto}' for n, texto in enumerate(textos, 1)]
    linhas.append('Ninguém é avisado.')
    return '\n'.join(linhas), {'meta_id': meta.pk, 'textos': textos}


def _exec_item_adicionar(user, d):
    from impulso import views as iv
    from impulso.models import MetaItem

    mid = d['meta_id']
    antes = MetaItem.objects.filter(meta_id=mid).order_by('-pk').values_list('pk', flat=True).first() or 0
    r = None
    for texto in d['textos']:
        r = chamar_view(iv.meta_item_add, user, f'/impulso/metas/{mid}/item/', dados={'texto': texto}, meta_id=mid)
        if not _sem_erro(r):
            break
    novos = list(MetaItem.objects.filter(meta_id=mid, pk__gt=antes).order_by('pk'))
    if not novos:
        return 'Nenhum passo foi acrescentado: ' + _motivo(r)
    linhas = [f'{len(novos)} passo(s) acrescentado(s) à meta #{mid}:'] + [f'  item #{i.pk} · {i.texto}' for i in novos]
    if len(novos) < len(d['textos']):
        linhas.append('Os demais não entraram: ' + _motivo(r))
    linhas.append(_meta_link(mid))
    return '\n'.join(linhas)


def _previa_item_marcar(user, args, concluir):
    item = _item_por_id(args)
    meta = item.meta
    if not _mexe_nos_passos(user, meta):
        raise Invalido('Você não pode marcar os passos desta meta (marcam os responsáveis, o gestor e o superadmin).')
    if item.concluido == concluir:
        raise Invalido('Esse passo já está feito.' if concluir else 'Esse passo já está em aberto.')
    feitos, total = meta.progresso_itens
    linhas = [f'{"Marcar como feito" if concluir else "Reabrir"} o passo item #{item.pk} "{item.texto}" da meta '
              f'#{meta.pk} "{meta.titulo}" — o to-do fica {feitos + (1 if concluir else -1)}/{total}.']
    if not concluir and item.concluido_por_id:
        linhas.append(f'Sai o registro de quem marcou ({_nome(item.concluido_por)}, {_dh(item.concluido_em)}).')
    linhas.append('Ninguém é avisado; quem acompanha vê o avanço na meta.')
    return '\n'.join(linhas), {'item_id': item.pk, 'meta_id': meta.pk, 'concluido': concluir}


def _exec_item_marcar(user, d):
    from impulso import views as iv
    from impulso.models import MetaItem

    item = MetaItem.objects.filter(pk=d['item_id']).first()
    if item is None:
        return 'Esse passo não existe mais.'
    # A view inverte o estado: se alguém já mexeu desde a prévia, inverter faria o contrário do pedido.
    if item.concluido == d['concluido']:
        return f'Nada a fazer: o passo item #{item.pk} já estava {"feito" if item.concluido else "em aberto"}.'
    r = chamar_view(iv.meta_item_toggle, user, f'/impulso/metas/item/{item.pk}/check/', item_id=item.pk)
    if not _ok_json(r):
        return 'O passo não mudou: ' + _motivo(r)
    corpo = r['json']
    return (f'Passo item #{item.pk} "{item.texto}" {"feito" if corpo.get("concluido") else "reaberto"} — to-do '
            f'{corpo.get("feitos")}/{corpo.get("total")}.\n{_meta_link(d["meta_id"])}')


def _previa_item_editar(user, args):
    item = _item_por_id(args)
    if not _mexe_nos_passos(user, item.meta):
        raise Invalido('Você não pode editar este passo (editam o gestor, os responsáveis da meta e o superadmin).')
    texto = ' '.join(str(args.get('texto') or '').split())[:300]
    if not texto:
        raise Invalido('O passo não pode ficar sem texto.')
    if texto == item.texto:
        raise Invalido('O texto pedido é igual ao de agora.')
    return (f'Trocar o texto do passo item #{item.pk} da meta #{item.meta_id} "{item.meta.titulo}": "{item.texto}" → '
            f'"{texto}". Ninguém é avisado.'), {'item_id': item.pk, 'meta_id': item.meta_id, 'texto': texto}


def _exec_item_editar(user, d):
    from impulso import views as iv

    r = chamar_view(iv.meta_item_editar, user, f'/impulso/metas/item/{d["item_id"]}/editar/', dados={'texto': d['texto']},
                    item_id=d['item_id'])
    if not _sem_erro(r):
        return 'O passo não foi alterado: ' + _motivo(r)
    return f'Passo item #{d["item_id"]} agora diz "{d["texto"]}".\n{_meta_link(d["meta_id"])}'


def _previa_item_excluir(user, args):
    item = _item_por_id(args)
    if not _mexe_nos_passos(user, item.meta):
        raise Invalido('Você não pode excluir este passo (excluem o gestor, os responsáveis da meta e o superadmin).')
    linhas = [f'Remover o passo item #{item.pk} "{item.texto}" do to-do da meta #{item.meta_id} "{item.meta.titulo}".']
    if item.concluido:
        linhas.append(f'Ele estava feito ({_nome(item.concluido_por)}, {_dh(item.concluido_em)}): esse registro some junto.')
    linhas.append('Não dá para desfazer.')
    return '\n'.join(linhas), {'item_id': item.pk, 'meta_id': item.meta_id}


def _exec_item_excluir(user, d):
    from impulso import views as iv
    from impulso.models import MetaItem

    iid = d['item_id']
    r = chamar_view(iv.meta_item_excluir, user, f'/impulso/metas/item/{iid}/excluir/', item_id=iid)
    if not _sem_erro(r) or MetaItem.objects.filter(pk=iid).exists():
        return 'O passo não foi removido: ' + _motivo(r)
    return f'Passo item #{iid} removido.\n{_meta_link(d["meta_id"])}'


def _previa_comentar(user, args):
    meta = _meta_visivel(user, args)
    mensagem = str(args.get('mensagem') or '').strip()
    if not mensagem:
        raise Invalido('Escreva a mensagem do comentário.')
    # A view avisa a outra ponta: o responsável principal e o gestor (não os demais participantes).
    avisados = {u.pk: u for u, pk in ((meta.colaborador, meta.colaborador_id), (meta.gestor, meta.gestor_id))
                if pk and pk != user.pk}
    linhas = [f'Comentar na meta #{meta.pk} "{meta.titulo}": "{_cortar(mensagem, 800)}"',
              f'Recebem aviso: {_nomes(avisados.values())}.' if avisados
              else 'Ninguém recebe aviso (você é o responsável e o gestor da meta).']
    return '\n'.join(linhas), {'meta_id': meta.pk, 'mensagem': mensagem}


def _exec_comentar(user, d):
    from impulso import views as iv
    from impulso.models import MetaComentario

    mid = d['meta_id']
    antes = MetaComentario.objects.filter(meta_id=mid).order_by('-pk').values_list('pk', flat=True).first() or 0
    r = chamar_view(iv.meta_add_comentario, user, f'/impulso/metas/{mid}/comentar/', dados={'mensagem': d['mensagem']},
                    meta_id=mid)
    novo = MetaComentario.objects.filter(meta_id=mid, autor=user, pk__gt=antes).order_by('-pk').first()
    if not _sem_erro(r) or novo is None:
        return 'O comentário não foi publicado: ' + _motivo(r)
    return f'Comentário #{novo.pk} publicado na meta #{mid}.\n{_meta_link(mid)}'


def _previa_excluir_comentario(user, args):
    from impulso.models import MetaComentario

    if not args.get('comentario_id'):
        raise Invalido('Informe comentario_id (os ids vêm em impulso_consultar o_que=meta).')
    c = (MetaComentario.objects.select_related('meta', 'autor')
         .filter(pk=_inteiro(args['comentario_id'], 'comentario_id')).first())
    if c is None:
        raise Invalido('Comentário não encontrado.')
    if not (user.is_superuser or c.autor_id == user.pk or c.meta.gestor_id == user.pk):
        raise Invalido('Você só pode excluir os seus próprios comentários (o gestor da meta e o superadmin apagam '
                       'qualquer um).')
    de = 'seu' if c.autor_id == user.pk else f'de {_nome(c.autor)}'
    return (f'Excluir o comentário #{c.pk} ({de}, {_dh(c.criado_em)}) da meta #{c.meta_id} "{c.meta.titulo}": '
            f'"{_cortar(c.mensagem, 300)}".\nNinguém é avisado. Não dá para desfazer.'), {
        'comentario_id': c.pk, 'meta_id': c.meta_id}


def _exec_excluir_comentario(user, d):
    from impulso import views as iv
    from impulso.models import MetaComentario

    cid = d['comentario_id']
    r = chamar_view(iv.meta_excluir_comentario, user, f'/impulso/metas/comentario/{cid}/excluir/', comentario_id=cid)
    if not _sem_erro(r) or MetaComentario.objects.filter(pk=cid).exists():
        return 'O comentário não foi excluído: ' + _motivo(r)
    return f'Comentário #{cid} excluído.\n{_meta_link(d["meta_id"])}'


def _previa_anexar_link(user, args):
    meta = _meta_visivel(user, args)
    url = _link_valido(args.get('url'))
    titulo = ' '.join(str(args.get('titulo') or '').split())[:200]
    return (f'Anexar o link {url}' + (f' como "{titulo}"' if titulo else '') + f' à meta #{meta.pk} "{meta.titulo}".\n'
            'Ninguém é avisado; o card mostra a novidade para quem acompanha. ' + SEM_ARQUIVO), {
        'meta_id': meta.pk, 'url': url, 'titulo': titulo}


def _exec_anexar_link(user, d):
    from impulso import views as iv
    from impulso.models import MetaAnexo

    mid = d['meta_id']
    antes = MetaAnexo.objects.filter(meta_id=mid).order_by('-pk').values_list('pk', flat=True).first() or 0
    r = chamar_view(iv.meta_add_anexo, user, f'/impulso/metas/{mid}/anexo/', dados={'url': d['url'], 'titulo': d['titulo']},
                    meta_id=mid)
    novo = MetaAnexo.objects.filter(meta_id=mid, enviado_por=user, pk__gt=antes).order_by('-pk').first()
    if not _sem_erro(r) or novo is None:
        return 'O link não foi anexado: ' + _motivo(r)
    return f'Link anexado: anexo #{novo.pk} "{novo.nome_exibicao}".\n{_meta_link(mid)}'


def _anexo_da_meta(user, args, verbo):
    from impulso import views as iv
    from impulso.models import MetaAnexo

    if not args.get('anexo_id'):
        raise Invalido('Informe anexo_id (os ids vêm em impulso_consultar o_que=meta).')
    anexo = (MetaAnexo.objects.select_related('meta', 'enviado_por')
             .filter(pk=_inteiro(args['anexo_id'], 'anexo_id')).first())
    if anexo is None or not iv._pode_ver_meta(user, anexo.meta):
        raise Invalido('Anexo não encontrado — ou você não tem acesso à meta dele.')
    if not iv._pode_mexer_no_anexo(user, anexo):
        raise Invalido(f'Você não pode {verbo} este anexo: mexe nele quem anexou, o gestor da meta e o superadmin.')
    return anexo


def _previa_renomear_anexo(user, args):
    anexo = _anexo_da_meta(user, args, 'editar')
    titulo = ' '.join(str(args['titulo']).split())[:200] if args.get('titulo') is not None else anexo.titulo
    url = anexo.url
    if args.get('url'):
        if anexo.tipo != anexo.Tipo.LINK:
            raise Invalido('Esse anexo é um arquivo: dá para renomear, mas trocar o arquivo só pela tela.')
        url = _link_valido(args['url'])
    if titulo == anexo.titulo and url == anexo.url:
        raise Invalido('Nada muda: informe o novo titulo (ou a nova url, se o anexo for link).')
    mudancas = []
    if titulo != anexo.titulo:
        mudancas.append(f'título "{anexo.titulo or anexo.nome_exibicao}" → "{titulo or "(sem título)"}"')
    if url != anexo.url:
        mudancas.append(f'link {anexo.url} → {url}')
    dados = {'meta_id': anexo.meta_id, 'anexo_id': anexo.pk, 'titulo': titulo}
    if anexo.tipo == anexo.Tipo.LINK:
        dados['url'] = url
    return (f'Alterar o anexo #{anexo.pk} da meta #{anexo.meta_id} "{anexo.meta.titulo}": ' + '; '.join(mudancas)
            + '. Ninguém é avisado.'), dados


def _exec_renomear_anexo(user, d):
    from impulso import views as iv
    from impulso.models import MetaAnexo

    mid, aid = d['meta_id'], d['anexo_id']
    r = chamar_view(iv.meta_anexo_editar, user, f'/impulso/metas/{mid}/anexo/{aid}/editar/',
                    dados={k: d[k] for k in ('titulo', 'url') if k in d}, meta_id=mid, anexo_id=aid)
    if not _sem_erro(r):
        return 'O anexo não foi alterado: ' + _motivo(r)
    anexo = MetaAnexo.objects.get(pk=aid)
    return (f'Anexo #{aid} atualizado: "{anexo.nome_exibicao}"' + (f' · {anexo.url}' if anexo.url else '')
            + f'.\n{_meta_link(mid)}')


def _previa_excluir_anexo(user, args):
    anexo = _anexo_da_meta(user, args, 'excluir')
    oque = f'o link {anexo.url}' if anexo.tipo == anexo.Tipo.LINK else 'o arquivo, que é apagado do armazenamento'
    return (f'Excluir o anexo #{anexo.pk} "{anexo.nome_exibicao}" da meta #{anexo.meta_id} "{anexo.meta.titulo}" — '
            f'{oque} (enviado por {_nome(anexo.enviado_por)}).\nNinguém é avisado. Não dá para desfazer.'), {
        'meta_id': anexo.meta_id, 'anexo_id': anexo.pk}


def _exec_excluir_anexo(user, d):
    from impulso import views as iv
    from impulso.models import MetaAnexo

    mid, aid = d['meta_id'], d['anexo_id']
    r = chamar_view(iv.meta_anexo_excluir, user, f'/impulso/metas/{mid}/anexo/{aid}/excluir/', meta_id=mid, anexo_id=aid)
    if not _sem_erro(r) or MetaAnexo.objects.filter(pk=aid).exists():
        return 'O anexo não foi excluído: ' + _motivo(r)
    return f'Anexo #{aid} excluído.\n{_meta_link(mid)}'


# ─── Ações: feedback e Conectar ──────────────────────────────────────────────

def _previa_feedback_criar(user, args):
    from impulso import filtros
    from impulso.models import ImpulsoFeedback
    from impulso.utils import get_colaboradores

    if not _gestor(user):
        raise Invalido('Ação disponível apenas para gestores do Impulso: só gestor registra feedback.')
    if not args.get('colaborador_id'):
        raise Invalido('Diga para quem é o feedback (colaborador_id; veja impulso_consultar o_que=pessoas).')
    colaborador = get_colaboradores().filter(pk=_inteiro(args['colaborador_id'], 'colaborador_id')).first()
    if colaborador is None:
        raise Invalido('Essa pessoa não é colaboradora do Impulso (veja impulso_consultar o_que=pessoas).')
    texto = str(args.get('mes') or '').strip()
    if texto:
        periodo = filtros.periodo_do_mes(texto)
        if not periodo:
            raise Invalido(f'mes inválido ("{texto}"). Use AAAA-MM.')
        referencia = periodo[0]
    else:
        hoje = timezone.localdate()
        referencia = date(hoje.year, hoje.month, 1)
    fortes = str(args.get('pontos_fortes') or '').strip()
    melhoria = str(args.get('pontos_melhoria') or '').strip()
    comentario = str(args.get('comentario') or '').strip()
    if not (fortes and melhoria):
        raise Invalido('Preencha pontos_fortes e pontos_melhoria (a tela exige os dois).')
    linhas = [f'Registrar o feedback de {_mm(referencia)} para {_nome(colaborador)}, com você como gestor.',
              'Pontos fortes: ' + _cortar(fortes, 400), 'Pontos a melhorar: ' + _cortar(melhoria, 400)]
    if comentario:
        linhas.append('Comentário geral: ' + _cortar(comentario, 400))
    ja = ImpulsoFeedback.objects.filter(colaborador=colaborador, referencia_mes=referencia).count()
    if ja:
        linhas.append(f'Já existe(m) {ja} feedback(s) de {_mm(referencia)} para essa pessoa: este seria mais um.')
    linhas.append('Ao salvar, a IA (OpenAI) lê o texto e dá a nota de 0 a 10 — leva alguns segundos; se falhar, o '
                  'feedback fica salvo sem análise e dá para tentar de novo.')
    linhas.append(f'{_nome(colaborador)} recebe aviso.')
    return '\n'.join(linhas), {'colaborador': str(colaborador.pk), 'referencia_mes': f'{referencia:%Y-%m}',
                               'pontos_fortes': fortes, 'pontos_melhoria': melhoria, 'comentario': comentario}


def _exec_feedback_criar(user, d):
    from impulso import views as iv
    from impulso.models import ImpulsoFeedback

    r = chamar_view(iv.feedback_create, user, '/impulso/feedbacks/novo/', dados=d)
    fid = _id_em(r['destino'], r'^/impulso/feedbacks/(\d+)/$')
    if not _sem_erro(r) or not fid:
        return 'O feedback não foi registrado: ' + _motivo(r)
    fb = ImpulsoFeedback.objects.select_related('colaborador').get(pk=fid)
    if fb.ai_summary:
        ia = f'Análise da IA: {_nota_ia(fb)} — {_cortar(fb.resumo_curto, 400)}'
    else:
        erro = f' ({_cortar(fb.ai_summary_error, 200)})' if fb.ai_summary_error else ''
        ia = f'A análise da IA não saiu{erro}: dá para tentar de novo com acao=regenerar_ia.'
    return (f'Feedback #{fb.pk} de {_mm(fb.referencia_mes)} registrado para {_nome(fb.colaborador)}, que foi avisado(a).'
            f'\n{ia}\n{_tela(f"/impulso/feedbacks/{fb.pk}/")}')


def _previa_feedback_regenerar(user, args):
    from impulso.models import ImpulsoFeedback

    if not args.get('feedback_id'):
        raise Invalido('Informe feedback_id (os ids vêm em impulso_consultar o_que=feedbacks).')
    fb = (ImpulsoFeedback.objects.select_related('colaborador')
          .filter(pk=_inteiro(args['feedback_id'], 'feedback_id')).first())
    if fb is None or not (user.is_superuser or user.pk in (fb.gestor_id, fb.colaborador_id)):
        raise Invalido('Feedback não encontrado — ou você não tem acesso a ele.')
    if not (_gestor(user) and (fb.gestor_id == user.pk or user.is_superuser)):
        raise Invalido('Só quem registrou o feedback (sendo gestor do Impulso) ou o superadmin refaz a análise da IA.')
    linhas = [f'Refazer a análise da IA (OpenAI) do feedback #{fb.pk} ({_mm(fb.referencia_mes)}, {_nome(fb.colaborador)}).']
    if fb.ai_summary:
        linhas.append(f'A análise atual ({_nota_ia(fb)}) é substituída pelo que a IA responder.')
    linhas.append('Leva alguns segundos (a IA é chamada até 4 vezes se falhar). Ninguém é avisado.')
    return '\n'.join(linhas), {'feedback_id': fb.pk}


def _exec_feedback_regenerar(user, d):
    from impulso import views as iv
    from impulso.models import ImpulsoFeedback

    fid = d['feedback_id']
    r = chamar_view(iv.feedback_regenerar_ia, user, f'/impulso/feedbacks/{fid}/ia/', fb_id=fid)
    if not _sem_erro(r):
        return 'A análise não foi refeita: ' + _motivo(r)
    fb = ImpulsoFeedback.objects.get(pk=fid)
    return (f'Análise da IA do feedback #{fid} refeita: {_nota_ia(fb)} — {_cortar(fb.resumo_curto, 400)}\n'
            f'{_tela(f"/impulso/feedbacks/{fid}/")}')


def _conteudo_por_id(args):
    from impulso.models import ConteudoConectar

    if not args.get('conteudo_id'):
        raise Invalido('Informe conteudo_id (os ids vêm em impulso_consultar o_que=conectar).')
    conteudo = ConteudoConectar.objects.filter(pk=_inteiro(args['conteudo_id'], 'conteudo_id')).first()
    if conteudo is None:
        raise Invalido('Conteúdo não encontrado.')
    return conteudo


def _previa_concluir_conteudo(user, args):
    from impulso import views as iv
    from impulso.models import ConclusaoConteudo
    from impulso.utils import get_gestores_do_setor

    conteudo = _conteudo_por_id(args)
    if not iv.pode_ver_conteudo(user, conteudo):
        raise Invalido('Este conteúdo não foi direcionado para você.')
    conclusao = ConclusaoConteudo.objects.filter(conteudo=conteudo, user=user).first()
    if conteudo.video_reproduzivel and not (conclusao and conclusao.video_concluido):
        raise Invalido('Esse conteúdo tem vídeo no portal: é preciso assistir até o fim na tela para concluir (o avanço '
                       f'do vídeo é conferido pelo servidor). {_tela(f"/impulso/conectar/{conteudo.pk}/")}')
    gestores = list(get_gestores_do_setor(user))
    linhas = [f'Marcar como concluído o conteúdo #{conteudo.pk} "{conteudo.titulo}" ({conteudo.rotulo}).',
              'Vai para a conferência: os pontos só entram quando um gestor do Impulso aprovar.',
              f'Recebem aviso: {_nomes(gestores)}.' if gestores
              else 'Não há gestor do Impulso no seu setor para ser avisado; qualquer gestor do Impulso pode conferir.',
              'Sem certificado por aqui. ' + SEM_ARQUIVO]
    if conclusao and conclusao.concluido:
        if conclusao.aprovacao == ConclusaoConteudo.Aprovacao.APROVADA:
            linhas.append('Atenção: ele já estava concluído e APROVADO — marcar de novo volta para a fila de conferência, '
                          'e os pontos saem até aprovarem outra vez.')
        elif conclusao.aprovacao == ConclusaoConteudo.Aprovacao.RECUSADA:
            linhas.append(f'Ele tinha sido recusado ("{_cortar(conclusao.observacao, 200)}"): este é o reenvio para nova '
                          'conferência.')
        else:
            linhas.append('Ele já está concluído e esperando conferência: marcar de novo só renova a data.')
    return '\n'.join(linhas), {'conteudo_id': conteudo.pk}


def _exec_concluir_conteudo(user, d):
    from impulso import views as iv
    from impulso.models import ConclusaoConteudo

    cid = d['conteudo_id']
    r = chamar_view(iv.conteudo_concluir, user, f'/impulso/conectar/{cid}/concluir/', conteudo_id=cid)
    conclusao = ConclusaoConteudo.objects.filter(conteudo_id=cid, user=user).first()
    if not _sem_erro(r) or not (conclusao and conclusao.concluido):
        return 'O conteúdo não foi concluído: ' + _motivo(r)
    return (f'Conteúdo #{cid} marcado como concluído (conclusão #{conclusao.pk}), esperando a conferência do gestor.\n'
            f'{_tela(f"/impulso/conectar/{cid}/")}')


def _previa_decidir_conclusao(user, args, decisao):
    from impulso.models import ConclusaoConteudo

    if not args.get('conclusao_id'):
        raise Invalido('Informe conclusao_id (os ids vêm em impulso_consultar o_que=conectar).')
    c = (ConclusaoConteudo.objects.select_related('user', 'conteudo', 'decidida_por')
         .filter(pk=_inteiro(args['conclusao_id'], 'conclusao_id')).first())
    if c is None:
        raise Invalido('Conclusão não encontrada.')
    if not c.pode_decidir(user):
        if c.user_id == user.pk:
            raise Invalido('Você não confere a própria conclusão: outro gestor confere.')
        if not c.concluido:
            raise Invalido('Essa conclusão ainda não foi marcada como feita.')
        raise Invalido('Você não confere esta conclusão (conferem os gestores do Impulso).')
    observacao = str(args.get('observacao') or '').strip()[:2000]
    if decisao == 'recusar' and not observacao:
        raise Invalido('Escreva o motivo da recusa (observacao) — é o que a pessoa lê para corrigir e reenviar.')
    linhas = [f'{"Aprovar" if decisao == "aprovar" else "Recusar"} a conclusão #{c.pk} de {_nome(c.user)}: '
              f'"{c.conteudo.titulo}" (concluído em {_dh(c.concluido_em)}, {"com" if c.certificado else "sem"} certificado).']
    if c.aprovacao != ConclusaoConteudo.Aprovacao.PENDENTE:
        linhas.append(f'Ela já estava {c.get_aprovacao_display().lower()}'
                      + (f' por {_nome(c.decidida_por)} em {_dh(c.decidida_em)}' if c.decidida_por_id else '')
                      + ': a decisão é substituída.')
    if decisao == 'aprovar':
        linhas.append(f'{_nome(c.user)} recebe aviso e os pontos do Conectar valem no mês.'
                      + (f' Observação: "{_cortar(observacao, 300)}".' if observacao else ''))
    else:
        linhas.append(f'Motivo: "{_cortar(observacao, 300)}". {_nome(c.user)} recebe aviso para corrigir e marcar de novo; '
                      'até lá não conta ponto.')
    return '\n'.join(linhas), {'conclusao_id': c.pk, 'decisao': decisao, 'observacao': observacao}


def _exec_decidir_conclusao(user, d):
    from impulso import views as iv
    from impulso.models import ConclusaoConteudo

    cid = d['conclusao_id']
    r = chamar_view(iv.conclusao_decidir, user, f'/impulso/conectar/conclusao/{cid}/decidir/',
                    dados={'decisao': d['decisao'], 'observacao': d['observacao']}, conclusao_id=cid)
    c = ConclusaoConteudo.objects.select_related('user', 'conteudo').get(pk=cid)
    aprovar = d['decisao'] == 'aprovar'
    esperado = ConclusaoConteudo.Aprovacao.APROVADA if aprovar else ConclusaoConteudo.Aprovacao.RECUSADA
    if not _sem_erro(r) or c.aprovacao != esperado:
        return 'A conclusão não foi conferida: ' + _motivo(r)
    return (f'Conclusão #{cid} ({_nome(c.user)}, "{c.conteudo.titulo}") {"aprovada" if aprovar else "recusada"}; a pessoa '
            f'foi avisada.\n{_tela("/impulso/conectar/")}')


GRUPOS_CONTEUDO = {'curso': 'CURSO', 'pop_video': 'POP_VIDEO', 'pop': 'POP_VIDEO', 'video': 'POP_VIDEO',
                   'video_e_pop': 'POP_VIDEO', 'pop_e_video': 'POP_VIDEO'}


def _grupo(valor, padrao):
    texto = _normal(valor).replace(' ', '_').replace('-', '_')
    if not texto:
        return padrao
    if texto not in GRUPOS_CONTEUDO:
        raise Invalido('grupo deve ser curso ou pop_video (vídeo e POP).')
    return GRUPOS_CONTEUDO[texto]


def _datas_conteudo(args, inicio, fim):
    """inicio/fim do período: data nova, "" para limpar, ou ausente para manter."""
    if args.get('inicio') is not None:
        inicio = _data(args['inicio'], 'inicio') if str(args['inicio']).strip() else None
    if args.get('fim') is not None:
        fim = _data(args['fim'], 'fim') if str(args['fim']).strip() else None
    if inicio and fim and fim < inicio:
        raise Invalido('O fim do período não pode ser antes do início.')
    return inicio, fim


def _post_conteudo(grupo, titulo, descricao, url, obrigatorio, inicio, fim, pessoas):
    """O formulário do Conectar inteiro: caixa desmarcada e pessoa fora da lista são apagadas pela view."""
    post = {'grupo': grupo, 'titulo': titulo, 'descricao': descricao, 'url': url,
            'inicio': inicio.isoformat() if inicio else '', 'fim': fim.isoformat() if fim else '',
            'obrigatorio_para': [str(i) for i in pessoas]}
    if obrigatorio:
        post['obrigatorio'] = 'on'
    return post


def _previa_criar_conteudo(user, args):
    from impulso.models import ConteudoConectar

    gestor = _gestor(user)
    grupo = _grupo(args.get('grupo'), ConteudoConectar.GRUPO_POP_VIDEO)
    if not gestor and grupo != ConteudoConectar.GRUPO_POP_VIDEO:
        raise Invalido('A equipe só pode subir POPs e vídeos; curso é publicado pelo gestor.')
    if not gestor and args.get('pessoas'):
        raise Invalido('Só o gestor direciona conteúdo a pessoas; o que a equipe sobe vale para todos.')
    titulo = _titulo(args, obrigatorio='Informe o título do conteúdo.')[:200]
    descricao = str(args.get('descricao') or '').strip()
    url = _link_valido(args['url']) if args.get('url') else ''
    inicio, fim = _datas_conteudo(args, None, None)
    obrigatorio = _sim(args.get('obrigatorio'), padrao=True) if gestor else False
    pessoas = _colaboradores_por_ids(_ids(args.get('pessoas'), 'pessoas'), 'pessoas') if gestor else []
    linhas = [f'Publicar no Conectar ({dict(ConteudoConectar.GRUPOS)[grupo]}): "{titulo}"'
              + (f', com o link {url}' if url else ', sem link') + '.',
              ('Obrigatório' if obrigatorio else 'Opcional') + f' · {_periodo_txt(inicio, fim)}.']
    if descricao:
        linhas.append('Descrição: ' + _cortar(descricao, 300))
    linhas.append(f'Direcionado a {_nomes(pessoas)} — cada um recebe aviso.' if pessoas
                  else 'Sem direcionamento: vale para toda a equipe' + ('' if gestor else ' (enviado pela equipe)')
                  + '; ninguém recebe aviso.')
    linhas.append('Só link e texto: ' + SEM_ARQUIVO)
    return '\n'.join(linhas), {'post': _post_conteudo(grupo, titulo, descricao, url, obrigatorio, inicio, fim,
                                                      [u.pk for u in pessoas])}


def _exec_criar_conteudo(user, d):
    from impulso import views as iv
    from impulso.models import ConteudoConectar

    r = chamar_view(iv.conteudo_create, user, '/impulso/conectar/novo/', dados=d['post'])
    cid = _id_em(r['destino'], r'^/impulso/conectar/(\d+)/$')
    if not _sem_erro(r) or not cid:
        return 'O conteúdo não foi publicado: ' + _motivo(r)
    c = ConteudoConectar.objects.get(pk=cid)
    para = c.obrigatorio_para.count()
    return (f'Conteúdo publicado: #{c.pk} "{c.titulo}" ({c.rotulo}, {"obrigatório" if c.obrigatorio else "opcional"}, '
            + (f'direcionado a {para} pessoa(s)' if para else 'para toda a equipe') + ').\n'
            + _tela(f'/impulso/conectar/{c.pk}/'))


def _previa_editar_conteudo(user, args):
    from impulso.models import ConteudoConectar
    from impulso.utils import get_colaboradores

    conteudo = _conteudo_por_id(args)
    if not conteudo.pode_excluir(user):
        raise Invalido('Só o SUPERADMIN ou um gestor do Impulso pode editar conteúdo.')
    rotulos = dict(ConteudoConectar.GRUPOS)
    grupo = _grupo(args.get('grupo'), conteudo.grupo)
    titulo = _titulo(args)[:200] if args.get('titulo') is not None else conteudo.titulo
    descricao = str(args['descricao']).strip() if args.get('descricao') is not None else conteudo.descricao
    url = conteudo.url
    if args.get('url') is not None:
        url = _link_valido(args['url']) if str(args['url']).strip() else ''
    obrigatorio = _sim(args['obrigatorio']) if args.get('obrigatorio') is not None else conteudo.obrigatorio
    inicio, fim = _datas_conteudo(args, conteudo.inicio, conteudo.fim)
    atuais = list(conteudo.obrigatorio_para.all())
    atuais_ids = {u.pk for u in atuais}
    validos = set(get_colaboradores().filter(pk__in=atuais_ids).values_list('pk', flat=True))
    sai = set(_ids(args.get('remover_pessoas'), 'remover_pessoas'))
    novos = [u for u in _colaboradores_por_ids(_ids(args.get('adicionar_pessoas'), 'adicionar_pessoas'),
                                               'adicionar_pessoas') if u.pk not in atuais_ids]
    final = (validos - sai) | {u.pk for u in novos}

    mudancas = []
    if grupo != conteudo.grupo:
        mudancas.append(f'grupo: {rotulos[conteudo.grupo]} → {rotulos[grupo]}')
    if titulo != conteudo.titulo:
        mudancas.append(f'título: "{conteudo.titulo}" → "{titulo}"')
    if descricao != conteudo.descricao:
        mudancas.append('descrição nova: ' + (_cortar(descricao, 300) or '(vazia)'))
    if url != conteudo.url:
        mudancas.append(f'link: {conteudo.url or "(nenhum)"} → {url or "(nenhum)"}')
    if obrigatorio != conteudo.obrigatorio:
        mudancas.append('passa a ser obrigatório' if obrigatorio else 'passa a ser opcional')
    if (inicio, fim) != (conteudo.inicio, conteudo.fim):
        mudancas.append(f'{_periodo_txt(conteudo.inicio, conteudo.fim)} → {_periodo_txt(inicio, fim)}')
    if novos:
        mudancas.append(f'direcionar também a {_nomes(novos)} (recebem aviso)')
    tirados = [u for u in atuais if u.pk in sai]
    if tirados:
        mudancas.append(f'deixa de ser direcionado a {_nomes(tirados)}')
    perdidos = [u for u in atuais if u.pk not in validos and u.pk not in sai]
    if perdidos:
        mudancas.append(f'{_nomes(perdidos)} não está(ão) mais no Impulso e sai(em) do direcionamento')
    if atuais_ids and not final:
        mudancas.append('sem ninguém direcionado, passa a valer para toda a equipe')
    if not mudancas:
        raise Invalido('Nada muda: informe o que alterar (titulo, descricao, url, grupo, obrigatorio, inicio, fim, '
                       'adicionar_pessoas ou remover_pessoas).')
    linhas = [f'Alterar o conteúdo #{conteudo.pk} "{conteudo.titulo}" do Conectar:'] + [f'• {m}' for m in mudancas]
    linhas.append('Arquivo e vídeo já anexados continuam como estão. ' + SEM_ARQUIVO)
    return '\n'.join(linhas), {'conteudo_id': conteudo.pk, 'post': _post_conteudo(
        grupo, titulo, descricao, url, obrigatorio, inicio, fim, sorted(final))}


def _exec_editar_conteudo(user, d):
    from impulso import views as iv
    from impulso.models import ConteudoConectar

    cid = d['conteudo_id']
    r = chamar_view(iv.conteudo_editar, user, f'/impulso/conectar/{cid}/editar/', dados=d['post'], conteudo_id=cid)
    if not _sem_erro(r) or r['destino'] != f'/impulso/conectar/{cid}/':
        return 'O conteúdo não foi alterado: ' + _motivo(r)
    c = ConteudoConectar.objects.get(pk=cid)
    para = c.obrigatorio_para.count()
    return (f'Conteúdo #{cid} atualizado: "{c.titulo}" ({c.rotulo}, {"obrigatório" if c.obrigatorio else "opcional"}, '
            + (f'direcionado a {para} pessoa(s)' if para else 'para toda a equipe') + ').\n'
            + _tela(f'/impulso/conectar/{cid}/'))


def _previa_excluir_conteudo(user, args):
    conteudo = _conteudo_por_id(args)
    if not conteudo.pode_excluir(user):
        raise Invalido('Só o SUPERADMIN ou um gestor do Impulso pode excluir conteúdo.')
    impacto = conteudo.impacto_da_exclusao
    linhas = [f'Excluir do Conectar o conteúdo #{conteudo.pk} "{conteudo.titulo}" ({conteudo.rotulo}).']
    if impacto['conclusoes']:
        linhas.append(f'Somem junto {impacto["conclusoes"]} registro(s) de conclusão — {impacto["concluidos"]} de quem já '
                      'tinha concluído (a pontuação do mês dessas pessoas é recalculada, e elas recebem aviso) — e '
                      f'{impacto["certificados"]} certificado(s).')
    else:
        linhas.append('Ninguém concluiu ainda.')
    linhas.append('Não dá para desfazer.')
    return '\n'.join(linhas), {'conteudo_id': conteudo.pk}


def _exec_excluir_conteudo(user, d):
    from impulso import views as iv
    from impulso.models import ConteudoConectar

    cid = d['conteudo_id']
    r = chamar_view(iv.conteudo_excluir, user, f'/impulso/conectar/{cid}/excluir/', conteudo_id=cid)
    if not _sem_erro(r) or ConteudoConectar.objects.filter(pk=cid).exists():
        return 'O conteúdo não foi excluído: ' + _motivo(r)
    return (_recados(r) or f'Conteúdo #{cid} excluído.') + f' {_url("/impulso/conectar/")}'


# ─── Ações: projetos foco ────────────────────────────────────────────────────

def _so_gestor(user, oque):
    if not _gestor(user):
        raise Invalido(f'Ação disponível apenas para gestores do Impulso: {oque}.')


def _projeto_por_id(args):
    from impulso.models import ProjetoFoco

    if not args.get('projeto_id'):
        raise Invalido('Informe projeto_id (os ids vêm em impulso_consultar o_que=projetos).')
    projeto = ProjetoFoco.objects.filter(pk=_inteiro(args['projeto_id'], 'projeto_id')).first()
    if projeto is None:
        raise Invalido('Projeto não encontrado.')
    return projeto


def _link_projeto(pid):
    return _tela(f'/impulso/conectar/projetos/{pid}/')


def _previa_projeto_criar(user, args):
    _so_gestor(user, 'só gestor cria projeto foco')
    nome = _titulo(args, 'nome', 'Informe o nome do projeto.')[:200]
    descricao = str(args.get('descricao') or '').strip()
    membros = _colaboradores_por_ids(_ids(args.get('membros'), 'membros'), 'membros')
    linhas = [f'Criar o projeto foco "{nome}".']
    if descricao:
        linhas.append('Descrição: ' + _cortar(descricao, 300))
    linhas.append(f'Equipe: {_nomes(membros)} — cada um recebe aviso.' if membros
                  else 'Sem equipe por enquanto (dá para incluir depois com acao=editar).')
    linhas.append('As tarefas entram depois, com acao=criar_tarefa.')
    return '\n'.join(linhas), {'nome': nome, 'descricao': descricao, 'membros': [str(u.pk) for u in membros]}


def _exec_projeto_criar(user, d):
    from impulso import views as iv
    from impulso.models import ProjetoFoco

    r = chamar_view(iv.projeto_foco_create, user, '/impulso/conectar/projetos/novo/', dados=d)
    pid = _id_em(r['destino'], r'^/impulso/conectar/projetos/(\d+)/$')
    if not _sem_erro(r) or not pid:
        return 'O projeto não foi criado: ' + _motivo(r)
    projeto = ProjetoFoco.objects.get(pk=pid)
    return f'Projeto foco criado: #{pid} "{projeto.nome}", com {projeto.membros.count()} membro(s).\n{_link_projeto(pid)}'


def _previa_projeto_editar(user, args):
    from impulso.utils import get_colaboradores

    _so_gestor(user, 'só gestor edita projeto foco')
    projeto = _projeto_por_id(args)
    nome = _titulo(args, 'nome', 'O projeto precisa de nome.')[:200] if args.get('nome') is not None else projeto.nome
    descricao = str(args['descricao']).strip() if args.get('descricao') is not None else projeto.descricao
    ativo = _sim(args['ativo']) if args.get('ativo') is not None else projeto.ativo
    atuais = list(projeto.membros.all())
    atuais_ids = {u.pk for u in atuais}
    # A tela grava a equipe inteira filtrada pelos colaboradores do Impulso: quem saiu do grupo cai junto.
    validos = set(get_colaboradores().filter(pk__in=atuais_ids).values_list('pk', flat=True))
    sai = set(_ids(args.get('remover_membros'), 'remover_membros'))
    novos = [u for u in _colaboradores_por_ids(_ids(args.get('adicionar_membros'), 'adicionar_membros'),
                                               'adicionar_membros') if u.pk not in atuais_ids]
    final = (validos - sai) | {u.pk for u in novos}
    mudancas = []
    if nome != projeto.nome:
        mudancas.append(f'nome: "{projeto.nome}" → "{nome}"')
    if descricao != projeto.descricao:
        mudancas.append('descrição nova: ' + (_cortar(descricao, 300) or '(vazia)'))
    if ativo != projeto.ativo:
        mudancas.append('reativar o projeto' if ativo else 'desativar o projeto: some da lista dos membros e as tarefas '
                                                           'dele deixam de pontuar')
    if novos:
        mudancas.append(f'incluir na equipe: {_nomes(novos)} (recebem aviso)')
    tirados = [u for u in atuais if u.pk in sai]
    if tirados:
        mudancas.append(f'tirar da equipe: {_nomes(tirados)} (recebem aviso; as tarefas deles no projeto ficam)')
    perdidos = [u for u in atuais if u.pk not in validos and u.pk not in sai]
    if perdidos:
        mudancas.append(f'{_nomes(perdidos)} não está(ão) mais no Impulso e sai(em) da equipe')
    if not mudancas:
        raise Invalido('Nada muda: informe nome, descricao, ativo, adicionar_membros ou remover_membros.')
    post = {'nome': nome, 'descricao': descricao, 'membros': [str(i) for i in sorted(final)]}
    if ativo:
        post['ativo'] = 'on'
    return (f'Alterar o projeto foco #{projeto.pk} "{projeto.nome}":\n' + '\n'.join(f'• {m}' for m in mudancas),
            {'projeto_id': projeto.pk, 'post': post})


def _exec_projeto_editar(user, d):
    from impulso import views as iv
    from impulso.models import ProjetoFoco

    pid = d['projeto_id']
    r = chamar_view(iv.projeto_foco_edit, user, f'/impulso/conectar/projetos/{pid}/editar/', dados=d['post'],
                    projeto_id=pid)
    if not _sem_erro(r) or r['destino'] != f'/impulso/conectar/projetos/{pid}/':
        return 'O projeto não foi alterado: ' + _motivo(r)
    projeto = ProjetoFoco.objects.get(pk=pid)
    return (f'Projeto #{pid} atualizado: "{projeto.nome}" · {"ativo" if projeto.ativo else "inativo"} · '
            f'{projeto.membros.count()} membro(s).\n{_link_projeto(pid)}')


def _previa_projeto_concluir(user, args, reabrir):
    _so_gestor(user, 'só gestor conclui ou reabre projeto foco')
    projeto = _projeto_por_id(args)
    envolvidos = list(User.objects.filter(
        pk__in=projeto.tarefas.exclude(responsavel__isnull=True).values_list('responsavel_id', flat=True)))
    if reabrir:
        if not projeto.concluido:
            raise Invalido('Este projeto não está concluído.')
        linhas = [f'Reabrir o projeto foco #{projeto.pk} "{projeto.nome}" (concluído em {_dh(projeto.concluido_em)}).',
                  'A metade dos pontos do Projeto FOCO pela conclusão sai da pontuação do mês de quem tem tarefa nele'
                  + (f' ({_nomes(envolvidos)})' if envolvidos else '') + '. Ninguém é avisado.']
    else:
        if projeto.concluido:
            raise Invalido('Este projeto já está concluído.')
        feitas, total = projeto.progresso_tarefas
        linhas = [f'Concluir o projeto foco #{projeto.pk} "{projeto.nome}".']
        if not total:
            linhas.append('Atenção: o projeto não tem nenhuma tarefa.')
        elif feitas < total:
            linhas.append(f'Atenção: só {feitas} de {total} tarefa(s) estão concluídas.')
        linhas.append(f'{len(envolvidos)} pessoa(s) com tarefa no projeto ({_nomes(envolvidos)}) recebem aviso e ganham a '
                      'metade dos pontos do Projeto FOCO pela conclusão.' if envolvidos
                      else 'Ninguém tem tarefa no projeto, então ninguém ganha os pontos da conclusão.')
        if not projeto.ativo:
            linhas.append('Atenção: o projeto está inativo — as tarefas dele não pontuam enquanto estiver assim.')
    return '\n'.join(linhas), {'projeto_id': projeto.pk, 'reabrir': reabrir}


def _exec_projeto_concluir(user, d):
    from impulso import views as iv
    from impulso.models import ProjetoFoco

    pid = d['projeto_id']
    r = chamar_view(iv.projeto_foco_concluir, user, f'/impulso/conectar/projetos/{pid}/concluir/',
                    dados={'reabrir': '1'} if d['reabrir'] else {}, projeto_id=pid)
    projeto = ProjetoFoco.objects.get(pk=pid)
    if not _sem_erro(r) or projeto.concluido == d['reabrir']:
        return f'O projeto não foi {"reaberto" if d["reabrir"] else "concluído"}: ' + _motivo(r)
    return (_recados(r) or f'Projeto #{pid} {"reaberto" if d["reabrir"] else "concluído"}.') + f'\n{_link_projeto(pid)}'


def _previa_tarefa_criar(user, args):
    _so_gestor(user, 'só gestor cria tarefa em projeto foco')
    projeto = _projeto_por_id(args)
    titulo = _titulo(args, obrigatorio='Informe o título da tarefa.')[:200]
    descricao = str(args.get('descricao') or '').strip()
    responsavel = None
    if args.get('responsavel_id'):
        responsavel = projeto.membros.filter(pk=_inteiro(args['responsavel_id'], 'responsavel_id')).first()
        if responsavel is None:
            membros = list(projeto.membros.all())
            raise Invalido('O responsável precisa ser da equipe do projeto: '
                           + (_nomes(membros) or 'a equipe está vazia — inclua a pessoa com acao=editar') + '.')
    prazo = _data(args['prazo'], 'prazo') if args.get('prazo') else None
    linhas = [f'Criar a tarefa "{titulo}" no projeto foco #{projeto.pk} "{projeto.nome}"'
              + (f', prazo {_d(prazo)}' if prazo else ', sem prazo') + '.',
              f'Responsável: {_nome(responsavel)} — recebe aviso.' if responsavel
              else 'Sem responsável: ninguém pontua por ela enquanto não tiver um.']
    if descricao:
        linhas.append('Descrição: ' + _cortar(descricao, 300))
    if prazo and prazo < timezone.localdate():
        linhas.append('Atenção: esse prazo já passou.')
    if projeto.concluido:
        linhas.append('Atenção: o projeto já está concluído.')
    if not projeto.ativo:
        linhas.append('Atenção: o projeto está inativo — a tarefa não pontua.')
    return '\n'.join(linhas), {'projeto_id': projeto.pk, 'titulo': titulo, 'descricao': descricao,
                               'responsavel': str(responsavel.pk) if responsavel else '',
                               'prazo': prazo.isoformat() if prazo else ''}


def _exec_tarefa_criar(user, d):
    from impulso import views as iv
    from impulso.models import TarefaProjeto

    pid = d['projeto_id']
    antes = TarefaProjeto.objects.filter(projeto_id=pid).order_by('-pk').values_list('pk', flat=True).first() or 0
    r = chamar_view(iv.tarefa_create, user, f'/impulso/conectar/projetos/{pid}/tarefa/',
                    dados={k: v for k, v in d.items() if k != 'projeto_id'}, projeto_id=pid)
    nova = (TarefaProjeto.objects.select_related('responsavel')
            .filter(projeto_id=pid, pk__gt=antes, titulo=d['titulo']).order_by('-pk').first())
    if not _sem_erro(r) or nova is None:
        return 'A tarefa não foi criada: ' + _motivo(r)
    return (f'Tarefa criada: #{nova.pk} "{nova.titulo}" · responsável '
            f'{_nome(nova.responsavel) if nova.responsavel_id else "nenhum"} · '
            f'{"prazo " + _d(nova.prazo) if nova.prazo else "sem prazo"}.\n{_link_projeto(pid)}')


def _previa_tarefa_status(user, args):
    from impulso.models import TarefaProjeto

    if not args.get('tarefa_id'):
        raise Invalido('Informe tarefa_id (os ids vêm em impulso_consultar o_que=projetos ou projeto).')
    tarefa = (TarefaProjeto.objects.select_related('projeto', 'responsavel')
              .filter(pk=_inteiro(args['tarefa_id'], 'tarefa_id')).first())
    if tarefa is None:
        raise Invalido('Tarefa não encontrada.')
    if not (_gestor(user) or tarefa.responsavel_id == user.pk):
        raise Invalido('Sem permissão para alterar esta tarefa: muda o status o responsável por ela ou um gestor do Impulso.')
    novo = _escolha(args.get('status'), TarefaProjeto.Status.values, 'status', STATUS_TAREFA_EXTRA)
    if not novo:
        raise Invalido('Informe o status: A_FAZER, EM_ANDAMENTO ou CONCLUIDA.')
    if novo == tarefa.status:
        raise Invalido(f'A tarefa já está em {tarefa.get_status_display()}.')
    quem = _nome(tarefa.responsavel) if tarefa.responsavel_id else 'nenhum'
    linhas = [f'Mudar a tarefa #{tarefa.pk} "{tarefa.titulo}" (projeto "{tarefa.projeto.nome}", responsável {quem}) de '
              f'{tarefa.get_status_display()} para {dict(TarefaProjeto.Status.choices)[novo]}.']
    if novo == TarefaProjeto.Status.CONCLUIDA and tarefa.responsavel_id:
        linhas.append(f'Conta na metade dos pontos do Projeto FOCO pela entrega de {quem} (proporcional às tarefas do mês).')
    elif tarefa.status == TarefaProjeto.Status.CONCLUIDA:
        linhas.append('Ela deixa de contar como entregue na pontuação do Projeto FOCO.')
    linhas.append('Ninguém é avisado.')
    return '\n'.join(linhas), {'tarefa_id': tarefa.pk, 'projeto_id': tarefa.projeto_id, 'status': novo}


def _exec_tarefa_status(user, d):
    from impulso import views as iv
    from impulso.models import TarefaProjeto

    tid = d['tarefa_id']
    r = chamar_view(iv.tarefa_update_status, user, f'/impulso/conectar/tarefa/{tid}/status/', dados={'status': d['status']},
                    tarefa_id=tid)
    tarefa = TarefaProjeto.objects.get(pk=tid)
    if not _sem_erro(r) or tarefa.status != d['status']:
        return 'A tarefa não mudou: ' + _motivo(r)
    return f'Tarefa #{tid} "{tarefa.titulo}" agora está em {tarefa.get_status_display()}.\n{_link_projeto(d["projeto_id"])}'


def _previa_projeto_link(user, args):
    projeto = _projeto_visivel(user, args)
    url = _link_valido(args.get('url'))
    titulo = ' '.join(str(args.get('titulo') or '').split())[:200]
    return (f'Anexar o link {url}' + (f' como "{titulo}"' if titulo else '')
            + f' ao projeto foco #{projeto.pk} "{projeto.nome}".\nNinguém é avisado. ' + SEM_ARQUIVO), {
        'projeto_id': projeto.pk, 'url': url, 'titulo': titulo}


def _exec_projeto_link(user, d):
    from impulso import views as iv
    from impulso.models import ProjetoAnexo

    pid = d['projeto_id']
    antes = ProjetoAnexo.objects.filter(projeto_id=pid).order_by('-pk').values_list('pk', flat=True).first() or 0
    r = chamar_view(iv.projeto_anexo_add, user, f'/impulso/conectar/projetos/{pid}/anexo/',
                    dados={'url': d['url'], 'titulo': d['titulo']}, projeto_id=pid)
    novo = ProjetoAnexo.objects.filter(projeto_id=pid, enviado_por=user, pk__gt=antes).order_by('-pk').first()
    if not _sem_erro(r) or novo is None:
        return 'O link não foi anexado: ' + _motivo(r)
    return f'Link anexado ao projeto: anexo #{novo.pk} "{novo.nome_exibicao}".\n{_link_projeto(pid)}'


def _previa_projeto_anexo_excluir(user, args):
    from impulso import views as iv
    from impulso.models import ProjetoAnexo

    if not args.get('anexo_id'):
        raise Invalido('Informe anexo_id (os ids vêm em impulso_consultar o_que=projeto).')
    anexo = (ProjetoAnexo.objects.select_related('projeto', 'enviado_por')
             .filter(pk=_inteiro(args['anexo_id'], 'anexo_id')).first())
    if anexo is None or not iv._pode_ver_projeto(user, anexo.projeto):
        raise Invalido('Anexo não encontrado — ou você não faz parte do projeto dele.')
    if not anexo.pode_mexer(user):
        raise Invalido('Você não pode excluir este anexo: exclui quem anexou, quem criou o projeto e o superadmin.')
    oque = f'o link {anexo.url}' if anexo.tipo == anexo.Tipo.LINK else 'o arquivo, que é apagado do armazenamento'
    return (f'Excluir o anexo #{anexo.pk} "{anexo.nome_exibicao}" do projeto #{anexo.projeto_id} "{anexo.projeto.nome}" — '
            f'{oque} (enviado por {_nome(anexo.enviado_por)}).\nNinguém é avisado. Não dá para desfazer.'), {
        'projeto_id': anexo.projeto_id, 'anexo_id': anexo.pk}


def _exec_projeto_anexo_excluir(user, d):
    from impulso import views as iv
    from impulso.models import ProjetoAnexo

    pid, aid = d['projeto_id'], d['anexo_id']
    r = chamar_view(iv.projeto_anexo_excluir, user, f'/impulso/conectar/projetos/{pid}/anexo/{aid}/excluir/',
                    projeto_id=pid, anexo_id=aid)
    if not _sem_erro(r) or ProjetoAnexo.objects.filter(pk=aid).exists():
        return 'O anexo não foi excluído: ' + _motivo(r)
    return f'Anexo #{aid} excluído do projeto.\n{_link_projeto(pid)}'


# ─── Ações: ideias (Inovar) ──────────────────────────────────────────────────

def _ideia_por_id(args):
    from impulso.models import Ideia

    if not args.get('ideia_id'):
        raise Invalido('Informe ideia_id (os ids vêm em impulso_consultar o_que=ideias).')
    ideia = Ideia.objects.filter(pk=_inteiro(args['ideia_id'], 'ideia_id')).first()
    if ideia is None:
        raise Invalido('Ideia não encontrada.')
    return ideia


def _textos_da_ideia(args, ideia=None):
    campos = {}
    for campo, rotulo in (('descricao', 'a ideia (descricao)'), ('setor_impacto', 'o setor_impacto'),
                          ('motivo', 'o motivo')):
        if args.get(campo) is None and ideia is not None:
            campos[campo] = getattr(ideia, campo)
            continue
        valor = str(args.get(campo) or '').strip()
        if campo == 'setor_impacto':
            valor = ' '.join(valor.split())
        if not valor:
            raise Invalido(f'Preencha {rotulo}: a tela exige a ideia, o setor de impacto e o motivo.')
        campos[campo] = valor
    if len(campos['setor_impacto']) > 150:
        raise Invalido('setor_impacto passa de 150 caracteres: resuma (ex.: "Financeiro", "Lojas da região X").')
    return campos


def _previa_ideia_criar(user, args):
    from impulso.models import Ideia

    campos = _textos_da_ideia(args)
    participantes = [u for u in _colaboradores_por_ids(_ids(args.get('participantes'), 'participantes'), 'participantes')
                     if u.pk != user.pk]
    if len(participantes) > Ideia.MAX_PARTICIPANTES:
        raise Invalido(f'Escolha no máximo {Ideia.MAX_PARTICIPANTES} pessoas além de você. O limite existe para a ideia '
                       'ter donos claros.')
    linhas = [f'Enviar a ideia para "{campos["setor_impacto"]}": "{_cortar(campos["descricao"], 500)}"',
              'Motivo: ' + _cortar(campos['motivo'], 300),
              f'Também assinam: {_nomes(participantes)} — recebem aviso e pontuam junto.' if participantes
              else 'Só você assina.',
              'O gestor avalia sem ver quem escreveu. Propor 3 ideias no mês vale os pontos do Inovar.']
    return '\n'.join(linhas), dict(campos, participantes=[str(u.pk) for u in participantes])


def _exec_ideia_criar(user, d):
    from impulso import views as iv
    from impulso.models import Ideia

    antes = Ideia.objects.order_by('-pk').values_list('pk', flat=True).first() or 0
    r = chamar_view(iv.ideia_create, user, '/impulso/inovar/nova/', dados=d)
    nova = Ideia.objects.filter(autor=user, pk__gt=antes).order_by('-pk').first()
    if not _sem_erro(r) or nova is None:
        return 'A ideia não foi enviada: ' + _motivo(r)
    return (f'Ideia enviada: #{nova.pk} (impacto: {nova.setor_impacto}), com {nova.participantes.count()} participante(s) '
            f'além de você.\n{_tela("/impulso/inovar/")}')


def _previa_ideia_editar(user, args):
    from impulso.models import Ideia
    from impulso.utils import get_colaboradores

    ideia = _ideia_por_id(args)
    if ideia.autor_id != user.pk and not user.is_superuser:
        raise Invalido('Você só pode editar as suas próprias ideias.')
    if not ideia.editavel:
        raise Invalido(f'Esta ideia já foi {ideia.get_status_display().lower()} e não pode mais ser editada.')
    campos = _textos_da_ideia(args, ideia)
    atuais = list(ideia.participantes.all())
    atuais_ids = {u.pk for u in atuais}
    validos = set(get_colaboradores().filter(pk__in=atuais_ids).exclude(pk=ideia.autor_id).values_list('pk', flat=True))
    sai = set(_ids(args.get('remover_participantes'), 'remover_participantes'))
    novos = [u for u in _colaboradores_por_ids(_ids(args.get('adicionar_participantes'), 'adicionar_participantes'),
                                               'adicionar_participantes')
             if u.pk not in atuais_ids and u.pk != ideia.autor_id]
    final = (validos - sai) | {u.pk for u in novos}
    if len(final) > Ideia.MAX_PARTICIPANTES:
        raise Invalido(f'A ideia ficaria com {len(final)} participantes: o máximo é {Ideia.MAX_PARTICIPANTES} além do autor.')
    mudancas = []
    for campo, rotulo in (('descricao', 'ideia'), ('setor_impacto', 'setor de impacto'), ('motivo', 'motivo')):
        if campos[campo] != getattr(ideia, campo):
            mudancas.append(f'{rotulo}: "{_cortar(campos[campo], 300)}"')
    if novos:
        mudancas.append(f'incluir {_nomes(novos)} (recebem aviso e pontuam junto)')
    tirados = [u for u in atuais if u.pk in sai]
    if tirados:
        mudancas.append(f'tirar {_nomes(tirados)}')
    perdidos = [u for u in atuais if u.pk not in validos and u.pk not in sai]
    if perdidos:
        mudancas.append(f'{len(perdidos)} participante(s) que não está(ão) mais no Impulso sai(em) junto')
    if not mudancas:
        raise Invalido('Nada muda: informe descricao, setor_impacto, motivo, adicionar_participantes ou '
                       'remover_participantes.')
    return (f'Alterar a ideia #{ideia.pk}:\n' + '\n'.join(f'• {m}' for m in mudancas),
            {'ideia_id': ideia.pk, 'post': dict(campos, participantes=[str(i) for i in sorted(final)])})


def _exec_ideia_editar(user, d):
    from impulso import views as iv

    iid = d['ideia_id']
    r = chamar_view(iv.ideia_edit, user, f'/impulso/inovar/{iid}/editar/', dados=d['post'], ideia_id=iid)
    if not _sem_erro(r) or r['destino'] != '/impulso/inovar/':
        return 'A ideia não foi alterada: ' + _motivo(r)
    return f'Ideia #{iid} atualizada.\n{_tela("/impulso/inovar/")}'


def _previa_ideia_decidir(user, args):
    from impulso.models import Ideia

    _so_gestor(user, 'só gestor decide ideias')
    ideia = _ideia_por_id(args)
    novo = _escolha(args.get('status'), Ideia.Status.values, 'status', STATUS_IDEIA_EXTRA) or ideia.status
    resposta = str(args['resposta']).strip() if args.get('resposta') is not None else ideia.resposta_gestor
    if novo == ideia.status and resposta == ideia.resposta_gestor:
        raise Invalido('Nada muda: informe o novo status (NOVA, EM_ANALISE, APROVADA, ARQUIVADA) e/ou a resposta ao autor.')
    rotulos = dict(Ideia.Status.choices)
    linhas = [f'Ideia #{ideia.pk} (impacto: "{ideia.setor_impacto}"): '
              + (f'status {ideia.get_status_display()} → {rotulos[novo]}.' if novo != ideia.status
                 else f'o status continua {ideia.get_status_display()}.')]
    if resposta != ideia.resposta_gestor:
        linhas.append(f'Retorno ao autor: "{_cortar(resposta, 500)}".' if resposta else 'O retorno anterior é apagado.')
    linhas.append('O autor recebe aviso' + ('' if ideia.autor_id == user.pk else ' (a autoria continua oculta para você)')
                  + '.')
    if novo == Ideia.Status.APROVADA and ideia.status != Ideia.Status.APROVADA:
        linhas.append(f'Ideia aprovada vale os pontos de "ideia aprovada" no Inovar de '
                      f'{_mm(timezone.localtime(ideia.criado_em))} (o mês em que foi criada), para o autor e quem participa.')
    elif ideia.status == Ideia.Status.APROVADA and novo != Ideia.Status.APROVADA:
        linhas.append('Ela deixa de contar como ideia aprovada na pontuação.')
    if novo in (Ideia.Status.APROVADA, Ideia.Status.ARQUIVADA):
        linhas.append('Depois disso o autor não edita mais o texto.')
    return '\n'.join(linhas), {'ideia_id': ideia.pk, 'status': novo, 'resposta_gestor': resposta}


def _exec_ideia_decidir(user, d):
    from impulso import views as iv
    from impulso.models import Ideia

    iid = d['ideia_id']
    r = chamar_view(iv.ideia_update_status, user, f'/impulso/inovar/{iid}/status/',
                    dados={'status': d['status'], 'resposta_gestor': d['resposta_gestor']}, ideia_id=iid)
    ideia = Ideia.objects.get(pk=iid)
    if not _sem_erro(r) or ideia.status != d['status']:
        return 'A ideia não foi atualizada: ' + _motivo(r)
    return f'Ideia #{iid} agora está {ideia.get_status_display().lower()}; o autor foi avisado.\n{_tela("/impulso/inovar/")}'


# ─── Ações: administração (exceções de assiduidade e fechamento de mês) ─────

def _previa_excecao_adicionar(user, args):
    from impulso.models import ExcecaoAssiduidade

    if not user.is_superuser:
        raise Invalido('Apenas o superadmin cria exceção de assiduidade: ela mexe na nota de todo mundo naquele mês.')
    dia = _data(args.get('data'), 'data')
    motivo = ' '.join(str(args.get('motivo') or '').split())[:200]
    if not motivo:
        raise Invalido('Escreva o motivo — ele aparece na tela para todo mundo.')
    existente = ExcecaoAssiduidade.objects.filter(data=dia).first()
    if existente:
        raise Invalido(f'{_d(dia)} já é uma exceção (#{existente.pk}: "{existente.motivo}").')
    return '\n'.join([
        f'Tornar {_dia(dia)} exceção de assiduidade: o ajuste de ponto desse dia deixa de contar no limite de ajustes do '
        'mês, para todo mundo.',
        f'Motivo (aparece na tela para todos): "{motivo}".',
        'O dia continua exigindo as 4 batidas, e falta injustificada nele continua zerando. Ninguém é avisado.',
    ]), {'data': dia.isoformat(), 'motivo': motivo}


def _exec_excecao_adicionar(user, d):
    from impulso import views as iv
    from impulso.models import ExcecaoAssiduidade

    r = chamar_view(iv.assiduidade_excecao_add, user, '/impulso/assiduidade/excecao/', dados=d)
    excecao = ExcecaoAssiduidade.objects.filter(data=d['data']).first()
    if not _sem_erro(r) or excecao is None:
        return 'A exceção não foi criada: ' + _motivo(r)
    return (f'{_recados(r) or "Exceção criada."} (exceção #{excecao.pk})\n'
            f'{_tela(f"/impulso/assiduidade/?mes={excecao.data.month}&ano={excecao.data.year}")}')


def _previa_excecao_remover(user, args):
    from impulso.models import ExcecaoAssiduidade

    if not user.is_superuser:
        raise Invalido('Apenas o superadmin mexe nas exceções de assiduidade.')
    if args.get('excecao_id'):
        excecao = ExcecaoAssiduidade.objects.filter(pk=_inteiro(args['excecao_id'], 'excecao_id')).first()
    elif args.get('data'):
        excecao = ExcecaoAssiduidade.objects.filter(data=_data(args['data'], 'data')).first()
    else:
        raise Invalido('Informe excecao_id ou data (veja impulso_consultar o_que=assiduidade).')
    if excecao is None:
        raise Invalido('Exceção de assiduidade não encontrada.')
    return (f'Desfazer a exceção #{excecao.pk} de {_dia(excecao.data)} ("{excecao.motivo}"): o ajuste de ponto desse dia '
            'volta a contar no limite do mês, para todo mundo. Ninguém é avisado.'), {'excecao_id': excecao.pk}


def _exec_excecao_remover(user, d):
    from impulso import views as iv
    from impulso.models import ExcecaoAssiduidade

    eid = d['excecao_id']
    r = chamar_view(iv.assiduidade_excecao_excluir, user, f'/impulso/assiduidade/excecao/{eid}/excluir/', excecao_id=eid)
    if not _sem_erro(r) or ExcecaoAssiduidade.objects.filter(pk=eid).exists():
        return 'A exceção não foi desfeita: ' + _motivo(r)
    return _recados(r) or f'Exceção #{eid} desfeita.'


def _mes_para_administrar(user, args, oque):
    _so_gestor(user, oque)
    mes = _mes_do_ciclo(args)
    if mes is None:
        raise Invalido('Informe mes_id (ou mes=AAAA-MM) — os ids vêm em impulso_consultar o_que=ciclos.')
    return mes


def _previa_fechar_mes(user, args):
    from impulso.utils import get_colaboradores

    mes = _mes_para_administrar(user, args, 'só gestor fecha mês do ciclo')
    if mes.is_fechado:
        raise Invalido(f'O mês {_mm(mes.referencia)} já está fechado (em {_dh(mes.fechado_em)}, por '
                       f'{_nome(mes.fechado_por)}).')
    hoje = timezone.localdate()
    linhas = [f'Fechar o mês {_mm(mes.referencia)} do ciclo "{mes.ciclo.nome}": congela a pontuação de '
              f'{get_colaboradores().count()} colaborador(es) como está agora — a faixa de cada um e as C$ reservadas '
              '(100 C$ por mês Ouro ou Impulso, pagas só quando o ciclo é encerrado, na tela).']
    if (mes.referencia.year, mes.referencia.month) >= (hoje.year, hoje.month):
        linhas.append('Atenção: esse mês ainda não terminou — o que acontecer depois do fechamento não entra, a não ser '
                      'que ele seja reaberto e fechado de novo.')
    linhas.append('Leva alguns segundos (calcula a pontuação de cada pessoa). Ninguém é avisado. Dá para reabrir enquanto '
                  'o ciclo não for encerrado.')
    return '\n'.join(linhas), {'mes_id': mes.pk}


def _exec_fechar_mes(user, d):
    from impulso import views as iv
    from impulso.models import CicloMes

    mid = d['mes_id']
    r = chamar_view(iv.mes_fechar, user, f'/impulso/ciclos/mes/{mid}/fechar/', mes_id=mid)
    mes = CicloMes.objects.get(pk=mid)
    if not _sem_erro(r) or not mes.is_fechado:
        return 'O mês não foi fechado: ' + _motivo(r)
    return f'{_recados(r) or "Mês fechado."}\n{_tela(f"/impulso/ciclos/mes/{mid}/")}'


def _previa_reabrir_mes(user, args):
    from impulso.models import Ciclo

    mes = _mes_para_administrar(user, args, 'só gestor reabre mês do ciclo')
    if mes.ciclo.status == Ciclo.Status.ENCERRADO:
        raise Invalido('Não é possível reabrir um mês de ciclo encerrado.')
    if not mes.is_fechado:
        raise Invalido(f'O mês {_mm(mes.referencia)} já está aberto.')
    return (f'Reabrir o mês {_mm(mes.referencia)} do ciclo "{mes.ciclo.nome}" para recálculo. As '
            f'{mes.pontuacoes.count()} pontuação(ões) congelada(s) continuam na tela até o mês ser fechado de novo, quando '
            'são recalculadas. Ninguém é avisado.'), {'mes_id': mes.pk}


def _exec_reabrir_mes(user, d):
    from impulso import views as iv
    from impulso.models import CicloMes

    mid = d['mes_id']
    r = chamar_view(iv.mes_reabrir, user, f'/impulso/ciclos/mes/{mid}/reabrir/', mes_id=mid)
    mes = CicloMes.objects.get(pk=mid)
    if not _sem_erro(r) or mes.is_fechado:
        return 'O mês não foi reaberto: ' + _motivo(r)
    return f'{_recados(r) or "Mês reaberto."}\n{_tela(f"/impulso/ciclos/mes/{mid}/")}'


# ─── Registro ────────────────────────────────────────────────────────────────
# Uma ferramenta por assunto, com o campo acao escolhendo o que fazer: a prévia
# guarda qual foi, e a confirmação executa exatamente aquela.

def _com_acao(tabela):
    def previa(user, args):
        _membro(user)
        acao = _normal(args.get('acao')).replace(' ', '_').replace('-', '_')
        if acao not in tabela:
            raise Invalido('acao deve ser ' + ' | '.join(tabela) + '.')
        resumo, dados = tabela[acao][0](user, args)
        return resumo, {'acao': acao, 'dados': dados}

    def execucao(user, pendente):
        return tabela[pendente['acao']][1](user, pendente['dados'])

    return previa, execucao


def _com(funcao, *extra):
    return lambda user, args: funcao(user, args, *extra)


_LIMITE = _int('Quantos listar.')
_META = _int('Id da meta (de impulso_consultar o_que=metas, solicitacoes ou atividades).')
_PRAZO = _txt('AAAA-MM-DD.')
_RECORRENCIA = _txt('unica | diaria | semanal | quinzenal | mensal.')

TOOLS.update({
    'impulso_consultar': {
        'fn': _impulso_consultar,
        'description': (
            'Impulso (/impulso/), com as permissões que o usuário tem na tela. o_que: painel (pontuação do mês e '
            'pendências) | metas (Kanban das metas aprovadas; mes padrão = mês atual, cortado pelo prazo; filtros '
            'colaborador_id, busca, setor_id, status) | meta (detalhe: to-do, anexos, comentários e o que ele pode fazer; '
            'meta_id) | solicitacoes (pedidos de meta pendentes e recusados) | atividades (metas não concluídas por prazo) '
            '| ranking (mês corrente ao vivo; calcula todos os colaboradores e pode demorar — filtre por setor_id ou '
            'busca) | pontuacao (item a item e meses fechados; colaborador_id — gestor vê qualquer um) | feedbacks '
            '(status sem_analise | com_analise | atencao) | feedback (texto e análise da IA já gerada; feedback_id) | '
            'assiduidade (ponto do mês, equipe e exceções) | conectar (cursos, vídeos e POPs, a conclusão dele e a fila de '
            'conferência do gestor) | projetos (projetos foco e tarefas) | projeto (projeto_id) | ideias (Inovar: autoria '
            'oculta para o gestor; só mes e status) | ciclos (lista; ciclo_id, mes_id ou mes=AAAA-MM para o detalhe) | '
            'pessoas (ids de quem ele pode escolher como colaborador, gestor ou participante). Não confundir com as '
            'metas comerciais do Power BI.'),
        'input_schema': _obj(
            ['o_que'],
            o_que=_txt('painel | metas | meta | solicitacoes | atividades | ranking | pontuacao | feedbacks | feedback | '
                       'assiduidade | conectar | projetos | projeto | ideias | ciclos | pessoas.'),
            meta_id=_META, feedback_id=_int('Id do feedback.'), projeto_id=_int('Id do projeto foco.'),
            colaborador_id=_int('Filtrar ou escolher um colaborador (id de o_que=pessoas).'),
            setor_id=_int('Filtrar por setor (principal ou vinculado).'),
            ciclo_id=_int('Id do ciclo (em ciclos).'), mes_id=_int('Id do mês do ciclo (em ciclos).'),
            mes=_txt('AAAA-MM ou todos. Nas metas o padrão é o mês atual; nas outras telas, todos.'),
            busca=_txt('Parte do nome da pessoa (em feedbacks, também o texto).'),
            status=_txt('metas: A_FAZER | EM_ANDAMENTO | ENTREGUE | CONCLUIDA; projetos: status da tarefa; ideias: NOVA | '
                        'EM_ANALISE | APROVADA | ARQUIVADA; feedbacks: sem_analise | com_analise | atencao.'),
            limite=_LIMITE),
    },
    'impulso_criar_meta': _acao(
        _previa_criar_meta, _exec_criar_meta,
        'Impulso: cria meta (atividade do Kanban). Gestor do Impulso cria para um colaborador (colaborador_id), escolhe o '
        'gestor responsável e outros responsáveis; para gente de outra área, a meta vai para aprovação de um gestor de lá. '
        'Colaborador cria só para si, com um gestor do próprio setor, e escolhe se passa por aprovação (padrão sim). Ids em '
        'impulso_consultar o_que=pessoas.',
        _obj(['titulo', 'descricao', 'prazo'], titulo=_txt('Título.'), descricao=_txt('Descrição.'),
             prazo=_txt('AAAA-MM-DD, hoje ou depois.'), recorrencia=_txt('unica (padrão) | diaria | semanal | quinzenal | '
                                                                         'mensal.'),
             apenas_dias_uteis=_bool('Com recorrência: prazo que cai no fim de semana anda para a segunda.'),
             itens=_lista_txt('Passos do to-do, na ordem.'), colaborador_id=_int('Gestor: para quem é a meta.'),
             gestor_id=_int('Gestor responsável — gestor: qualquer gestor do Impulso (padrão você); colaborador: um '
                            'gestor do seu setor (obrigatório).'),
             gestor_aprovador_id=_int('Gestor, meta para outra área: qual gestor de lá fica com ela.'),
             participantes=_lista_ids('Gestor: outros responsáveis.'),
             precisa_aprovacao=_bool('Colaborador: passar pela aprovação do gestor (padrão sim).'))),
    'impulso_alterar_meta': _acao(
        _previa_alterar_meta, _exec_alterar_meta,
        'Impulso: altera título, descrição, prazo (pode ir para trás), recorrência e dias úteis de uma meta (edita o gestor '
        'que responde por ela ou o superadmin) e/ou os outros responsáveis (só o gestor da meta ou o superadmin). Envie só '
        'o que muda.',
        _obj(['meta_id'], meta_id=_META, titulo=_txt('Novo título.'), descricao=_txt('Nova descrição.'),
             prazo=_txt('Novo prazo, AAAA-MM-DD.'), recorrencia=_RECORRENCIA,
             apenas_dias_uteis=_bool('Somente dias úteis (só vale com recorrência).'),
             adicionar_participantes=_lista_ids('Ids de quem passa a ser responsável também.'),
             remover_participantes=_lista_ids('Ids de quem deixa de ser responsável.'))),
    'impulso_andamento_meta': _acao(
        *_com_acao({'mover': (_previa_mover, _exec_mover), 'entregar': (_previa_entregar, _exec_entregar),
                    'avaliar': (_previa_avaliar, _exec_avaliar)}),
        'Impulso: andamento de uma meta aprovada. acao=mover (status A_FAZER | EM_ANDAMENTO | ENTREGUE; quem vê a meta), '
        'entregar (responsável principal; entrega_link opcional; avisa o gestor) ou avaliar (gestor da meta: nota_qualidade '
        'e nota_prazo de 0 a 5 e comentario; conclui a meta e, se ela repete, cria a próxima ocorrência).',
        _obj(['acao', 'meta_id'], acao=_txt('mover | entregar | avaliar.'), meta_id=_META,
             status=_txt('mover: A_FAZER | EM_ANDAMENTO | ENTREGUE.'), entrega_link=_txt('entregar: link (http...).'),
             nota_qualidade=_int('avaliar: 0 a 5.'), nota_prazo=_int('avaliar: 0 a 5.'),
             comentario=_txt('avaliar: comentário do gestor.'))),
    'impulso_decidir_meta': _acao(
        *_com_acao({'aprovar': (_com(_previa_decidir, 'aprovar'), _exec_decidir),
                    'recusar': (_com(_previa_decidir, 'recusar'), _exec_decidir),
                    'cancelar_solicitacao': (_previa_cancelar_solicitacao, _exec_cancelar_solicitacao)}),
        'Impulso: solicitação de meta pendente. acao=aprovar ou recusar (o gestor escolhido ou um gestor da área do '
        'colaborador, nunca quem pediu; motivo opcional na recusa) ou cancelar_solicitacao (só quem pediu; apaga o pedido).',
        _obj(['acao', 'meta_id'], acao=_txt('aprovar | recusar | cancelar_solicitacao.'), meta_id=_META,
             motivo=_txt('Motivo da recusa.'))),
    'impulso_copiar_ou_excluir_meta': _acao(
        *_com_acao({'duplicar': (_previa_duplicar, _exec_duplicar),
                    'solicitar_duplicacao': (_previa_solicitar_duplicacao, _exec_solicitar_duplicacao),
                    'excluir': (_previa_excluir_meta, _exec_excluir_meta)}),
        'Impulso: acao=duplicar (quem edita a meta; copia descrição, recorrência, to-do e responsáveis, opcionalmente para '
        'outro colaborador_id), solicitar_duplicacao (dono ou participante sem edição: pede a cópia a um gestor do setor; '
        'titulo, prazo e gestor_id opcionais) ou excluir (gestor que responde pela meta ou superadmin; apaga to-do, anexos '
        'e comentários).',
        _obj(['acao', 'meta_id'], acao=_txt('duplicar | solicitar_duplicacao | excluir.'), meta_id=_META,
             colaborador_id=_int('duplicar: quem recebe a cópia (padrão o mesmo).'),
             titulo=_txt('solicitar_duplicacao: título da cópia.'), prazo=_PRAZO,
             gestor_id=_int('solicitar_duplicacao: gestor do seu setor que aprova.'))),
    'impulso_itens_meta': _acao(
        *_com_acao({'adicionar': (_previa_item_adicionar, _exec_item_adicionar),
                    'concluir': (_com(_previa_item_marcar, True), _exec_item_marcar),
                    'reabrir': (_com(_previa_item_marcar, False), _exec_item_marcar),
                    'editar': (_previa_item_editar, _exec_item_editar),
                    'excluir': (_previa_item_excluir, _exec_item_excluir)}),
        'Impulso: to-do de uma meta. acao=adicionar (meta_id e texto ou textos; gestor, responsável principal ou '
        'superadmin), concluir, reabrir, editar (com texto) ou excluir (item_id; responsáveis, gestor ou superadmin).',
        _obj(['acao'], acao=_txt('adicionar | concluir | reabrir | editar | excluir.'), meta_id=_META,
             item_id=_int('Id do passo (de o_que=meta).'), texto=_txt('Texto do passo.'),
             textos=_lista_txt('adicionar: vários passos de uma vez.'))),
    'impulso_conversa_meta': _acao(
        *_com_acao({'comentar': (_previa_comentar, _exec_comentar),
                    'excluir_comentario': (_previa_excluir_comentario, _exec_excluir_comentario),
                    'anexar_link': (_previa_anexar_link, _exec_anexar_link),
                    'renomear_anexo': (_previa_renomear_anexo, _exec_renomear_anexo),
                    'excluir_anexo': (_previa_excluir_anexo, _exec_excluir_anexo)}),
        'Impulso: conversa e links de uma meta. acao=comentar (meta_id, mensagem; avisa o responsável e o gestor), '
        'excluir_comentario (comentario_id; autor, gestor da meta ou superadmin), anexar_link (meta_id, url, titulo), '
        'renomear_anexo (anexo_id, titulo e/ou url se for link) ou excluir_anexo (anexo_id; quem anexou, gestor da meta ou '
        'superadmin). Arquivo só pela tela.',
        _obj(['acao'], acao=_txt('comentar | excluir_comentario | anexar_link | renomear_anexo | excluir_anexo.'),
             meta_id=_META, comentario_id=_int('Id do comentário (de o_que=meta).'),
             anexo_id=_int('Id do anexo (de o_que=meta).'), mensagem=_txt('Texto do comentário.'),
             url=_txt('Link completo (http...).'), titulo=_txt('Título do anexo.'))),
    'impulso_feedback': _acao(
        *_com_acao({'criar': (_previa_feedback_criar, _exec_feedback_criar),
                    'regenerar_ia': (_previa_feedback_regenerar, _exec_feedback_regenerar)}),
        'Impulso: feedback mensal (só gestor do Impulso). acao=criar (colaborador_id, mes AAAA-MM — padrão o atual —, '
        'pontos_fortes, pontos_melhoria, comentario; ao salvar, a IA analisa e dá a nota, e o colaborador é avisado) ou '
        'regenerar_ia (feedback_id; quem deu o feedback ou superadmin).',
        _obj(['acao'], acao=_txt('criar | regenerar_ia.'), colaborador_id=_int('Para quem é o feedback.'),
             mes=_txt('Mês de referência, AAAA-MM.'), pontos_fortes=_txt('Pontos fortes.'),
             pontos_melhoria=_txt('Pontos a melhorar.'), comentario=_txt('Comentário geral.'),
             feedback_id=_int('Id do feedback.'))),
    'impulso_conectar': _acao(
        *_com_acao({'concluir': (_previa_concluir_conteudo, _exec_concluir_conteudo),
                    'aprovar_conclusao': (_com(_previa_decidir_conclusao, 'aprovar'), _exec_decidir_conclusao),
                    'recusar_conclusao': (_com(_previa_decidir_conclusao, 'recusar'), _exec_decidir_conclusao),
                    'criar_conteudo': (_previa_criar_conteudo, _exec_criar_conteudo),
                    'editar_conteudo': (_previa_editar_conteudo, _exec_editar_conteudo),
                    'excluir_conteudo': (_previa_excluir_conteudo, _exec_excluir_conteudo)}),
        'Impulso Conectar. acao=concluir (conteudo_id; vai para a conferência do gestor — vídeo do portal só depois de '
        'assistido na tela), aprovar_conclusao ou recusar_conclusao (conclusao_id; observacao obrigatória na recusa; '
        'gestor, nunca a própria), criar_conteudo (titulo, grupo curso | pop_video, url, descricao, obrigatorio, inicio, '
        'fim, pessoas; a equipe só sobe pop_video), editar_conteudo ou excluir_conteudo (conteudo_id; gestor ou '
        'superadmin). Só link e texto: arquivo, vídeo e certificado pela tela.',
        _obj(['acao'], acao=_txt('concluir | aprovar_conclusao | recusar_conclusao | criar_conteudo | editar_conteudo | '
                                 'excluir_conteudo.'),
             conteudo_id=_int('Id do conteúdo (de o_que=conectar).'), conclusao_id=_int('Id da conclusão a conferir.'),
             observacao=_txt('Observação da conferência (obrigatória na recusa).'), titulo=_txt('Título do conteúdo.'),
             grupo=_txt('curso | pop_video.'), url=_txt('Link externo (http...).'), descricao=_txt('Descrição.'),
             obrigatorio=_bool('Obrigatório (gestor; padrão sim).'), inicio=_txt('Início do período, AAAA-MM-DD.'),
             fim=_txt('Fim do período, AAAA-MM-DD.'), pessoas=_lista_ids('criar: direcionar a estas pessoas.'),
             adicionar_pessoas=_lista_ids('editar: direcionar também a.'),
             remover_pessoas=_lista_ids('editar: deixar de direcionar a.'))),
    'impulso_projeto': _acao(
        *_com_acao({'criar': (_previa_projeto_criar, _exec_projeto_criar),
                    'editar': (_previa_projeto_editar, _exec_projeto_editar),
                    'concluir': (_com(_previa_projeto_concluir, False), _exec_projeto_concluir),
                    'reabrir': (_com(_previa_projeto_concluir, True), _exec_projeto_concluir),
                    'criar_tarefa': (_previa_tarefa_criar, _exec_tarefa_criar),
                    'status_tarefa': (_previa_tarefa_status, _exec_tarefa_status),
                    'anexar_link': (_previa_projeto_link, _exec_projeto_link),
                    'excluir_anexo': (_previa_projeto_anexo_excluir, _exec_projeto_anexo_excluir)}),
        'Impulso projetos foco. Só gestor: acao=criar (nome, descricao, membros), editar (projeto_id; nome, descricao, '
        'ativo, adicionar_membros, remover_membros), concluir, reabrir, criar_tarefa (projeto_id, titulo, descricao, '
        'responsavel_id da equipe, prazo). Responsável ou gestor: status_tarefa (tarefa_id, status). Membro ou gestor: '
        'anexar_link (projeto_id, url, titulo) e excluir_anexo (anexo_id; quem anexou ou quem criou o projeto).',
        _obj(['acao'], acao=_txt('criar | editar | concluir | reabrir | criar_tarefa | status_tarefa | anexar_link | '
                                 'excluir_anexo.'),
             projeto_id=_int('Id do projeto foco.'), tarefa_id=_int('Id da tarefa.'), anexo_id=_int('Id do anexo.'),
             nome=_txt('Nome do projeto.'), descricao=_txt('Descrição do projeto ou da tarefa.'),
             ativo=_bool('Projeto ativo.'), membros=_lista_ids('criar: equipe.'),
             adicionar_membros=_lista_ids('editar: entram na equipe.'), remover_membros=_lista_ids('editar: saem.'),
             titulo=_txt('Título da tarefa ou do link.'), responsavel_id=_int('Responsável pela tarefa (da equipe).'),
             prazo=_PRAZO, status=_txt('A_FAZER | EM_ANDAMENTO | CONCLUIDA.'), url=_txt('Link completo (http...).'))),
    'impulso_ideia': _acao(
        *_com_acao({'criar': (_previa_ideia_criar, _exec_ideia_criar),
                    'editar': (_previa_ideia_editar, _exec_ideia_editar),
                    'decidir': (_previa_ideia_decidir, _exec_ideia_decidir)}),
        'Impulso Inovar. acao=criar (descricao, setor_impacto, motivo, participantes — até 3), editar (ideia_id; autor ou '
        'superadmin, enquanto NOVA ou EM_ANALISE; adicionar_participantes, remover_participantes) ou decidir (ideia_id, '
        'status, resposta ao autor; só gestor, que não vê a autoria).',
        _obj(['acao'], acao=_txt('criar | editar | decidir.'), ideia_id=_int('Id da ideia (de o_que=ideias).'),
             descricao=_txt('A ideia.'), setor_impacto=_txt('Setor de impacto (até 150 caracteres).'),
             motivo=_txt('Por que fazer.'), participantes=_lista_ids('criar: quem assina junto (até 3).'),
             adicionar_participantes=_lista_ids('editar: entram.'), remover_participantes=_lista_ids('editar: saem.'),
             status=_txt('decidir: NOVA | EM_ANALISE | APROVADA | ARQUIVADA.'),
             resposta=_txt('decidir: retorno ao autor.'))),
    'impulso_administrar': _acao(
        *_com_acao({'excecao_assiduidade_adicionar': (_previa_excecao_adicionar, _exec_excecao_adicionar),
                    'excecao_assiduidade_remover': (_previa_excecao_remover, _exec_excecao_remover),
                    'fechar_mes': (_previa_fechar_mes, _exec_fechar_mes),
                    'reabrir_mes': (_previa_reabrir_mes, _exec_reabrir_mes)}),
        'Impulso administração. acao=excecao_assiduidade_adicionar (data, motivo; só superadmin: o ajuste de ponto do dia '
        'deixa de contar para todos), excecao_assiduidade_remover (excecao_id ou data; só superadmin), fechar_mes ou '
        'reabrir_mes (mes_id ou mes=AAAA-MM; gestor do Impulso). A régua de pesos e o encerramento do ciclo (credita C$) '
        'ficam só na tela.',
        _obj(['acao'], acao=_txt('excecao_assiduidade_adicionar | excecao_assiduidade_remover | fechar_mes | reabrir_mes.'),
             data=_txt('Dia da exceção, AAAA-MM-DD.'), motivo=_txt('Motivo da exceção (aparece para todos).'),
             excecao_id=_int('Id da exceção (de o_que=assiduidade).'), mes_id=_int('Id do mês do ciclo (de o_que=ciclos).'),
             mes=_txt('Mês do ciclo, AAAA-MM.'))),
})
