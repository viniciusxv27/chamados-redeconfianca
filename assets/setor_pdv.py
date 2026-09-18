"""Setor e PDV dos ativos legados.

O Setor é só "Loja" ou "Escritório"; o PDV é um setor do portal: na loja, um dos
setores com "Loja" no nome; no escritório, qualquer setor. Os dois continuam
gravados como texto no ``Asset`` (o nome do setor vai no PDV).

Os ativos antigos vieram de planilha com outro jeito de preencher (Setor
"Salão", "Retaguarda"...; PDV "Glória", "ESCRITÓRIO/ADM"...). Para eles,
``sugerir`` acha o setor do portal que corresponde ao PDV antigo e, por ele, se é
Loja ou Escritório — a tela de edição abre com isso escolhido.
"""
import re
import unicodedata

LOJA = 'Loja'
ESCRITORIO = 'Escritório'
SETORES = [(LOJA, 'Loja'), (ESCRITORIO, 'Escritório')]


def pdvs_por_setor():
    """{'Loja': [setores com "Loja" no nome], 'Escritório': [todos os setores]} — os nomes, em ordem."""
    from users.models import Sector

    todos = list(Sector.objects.order_by('name').values_list('name', flat=True))
    return {LOJA: [nome for nome in todos if 'loja' in nome.lower()], ESCRITORIO: todos}


def normal(texto):
    """Sem acento, caixa nem pontuação: "ESCRITÓRIO/ADM" → "escritorio adm"."""
    texto = unicodedata.normalize('NFKD', str(texto or '').lower())
    texto = ''.join(c for c in texto if not unicodedata.combining(c))
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', texto).split())


# Palavras que não distinguem um PDV de outro ("Centro de Vila Velha", "Sh. Norte Sul") e o
# apelido que a planilha usa para Vitória.
PALAVRAS_VAZIAS = {'de', 'do', 'da', 'dos', 'das', 'e', 'loja', 'sh', 'shopping'}
SINONIMOS = {'vix': 'vitoria'}


def palavras(texto):
    """As palavras que identificam o nome: "Loja Centro VIX" e "Centro de Vitória" dão {centro, vitoria}."""
    return frozenset(SINONIMOS.get(p, p) for p in normal(texto).split() if p not in PALAVRAS_VAZIAS)


def sugerir(setor_antigo, pdv_antigo, pdvs=None):
    """(setor, pdv) no formato novo para um ativo antigo; '' onde não dá para saber com segurança.

    O PDV vale quando bate com um único setor do portal, comparando as palavras sem
    acento, caixa, "Loja", "de", "Sh." ("Centro de Vila Velha" → "Loja Centro Vila
    Velha", "Glória" → "Loja Glória"); se nenhum bate igual, quando as palavras de um
    estão todas no outro ("Paduá" → "Loja Santo Antonio de Padua"). O Setor sai do que
    já estava escrito ("Loja", "Escritório") ou, se não, do PDV achado.
    """
    pdvs = pdvs or pdvs_por_setor()
    todos, lojas = pdvs[ESCRITORIO], set(pdvs[LOJA])
    pdv = ''
    alvo = palavras(pdv_antigo)
    if alvo:
        iguais = [nome for nome in todos if palavras(nome) == alvo]
        if len(iguais) == 1:
            pdv = iguais[0]
        elif not iguais:
            parecidos = [nome for nome in todos if palavras(nome) and (alvo <= palavras(nome) or palavras(nome) <= alvo)]
            if len(parecidos) == 1:
                pdv = parecidos[0]

    setor = {normal(LOJA): LOJA, normal(ESCRITORIO): ESCRITORIO}.get(normal(setor_antigo), '')
    if not setor and pdv:
        setor = LOJA if pdv in lojas else ESCRITORIO
    if setor == LOJA and pdv and pdv not in lojas:
        pdv = ''                         # PDV de escritório num ativo marcado como loja: melhor escolher de novo
    return setor, pdv


def conferir(setor, pdv, pdvs=None):
    """Mensagem de erro da combinação Setor/PDV, ou '' quando ela vale."""
    pdvs = pdvs or pdvs_por_setor()
    if setor not in dict(SETORES):
        return 'Escolha se o ativo está numa Loja ou no Escritório.'
    if not pdv:
        return 'Escolha o PDV.'
    if pdv not in pdvs[setor]:
        return ('Na loja, o PDV é uma das lojas.' if setor == LOJA
                else 'Esse PDV não está entre os setores do portal.')
    return ''
