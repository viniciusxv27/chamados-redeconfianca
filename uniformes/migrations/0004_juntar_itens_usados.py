"""Junta os itens "… · Usado" / "… - Usado" ao item da mesma peça (14/11/2026).

Antes, a peça usada era um item à parte ("Calça · CN Feminino · Usado · 38"). Agora
cada peça é um item só, com estoque novo (`saldo`) e quantidade usados
(`saldo_usado`), e cada movimento diz a condição.

Para cada item cuja descrição termina em "Usado" (com "·", "-", "–", "/" ou só
espaço antes; sem diferença de maiúscula/acento/espaço):

- se existe o item base (mesmo tipo e tamanho, descrição sem o sufixo): o saldo
  do usado vai para `saldo_usado` do base, os movimentos dele passam para o base
  com condição USADO (datas, autores e observações intactos) e o item usado é
  apagado;
- se não existe: o próprio item vira o base (descrição sem o sufixo, saldo vai
  para `saldo_usado`, saldo novo 0, movimentos USADO).

Depois o `saldo_apos` dos movimentos USADO de cada item mexido é refeito em ordem
(registrado_em, id), e a soma dos deltas de cada condição tem que bater com o
saldo dela — senão a migração para e nada muda. Rodar de novo não faz nada (não
sobra item com o sufixo). Os demais movimentos ficam NOVO (db_default da 0003).

A volta separa de novo: item que só tem usados volta a ser "… · Usado"; item com
novos e usados ganha (ou reaproveita) o item "… · Usado" com os movimentos USADO.
O sufixo volta sempre como " · Usado" (o "- Usado" escrito à mão não é lembrado).

As funções recebem os models (os históricos da migração ou os de verdade, nos testes).
"""
import re
import unicodedata

from django.db import migrations

NOVO, USADO = 'NOVO', 'USADO'
SUFIXO_USADO = re.compile(r'^(?P<base>.*?)(?:^|\s*[-·–—/(:]\s*|\s+)usad[oa]s?\)?\s*$', re.I)


def _norm(texto):
    texto = unicodedata.normalize('NFKD', str(texto or ''))
    return ' '.join(''.join(c for c in texto if not unicodedata.combining(c)).lower().split())


def _tamanho(tamanho):
    tamanho = str(tamanho if tamanho is not None else '').strip()
    if tamanho.endswith('.0') and tamanho[:-2].isdigit():
        tamanho = tamanho[:-2]
    return ' '.join(tamanho.upper().split())[:10]


def _descricao(descricao):
    return ' '.join(str(descricao or '').split())[:120]


def _chave(tipo, tamanho, descricao):
    return (_norm(tipo), _norm(_tamanho(tamanho)), _norm(descricao))


def separar_usado(descricao):
    """("CN Feminino", True) para "CN Feminino · Usado"; (descrição, False) se não tem o sufixo."""
    descricao = _descricao(descricao)
    achou = SUFIXO_USADO.match(_norm_espacos_acentos(descricao))
    if not achou:
        return descricao, False
    # O casamento foi no texto sem acento; o tamanho da base é o mesmo (só tira acento, não letras).
    base = descricao[:len(achou.group('base'))]
    return _descricao(base.rstrip(' -·–—/(:')), True


def _norm_espacos_acentos(texto):
    """Sem acento, sem mexer em maiúsculas nem no comprimento (para o sufixo "Usádo" também casar)."""
    return ''.join(unicodedata.normalize('NFKD', c)[0] for c in texto)


def _refazer_saldo_apos(Mov, item, condicao):
    """Refaz o saldo_apos das linhas da condição em ordem; devolve a soma dos deltas."""
    soma = 0
    for mov in Mov.objects.filter(item=item, condicao=condicao).order_by('registrado_em', 'id'):
        soma += mov.delta
        if mov.saldo_apos != soma:
            Mov.objects.filter(pk=mov.pk).update(saldo_apos=soma)
    return soma


def _conferir(Mov, item):
    for condicao, saldo in ((NOVO, item.saldo), (USADO, item.saldo_usado)):
        soma = sum(Mov.objects.filter(item=item, condicao=condicao).values_list('delta', flat=True))
        if soma != saldo:
            raise RuntimeError(
                f'Uniformes: item {item.pk} ({item.tipo} {item.descricao} {item.tamanho}) — soma dos movimentos '
                f'{condicao} = {soma}, saldo = {saldo}. Nada foi mudado.')


def juntar(Item, Mov):
    """Junta os itens usados; devolve {'juntados', 'transformados', 'movimentos', 'itens': [(de, para)]}."""
    relatorio = {'juntados': 0, 'transformados': 0, 'movimentos': 0, 'itens': []}
    usados = [i for i in Item.objects.order_by('id') if separar_usado(i.descricao)[1]]
    mexidos = {}
    for usado in usados:
        base_desc = separar_usado(usado.descricao)[0]
        chave = _chave(usado.tipo, usado.tamanho, base_desc)
        base = None
        for candidato in Item.objects.exclude(pk=usado.pk).order_by('id'):
            if not separar_usado(candidato.descricao)[1] and _chave(
                    candidato.tipo, candidato.tamanho, candidato.descricao) == chave:
                base = candidato
                break
        pecas = usado.saldo + usado.saldo_usado     # tudo que está no item "usado" é peça usada
        de = f'{usado.pk}:{usado.tipo} | {usado.descricao} | {usado.tamanho}'
        if base is not None:
            relatorio['movimentos'] += Mov.objects.filter(item=usado).update(item=base, condicao=USADO)
            base.saldo_usado += pecas
            base.ativo = base.ativo or usado.ativo
            base.save(update_fields=['saldo_usado', 'ativo'])
            usado.delete()
            relatorio['juntados'] += 1
            alvo = base
        else:
            relatorio['movimentos'] += Mov.objects.filter(item=usado).update(condicao=USADO)
            usado.descricao = base_desc
            usado.saldo_usado = pecas
            usado.saldo = 0
            usado.save(update_fields=['descricao', 'saldo', 'saldo_usado'])
            relatorio['transformados'] += 1
            alvo = usado
        mexidos[alvo.pk] = alvo
        relatorio['itens'].append((de, f'{alvo.pk}:{alvo.tipo} | {alvo.descricao} | {alvo.tamanho}'))
    for item in mexidos.values():
        item.refresh_from_db()
        _refazer_saldo_apos(Mov, item, USADO)
        _conferir(Mov, item)
    return relatorio


def separar(Item, Mov):
    """A volta: os usados de cada item voltam para um item "… · Usado" à parte."""
    com_usado = set(Item.objects.filter(saldo_usado__gt=0).values_list('id', flat=True))
    com_usado |= set(Mov.objects.filter(condicao=USADO).values_list('item_id', flat=True))
    for item in Item.objects.filter(pk__in=com_usado).order_by('id'):
        desc_usado = _descricao(f'{item.descricao} · Usado' if item.descricao else 'Usado')
        chave = _chave(item.tipo, item.tamanho, desc_usado)
        existente = next((i for i in Item.objects.exclude(pk=item.pk)
                          if _chave(i.tipo, i.tamanho, i.descricao) == chave), None)
        so_usados = item.saldo == 0 and not Mov.objects.filter(item=item, condicao=NOVO).exists()
        if so_usados and existente is None:
            Mov.objects.filter(item=item).update(condicao=NOVO)
            item.descricao = desc_usado
            item.saldo, item.saldo_usado = item.saldo_usado, 0
            item.save(update_fields=['descricao', 'saldo', 'saldo_usado'])
            alvo = item
        else:
            alvo = existente or Item.objects.create(
                tipo=item.tipo, tamanho=item.tamanho, descricao=desc_usado, ativo=item.ativo)
            Mov.objects.filter(item=item, condicao=USADO).update(item=alvo, condicao=NOVO)
            # No item "… · Usado" tudo é peça usada e fica num saldo só (o antigo).
            alvo.saldo += item.saldo_usado + alvo.saldo_usado
            alvo.saldo_usado = 0
            alvo.save(update_fields=['saldo', 'saldo_usado'])
            item.saldo_usado = 0
            item.save(update_fields=['saldo_usado'])
        Mov.objects.filter(item=alvo).update(condicao=NOVO)
        _refazer_saldo_apos(Mov, alvo, NOVO)
        alvo.refresh_from_db()
        _conferir(Mov, alvo)


def _ida(apps, schema_editor):
    juntar(apps.get_model('uniformes', 'ItemUniforme'), apps.get_model('uniformes', 'MovimentoUniforme'))


def _volta(apps, schema_editor):
    separar(apps.get_model('uniformes', 'ItemUniforme'), apps.get_model('uniformes', 'MovimentoUniforme'))


class Migration(migrations.Migration):

    dependencies = [
        ('uniformes', '0003_novo_e_usado_no_mesmo_item'),
    ]

    operations = [
        migrations.RunPython(_ida, _volta),
    ]
