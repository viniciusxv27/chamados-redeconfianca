"""Aviso no WhatsApp do que mudou de preço ao importar a tabela (/vendas/precos/importar/).

Pedido: "Ao atualizar tabela de preços, SUPERADMIN deve poder selecionar quem vai
receber um aviso no WhatsApp pelo WhatsApp configurado com produtos que subiram e
produtos que diminuíram valor."

O que define "subiu" / "diminuiu"
---------------------------------
Cada produto é comparado em duas colunas — as mesmas que o catálogo mostra no cartão:

- **Preço**: o "a partir de" — o menor *Preço* (cartão sem juros) entre os grupos de
  plano da Tabela Regular (SMARTPHONES / ELETRÔNICOS_LP_Conectados); nos
  Não Conectados é o *PVP Base*;
- **PIX**: o menor *PIX e Vivo Pay* entre os grupos (Não Conectados não têm).

A direção vem do **Preço**; se o Preço não mudou e só o PIX mudou, vem do PIX.
Cada coluna que mudou entra na linha do produto (de → para e %). Mudança só nas
parcelas ou num grupo que não é o mais barato não muda nenhuma das duas colunas:
conta como "outros ajustes", sem lista. Produto novo e produto que saiu da tabela
vêm em listas curtas à parte. Só as três abas importadas entram na comparação.

Envio
-----
Pelo WhatsApp que o portal já usa (``core.evolution``, o mesmo da rotina gerencial e
da análise de ponto), uma mensagem por pessoa, no telefone do cadastro. Depois que a
importação já gravou: falha no envio não desfaz nada — volta como resultado por pessoa.
"""
import logging
from decimal import ROUND_HALF_UP, Decimal

from django.db import DatabaseError, transaction

logger = logging.getLogger(__name__)

COND_BASE = 'Preço'
COND_PIX = 'PIX e Vivo Pay'
COLUNAS = ('Preço', 'PIX')            # rótulos curtos (mensagem e tela), na ordem de prioridade

TOP_MUDANCAS = 10                     # quantos que subiram / baixaram aparecem na mensagem
TOP_LISTAS = 5                        # quantos novos / removidos aparecem pelo nome
LIMITE_MENSAGEM = 3500                # bem abaixo do teto do WhatsApp e ainda legível no celular
MAX_NA_TELA = 200                     # quantos de cada lista vão para a tela de resultado


# ── Comparação ─────────────────────────────────────────────────────────────────

def _dec(v):
    if v in (None, ''):
        return None
    try:
        d = Decimal(str(v)).quantize(Decimal('0.01'))
    except Exception:  # noqa: BLE001
        return None
    return d if d > 0 else None


def precos_do_item(valor, extra):
    """{'Preço': Decimal|None, 'PIX': Decimal|None, 'tabela': dict|None} de um produto."""
    extra = extra if isinstance(extra, dict) else {}
    tabela = extra.get('tabela') or None
    if tabela:
        base = [d for d in (_dec(g.get(COND_BASE)) for g in tabela.values() if isinstance(g, dict)) if d]
        pix = [d for d in (_dec(g.get(COND_PIX)) for g in tabela.values() if isinstance(g, dict)) if d]
        return {'Preço': min(base) if base else _dec(valor), 'PIX': min(pix) if pix else None, 'tabela': tabela}
    return {'Preço': _dec(valor), 'PIX': None, 'tabela': None}


def _pct(de, para):
    return ((para - de) / de * 100).quantize(Decimal('0.1'), ROUND_HALF_UP)


def calcular_variacao(antes, depois):
    """Compara dois retratos {(categoria, nome): precos_do_item(...)}.

    Devolve {'subiram': [...], 'diminuiram': [...], 'novos': [...], 'removidos': [...],
    'outros': int}. Cada item de subiram/diminuiram: {'categoria', 'nome',
    'mudancas': [{'coluna', 'de', 'para', 'pct'}]} (Decimais), ordenados da maior
    alta / maior queda para a menor.
    """
    subiram, diminuiram, outros = [], [], 0
    for chave in antes.keys() & depois.keys():
        a, d = antes[chave], depois[chave]
        mudancas = []
        for col in COLUNAS:
            de, para = a.get(col), d.get(col)
            if de and para and de != para:
                mudancas.append({'coluna': col, 'de': de, 'para': para, 'pct': _pct(de, para)})
        if not mudancas:
            if (a.get('tabela') or None) != (d.get('tabela') or None):
                outros += 1
            continue
        item = {'categoria': chave[0], 'nome': chave[1], 'mudancas': mudancas}
        (subiram if mudancas[0]['para'] > mudancas[0]['de'] else diminuiram).append(item)
    subiram.sort(key=lambda i: (-i['mudancas'][0]['pct'], i['nome']))
    diminuiram.sort(key=lambda i: (i['mudancas'][0]['pct'], i['nome']))
    novos = sorted(({'categoria': c, 'nome': n, 'preco': depois[(c, n)].get('Preço')}
                    for c, n in depois.keys() - antes.keys()), key=lambda i: i['nome'])
    removidos = sorted(({'categoria': c, 'nome': n, 'preco': antes[(c, n)].get('Preço')}
                        for c, n in antes.keys() - depois.keys()), key=lambda i: i['nome'])
    return {'subiram': subiram, 'diminuiram': diminuiram, 'novos': novos, 'removidos': removidos, 'outros': outros}


# ── Texto ──────────────────────────────────────────────────────────────────────

def brl(valor):
    if valor is None:
        return '—'
    texto = f'{Decimal(valor):,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')
    return f'R$ {texto}'


def pct_texto(pct):
    sinal = '+' if pct > 0 else ''
    return f'{sinal}{pct}%'.replace('.', ',')


def _curto(nome, n=60):
    nome = ' '.join(str(nome).split())
    return nome if len(nome) <= n else nome[:n - 1] + '…'


def _linha_mudanca(item):
    partes = [f"{m['coluna']} {brl(m['de'])} → {brl(m['para'])} ({pct_texto(m['pct'])})" for m in item['mudancas']]
    return f"• {_curto(item['nome'])}: " + '; '.join(partes)


def _bloco_nomes(titulo, itens, top):
    if not itens:
        return []
    nomes = ', '.join(_curto(i['nome'], 40) for i in itens[:top])
    mais = f' (+{len(itens) - top} outros)' if len(itens) > top else ''
    return ['', f'*{titulo} ({len(itens)}):* {nomes}{mais}']


def montar_mensagem(variacao, *, arquivo='', quando=None, nome='', top=TOP_MUDANCAS, limite=LIMITE_MENSAGEM):
    """A mensagem do WhatsApp: contagens + as maiores altas e quedas, e quantos ficaram de fora.

    Se passar de ``limite`` caracteres, mostra menos produtos por lista até caber.
    """
    def montar(n, n_listas):
        linhas = []
        if nome:
            linhas.append(f'Olá, {nome}!')
        cab = '📊 *Tabela de preços atualizada*'
        if quando:
            cab += f" — {quando.strftime('%d/%m/%Y %H:%M')}"
        linhas.append(cab)
        if arquivo:
            linhas.append(f'Arquivo: {_curto(arquivo, 80)}')
        linhas += ['',
                   f"⬆️ Subiram: {len(variacao['subiram'])}",
                   f"⬇️ Diminuíram: {len(variacao['diminuiram'])}",
                   f"🆕 Novos: {len(variacao['novos'])}",
                   f"❌ Saíram da tabela: {len(variacao['removidos'])}"]
        if variacao.get('outros'):
            linhas.append(f"🔧 Outros ajustes (parcelas/grupos): {variacao['outros']}")
        for titulo, itens in (('⬆️ Maiores altas', variacao['subiram']), ('⬇️ Maiores quedas', variacao['diminuiram'])):
            if not itens:
                continue
            linhas += ['', f'*{titulo}:*'] + [_linha_mudanca(i) for i in itens[:n]]
            if len(itens) > n:
                linhas.append(f'… e mais {len(itens) - n}.')
        linhas += _bloco_nomes('🆕 Novos', variacao['novos'], n_listas)
        linhas += _bloco_nomes('❌ Saíram', variacao['removidos'], n_listas)
        if not any(variacao[k] for k in ('subiram', 'diminuiram', 'novos', 'removidos')):
            linhas += ['', 'Nenhum preço mudou.']
        linhas += ['', 'Preço = "a partir de" (menor preço entre os grupos); PIX = menor PIX e Vivo Pay.',
                   'Detalhes no portal: Vendas › Tabela de preços.']
        return '\n'.join(linhas)

    n, n_listas = top, TOP_LISTAS
    texto = montar(n, n_listas)
    while len(texto) > limite and (n > 1 or n_listas > 1):
        n, n_listas = max(1, n - 2), max(1, n_listas - 1)
        texto = montar(n, n_listas)
    if len(texto) > limite:                  # nomes enormes: corta no limite, sem quebrar o WhatsApp
        texto = texto[:limite - 1] + '…'
    return texto


# ── Tela ───────────────────────────────────────────────────────────────────────

def para_tela(variacao):
    """A variação em texto (vai para a sessão e para o template)."""
    def mud(i):
        return {'categoria': i['categoria'], 'nome': i['nome'],
                'mudancas': [{'coluna': m['coluna'], 'de': brl(m['de']), 'para': brl(m['para']),
                              'pct': pct_texto(m['pct'])} for m in i['mudancas']]}

    def simples(i):
        return {'categoria': i['categoria'], 'nome': i['nome'], 'preco': brl(i['preco'])}

    return {
        'contagens': {k: len(variacao[k]) for k in ('subiram', 'diminuiram', 'novos', 'removidos')},
        'outros': variacao.get('outros', 0),
        'subiram': [mud(i) for i in variacao['subiram'][:MAX_NA_TELA]],
        'diminuiram': [mud(i) for i in variacao['diminuiram'][:MAX_NA_TELA]],
        'novos': [simples(i) for i in variacao['novos'][:MAX_NA_TELA]],
        'removidos': [simples(i) for i in variacao['removidos'][:MAX_NA_TELA]],
    }


# ── Destinatários ──────────────────────────────────────────────────────────────

def numero_do(user):
    from core.evolution import normalizar_numero
    return normalizar_numero(getattr(user, 'phone', '') or '')


def destinatarios_salvos():
    """IDs da última seleção. Banco ainda sem a migration 0010: nenhum (a tela não quebra)."""
    from .models import ConfiguracaoVendas
    try:
        with transaction.atomic():
            return set(ConfiguracaoVendas.atual().aviso_precos_destinatarios.values_list('id', flat=True))
    except DatabaseError:
        logger.warning('Aviso de preços: tabela dos destinatários ainda não existe (falta migrate).')
        return set()


def salvar_destinatarios(usuarios, por=None):
    """Grava a seleção como padrão da próxima vez. Devolve False se não deu (falta migrate)."""
    from .models import ConfiguracaoVendas
    try:
        with transaction.atomic():
            config = ConfiguracaoVendas.atual()
            config.aviso_precos_destinatarios.set(usuarios)
            if por is not None:
                ConfiguracaoVendas.objects.filter(pk=config.pk).update(atualizado_por=por)
        return True
    except DatabaseError:
        logger.warning('Aviso de preços: não gravei a seleção (falta migrate?).', exc_info=True)
        return False


def avisar(usuarios, variacao, *, arquivo='', quando=None):
    """Uma mensagem por pessoa. Nunca levanta: devolve o resultado de cada uma.

    [{'id', 'nome', 'status': 'enviado'|'falhou'|'sem_telefone', 'detalhe'}]
    """
    # Pelo módulo: o dublê dos testes troca `core.evolution.enviar_texto`.
    from core import evolution
    from core.telefone import problema

    resultado = []
    for user in usuarios:
        nome = (user.get_full_name() or user.username).strip()
        linha = {'id': user.pk, 'nome': nome, 'status': 'falhou', 'detalhe': ''}
        try:
            numero = numero_do(user)
            if not numero:
                linha.update(status='sem_telefone', detalhe=problema(getattr(user, 'phone', '') or '')
                             or 'telefone do cadastro inválido')
            else:
                texto = montar_mensagem(variacao, arquivo=arquivo, quando=quando,
                                        nome=(user.first_name or nome).split(' ')[0])
                ok, detalhe = evolution.enviar_texto(numero, texto, timeout=15)
                linha.update(status='enviado' if ok else 'falhou', detalhe='' if ok else str(detalhe or '')[:200])
        except Exception as exc:  # noqa: BLE001 — o aviso nunca derruba a importação
            logger.exception('Aviso de preços: falha ao enviar para %s', user.pk)
            linha.update(status='falhou', detalhe=f'Erro inesperado: {exc}'[:200])
        resultado.append(linha)
    return resultado
