"""Prêmios (/prizes/, /prizes/manage/, /prizes/<id>/edit/) — ajustes de 26/11/2026.

- Resgates e estoque: uma régua só (pendente, aprovado e entregue ocupam o estoque;
  cancelado devolve). Antes a gestão mostrava 0 resgates (a view não anotava
  total_redemptions), o Mercadinho mostrava "restantes" vazio (prize.stock_remaining
  não existia) e o estoque da gestão era o número cru cadastrado.
- Edição: o valor saía localizado ("8250,00") num <input type=number> e o navegador
  descartava; agora sai com |unlocalize. O campo de estoque é o disponível agora.
- Exclusão lógica: some da gestão e do Mercadinho; resgates e histórico ficam.
- Imagem do Mercadinho inteira (object-fit: contain).

Roda dentro de uma transação desfeita no fim (nada fica no banco) e com cache em memória
(o `default` é o Redis remoto do dev). Os prêmios de teste não têm imagem (nada sobe para o MinIO).
"""
import os
import re
import sys
from decimal import Decimal

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
import redeconfianca.settings as _cfg  # noqa: E402
_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'teste-{n}'} for n in _cfg.CACHES}
django.setup()

from django.conf import settings  # noqa: E402

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model  # noqa: E402
from django.db import transaction  # noqa: E402
from django.test import Client  # noqa: E402

from prizes.models import STATUS_QUE_OCUPAM_ESTOQUE, CSTransaction, Prize, PrizeCategory, Redemption  # noqa: E402
from users.models import Sector  # noqa: E402

User = get_user_model()
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def html(r):
    return r.content.decode('utf-8', 'replace')


def cartao(pagina, atributo, pk):
    """O <article> do prêmio na página (do data-* até o fim do cartão)."""
    m = re.search(rf'{atributo}="{pk}"(.*?)</article>', pagina, re.S)
    return m.group(1) if m else ''


marcador = transaction.atomic()
marcador.__enter__()
try:
    setor = Sector.objects.create(name='ZZ Setor Prêmios Teste')

    def novo(apelido, hierarquia='PADRAO', saldo='0'):
        return User.objects.create_user(
            username=f'zzpremio.{apelido}', email=f'zzpremio.{apelido}@exemplo-teste.local', password='S3nha!teste',
            first_name=f'ZZ{apelido.title()}', last_name='Teste', sector=setor, hierarchy=hierarquia,
            balance_cs=Decimal(saldo))

    gestor = novo('gestor', 'SUPERADMIN')
    ana = novo('ana', saldo='5000')
    beto = novo('beto', saldo='5000')
    caio = novo('caio', saldo='5000')
    cat = PrizeCategory.objects.create(name='ZZ Cat Prêmios Teste')

    def premio(nome, valor='100', **kw):
        kw.setdefault('stock', 10)
        return Prize.objects.create(name=nome, description=f'{nome} descrição', category=cat,
                                    value_cs=Decimal(valor), created_by=gestor, **kw)

    def resgate(user, p, status):
        return Redemption.objects.create(user=user, prize=p, status=status, original_value=p.value_cs,
                                         final_value=p.value_cs)

    p = premio('ZZ Teste Sanduicheira', '8250', stock=10)
    for st in ('PENDENTE', 'APROVADO', 'ENTREGUE', 'ENTREGUE', 'CANCELADO', 'CANCELADO'):
        resgate(ana, p, st)

    cg = Client(); cg.force_login(gestor)
    ca = Client(); ca.force_login(ana)
    cb = Client(); cb.force_login(beto)
    cc = Client(); cc.force_login(caio)

    print('== RÉGUA DO ESTOQUE ==')
    t('régua: pendente, aprovado e entregue ocupam', set(STATUS_QUE_OCUPAM_ESTOQUE) == {'PENDENTE', 'APROVADO', 'ENTREGUE'})
    p.refresh_from_db()
    t('resgates_ativos = 4 (cancelados fora)', p.resgates_ativos == 4, p.resgates_ativos)
    t('estoque_restante = 10 - 4 = 6', p.estoque_restante == 6, p.estoque_restante)
    anotado = Prize.objects.com_resgates().get(pk=p.pk)
    t('anotação bate com a conta no banco', (anotado.n_resgates_ativos, anotado.n_pendentes, anotado.n_aprovados,
                                             anotado.n_entregues, anotado.n_cancelados) == (4, 1, 1, 2, 2),
      (anotado.n_resgates_ativos, anotado.n_cancelados))
    t('ilimitado: restante None e nunca esgota', premio('ZZ Teste Ilimitado', unlimited_stock=True, stock=0).estoque_restante is None)
    t('availability_percentage = 60%', round(p.availability_percentage) == 60, p.availability_percentage)

    print('\n== /prizes/ (Mercadinho) ==')
    r = ca.get('/prizes/?search=ZZ Teste')
    pag = html(r)
    t('abre (200)', r.status_code == 200, r.status_code)
    c = cartao(pag, 'data-premio', p.pk)
    t('mostra "6 restantes"', 'data-restante="6"' in c and '6 restantes' in c, c[:400])
    t('imagem inteira: CSS com object-fit: contain', 'object-fit: contain' in pag)
    t('sem object-cover na foto do prêmio', 'object-cover' not in c)
    t('visual novo: rc-hero + script das animações', 'rc-hero' in pag and 'id="rcTopo"' in pag)
    t('saldo no topo', 'data-rc-conta="5000"' in pag, re.findall(r'data-rc-conta="[^"]*"', pag)[:3])

    print('\n== /prizes/manage/ ==')
    r = cg.get('/prizes/manage/')
    pag = html(r)
    t('abre (200)', r.status_code == 200, r.status_code)
    c = cartao(pag, 'data-rc-premio', p.pk)
    t('resgates = 4 (antes saía 0)', 'data-resgates="4"' in c, re.findall(r'data-resgates="\d+"', c))
    t('estoque dinâmico: 6 de 10', 'data-restante="6"' in c and 'de 10' in c)
    t('detalhe por status, cancelado à parte', '1 pendente' in c and '2 entregues' in c and '2 cancelados' in c)
    t('botão Excluir no cartão', 'pedirExclusao(' in c)
    t('PADRÃO não entra na gestão', ca.get('/prizes/manage/').status_code == 302)
    t('cópia antiga /users/manage/prizes/ redireciona', cg.get('/users/manage/prizes/').url == '/prizes/manage/')
    t('edição antiga /users/.../edit/ redireciona', cg.get(f'/users/manage/prizes/{p.pk}/edit/').url == f'/prizes/{p.pk}/edit/')

    print('\n== /prizes/<id>/edit/ (GET) ==')
    r = cg.get(f'/prizes/{p.pk}/edit/')
    pag = html(r)
    t('abre (200)', r.status_code == 200, r.status_code)
    m = re.search(r'name="value_cs" value="([^"]*)"', pag)
    t('valor sem localizar ("8250.00", não "8250,00")', m and m.group(1) == '8250.00', m and m.group(1))
    m = re.search(r'name="estoque_disponivel"[^>]*value="([^"]*)"', pag, re.S)
    t('campo de estoque = disponível agora (6)', m and m.group(1) == '6', m and m.group(1))
    t('histórico com o status de verdade (Cancelado aparece)', 'Cancelado' in pag and 'Entregue' in pag)

    print('\n== /prizes/<id>/edit/ (POST) ==')
    base = {'name': 'ZZ Teste Sanduicheira 2', 'description': 'nova', 'category': str(cat.pk), 'value_cs': '1234.50',
            'priority': 'ALTA', 'estoque_disponivel': '20', 'terms': 'termos', 'valid_until': '', 'is_active': 'on'}
    r = cg.post(f'/prizes/{p.pk}/edit/', base)
    p.refresh_from_db()
    t('salva e volta para a gestão', r.status_code == 302 and r.url == '/prizes/manage/', (r.status_code, getattr(r, 'url', '')))
    t('valor gravado (1234.50)', p.value_cs == Decimal('1234.50'), p.value_cs)
    t('nome e prioridade gravados', p.name == 'ZZ Teste Sanduicheira 2' and p.priority == 'ALTA')
    t('estoque: total = 20 disponíveis + 4 resgates = 24', p.stock == 24 and p.estoque_restante == 20, (p.stock, p.estoque_restante))
    r = cg.get(f'/prizes/{p.pk}/edit/')
    t('reabrir a edição mostra o valor salvo', 'name="value_cs" value="1234.50"' in html(r))

    r = cg.post(f'/prizes/{p.pk}/edit/', {**base, 'value_cs': '1.500,75', 'has_discount': 'on', 'discount_percentage': '10'})
    p.refresh_from_db()
    t('aceita "1.500,75" e calcula o desconto', p.value_cs == Decimal('1500.75') and p.has_discount
      and p.discounted_value == Decimal('1350.68'), (p.value_cs, p.discounted_value))
    r = cg.get(f'/prizes/{p.pk}/edit/')
    t('desconto sem localizar no campo', 'value="10.00"' in html(r))

    antes = (p.value_cs, p.stock)
    r = cg.post(f'/prizes/{p.pk}/edit/', {**base, 'value_cs': ''})
    p.refresh_from_db()
    t('valor vazio: não grava e avisa', r.status_code == 200 and (p.value_cs, p.stock) == antes
      and 'Confira os números' in html(r), r.status_code)
    r = cg.post(f'/prizes/{p.pk}/edit/', {**base, 'has_discount': 'on', 'discount_percentage': ''})
    p.refresh_from_db()
    t('desconto marcado sem %: não grava e avisa', p.value_cs == antes[0] and 'porcentagem do desconto' in html(r))
    r = cg.post(f'/prizes/{p.pk}/edit/', {**base, 'estoque_disponivel': '-3'})
    p.refresh_from_db()
    t('estoque negativo: recusa', p.stock == antes[1] and 'negativo' in html(r))
    r = cg.post(f'/prizes/{p.pk}/edit/', {**base, 'unlimited_stock': 'on', 'estoque_disponivel': ''})
    p.refresh_from_db()
    t('ilimitado com o campo vazio: grava sem erro e mantém o total', r.status_code == 302 and p.unlimited_stock
      and p.stock == 24, (r.status_code, p.unlimited_stock, p.stock))
    cg.post(f'/prizes/{p.pk}/edit/', base)
    p.refresh_from_db()
    t('volta a limitado (20 disponíveis)', not p.unlimited_stock and p.estoque_restante == 20)
    t('PADRÃO não edita', ca.post(f'/prizes/{p.pk}/edit/', {**base, 'value_cs': '1'}).status_code == 302
      and Prize.objects.get(pk=p.pk).value_cs == Decimal('1234.50'))

    print('\n== ESTOQUE NO RESGATE ==')
    u = premio('ZZ Teste Última Unidade', '10', stock=1)
    r = cb.post(f'/prizes/redeem/{u.pk}/')
    t('1º resgate da última unidade passa', r.json().get('success') is True, r.content[:200])
    r = cc.post(f'/prizes/redeem/{u.pk}/')
    t('2º resgate com o 1º ainda pendente: recusa', r.json().get('error') == 'Prêmio não disponível', r.content[:200])
    pag = html(ca.get('/prizes/?search=ZZ Teste'))
    t('Mercadinho mostra Esgotado', 'data-restante="0"' in cartao(pag, 'data-premio', u.pk))
    rb = Redemption.objects.get(prize=u, user=beto)
    cb.post(f'/prizes/redemptions/{rb.pk}/cancel/')
    u.refresh_from_db()
    t('cancelar devolve a unidade', u.estoque_restante == 1, u.estoque_restante)
    r = cc.post(f'/prizes/redeem/{u.pk}/')
    t('depois do cancelamento outro resgata', r.json().get('success') is True, r.content[:200])

    print('\n== EXCLUIR (lógico) ==')
    n_resgates = Redemption.objects.filter(prize=p).count()
    n_trans = CSTransaction.objects.filter(related_redemption__prize=p).count()
    t('PADRÃO não exclui (403)', ca.post(f'/prizes/{p.pk}/excluir/').status_code == 403)
    t('excluir por GET: 405', cg.get(f'/prizes/{p.pk}/excluir/').status_code == 405)
    r = cg.post(f'/prizes/{p.pk}/excluir/')
    p.refresh_from_db()
    t('exclui', r.json().get('success') is True and p.excluido_em is not None and p.excluido_por_id == gestor.pk
      and not p.is_active, r.content[:200])
    t('o prêmio continua no banco', Prize.objects.filter(pk=p.pk).exists())
    t('resgates intactos', Redemption.objects.filter(prize=p).count() == n_resgates == 6)
    t('transações intactas', CSTransaction.objects.filter(related_redemption__prize=p).count() == n_trans)
    pag = html(cg.get('/prizes/manage/'))
    t('some da gestão', f'data-rc-premio="{p.pk}"' not in pag)
    pag = html(cg.get('/prizes/manage/?excluidos=1'))
    t('aparece em Excluídos, com Restaurar', f'data-rc-premio="{p.pk}"' in pag and f'restaurarPremio({p.pk})' in pag)
    t('some do Mercadinho', f'data-premio="{p.pk}"' not in html(ca.get('/prizes/?search=ZZ Teste')))
    pag = html(ca.get('/prizes/my-redemptions/'))
    t('quem resgatou continua vendo no histórico', 'ZZ Teste Sanduicheira 2' in pag)
    t('resgatar excluído: 404', cb.post(f'/prizes/redeem/{p.pk}/').status_code == 404)
    r = cg.get(f'/prizes/{p.pk}/edit/')
    t('editar excluído manda para Excluídos', r.status_code == 302 and 'excluidos=1' in r.url)
    t('excluir de novo: 404', cg.post(f'/prizes/{p.pk}/excluir/').status_code == 404)
    t('gestão de resgates continua listando', 'ZZ Teste Sanduicheira 2' in html(cg.get('/prizes/redemptions/?search=ZZ Teste')))

    r = cg.post(f'/prizes/{p.pk}/restaurar/')
    p.refresh_from_db()
    t('restaurar volta pausado', r.json().get('success') is True and p.excluido_em is None and not p.is_active)
    t('volta para a gestão', f'data-rc-premio="{p.pk}"' in html(cg.get('/prizes/manage/')))

    print('\n== PAUSAR/ATIVAR ==')
    r = cg.post(f'/prizes/{p.pk}/status/')
    p.refresh_from_db()
    t('ativa pelo endpoint do app prizes', r.json().get('is_active') is True and p.is_active)
    t('PADRÃO não pausa (403)', ca.post(f'/prizes/{p.pk}/status/').status_code == 403)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
