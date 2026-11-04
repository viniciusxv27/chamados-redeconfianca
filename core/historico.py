"""Regras do Histórico de Atividades (/activity-history/).

O que o log guarda de fato (`core.SystemLog`): quem, tipo de ação, um texto
livre, IP, navegador e quando. **Não há colunas de antes/depois, de módulo nem de
localização.** Por isso, tudo o que a tela mostra a mais é derivado, e só do que
existe:

* **módulo**: sai do prefixo do código da ação (`TICKET_*` é Chamados). Ação
  genérica como `ADMIN_ACTION` não diz de qual módulo veio, e fica em Administração;
* **antes → depois**: sai do texto, nos poucos formatos que o gravam
  (`diferencas_do_texto`), ou da tabela `users.UserChangeLog`, que guarda campo a
  campo a edição de cadastro. Sem nenhum dos dois, a tela não mostra diferença:
  melhor nada do que um "antes" inventado;
* **localização**: é a posição da pessoa mais próxima do momento (app `maps`), e só
  para quem pode ver o mapa. Ela diz onde a pessoa estava perto dali, não de onde
  a ação foi feita.
"""
import re
from datetime import date, datetime, time, timedelta

from django.db.models import F, Q
from django.utils import timezone

# Quanto apagar por requisição. Cada lote é um DELETE curto em transação própria:
# os outros servidores usam o mesmo banco, e o gunicorn síncrono mata a requisição
# longa. Quem repete é a tela, mostrando o progresso.
LOTE_LIMPEZA = 2000

# Sem notícia da tela há mais que isso, a limpeza é dada como interrompida.
LIMPEZA_PARADA_MINUTOS = 2

ACAO_LIMPEZA = 'HISTORY_PURGE'
PALAVRA_CONFIRMACAO = 'LIMPAR'


def pode_ver_historico(user) -> bool:
    """SUPERADMIN do portal: o menu já oferecia a tela por hierarquia."""
    return bool(
        user and getattr(user, 'is_authenticated', False)
        and (user.is_superuser or getattr(user, 'hierarchy', None) == 'SUPERADMIN')
    )


def pode_limpar_historico(user) -> bool:
    """Separado de ver de propósito: apagar log não deve herdar quem só consulta."""
    return pode_ver_historico(user)


# ─── Catálogo de ações ────────────────────────────────────────────────────────
# O `ACTION_TYPES` do modelo lista 26 códigos, mas o portal grava mais de 60 e o
# banco aceita qualquer um. Sem este rótulo, a tela mostraria o código cru.

ROTULOS = {
    'ADMIN_ACTION': 'Ação administrativa',
    'APPROVER_CREATE': 'Aprovador criado',
    'APPROVER_DELETE': 'Aprovador removido',
    'APPROVER_UPDATE': 'Aprovador alterado',
    'ASSET_EXPORT': 'Exportação de ativos',
    'ASSET_IMPORT': 'Importação de ativos',
    'CATEGORY_CREATE': 'Categoria criada',
    'CATEGORY_DELETE': 'Categoria excluída',
    'CATEGORY_UPDATE': 'Categoria alterada',
    'COMMUNICATION_CREATE': 'Comunicado criado',
    'COMMUNICATION_DELETE': 'Comunicado excluído',
    'COMMUNICATION_SEND': 'Comunicado enviado',
    'COMMUNICATION_UPDATE': 'Comunicado alterado',
    'CS_ADD': 'C$ adicionado',
    'CS_ADD_REQUEST': 'Pedido de C$',
    'CS_APPROVE': 'C$ aprovado',
    'CS_CHANGE': 'C$ alterado',
    'CS_EXPORT': 'Exportação de C$',
    'CS_IMPORT_REQUEST': 'Pedido de importação de C$',
    'CS_REJECT': 'C$ recusado',
    'CS_REMOVE': 'C$ removido',
    'GROUP_CREATE': 'Grupo criado',
    'GROUP_DELETE': 'Grupo excluído',
    'GROUP_UPDATE': 'Grupo alterado',
    ACAO_LIMPEZA: 'Limpeza do histórico',
    'PASSWORD_CHANGE': 'Troca de senha',
    'PASSWORD_RESET': 'Senha redefinida',
    'PASSWORD_RESET_REQUEST': 'Pedido de nova senha',
    'PRIZE_CATEGORY_CREATE': 'Categoria de prêmio criada',
    'PRIZE_CATEGORY_DELETE': 'Categoria de prêmio excluída',
    'PRIZE_CATEGORY_UPDATE': 'Categoria de prêmio alterada',
    'PRIZE_REDEEM': 'Resgate de prêmio',
    'PRIZE_STATUS_TOGGLE': 'Prêmio ligado/desligado',
    'PRIZE_UPDATE': 'Prêmio alterado',
    'REDEMPTION_CANCELLED': 'Resgate cancelado',
    'REDEMPTION_PICKUP_NOTIFY': 'Aviso de retirada',
    'REDEMPTION_STATUS_UPDATE': 'Status de resgate alterado',
    'REPORT_COMMENT': 'Comentário em denúncia',
    'REPORT_CREATE': 'Denúncia criada',
    'REPORT_UPDATE': 'Denúncia alterada',
    'SECTOR_CREATE': 'Setor criado',
    'SECTOR_DELETE': 'Setor excluído',
    'SECTOR_EDIT': 'Setor alterado',
    'TICKET_ASSUME': 'Chamado assumido',
    'TICKET_ATTACHMENT': 'Anexo em chamado',
    'TICKET_BULK_DELETE': 'Chamados excluídos em lote',
    'TICKET_COMMENT': 'Comentário em chamado',
    'TICKET_COMMENT_DELETE': 'Comentário de chamado excluído',
    'TICKET_CREATE': 'Chamado criado',
    'TICKET_DELETE': 'Chamado excluído',
    'TICKET_EDIT': 'Chamado editado',
    'TICKET_PRIORITY_CHANGE': 'Prioridade de chamado alterada',
    'TICKET_UPDATE': 'Status de chamado alterado',
    'USER_CREATE': 'Usuário criado',
    'USER_EXPORT': 'Exportação de usuários',
    'USER_IMPORT': 'Importação de usuários',
    'USER_IMPORT_ADMISSION_SECTOR_TEMP': 'Importação de admissão/setor',
    'USER_IMPORT_COLABORADORES': 'Importação de colaboradores',
    'USER_LOGIN': 'Entrada no portal',
    'USER_LOGOUT': 'Saída do portal',
    'USER_PASSWORD_CHANGE': 'Senha de usuário alterada',
    'USER_UPDATE': 'Usuário alterado',
    'WEBHOOK_CREATE': 'Webhook criado',
    'WEBHOOK_DELETE': 'Webhook excluído',
    'WEBHOOK_ERROR': 'Erro de webhook',
    'WEBHOOK_SUCCESS': 'Webhook entregue',
    'WEBHOOK_TRIGGER': 'Webhook disparado',
    'WEBHOOK_UPDATE': 'Webhook alterado',
}

# Em ordem: o primeiro prefixo que casa decide. `acesso` vem antes de `usuarios`
# porque USER_LOGIN também começa com USER_.
MODULOS = [
    ('acesso', 'Acesso e senhas', ('USER_LOGIN', 'USER_LOGOUT', 'USER_PASSWORD', 'PASSWORD_')),
    ('usuarios', 'Usuários', ('USER_',)),
    ('chamados', 'Chamados', ('TICKET_', 'CATEGORY_', 'APPROVER_')),
    ('comunicados', 'Comunicados', ('COMMUNICATION_',)),
    ('premios', 'Prêmios e C$', ('CS_', 'PRIZE_', 'REDEMPTION_')),
    ('setores', 'Setores e grupos', ('SECTOR_', 'GROUP_')),
    ('ativos', 'Ativos', ('ASSET_',)),
    ('integracoes', 'Integrações', ('WEBHOOK_',)),
    ('denuncias', 'Denúncias', ('REPORT_',)),
    ('administracao', 'Administração', ('ADMIN_', 'HISTORY_')),
]
OUTROS = ('outros', 'Outros')

# Código da ação → (ícone, cor). Primeira palavra que aparecer no código decide.
_VISUAL = [
    (('PURGE',), 'fa-broom', 'vermelho'),
    (('LOGIN',), 'fa-right-to-bracket', 'verde'),
    (('LOGOUT',), 'fa-right-from-bracket', 'cinza'),
    (('PASSWORD',), 'fa-key', 'roxo'),
    (('ERROR',), 'fa-triangle-exclamation', 'vermelho'),
    (('DELETE', 'REMOVE', 'REJECT', 'CANCELLED'), 'fa-trash-can', 'vermelho'),
    (('EXPORT',), 'fa-file-export', 'ciano'),
    (('IMPORT',), 'fa-file-import', 'ciano'),
    (('WEBHOOK',), 'fa-plug', 'ciano'),
    (('COMMENT',), 'fa-comment', 'azul'),
    (('ATTACHMENT',), 'fa-paperclip', 'azul'),
    (('ASSUME',), 'fa-hand', 'azul'),
    (('REDEEM', 'REDEMPTION'), 'fa-gift', 'rosa'),
    (('APPROVE',), 'fa-circle-check', 'verde'),
    (('SEND',), 'fa-paper-plane', 'azul'),
    (('CREATE', 'ADD'), 'fa-plus', 'azul'),
    (('UPDATE', 'EDIT', 'CHANGE', 'TOGGLE'), 'fa-pen', 'ambar'),
]


def modulo_de(codigo):
    for chave, rotulo, prefixos in MODULOS:
        if codigo.startswith(prefixos):
            return chave, rotulo
    return OUTROS


def dados_da_acao(codigo):
    """Como a tela apresenta um código de ação, mesmo um que ainda não catalogamos."""
    codigo = codigo or ''
    icone, cor = 'fa-circle-dot', 'cinza'
    for palavras, ic, c in _VISUAL:
        if any(p in codigo for p in palavras):
            icone, cor = ic, c
            break
    chave_modulo, rotulo_modulo = modulo_de(codigo)
    return {
        'codigo': codigo,
        'rotulo': ROTULOS.get(codigo) or codigo.replace('_', ' ').capitalize(),
        'icone': icone,
        'cor': cor,
        'modulo': chave_modulo,
        'modulo_rotulo': rotulo_modulo,
    }


def filtro_de_modulo(chave):
    """Q que devolve as ações de um módulo: a mesma regra de `modulo_de`, no banco."""
    anteriores = []
    for atual, _rotulo, prefixos in MODULOS:
        if atual == chave:
            q = Q()
            for p in prefixos:
                q |= Q(action_type__startswith=p)
            for p in anteriores:
                q &= ~Q(action_type__startswith=p)
            return q
        anteriores.extend(prefixos)
    if chave == OUTROS[0]:
        q = Q()
        for p in anteriores:
            q &= ~Q(action_type__startswith=p)
        return q
    return None


def acoes_por_modulo():
    """Para o seletor de ação, agrupado: [(rótulo do módulo, [(código, rótulo)])]."""
    grupos = {}
    for codigo, rotulo in ROTULOS.items():
        grupos.setdefault(modulo_de(codigo)[1], []).append((codigo, rotulo))
    return [(rotulo, sorted(grupos[rotulo], key=lambda x: x[1]))
            for _c, rotulo, _p in MODULOS if rotulo in grupos]


# ─── Filtros ──────────────────────────────────────────────────────────────────

def _dia(texto):
    try:
        return datetime.strptime((texto or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        return None


def _inicio_do_dia(dia):
    return timezone.make_aware(datetime.combine(dia, time.min))


def filtrar(qs, parametros):
    """Aplica os filtros da tela (a mesma regra na lista e no CSV)."""
    acao = (parametros.get('action_type') or '').strip()
    modulo = (parametros.get('modulo') or '').strip()
    pessoa = (parametros.get('user') or '').strip()
    de = _dia(parametros.get('date_from'))
    ate = _dia(parametros.get('date_to'))
    busca = (parametros.get('q') or '').strip()

    if acao:
        qs = qs.filter(action_type=acao)
    q_modulo = filtro_de_modulo(modulo) if modulo else None
    if q_modulo is not None:
        qs = qs.filter(q_modulo)
    else:
        modulo = ''
    if pessoa.isdigit():
        qs = qs.filter(user_id=int(pessoa))
    else:
        pessoa = ''
    if de:
        qs = qs.filter(created_at__gte=_inicio_do_dia(de))
    if ate:
        qs = qs.filter(created_at__lt=_inicio_do_dia(ate + timedelta(days=1)))
    if busca:
        qs = qs.filter(
            Q(description__icontains=busca)
            | Q(user__first_name__icontains=busca)
            | Q(user__last_name__icontains=busca)
            | Q(user__username__icontains=busca))

    return qs, {
        'action_type': acao, 'modulo': modulo, 'user': pessoa,
        'date_from': de.isoformat() if de else '',
        'date_to': ate.isoformat() if ate else '',
        'q': busca,
    }


# ─── Antes → depois ───────────────────────────────────────────────────────────

_CODIGO = re.compile(r'^[A-Z][A-Z_]+$')

_NOMES_CAMPO = {
    'title': 'Título', 'description': 'Descrição', 'status': 'Status',
    'priority': 'Prioridade', 'sector': 'Setor', 'category': 'Categoria',
    'assigned_to': 'Responsável', 'due_date': 'Prazo',
}

_RE_STATUS = re.compile(r'^(Status do chamado #\d+) alterado: (.+?) → (.+)$', re.S)
_RE_RESGATE = re.compile(r'^(Status do resgate #\d+) alterado de (.+?) para (.+)$', re.S)
_RE_PRIORIDADE = re.compile(r'^(Prioridade do chamado #\d+) alterada para (.+)$', re.S)
_RE_SETOR = re.compile(r'^Setor editado: (.+?) → (.+)$', re.S)
_RE_APROVADOR = re.compile(r'^(Aprovador atualizado: .+?) - Ordem (.+?)→(.+?) - R\$ (.+?)→(.+)$', re.S)
_RE_EDICAO = re.compile(r'^(Chamado #\d+ editado): (.+)$', re.S)
_RE_ITEM_EDICAO = re.compile(r'(?:^|; )(\w+): ')


def _legivel(valor):
    """`EM_ANDAMENTO` vira `Em andamento`; texto comum fica como foi gravado."""
    valor = (valor or '').strip()
    if _CODIGO.match(valor):
        return valor.replace('_', ' ').capitalize()
    return valor


def _mudanca(campo, antes, depois):
    return {'campo': campo, 'antes': _legivel(antes) if antes is not None else None,
            'depois': _legivel(depois)}


def diferencas_do_texto(descricao):
    """Antes/depois escritos no texto do log. Devolve (título, mudanças).

    Reconhece só os formatos que o portal grava hoje, conferidos no banco (status e
    edição de chamado, resgate, setor, aprovador). `antes` é `None` quando o log só
    escreveu o valor novo (prioridade do chamado). Texto que não casa devolve lista
    vazia: nunca adivinhamos um "antes".
    """
    texto = descricao or ''

    m = _RE_STATUS.match(texto) or _RE_RESGATE.match(texto)
    if m:
        return m.group(1), [_mudanca('Status', m.group(2), m.group(3))]

    m = _RE_PRIORIDADE.match(texto)
    if m:
        return m.group(1), [_mudanca('Prioridade', None, m.group(2))]

    m = _RE_SETOR.match(texto)
    if m:
        return 'Setor editado', [_mudanca('Nome', m.group(1), m.group(2))]

    m = _RE_APROVADOR.match(texto)
    if m:
        return m.group(1), [_mudanca('Ordem', m.group(2), m.group(3)),
                            _mudanca('Valor máximo (R$)', m.group(4), m.group(5))]

    m = _RE_EDICAO.match(texto)
    if m:
        corpo = m.group(2)
        achados = list(_RE_ITEM_EDICAO.finditer(corpo))
        mudancas = []
        for i, item in enumerate(achados):
            fim = achados[i + 1].start() if i + 1 < len(achados) else len(corpo)
            antes, seta, depois = corpo[item.end():fim].partition(' → ')
            if seta:
                campo = item.group(1)
                mudancas.append(_mudanca(_NOMES_CAMPO.get(campo, campo), antes, depois))
        if mudancas:
            return m.group(1), mudancas

    return texto, []


# Entre a gravação do UserChangeLog e a do SystemLog da mesma edição passam
# milissegundos; a folga cobre um salvamento lento sem misturar duas edições.
_FOLGA_CADASTRO = timedelta(seconds=15)
_RE_EMAIL_EDITADO = re.compile(r'\(([^()\s]+@[^()\s]+)\)')


def diferencas_de_cadastro(logs):
    """Liga os `USER_UPDATE` da página às linhas de `UserChangeLog` (uma consulta).

    Não existe chave entre as duas tabelas: a ligação é autor + e-mail do colaborador
    escrito no texto + horário próximo. Devolve {id do log: [mudanças]}.
    """
    from users.models import UserChangeLog

    candidatos = [log for log in logs
                  if log.action_type == 'USER_UPDATE' and log.user_id
                  and _RE_EMAIL_EDITADO.search(log.description or '')]
    if not candidatos:
        return {}

    momentos = [log.created_at for log in candidatos]
    linhas = (UserChangeLog.objects
              .filter(changed_by_id__in={log.user_id for log in candidatos},
                      created_at__gte=min(momentos) - _FOLGA_CADASTRO,
                      created_at__lte=max(momentos) + _FOLGA_CADASTRO)
              .select_related('target')
              .order_by('created_at', 'field_label'))

    por_autor = {}
    for linha in linhas:
        por_autor.setdefault(linha.changed_by_id, []).append(linha)

    saida = {}
    usados = set()
    for log in candidatos:
        email = _RE_EMAIL_EDITADO.search(log.description).group(1).lower()
        for linha in por_autor.get(log.user_id, []):
            if linha.id in usados or (linha.target.email or '').lower() != email:
                continue
            if abs(linha.created_at - log.created_at) > _FOLGA_CADASTRO:
                continue
            usados.add(linha.id)
            saida.setdefault(log.id, []).append(
                {'campo': linha.field_label, 'antes': linha.old_value, 'depois': linha.new_value})
    return saida


# ─── Linha do tempo ───────────────────────────────────────────────────────────

# A partir daqui, ações iguais e seguidas da mesma pessoa viram um bloco recolhido.
MINIMO_PARA_AGRUPAR = 3


def _rotulo_do_dia(dia, hoje):
    if dia == hoje:
        return 'Hoje'
    if dia == hoje - timedelta(days=1):
        return 'Ontem'
    semana = ['segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado', 'domingo']
    return f'{semana[dia.weekday()]}, {dia:%d/%m/%Y}'


def montar_linha_do_tempo(logs, hoje=None):
    """Agrupa por dia e junta repetição seguida. `logs` já vem do mais novo ao mais antigo.

    Registro com mudança ou posição nunca fica recolhido: é o que se veio procurar.
    """
    hoje = hoje or timezone.localdate()
    dias = []
    for log in logs:
        dia = timezone.localtime(log.created_at).date()
        if not dias or dias[-1]['data'] != dia:
            dias.append({'data': dia, 'rotulo': _rotulo_do_dia(dia, hoje), 'total': 0, 'blocos': []})
        grupo = dias[-1]
        grupo['total'] += 1

        anterior = grupo['blocos'][-1] if grupo['blocos'] else None
        simples = not log.mudancas and not log.posicao
        if (anterior and simples and anterior['simples']
                and anterior['logs'][-1].user_id == log.user_id
                and anterior['logs'][-1].action_type == log.action_type):
            anterior['logs'].append(log)
        else:
            grupo['blocos'].append({'simples': simples, 'logs': [log]})

    for grupo in dias:
        for bloco in grupo['blocos']:
            bloco['agrupado'] = len(bloco['logs']) >= MINIMO_PARA_AGRUPAR
            bloco['primeiro'] = bloco['logs'][0]
            bloco['quantos'] = len(bloco['logs'])
            bloco['hora_fim'] = bloco['logs'][0].created_at
            bloco['hora_inicio'] = bloco['logs'][-1].created_at
    return dias


# ─── Limpeza ──────────────────────────────────────────────────────────────────

def corte_da_limpeza(hoje=None):
    """Meia-noite de um ano atrás: só sai o que já passou de 1 ano inteiro."""
    hoje = hoje or timezone.localdate()
    try:
        dia = hoje.replace(year=hoje.year - 1)
    except ValueError:              # 29 de fevereiro
        dia = date(hoje.year - 1, hoje.month, 28)
    return _inicio_do_dia(dia)


def registros_a_limpar(corte):
    """O que a limpeza alcança. O registro das limpezas anteriores nunca entra."""
    from .models import SystemLog
    return SystemLog.objects.filter(created_at__lt=corte).exclude(action_type=ACAO_LIMPEZA)


def previa_da_limpeza(corte=None):
    corte = corte or corte_da_limpeza()
    alvo = registros_a_limpar(corte)
    return {
        'corte': corte,
        'total': alvo.count(),
        'mais_antigo': alvo.order_by('created_at', 'id').values_list('created_at', flat=True).first(),
    }


def _texto_limpeza(limpeza):
    situacao = {
        limpeza.Situacao.ANDAMENTO: 'em andamento',
        limpeza.Situacao.CONCLUIDA: 'concluída',
        limpeza.Situacao.INTERROMPIDA: 'interrompida',
    }[limpeza.situacao]
    corte = timezone.localtime(limpeza.corte)
    inicio = (f'de {timezone.localtime(limpeza.mais_antigo):%d/%m/%Y} ' if limpeza.mais_antigo else '')
    return (f'Limpeza do histórico {situacao}: {limpeza.removidos} de {limpeza.previsto} '
            f'registro(s) removido(s), período {inicio}até antes de {corte:%d/%m/%Y}.')


def _sincronizar_registro(limpeza):
    """Mantém o texto da linha do tempo igual ao que aconteceu até aqui."""
    from .models import SystemLog
    if limpeza.id_registro:
        SystemLog.objects.filter(id=limpeza.id_registro).update(description=_texto_limpeza(limpeza))


def encerrar_interrompidas(usuario, agora=None):
    """Fecha limpezas sem notícia. Devolve a de outra pessoa ainda ativa, se houver."""
    from .models import LimpezaHistorico

    agora = agora or timezone.now()
    limite = agora - timedelta(minutes=LIMPEZA_PARADA_MINUTOS)
    ativa = None
    for limpeza in LimpezaHistorico.objects.filter(situacao=LimpezaHistorico.Situacao.ANDAMENTO):
        if limpeza.atualizado_em >= limite and limpeza.executado_por_id != usuario.id:
            ativa = limpeza
            continue
        limpeza.situacao = LimpezaHistorico.Situacao.INTERROMPIDA
        limpeza.concluido_em = agora
        limpeza.save(update_fields=['situacao', 'concluido_em', 'atualizado_em'])
        _sincronizar_registro(limpeza)
    return ativa


def iniciar_limpeza(usuario, ip=None, user_agent=''):
    """Abre a limpeza e já deixa o rastro, antes de apagar a primeira linha."""
    from .models import LimpezaHistorico, SystemLog

    previa = previa_da_limpeza()
    limpeza = LimpezaHistorico(
        executado_por=usuario,
        executado_por_nome=usuario.get_full_name() or usuario.username,
        corte=previa['corte'], mais_antigo=previa['mais_antigo'], previsto=previa['total'],
    )
    registro = SystemLog.objects.create(
        user=usuario, action_type=ACAO_LIMPEZA, description=_texto_limpeza(limpeza),
        ip_address=ip, user_agent=user_agent or '')
    limpeza.id_registro = registro.id
    limpeza.save()
    return limpeza


def apagar_um_lote(limpeza, tamanho=LOTE_LIMPEZA):
    """Apaga um lote, em transação própria, e devolve quantas linhas saíram.

    O corte gravado é conferido de novo: nem um registro adulterado faz sair algo com
    menos de 1 ano. Apagar pelos ids evita um DELETE sem limite na tabela toda, e a
    ordem pelo índice de `created_at` encontra os mais antigos sem varrer o resto.
    """
    from .models import LimpezaHistorico, SystemLog

    if limpeza.corte > corte_da_limpeza():
        raise ValueError('Corte da limpeza mais novo que 1 ano.')

    ids = list(registros_a_limpar(limpeza.corte)
               .order_by('created_at', 'id')
               .values_list('id', flat=True)[:tamanho])
    removidos = 0
    if ids:
        removidos, _detalhe = SystemLog.objects.filter(id__in=ids).delete()

    LimpezaHistorico.objects.filter(pk=limpeza.pk).update(
        removidos=F('removidos') + removidos, atualizado_em=timezone.now())
    limpeza.refresh_from_db()

    if len(ids) < tamanho:
        limpeza.situacao = LimpezaHistorico.Situacao.CONCLUIDA
        limpeza.concluido_em = timezone.now()
        limpeza.save(update_fields=['situacao', 'concluido_em', 'atualizado_em'])
    _sincronizar_registro(limpeza)
    return removidos
