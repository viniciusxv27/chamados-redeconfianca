"""/folha-ponto/admin/: filtro "Loja" (setores com "Loja" no nome).

Pedido: filtrar a lista por setor, com o rótulo "Loja", oferecendo todos os
setores que têm "Loja" no nome. A loja de cada pessoa é o setor principal
(``user.sector``) — o mesmo do relatório de assinaturas. Os setores extras
(M2M) ficam de fora de propósito: quem é de escritório costuma ter todas as
lojas ali e apareceria na folha de cada uma.

Nada é gravado: roda dentro de uma transação desfeita no fim. As folhas do
teste entram por bulk_create (sem sinais) e apontam para um PDF que não existe
— a lista não abre o arquivo.
"""
import os
import re
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils.html import escape

from folhaponto.models import FolhaPonto, FolhaPontoManagerPermission
from users.models import Sector

User = get_user_model()
URL = '/folha-ponto/admin/'
ANO = 2031                      # nenhuma folha real tem esse ano: a lista fica só com as do teste
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def opcoes_de_loja(html):
    """As opções do <select name="loja"> como [(valor, texto, marcada)]."""
    bloco = re.search(r'<select name="loja"[^>]*>(.*?)</select>', html, flags=re.S)
    if not bloco:
        return None
    return [(valor, texto.strip(), bool(marcada)) for valor, marcada, texto in
            re.findall(r'<option value="([^"]*)"\s*(selected)?\s*>([^<]*)</option>', bloco.group(1))]


def nomes_na_tabela(html):
    corpo = html.split('<tbody', 1)[1].split('</tbody>', 1)[0] if '<tbody' in html else ''
    return sorted(re.findall(r'<p class="text-sm font-medium text-gray-900">([^<]*)</p>', corpo))


marcador = transaction.atomic()
marcador.__enter__()
try:
    assert not FolhaPonto.objects.filter(year=ANO).exists(), f'já existe folha real em {ANO}'

    loja_a = Sector.objects.create(name='ZZ Loja Teste Filtro A')
    loja_b = Sector.objects.create(name='ZZ Loja Teste Filtro B')
    maiuscula = Sector.objects.create(name='ZZ LOJA TESTE FILTRO MAIÚSCULA')
    escritorio = Sector.objects.create(name='ZZ Escritório Teste Filtro')

    def novo(apelido, setor=None):
        return User.objects.create_user(
            username=f'zzfloja.{apelido}', email=f'zzfloja.{apelido}@exemplo-teste.local',
            password='S3nha!teste', first_name='ZZFiltroLoja', last_name=apelido.title(), sector=setor)

    gestor = novo('gestor', escritorio)
    FolhaPontoManagerPermission.objects.create(user=gestor)
    da_a = novo('vendedora', loja_a)
    da_b = novo('caixa', loja_b)
    supervisor = novo('supervisor', escritorio)
    supervisor.sectors.add(loja_a, loja_b)           # enxerga as lojas, mas não é de nenhuma
    avulso = novo('avulso')                          # sem setor
    sem_acesso = novo('semacesso', loja_a)           # é da loja A, mas não gere a folha de ponto

    FolhaPonto.objects.bulk_create([
        FolhaPonto(user=u, month=mes, year=ANO, employee_name=u.full_name,
                   pdf_file=f'zz-teste/{u.username}-{ANO}-{mes:02d}.pdf')
        for u, mes in ((da_a, 8), (da_a, 9), (da_b, 8), (supervisor, 8), (avulso, 8), (sem_acesso, 8))
    ])
    todos = sorted([da_a.full_name, da_a.full_name, da_b.full_name, supervisor.full_name,
                    avulso.full_name, sem_acesso.full_name])

    c = Client()
    c.force_login(gestor)

    print('== O FILTRO NA TELA ==')
    r = c.get(URL, {'year': ANO})
    html = r.content.decode()
    opcoes = opcoes_de_loja(html) or []
    valores = [o[0] for o in opcoes]
    esperadas = list(Sector.objects.filter(name__icontains='loja').order_by('name'))
    t('abre (200)', r.status_code == 200, r.status_code)
    t('tem o seletor com o rótulo "Loja"', bool(opcoes) and '>Loja</label>' in html)
    t('"Todas" vem primeiro', bool(opcoes) and opcoes[0][:2] == ('', 'Todas'), opcoes[:1])
    t('e depois todos os setores com "Loja" no nome, em ordem alfabética',
      opcoes[1:] and [(o[0], o[1]) for o in opcoes[1:]] == [(str(s.pk), escape(s.name)) for s in esperadas],
      (len(opcoes) - 1, len(esperadas)))
    t('as lojas de verdade estão lá', len(esperadas) > 4 and any(o[1].startswith('Loja ') for o in opcoes))
    t('"LOJA" em maiúsculas também conta', str(maiuscula.pk) in valores)
    seletor = re.search(r'<select name="loja"[^>]*>(.*?)</select>', html, flags=re.S)
    t('setor sem "Loja" no nome fica fora do seletor',
      str(escritorio.pk) not in valores and escape(escritorio.name) not in (seletor.group(1) if seletor else ''))
    t('sem filtro, nenhuma loja marcada', not any(o[2] for o in opcoes))
    t('sem filtro, a lista traz todas as folhas', nomes_na_tabela(html) == todos, nomes_na_tabela(html))

    print('\n== FILTRANDO POR LOJA ==')
    r = c.get(URL, {'year': ANO, 'loja': loja_a.pk})
    html = r.content.decode()
    nomes = nomes_na_tabela(html)
    t('loja A: só as folhas de quem é da loja A', nomes == sorted([da_a.full_name, da_a.full_name, sem_acesso.full_name]),
      nomes)
    t('a loja escolhida fica marcada no seletor', [o[0] for o in opcoes_de_loja(html) or [] if o[2]] == [str(loja_a.pk)])
    t('quem é de escritório e só enxerga a loja (setores extras) não entra', supervisor.full_name not in nomes)
    t('nem quem não tem setor', avulso.full_name not in nomes)
    nomes = nomes_na_tabela(c.get(URL, {'year': ANO, 'loja': loja_b.pk}).content.decode())
    t('loja B: só a folha da loja B', nomes == [da_b.full_name], nomes)
    html = c.get(URL, {'year': ANO, 'loja': maiuscula.pk}).content.decode()
    t('loja sem folha: mostra o aviso de lista vazia',
      'Nenhuma folha de ponto encontrada.' in html and not nomes_na_tabela(html))

    print('\n== JUNTO COM OS OUTROS FILTROS ==')
    nomes = nomes_na_tabela(c.get(URL, {'year': ANO, 'month': 9, 'loja': loja_a.pk}).content.decode())
    t('com o mês', nomes == [da_a.full_name], nomes)
    nomes = nomes_na_tabela(c.get(URL, {'year': ANO, 'q': 'Vendedora', 'loja': loja_a.pk}).content.decode())
    t('com a busca', nomes == [da_a.full_name, da_a.full_name], nomes)
    nomes = nomes_na_tabela(c.get(URL, {'year': ANO, 'q': 'Caixa', 'loja': loja_a.pk}).content.decode())
    t('busca de alguém de outra loja: vazio', nomes == [], nomes)
    nomes = nomes_na_tabela(c.get(URL, {'year': ANO, 'periodicity': 'mensal', 'loja': loja_a.pk}).content.decode())
    t('com a periodicidade (a de setembro é a semanal em aberto da vendedora)',
      nomes == sorted([da_a.full_name, sem_acesso.full_name]), nomes)

    print('\n== VALOR ESTRANHO NÃO QUEBRA NEM ESCONDE FOLHA ==')
    for rotulo, valor in (('texto', 'abc'), ('setor que não é loja', escritorio.pk), ('loja inexistente', 999999999)):
        r = c.get(URL, {'year': ANO, 'loja': valor})
        html = r.content.decode()
        t(f'{rotulo}: ignora e mostra tudo', r.status_code == 200 and nomes_na_tabela(html) == todos
          and not any(o[2] for o in opcoes_de_loja(html) or []), (r.status_code, nomes_na_tabela(html)))

    print('\n== ACESSO ==')
    outro = Client()
    outro.force_login(sem_acesso)
    r = outro.get(URL, {'loja': loja_a.pk})
    t('quem não gere a folha de ponto continua sem acesso', r.status_code == 302 and r['Location'] == '/folha-ponto/',
      (r.status_code, r.get('Location')))

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
