"""Busca por vaga em linguagem de gente.

O RH digita "vendedor para loja de viana", não "cargo=vendedor E cidade=Viana".
Aqui a frase é separada em duas intenções — **função** e **lugar** — e cada
currículo recebe uma nota pelas duas.

Por que nota e não filtro: filtro devolve vazio quando ninguém casa exatamente,
e vaga não espera. Nota devolve os mais próximos primeiro e diz por quê, que é
o que deixa o RH decidir.

Quem foi contratado ou descartado fica de fora — era o pedido, e é o que evita
o RH chamar alguém que já está na rede.
"""
import re

from .texto import VAZIAS, normalizar, palavras

# Preposições que ligam a marca de lugar ao nome dele.
LIGACOES = {'de', 'da', 'do', 'das', 'dos', 'em', 'no', 'na', 'para', 'pra'}

# ── Vocabulário ─────────────────────────────────────────────────────────────

# Sinônimos de função: o RH pede "vendedor" e o currículo diz "consultor de
# vendas". Sem isso a busca mais comum da rede não acha ninguém.
SINONIMOS_CARGO = {
    'vendedor': ['vendedor', 'vendedora', 'consultor de vendas',
                 'consultora de vendas', 'consultor', 'consultora',
                 'atendimento ao cliente', 'promotor', 'promotora', 'vendas'],
    'consultor': ['consultor', 'consultora', 'consultor de vendas', 'vendedor',
                  'vendedora'],
    'caixa': ['caixa', 'operador de caixa', 'operadora de caixa', 'tesouraria'],
    'gerente': ['gerente', 'gerente de loja', 'subgerente', 'supervisor',
                'supervisora', 'coordenador', 'coordenadora'],
    'supervisor': ['supervisor', 'supervisora', 'coordenador', 'coordenadora',
                   'gerente'],
    'estoquista': ['estoquista', 'estoque', 'repositor', 'almoxarife'],
    'atendente': ['atendente', 'atendimento', 'recepcionista', 'sac',
                  'telemarketing', 'atendimento ao cliente'],
    'administrativo': ['auxiliar administrativo', 'assistente administrativo',
                       'administrativo', 'assistente', 'auxiliar'],
    'aprendiz': ['jovem aprendiz', 'aprendiz', 'estagiario', 'estagiaria',
                 'estagio'],
}

# Palavras que dizem "isto é um lugar", não uma função.
MARCAS_DE_LUGAR = ('loja', 'unidade', 'filial', 'pdv', 'shopping', 'bairro',
                   'cidade', 'regiao', 'zona')


def _sinonimos(termo):
    saida = {termo}
    for chave, lista in SINONIMOS_CARGO.items():
        if termo == chave or termo in lista:
            saida.update(lista)
            saida.add(chave)
    return saida


def separar_intencao(consulta, lugares_conhecidos=()):
    """Divide a frase em (termos de função, termos de lugar).

    O que casa com um lugar conhecido do portal (setor/loja/cidade) vira lugar;
    o resto vira função. Assim "viana" é lugar porque existe Loja Viana, e
    "vendedor" é função porque não existe lugar com esse nome.
    """
    normalizada = normalizar(consulta)
    conhecidos = {normalizar(l) for l in lugares_conhecidos if l}
    conhecidos.discard('')

    # Lugar de duas palavras ("norte sul", "vila velha") precisa ser achado
    # antes de a frase ser quebrada em palavras soltas.
    lugares, restante = [], normalizada
    for lugar in sorted(conhecidos, key=len, reverse=True):
        if len(lugar.split()) > 1 and lugar in restante:
            lugares.append(lugar)
            restante = restante.replace(lugar, ' ')

    # A varredura é sobre TODAS as palavras, não só as "úteis": "loja" está na
    # lista de palavras vazias (aparece em todo currículo e não ajuda a achar
    # ninguém), mas é justamente ela que marca o que vem a seguir como lugar.
    # Filtrar antes fazia "loja de guarapari" perder o lugar.
    depois_de_marca = False
    funcoes = []
    for palavra in palavras(restante):
        if palavra in conhecidos:
            lugares.append(palavra)
            depois_de_marca = False
            continue
        if palavra in MARCAS_DE_LUGAR:
            depois_de_marca = True
            continue
        # "de", "da", "do" entre a marca e o nome não quebram a sequência:
        # "loja de viana" e "loja da serra" são a mesma frase.
        if depois_de_marca and palavra in LIGACOES:
            continue
        if depois_de_marca:
            lugares.append(palavra)
            depois_de_marca = False
            continue
        if palavra in VAZIAS or len(palavra) <= 2:
            continue
        funcoes.append(palavra)

    return funcoes, lugares


def _ocorrencias(texto, termo):
    return len(re.findall(rf'\b{re.escape(termo)}', texto)) if termo else 0


def pontuar(curriculo_busca, cargos_normalizados, endereco_normalizado,
            funcoes, lugares):
    """(nota, motivos) de um currículo para a consulta.

    Os pesos dizem o que importa: o cargo escrito no currículo vale mais do que
    a mesma palavra perdida no meio do texto, e lugar bate no endereço, não no
    currículo inteiro — senão "trabalhei em Viana" viraria "mora em Viana".
    """
    nota = 0
    motivos = []

    for funcao in funcoes:
        variantes = _sinonimos(funcao)
        no_cargo = any(v in cargos_normalizados for v in variantes)
        no_texto = sum(_ocorrencias(curriculo_busca, v) for v in variantes)

        if no_cargo:
            nota += 50
            motivos.append(f'já foi {funcao}')
        elif no_texto:
            nota += min(25, 8 * no_texto)
            motivos.append(f'{funcao} aparece na experiência')

    for lugar in lugares:
        if lugar in endereco_normalizado:
            nota += 30
            motivos.append(f'mora em {lugar}')
        elif lugar in curriculo_busca:
            nota += 8
            motivos.append(f'{lugar} citado no currículo')

    return nota, motivos


def procurar(consulta, base=None, lugares_conhecidos=(), limite=50):
    """Ordena os currículos disponíveis pela aderência à vaga.

    Devolve lista de dicts com o currículo, a nota e os motivos. Consulta vazia
    devolve o banco inteiro, do mais novo para o mais antigo — é a tela de
    listagem, não um erro.
    """
    from .models import Curriculo

    if base is None:
        base = Curriculo.objects.all()
    disponiveis = base.filter(
        situacao__in=[Curriculo.Situacao.NOVO, Curriculo.Situacao.ENTREVISTA])

    funcoes, lugares = separar_intencao(consulta, lugares_conhecidos)
    if not funcoes and not lugares:
        return [{'curriculo': c, 'nota': 0, 'motivos': []}
                for c in disponiveis.order_by('-enviado_em')[:limite]]

    # Traz só quem tem alguma chance: pelo menos um termo aparece em algum
    # lugar do currículo. Sem isso, uma base de milhares seria pontuada inteira.
    from django.db.models import Q
    filtro = Q()
    for termo in set(funcoes) | set(lugares):
        for variante in _sinonimos(termo):
            filtro |= Q(busca__contains=variante)
    candidatos = disponiveis.filter(filtro).distinct()[:600]

    resultados = []
    for c in candidatos:
        cargos = normalizar(c.cargos)
        endereco = normalizar(' '.join([c.endereco or '', c.cidade or '',
                                        c.bairro or '']))
        nota, motivos = pontuar(c.busca or '', cargos, endereco, funcoes, lugares)
        if nota > 0:
            resultados.append({'curriculo': c, 'nota': nota, 'motivos': motivos})

    resultados.sort(key=lambda r: (-r['nota'], r['curriculo'].nome or ''))
    return resultados[:limite]
