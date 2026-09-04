"""Filtro por nome e setor, igual em todas as telas do Impulso.

Cada tela lista uma coisa diferente (metas, feedbacks, conteúdos, ranking),
mas a pergunta de quem abre é sempre a mesma: "cadê o fulano?" ou "como está a
loja tal?". Antes disso aqui, cada tela respondia de um jeito — o Kanban tinha
um select de colaborador, o Feedback tinha outro, e o resto não tinha nada.

Um único lugar decide o que a busca aceita, para a mesma frase digitada achar
a mesma pessoa em qualquer aba.
"""
from calendar import monthrange
from datetime import date

from django.db.models import Q
from django.utils import timezone

PARAM_NOME = 'q'
PARAM_SETOR = 'setor'
PARAM_MES = 'mes'

# Quantos meses o seletor oferece para trás. Um ano cobre o ciclo inteiro sem
# virar uma lista que ninguém rola até o fim.
MESES_PARA_TRAS = 11


def periodo_do_mes(texto):
    """('YYYY-MM') -> (primeiro_dia, ultimo_dia). Texto inválido devolve None."""
    try:
        ano, mes = (int(x) for x in str(texto).split('-')[:2])
        if not (2000 <= ano <= 2100 and 1 <= mes <= 12):
            return None
    except (TypeError, ValueError):
        return None
    return date(ano, mes, 1), date(ano, mes, monthrange(ano, mes)[1])


def ler(request):
    """O que a pessoa pediu na URL.

    {'nome', 'setor', 'mes' ('YYYY-MM'), 'inicio', 'fim', 'ativo'}
    """
    nome = (request.GET.get(PARAM_NOME) or '').strip()
    setor = (request.GET.get(PARAM_SETOR) or '').strip()
    try:
        setor_id = int(setor) if setor else None
    except (TypeError, ValueError):
        setor_id = None

    mes = (request.GET.get(PARAM_MES) or '').strip()
    periodo = periodo_do_mes(mes) if mes else None
    if periodo is None:
        mes = ''

    return {
        'nome': nome[:80], 'setor': setor_id,
        'mes': mes,
        'inicio': periodo[0] if periodo else None,
        'fim': periodo[1] if periodo else None,
        'ativo': bool(nome or setor_id or mes),
    }


def _q_nome(nome):
    """Cada palavra digitada precisa bater em algum campo da pessoa.

    Buscar "ana lima" com um OR simples traria toda Ana e todo Lima; exigindo
    que cada termo apareça em algum lugar, o nome composto funciona sem obrigar
    a digitar exatamente como está cadastrado.
    """
    filtro = Q()
    for termo in nome.split():
        filtro &= (Q(first_name__icontains=termo)
                   | Q(last_name__icontains=termo)
                   | Q(username__icontains=termo)
                   | Q(email__icontains=termo))
    return filtro


def _q_setor(setor_id):
    """Principal ou vinculado — a mesma régua de `setores_do_usuario`."""
    return Q(sector_id=setor_id) | Q(sectors__id=setor_id)


def pessoas(qs, f):
    """Filtra um queryset de User."""
    if f['nome']:
        qs = qs.filter(_q_nome(f['nome']))
    if f['setor']:
        qs = qs.filter(_q_setor(f['setor']))
    return qs.distinct() if f['ativo'] else qs


def por(qs, f, campo):
    """Filtra qualquer queryset pelo caminho até a pessoa (ex.: 'colaborador').

    `campo` pode ser um caminho ('projeto__responsavel') ou uma lista de
    caminhos, quando a tela aceita achar por mais de um papel.
    """
    if not f['ativo']:
        return qs
    campos = [campo] if isinstance(campo, str) else list(campo)

    if f['nome']:
        junto = Q()
        for c in campos:
            junto |= _prefixar(_q_nome(f['nome']), c)
        qs = qs.filter(junto)
    if f['setor']:
        junto = Q()
        for c in campos:
            junto |= _prefixar(_q_setor(f['setor']), c)
        qs = qs.filter(junto)
    return qs.distinct()


def por_mes(qs, f, campo):
    """Corta o queryset pelo mês escolhido, num campo de data.

    `campo` aceita DateField e DateTimeField — para o segundo, use o sufixo
    `__date` no caminho (ex.: 'criado_em__date').
    """
    if not f.get('inicio'):
        return qs
    return qs.filter(**{f'{campo}__gte': f['inicio'], f'{campo}__lte': f['fim']})


def por_periodo(qs, f, campo_inicio, campo_fim):
    """Para o que tem vigência (início/fim): mantém o que cruza o mês.

    Um conteúdo sem período vale sempre — some-lo do mês filtrado esconderia
    justamente o material permanente, que é a maior parte do Conectar.
    """
    if not f.get('inicio'):
        return qs
    return qs.filter(
        (Q(**{f'{campo_inicio}__isnull': True})
         | Q(**{f'{campo_inicio}__lte': f['fim']}))
        & (Q(**{f'{campo_fim}__isnull': True})
           | Q(**{f'{campo_fim}__gte': f['inicio']}))
    )


def meses_disponiveis(hoje=None):
    """(valor, rótulo) do mês corrente para trás, para o seletor."""
    NOMES = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho',
             'agosto', 'setembro', 'outubro', 'novembro', 'dezembro']
    hoje = hoje or timezone.localdate()
    ano, mes = hoje.year, hoje.month
    saida = []
    for _ in range(MESES_PARA_TRAS + 1):
        saida.append((f'{ano:04d}-{mes:02d}', f'{NOMES[mes - 1].capitalize()} de {ano}'))
        mes -= 1
        if mes == 0:
            mes, ano = 12, ano - 1
    return saida


def _prefixar(filtro, caminho):
    """Reescreve um Q de User para valer a partir de `caminho`."""
    novo = Q()
    novo.connector = filtro.connector
    novo.negated = filtro.negated
    novo.children = [
        _prefixar(c, caminho) if isinstance(c, Q) else (f'{caminho}__{c[0]}', c[1])
        for c in filtro.children
    ]
    return novo


def _texto_da_pessoa(pessoa):
    return ' '.join(str(x or '') for x in (
        pessoa.first_name, pessoa.last_name, pessoa.username, pessoa.email))


def _setores_da_pessoa(pessoa):
    ids = set()
    if getattr(pessoa, 'sector_id', None):
        ids.add(pessoa.sector_id)
    try:
        ids.update(pessoa.sectors.values_list('id', flat=True))
    except Exception:
        pass
    return ids


def combina(pessoa, f):
    """A mesma regra do banco, para as telas que montam a lista em memória."""
    if not f['ativo'] or pessoa is None:
        return True
    if f['nome']:
        texto = _texto_da_pessoa(pessoa).lower()
        if not all(t.lower() in texto for t in f['nome'].split()):
            return False
    if f['setor'] and f['setor'] not in _setores_da_pessoa(pessoa):
        return False
    return True


def lista(itens, f, pegar):
    """Filtra uma lista já montada. `pegar` extrai a pessoa de cada item."""
    if not f['ativo']:
        return itens
    return [i for i in itens if combina(pegar(i), f)]


def setores_disponiveis():
    """Setores que têm gente do Impulso — não o cadastro inteiro.

    Oferecer os 40 setores da rede num módulo que atende o Escritório faria a
    pessoa rolar uma lista de opções que devolvem vazio.
    """
    from users.models import Sector

    from .utils import get_colaboradores

    ids = set(get_colaboradores().values_list('sector_id', flat=True))
    ids.update(get_colaboradores().values_list('sectors__id', flat=True))
    ids.discard(None)
    return Sector.objects.filter(id__in=ids).order_by('name')


def contexto(request, f=None):
    """O que o `_filtros.html` precisa, pronto para o context da view."""
    f = f or ler(request)
    guardar = {k: v for k, v in request.GET.items()
               if k not in (PARAM_NOME, PARAM_SETOR, PARAM_MES) and str(v).strip()}
    return {
        'filtro': f,
        'filtro_nome': f['nome'],
        'filtro_setor': f['setor'],
        'filtro_mes': f['mes'],
        'filtro_setores': setores_disponiveis(),
        'filtro_meses': meses_disponiveis(),
        'filtro_outros': guardar,
    }


def contexto_mes(request, f=None):
    """Só o seletor de mês — para telas onde filtrar por nome não pode.

    O Inovar é o caso: a autoria fica escondida do gestor de propósito, e um
    campo de busca por nome desmontaria isso (bastava digitar um nome e ver o
    que sobra na tela para descobrir quem escreveu).
    """
    f = f or ler(request)
    base = contexto(request, f)
    base['filtro_setores'] = None
    return base
