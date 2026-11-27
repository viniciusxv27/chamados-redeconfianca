"""/rotina-adm/ (12/11/2026): Renova compacta, espelho do check-list no Kanban do Impulso e ranking dos analistas.

Cobre:
- Renova da central: cartões só com os números; as listas recentes ficam nos modais "Ver detalhado";
  links seguem filtrados pela loja;
- marcar item do check-list mostra o cartão "Rotina ADM · Check-list da Loja X · dd/mm" no
  Kanban do Impulso de quem marcou, na coluna "Entregue (aguardando avaliação)" (23/11/2026); remarcar não duplica; desmarcar tira o item e, sem itens, o cartão;
  nenhum Meta é criado e a pontuação do Impulso não muda;
- quem vê o espelho: a própria pessoa, o superuser (com filtro de colaborador), não um colega;
- ranking do mês: ordem, % de conclusão, no prazo, dias 100%, domingo, broche configurável pelo SUPERADMIN;
  acesso igual às telas de gestão.

Roda num sqlite descartável; nada sai para a rede.
"""
import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-rotina-adm-ranking-'))
(PASTA / 'zz_rotina_adm_ranking_settings.py').write_text(f"""
from redeconfianca.settings import *              # noqa: F401,F403

DATABASES = {{'default': {{'ENGINE': 'django.db.backends.sqlite3',
                          'NAME': r'{PASTA}/banco.sqlite3'}}}}


class SemMigrations:
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


MIGRATION_MODULES = SemMigrations()

USE_S3 = False
MEDIA_ROOT = r'{PASTA}/media'
MEDIA_URL = '/media/'
STORAGES = {{
    'default': {{'BACKEND': 'django.core.files.storage.FileSystemStorage'}},
    'staticfiles': {{'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
}}
DEBUG = False
OPENAI_API_KEY = ''
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']
CACHES = {{
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-radm-rk'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-radm-rk-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_rotina_adm_ranking_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import json
from datetime import date, datetime, time, timedelta

import requests
from django.contrib.auth import get_user_model
from django.utils import timezone

from communications.models import CommunicationGroup
from impulso.models import Meta
from impulso.scoring import calcular_pontuacao
from rotina_adm import ranking, servicos
from rotina_adm.models import AcessoRotinaAdm, ConfiguracaoRotinaAdm, ItemChecklist, MarcacaoChecklist
from users.models import Sector

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


def sem_rede(*a, **k):
    raise AssertionError('teste tentou sair para a rede')


for nome in ('get', 'post', 'request'):
    mock.patch.object(requests, nome, sem_rede).start()

gloria = Sector.objects.create(name='Loja Glória')
serra = Sector.objects.create(name='Loja Serra Sede')
centro = Sector.objects.create(name='Loja Centro')
norte = Sector.objects.create(name='Loja Norte')
escritorio = Sector.objects.create(name='Financeiro')


def usuario(nome, hierarquia='PADRAO', setor=gloria, **extra):
    u = User.objects.create_user(username=f'zzrk.{nome}', email=f'zzrk.{nome}@exemplo-teste.local', password='S3nha!teste',
                                 first_name='ZZRK', last_name=nome, sector=setor, hierarchy=hierarquia, **extra)
    c = Client(); c.force_login(u)
    return u, c


chefe, cs = usuario('super', 'SUPERADMIN', escritorio, is_superuser=True)
ana, ca = usuario('ana')
bia, cb = usuario('bia', setor=serra)
duda, cd = usuario('duda', setor=centro)
edu, ce = usuario('edu', setor=norte)
gestor, cg = usuario('gestor', setor=escritorio)

adms_impulso = CommunicationGroup.objects.create(name="ADM's LOJAS", created_by=chefe)
adms_impulso.members.add(ana, bia, duda, edu)
grupo = CommunicationGroup.objects.create(name='ZZRK ADMs da Rotina', created_by=chefe)
grupo.members.add(ana, bia, duda, edu)
AcessoRotinaAdm.objects.create(grupo=grupo, papel=AcessoRotinaAdm.VISUALIZADOR)
AcessoRotinaAdm.objects.create(user=gestor, papel=AcessoRotinaAdm.GESTOR)

hoje = timezone.localdate()

# ─────────────────────────────────────────────────────────────────────────────
print('Renova compacta na central')
from auditoria_sap.models import VendaRenova  # noqa: E402
from renova import checklist as rn_checklist  # noqa: E402
from renova.models import Renova  # noqa: E402

rv = Renova.objects.create(criado_por=ana, marca='APPLE', modelo='iPhone 13', armazenamento='128GB', imei1='356938035643809',
                           loja=gloria, parecer=rn_checklist.APROVADO, vendedor_nome='ZZ Vendedor Renova',
                           aprovacao=Renova.AGUARDANDO_GERENTE)
VendaRenova.objects.create(ordem='9100001', cliente_nome='ZZ Cliente Sem', data_fatura=hoje, material='IPHONE 15',
                           setor=gloria, ativa=True)
# Os links para a Visão SAP/Renova só aparecem para quem pode abrir (liberação, 26/11/2026).
from users.models import UserModuleAccess as _UMA  # noqa: E402
_UMA.objects.get_or_create(user=ana, module_key='sap')
r = ca.get('/rotina-adm/')
h = r.content.decode()
t('central abre', r.status_code == 200, r.status_code)
t('dois cartões compactos com os números', h.count('class="ra-card ra-renova"') == 2 and 'aguardando gerente' in h
  and 'sem chamado no mês' in h)
t('botão "Ver detalhado" em cada um', 'data-abrir="raModalRenova"' in h and 'data-abrir="raModalSapRenova"' in h)
pos_modal_rn = h.find('id="raModalRenova"')
pos_modal_sap = h.find('id="raModalSapRenova"')
t('a lista de avaliações fica só no modal', pos_modal_rn > 0 and h.find(rv.codigo) > pos_modal_rn, (pos_modal_rn, h.find(rv.codigo)))
t('a lista do SAP fica só no modal', pos_modal_sap > 0 and h.find('ZZ Cliente Sem') > pos_modal_sap)
t('links seguem filtrados pela loja', f'/renova/?loja={gloria.id}' in h and f'/sap/renova/?loja={gloria.id}' in h
  and f'/sap/renova/?loja={gloria.id}&q=9100001' in h and f'/renova/{rv.pk}/' in h)
t('CSS com versão nova', 'rotina_adm.css?v=6' in h)
Renova.objects.all().delete()
VendaRenova.objects.all().delete()

# ─────────────────────────────────────────────────────────────────────────────
print('Espelho do check-list no Kanban do Impulso')
i1 = ItemChecklist.objects.create(loja=gloria, titulo='ZZ Abrir o caixa', horario=time(23, 59), ordem=1)
i2 = ItemChecklist.objects.create(loja=gloria, titulo='ZZ Conferir malotes', ordem=2)
i3 = ItemChecklist.objects.create(loja=gloria, titulo='ZZ Fechar o dia', horario=time(23, 59), ordem=3)
# Uma meta de verdade para a pontuação não ser zero: a comparação antes/depois tem o que comparar.
Meta.objects.create(gestor=gestor, colaborador=ana, titulo='ZZRK meta real', descricao='x', prazo=hoje,
                    aprovacao=Meta.Aprovacao.APROVADA, created_by=gestor)


def pontuacao(u):
    return json.dumps(calcular_pontuacao(u), default=str, sort_keys=True)


antes = pontuacao(ana)
metas_antes = Meta.objects.count()
titulo_cartao = f'Rotina ADM · Check-list da Loja Glória · {hoje:%d/%m}'


def marcar(cli, item, feito=True):
    return cli.post(f'/rotina-adm/checklist/{item.id}/marcar/', json.dumps({'feito': feito}), content_type='application/json').json()


def kanban(cli, **params):
    return cli.get('/impulso/metas/', params).content.decode()


k = kanban(ca)
t('sem marcação, sem espelho', 'data-espelho-rotina-adm' not in k)
t('marcar responde ok', marcar(ca, i1)['ok'])
k = kanban(ca)
t('marcar cria o cartão no Kanban de quem marcou', titulo_cartao in k and 'data-espelho-rotina-adm' in k)
# 23/11/2026: vai para "Entregue (aguardando avaliação)", não mais para "Concluída".
pos_entregue, pos_concluida = k.find('data-status="ENTREGUE"'), k.find('data-status="CONCLUIDA"')
t('o cartão fica na coluna Aguardando avaliação (não na Concluída)',
  0 < pos_entregue < k.find(titulo_cartao) < pos_concluida, (pos_entregue, k.find(titulo_cartao), pos_concluida))
t('o cartão diz que não pontua', 'Não entra na pontuação do Impulso' in k)
t('um item marcado', '1 item marcado' in k and 'loja 1/3 no dia' in k)
marcar(ca, i2)
marcar(ca, i1)                                             # remarcar o mesmo item
k = kanban(ca)
t('remarcar não duplica (uma marcação por item e dia)', MarcacaoChecklist.objects.filter(item=i1, data=hoje).count() == 1)
t('um cartão só para o dia, com os dois itens', k.count('data-espelho-cartao=') == 1 and '2 itens marcados' in k)
t('itens listados no cartão', 'ZZ Abrir o caixa' in k and 'ZZ Conferir malotes' in k and 'ZZ Fechar o dia' not in k)
t('nenhuma Meta criada', Meta.objects.count() == metas_antes)
marcar(ca, i3)
k = kanban(ca)
t('loja completa no dia aparece no cartão', 'loja 3/3 no dia' in k and 'Check-list da loja completo no dia' in k)
t('pontuação do Impulso inalterada depois de marcar', pontuacao(ana) == antes)

print('Quem vê o espelho')
kb = kanban(cb)
t('colega de outra loja não vê o cartão da Ana', titulo_cartao not in kb)
ks = kanban(cs)
t('superuser vê, com o nome de quem marcou', titulo_cartao in ks and 'ZZRK ana' in ks)
t('filtro de colaborador recorta o espelho', titulo_cartao not in kanban(cs, colaborador=bia.id)
  and titulo_cartao in kanban(cs, colaborador=ana.id))
t('"Criadas por mim" esconde o espelho (não tem criador)', 'data-espelho-rotina-adm' not in kanban(ca, criadas_por_mim='1'))
mes_passado = (hoje.replace(day=1) - timedelta(days=1))
t('mês anterior não mostra o cartão de hoje', titulo_cartao not in kanban(ca, mes=f'{mes_passado:%Y-%m}'))
t('busca por nome recorta o espelho', titulo_cartao not in kanban(cs, q='ZZRK bia') and titulo_cartao in kanban(cs, q='ZZRK ana'))

print('Desmarcar')
marcar(ca, i3, False)
marcar(ca, i1, False)
k = kanban(ca)
t('desmarcar tira o item do cartão', '1 item marcado' in k and 'ZZ Abrir o caixa' not in k)
marcar(ca, i2, False)
k = kanban(ca)
t('sem itens, o cartão some', titulo_cartao not in k and 'data-espelho-rotina-adm' not in k)
marcar(ca, i2)
k = kanban(ca)
t('remarcar reabre o mesmo cartão (um só)', k.count('data-espelho-cartao=') == 1 and titulo_cartao in k)
marcar(ca, i2, False)
t('pontuação do Impulso igual à de antes no fim', pontuacao(ana) == antes)
t('ainda nenhuma Meta criada', Meta.objects.count() == metas_antes)

# ─────────────────────────────────────────────────────────────────────────────
print('Ranking dos analistas (mês anterior)')
inicio = mes_passado.replace(day=1)
fim = mes_passado
dias = [inicio + timedelta(days=n) for n in range((fim - inicio).days + 1)]
uteis = [d for d in dias if d.weekday() != 6]
domingo = next(d for d in dias if d.weekday() == 6)
antigo = timezone.make_aware(datetime.combine(inicio - timedelta(days=5), time(8)))
for it in (i1, i2, i3):
    it.horario = {i1.id: time(10), i2.id: None, i3.id: time(12)}[it.id]
    it.save()
s1 = ItemChecklist.objects.create(loja=serra, titulo='ZZ Serra 1', horario=time(10), ordem=1)
s2 = ItemChecklist.objects.create(loja=serra, titulo='ZZ Serra 2', horario=time(10), ordem=2)
c1 = ItemChecklist.objects.create(loja=centro, titulo='ZZ Centro 1', ordem=1)
ItemChecklist.objects.all().update(criado_em=antigo)


def marca(item, dia, quem, hora):
    m = MarcacaoChecklist.objects.create(item=item, data=dia, feito_por=quem)
    MarcacaoChecklist.objects.filter(pk=m.pk).update(feito_em=timezone.make_aware(datetime.combine(dia, hora)))


for d in uteis:
    for it in (i1, i2, i3):
        marca(it, d, ana, time(9))                         # tudo, no prazo
    marca(s1, d, bia, time(11))                             # metade, fora do prazo
for it in (i1, i2, i3):                                     # a Ana abriu a loja num domingo
    marca(it, domingo, ana, time(9))

dados = ranking.calcular(f'{inicio:%Y-%m}')
linhas = {l['pessoa'].id: l for l in dados['linhas']}
ordem = [l['pessoa'].id for l in dados['linhas']]
t('ordem: Ana, Bia, Duda (0%), Edu (sem check-list)', ordem == [ana.id, bia.id, duda.id, edu.id],
  [l['nome'] for l in dados['linhas']])
la, lb, ld, le = linhas[ana.id], linhas[bia.id], linhas[duda.id], linhas[edu.id]
t('Ana: 100% concluído e no prazo', la['pct'] == 100 and la['pct_prazo'] == 100, (la['pct'], la['pct_prazo']))
t('Ana: domingo trabalhado conta, domingo fechado não', la['previstos'] == 3 * (len(uteis) + 1) and la['dias_100'] == len(uteis) + 1,
  (la['previstos'], la['dias_100'], len(uteis)))
t('Bia: 50% e nada no prazo', lb['pct'] == 50 and lb['pct_prazo'] == 0 and lb['dias_100'] == 0, (lb['pct'], lb['pct_prazo']))
t('Duda: loja com item e nada marcado = 0%', ld['pct'] == 0 and ld['previstos'] == len(uteis) and ld['feitos'] == 0)
t('Edu: loja sem check-list vai para o fim', le['previstos'] == 0 and le['posicao'] == 4)
t('broche padrão (90% e 80%): só a Ana', la['elegivel'] and not lb['elegivel'] and dados['elegiveis'] == 1)
t('gestor não aparece (não é de loja e não marcou)', gestor.id not in linhas)

print('Itens previstos ao longo do mês')
x = ItemChecklist.objects.create(loja=norte, titulo='ZZ removido', ordem=1, ativo=False)
ItemChecklist.objects.filter(pk=x.pk).update(criado_em=antigo,
                                             atualizado_em=timezone.make_aware(datetime.combine(inicio + timedelta(days=9), time(8))))
prev = ranking.Previstos({norte.id})
t('item removido conta até a véspera da remoção', x.id in prev.do_dia(norte.id, inicio + timedelta(days=8))
  and x.id not in prev.do_dia(norte.id, inicio + timedelta(days=9)))
y = ItemChecklist.objects.create(loja=norte, titulo='ZZ novo', ordem=2)
ItemChecklist.objects.filter(pk=y.pk).update(criado_em=timezone.make_aware(datetime.combine(inicio + timedelta(days=5), time(15))))
prev = ranking.Previstos({norte.id})
t('item novo conta a partir do dia em que foi criado', y.id not in prev.do_dia(norte.id, inicio + timedelta(days=4))
  and y.id in prev.do_dia(norte.id, inicio + timedelta(days=5)))
ItemChecklist.objects.filter(pk__in=[x.pk, y.pk]).delete()
p_ini, p_fim, p_ate = ranking.periodo('')
t('mês atual conta até ontem', p_ini == hoje.replace(day=1) and p_ate == hoje - timedelta(days=1))
t('mês do futuro volta para o atual', ranking.periodo('2999-01')[0] == hoje.replace(day=1))

print('A tela antiga do ranking saiu (27/11/2026): a URL leva para a Missão Guardião')
r = ca.get('/rotina-adm/ranking/', {'mes': f'{inicio:%Y-%m}'})
t('/rotina-adm/ranking/ redireciona para os Guardiões em Destaque, levando o mês',
  r.status_code == 302 and r['Location'] == f'/rotina-adm/guardiao/destaque/?mes={inicio:%Y-%m}', (r.status_code, r.get('Location')))
h = ca.get('/rotina-adm/').content.decode()
t('Visualizador não vê mais a aba do ranking nem a de montar check-list', '/rotina-adm/ranking/' not in h
  and 'Ranking dos analistas' not in h and 'href="/rotina-adm/checklist/' not in h)
t('central do Gestor sem a aba do ranking e com a Missão Guardião',
  '/rotina-adm/ranking/' not in (hg := cg.get('/rotina-adm/').content.decode()) and 'href="/rotina-adm/guardiao/"' in hg)
t('POST na URL antiga não muda o broche', cs.post('/rotina-adm/ranking/', {'broche_pct_minimo': '10'}).status_code in (301, 302, 405)
  and ConfiguracaoRotinaAdm.get().broche_pct_minimo == 90)
ConfiguracaoRotinaAdm.objects.filter(pk=ConfiguracaoRotinaAdm.get().pk).update(broche_pct_minimo=40, broche_no_prazo_minimo=0)
dados = ranking.calcular(f'{inicio:%Y-%m}')
t('com 40% e 0% a Bia também fica elegível', [l['pessoa'].id for l in dados['linhas'] if l['elegivel']] == [ana.id, bia.id])
t('pontuação do Impulso igual mesmo com o mês anterior marcado', pontuacao(ana) == antes)

print('Copiar check-list substituindo não infla o ranking (achado da revisão)')
# O item da Centro existe há tempo (criado e editado pela última vez antes do mês passado).
ItemChecklist.objects.filter(pk=c1.pk).update(criado_em=antigo, atualizado_em=antigo)
for d in uteis[:3]:                                         # a Duda marca 3 dias do mês passado
    marca(c1, d, duda, time(9))
este_mes = [hoje.replace(day=n) for n in range(1, hoje.day) if hoje.replace(day=n).weekday() != 6]
for d in este_mes[:1]:                                      # e 1 dia deste mês, antes da cópia
    marca(c1, d, duda, time(9))


def da_duda(mes):
    linha = next(l for l in ranking.calcular(mes)['linhas'] if l['pessoa'].id == duda.id)
    return linha['pct'], linha['previstos'], linha['feitos'], linha['elegivel']


mes_ant, mes_atual = f'{inicio:%Y-%m}', f'{hoje:%Y-%m}'
antes_ant, antes_atual = da_duda(mes_ant), da_duda(mes_atual)
r = cg.post('/rotina-adm/checklist/', {'loja': centro.id, 'acao': 'copiar', 'origem': serra.id, 'substituir': 'on'})
c1.refresh_from_db()
t('a cópia pela tela substituiu o check-list da Centro',
  r.status_code == 302 and not c1.ativo
  and set(ItemChecklist.objects.filter(loja=centro, ativo=True).values_list('titulo', flat=True)) == {'ZZ Serra 1', 'ZZ Serra 2'})
t('o item substituído guarda o dia da remoção (hoje)', timezone.localtime(c1.atualizado_em).date() == hoje,
  c1.atualizado_em)
t('mês passado da Duda igual depois da cópia (sem virar 100% nem broche)', da_duda(mes_ant) == antes_ant,
  (antes_ant, da_duda(mes_ant)))
t('mês atual da Duda igual depois da cópia (dias antes dela seguem previstos)', da_duda(mes_atual) == antes_atual,
  (antes_atual, da_duda(mes_atual)))
t('a Duda continua fora do broche', not da_duda(mes_ant)[3] and da_duda(mes_ant)[0] < 40, da_duda(mes_ant))

# ─────────────────────────────────────────────────────────────────────────────
print('Pontos (24/11/2026): check-list, pendências fiscais, contagem de estoque e assiduidade')
from rotina_adm.models import ContagemEstoque  # noqa: E402
P = ranking.PONTOS
dados = ranking.calcular(mes_ant)
la = next(l for l in dados['linhas'] if l['pessoa'].id == ana.id)
lb = next(l for l in dados['linhas'] if l['pessoa'].id == bia.id)
itens_ana = 3 * (len(uteis) + 1)
t('check-list da Ana: +2 por item no prazo e +3 por dia 100%',
  la['pontos']['checklist'] == itens_ana * P['item_no_prazo'] + (len(uteis) + 1) * P['dia_100'], la['pontos'])
t('check-list da Bia: +1 por item fora do prazo, sem bônus', lb['pontos']['checklist'] == len(uteis) * P['item_atrasado'], lb['pontos'])
t('total = soma das frentes e ordem pelo total', la['total'] == sum(la['pontos'].values())
  and [l['total'] for l in dados['linhas']] == sorted((l['total'] for l in dados['linhas']), reverse=True))
t('nível pelo total', la['nivel']['nome'] == ranking.nivel(la['total'])['nome'] and ranking.nivel(0)['nome'] == 'Bronze'
  and ranking.nivel(500)['nome'] == 'Diamante' and ranking.nivel(160)['falta'] == 140)

print('  Contagem de estoque')
ContagemEstoque.objects.all().delete()
r = ca.post('/rotina-adm/estoque/registrar/', {'categoria': 'SMARTPHONES', 'divergencias': '2', 'observacao': 'faltam 2'})
t('analista registra a contagem da loja dele', r.status_code == 302
  and ContagemEstoque.objects.filter(loja=gloria, categoria='SMARTPHONES', feito_por=ana, divergencias=2).exists())
t('mensagem sem os pontos do ranking antigo', 'Contagem de Smartphones registrada.' in (hm := ca.get('/rotina-adm/').content.decode())
  and 'pontos no ranking' not in hm, hm[hm.find('Contagem de'):][:120])
ca.post('/rotina-adm/estoque/registrar/', {'categoria': 'SMARTPHONES', 'divergencias': '0'})
h = ca.get('/rotina-adm/').content.decode()
t('segunda contagem da mesma categoria na semana é registrada, mas avisa que já foi contada',
  ContagemEstoque.objects.filter(loja=gloria, categoria='SMARTPHONES').count() == 2 and 'já tinha sido contada' in h)
ca.post('/rotina-adm/estoque/registrar/', {'categoria': 'PELICULAS', 'divergencias': '0'})
r = ca.post('/rotina-adm/estoque/registrar/', {'categoria': 'XPTO', 'divergencias': '0'})
t('categoria inválida é recusada', not ContagemEstoque.objects.filter(categoria='XPTO').exists())
ca.post('/rotina-adm/estoque/registrar/', {'categoria': 'SIMCARD', 'divergencias': 'abc'})
t('divergência que não é número é recusada', not ContagemEstoque.objects.filter(categoria='SIMCARD').exists())
cb.post('/rotina-adm/estoque/registrar/', {'categoria': 'SIMCARD', 'divergencias': '0', 'loja': gloria.id})
t('analista de outra loja não registra na Glória (vai para a loja dele)',
  not ContagemEstoque.objects.filter(loja=gloria, categoria='SIMCARD').exists()
  and ContagemEstoque.objects.filter(loja=serra, categoria='SIMCARD', feito_por=bia).exists())
la = next(l for l in ranking.calcular(mes_atual)['linhas'] if l['pessoa'].id == ana.id)
t('estoque da Ana no mês: 2 categorias na semana = 20 pts', la['pontos']['estoque'] == 2 * P['estoque'], la['pontos'])
h = ca.get('/rotina-adm/').content.decode()
t('central mostra a situação real da categoria', 'Contada na semana' in h and '2 divergências' not in h
  and 'bateu tudo' in h and 'Nunca contada' in h)
semana_passada = hoje - timedelta(days=hoje.weekday() + 3)
ContagemEstoque.objects.create(loja=gloria, categoria='ELETRONICOS', data=semana_passada, feito_por=ana)
t('contada só na semana passada = pendente na semana',
  next(c for c in servicos.contagem_estoque(gloria, hoje) if c['chave'] == 'ELETRONICOS')['tom'] == 'amarelo')

print('  Pendências fiscais')
from auditoria_sap.models import TIPOS_DA_TELA, LinhaAuditoria, SincronizacaoAuditoria  # noqa: E402
t('"SEM NF" é tipo da tela da Visão SAP', 'SEM NF' in TIPOS_DA_TELA)
sinc = SincronizacaoAuditoria.objects.create(total=1)
SincronizacaoAuditoria.objects.filter(pk=sinc.pk).update(quando=timezone.now() - timedelta(hours=2), segundos=10)


def linha_semnf(n, quem, ativa=True, horas=1, tipo='SEM NF'):
    linha = LinhaAuditoria.objects.create(chave=f'zz{n}', tipo_erro=tipo, pdv='GLORIA', id_venda=f'V{n}', ativa=ativa,
                                          data_venda=hoje)
    LinhaAuditoria.objects.filter(pk=linha.pk).update(resolvida=True, resolvida_por=quem,
                                                      resolvida_em=timezone.now() - timedelta(hours=horas))
    return linha


linha_semnf(1, ana, ativa=False)            # saiu do SAP: conta
linha_semnf(2, ana, ativa=True, horas=1)    # marcada depois da última leitura: conta (aguardando a leitura)
linha_semnf(3, ana, ativa=True, horas=5)    # o SAP leu de novo e ela continua lá: não conta
linha_semnf(4, bia, ativa=False)
linha_semnf(5, ana, ativa=False, tipo='VALOR')  # não é "Sem NF": não conta
la = next(l for l in ranking.calcular(mes_atual)['linhas'] if l['pessoa'].id == ana.id)
t('fiscal da Ana: 2 resolvidas que contam, 1 ainda no SAP fora', la['pontos']['fiscal'] == 2 * P['fiscal']
  and la['detalhe']['resolvidas'] == 2 and la['detalhe']['nao_confirmadas'] == 1, la['detalhe'])
with mock.patch('auditoria_sap.models.inicio_da_ultima_leitura', side_effect=RuntimeError('fora')):
    dados_fora = ranking.calcular(mes_atual)
t('Visão SAP fora: ranking abre sem os pontos fiscais', dados_fora['fiscal_fora']
  and all(l['pontos']['fiscal'] == 0 for l in dados_fora['linhas']))

print('  Assiduidade')
t('faixas: 0% = 150, 2% = 100, 5% = 50, acima = 0, sem dado = 0',
  [ranking.pontos_assiduidade(x) for x in (0, 1.5, 2, 4.9, 5, 5.01, None)] == [150, 100, 100, 50, 50, 0, 0])
User.objects.filter(pk__in=[ana.pk, bia.pk]).update(tangerino_employee_id=None)
User.objects.filter(pk=ana.pk).update(tangerino_employee_id=901)
User.objects.filter(pk=bia.pk).update(tangerino_employee_id=902)
ana.refresh_from_db(); bia.refresh_from_db()
H8 = 8 * 3600


def linhas_ponto(inicio_p, fim_p, usuarios=None, buscar=True, **k):
    assert buscar is False, 'o ranking não pode mandar buscar no Tangerino'
    saida = []
    for u in usuarios:
        for n in range(20):
            falta = u.pk == bia.pk and n == 0
            saida.append({'usuario': u, 'previsto_segundos': H8, 'trabalhado_segundos': 0 if falta else H8,
                          'pendencias': ['Não houve nenhuma batida no dia'] if falta else []})
    return saida


from django.core.cache import cache as _cache  # noqa: E402
_cache.clear()  # LocMem deste teste
with mock.patch('tangerino.pendencias.linhas_do_periodo', side_effect=linhas_ponto) as chamado:
    dados = ranking.calcular(mes_ant)
    ranking.calcular(mes_ant)
t('ponto lido uma vez só (guardado)', chamado.call_count == 1, chamado.call_count)
la = next(l for l in dados['linhas'] if l['pessoa'].id == ana.id)
lb = next(l for l in dados['linhas'] if l['pessoa'].id == bia.id)
ld = next(l for l in dados['linhas'] if l['pessoa'].id == duda.id)
t('Ana sem ausência: +150', la['pontos']['assiduidade'] == 150, la['detalhe']['absenteismo'])
t('Bia com 1 falta em 20 dias (5%): +50', lb['pontos']['assiduidade'] == 50 and lb['detalhe']['absenteismo']['dias_sem_batida'] == 1,
  lb['detalhe']['absenteismo'])
t('Duda sem ponto no Tangerino: 0', ld['pontos']['assiduidade'] == 0 and not ld['detalhe']['bate_ponto'])
_cache.clear()
with mock.patch('tangerino.pendencias.linhas_do_periodo', side_effect=RuntimeError('fora')):
    dados = ranking.calcular(mes_ant)
t('ponto fora: ranking abre e a assiduidade fica 0', all(l['pontos']['assiduidade'] == 0 for l in dados['linhas']))
User.objects.filter(pk__in=[ana.pk, bia.pk]).update(tangerino_employee_id=None)
ana.refresh_from_db(); bia.refresh_from_db()
_cache.clear()

print('  Marcar o check-list (sem a bolha de pontos do ranking antigo)')
MarcacaoChecklist.objects.filter(data=hoje).delete()
ItemChecklist.objects.filter(pk__in=[i1.pk, i3.pk]).update(horario=time(23, 59))
r1 = marcar(ca, i1)
r2 = marcar(ca, i2)
r3 = marcar(ca, i3)
t('marcar responde feito, sem pontos', r1.get('ok') and r1.get('feito') and 'pontos' not in r1 and r3.get('feito'), r1)
h = ca.get('/rotina-adm/').content.decode()
t('central sem o placar e sem os "+pts" do ranking antigo', 'class="ra-meus-pontos' not in h and 'pts por' not in h
  and 'ra-ganha' not in h and 'href="/rotina-adm/guardiao/"' in h)
t('pontuação do Impulso igual com os pontos novos', pontuacao(ana) == antes)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
