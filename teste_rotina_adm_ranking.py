"""/rotina-adm/ (12/11/2026): Renova compacta, espelho do check-list no Kanban do Impulso e ranking dos analistas.

Cobre:
- Renova da central: cartões só com os números; as listas recentes ficam nos modais "Ver detalhado";
  links seguem filtrados pela loja;
- marcar item do check-list mostra o cartão concluído "Rotina ADM · Check-list da Loja X · dd/mm" no
  Kanban do Impulso de quem marcou; remarcar não duplica; desmarcar tira o item e, sem itens, o cartão;
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
from rotina_adm import ranking
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
t('CSS com versão nova', 'rotina_adm.css?v=5' in h)
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
t('marcar cria o cartão concluído no Kanban de quem marcou', titulo_cartao in k and 'data-espelho-rotina-adm' in k)
pos_concluida = k.find('data-status="CONCLUIDA"')
t('o cartão fica na coluna Concluída', 0 < pos_concluida < k.find(titulo_cartao), (pos_concluida, k.find(titulo_cartao)))
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

print('Tela do ranking e acesso')
t('Visualizador não abre o ranking', ca.get('/rotina-adm/ranking/').status_code == 302)
r = cg.get('/rotina-adm/ranking/', {'mes': f'{inicio:%Y-%m}'})
h = r.content.decode()
t('Gestor abre o ranking', r.status_code == 200, r.status_code)
t('mostra o critério e quem é elegível', 'pelo menos 90%' in h and 'pelo menos 80%' in h and 'Elegível' in h)
t('Gestor vê o critério mas não o formulário', 'name="broche_pct_minimo"' not in h and 'ajustado pelo SUPERADMIN' in h)
t('a Ana vem em primeiro na tela', h.find('ZZRK ana') < h.find('ZZRK bia') < h.find('ZZRK duda') < h.find('ZZRK edu'))
t('aba do ranking no cabeçalho do módulo', 'href="/rotina-adm/ranking/"' in h and 'Ranking dos analistas' in h)
t('aba também na central do Gestor', 'href="/rotina-adm/ranking/"' in cg.get('/rotina-adm/').content.decode())
r = cg.post('/rotina-adm/ranking/', {'mes': f'{inicio:%Y-%m}', 'broche_pct_minimo': '10', 'broche_no_prazo_minimo': '0'})
t('Gestor não muda o critério', ConfiguracaoRotinaAdm.get().broche_pct_minimo == 90)
r = cs.post('/rotina-adm/ranking/', {'mes': f'{inicio:%Y-%m}', 'broche_pct_minimo': '150', 'broche_no_prazo_minimo': '0'})
t('valor acima de 100 é recusado', ConfiguracaoRotinaAdm.get().broche_pct_minimo == 90)
r = cs.post('/rotina-adm/ranking/', {'mes': f'{inicio:%Y-%m}', 'broche_pct_minimo': '40', 'broche_no_prazo_minimo': '0'})
cfg = ConfiguracaoRotinaAdm.get()
t('SUPERADMIN muda o critério', r.status_code == 302 and cfg.broche_pct_minimo == 40 and cfg.broche_no_prazo_minimo == 0)
dados = ranking.calcular(f'{inicio:%Y-%m}')
t('com 40% e 0% a Bia também fica elegível', [l['pessoa'].id for l in dados['linhas'] if l['elegivel']] == [ana.id, bia.id])
h = cs.get('/rotina-adm/ranking/', {'mes': f'{inicio:%Y-%m}'}).content.decode()
t('SUPERADMIN vê o formulário', 'name="broche_pct_minimo"' in h and 'value="40"' in h)
t('ranking do mês atual abre (dia de hoje não conta)', cs.get('/rotina-adm/ranking/').status_code == 200)
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

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
