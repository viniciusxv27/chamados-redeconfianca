"""/rotina-adm/ (30/10/2026): central do ADM de loja.

Cobre:
- só SUPERADMIN entra de saída; ele libera grupo ou pessoa como Visualizador ou Gestor
  (Gestor vale mais que Visualizador; a tela de acessos é só dele);
- Visualizador vê só a loja do setor principal (ignora ?loja=) e marca o check-list dela;
  Gestor escolhe a loja e monta/ordena/remove/copia o check-list;
- chamados em aberto = abertos por gente da loja e não resolvidos;
- pendências fiscais = linhas "Sem NF" abertas da loja na Visão SAP (grafia do SAP casada);
- agenda = Rotina Gerencial da pessoa, com o que foi concluído na semana;
- menu só para quem tem acesso.

Roda num sqlite descartável; nada sai para a rede.
"""
import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

# Banco e arquivos descartáveis: a tabela das entregas e a coluna
# `exige_entrega` só existem no Postgres compartilhado depois do migrate, e o
# migrate é dele. O schema aqui sai direto dos models (`--run-syncdb`), como em
# qualquer outra sessão — nada toca o banco de verdade nem o MinIO.
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-rotina-adm-'))
(PASTA / 'zz_rotina_adm_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-radm'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-radm-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_rotina_adm_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

import json
from datetime import date, time, timedelta

import requests
from django.utils import timezone

from auditoria_sap.models import LinhaAuditoria
from communications.models import CommunicationGroup
from rotina.models import AtividadeRotina, ConclusaoAtividade, RotinaGerencial
from rotina_adm.models import AcessoRotinaAdm, ItemChecklist, MarcacaoChecklist
from rotina_adm import servicos
from tickets.models import Ticket
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
escritorio = Sector.objects.create(name='Financeiro')


def usuario(nome, hierarquia='PADRAO', setor=gloria, **extra):
    u = User.objects.create_user(username=f'zzra.{nome}', email=f'zzra.{nome}@exemplo-teste.local', password='S3nha!teste',
                                 first_name='ZZRA', last_name=nome, sector=setor, hierarchy=hierarquia, **extra)
    c = Client(); c.force_login(u)
    return u, c


chefe, cs = usuario('super', 'SUPERADMIN', escritorio)
adm, ca = usuario('adm')
gestor, cg = usuario('gestor', setor=escritorio)
nada, cn = usuario('nada')
vendedor, _ = usuario('vendedor')
outro, _ = usuario('outro', setor=serra)

print('Acesso')
t('PADRÃO sem liberação volta para a home', cn.get('/rotina-adm/').status_code == 302)
t('e não vê o item no menu', 'href="/rotina-adm/"' not in cn.get('/', follow=True).content.decode())
r = cs.get('/rotina-adm/')
t('SUPERADMIN entra (abre a primeira loja, já que é do escritório)', r.status_code == 200 and 'Loja Glória' in r.content.decode(), r.status_code)
t('SUPERADMIN vê o item no menu e as abas', 'href="/rotina-adm/"' in r.content.decode() and 'Check-list das lojas' in r.content.decode())

grupo = CommunicationGroup.objects.create(name='ZZ ADMs de loja', created_by=chefe)
grupo.members.add(adm, gestor)
r = cs.post('/rotina-adm/acessos/', {'acao': 'adicionar', 'alvo': f'g:{grupo.id}', 'papel': 'VISUALIZADOR'})
t('SUPERADMIN libera um grupo como Visualizador', r.status_code == 302 and AcessoRotinaAdm.objects.filter(grupo=grupo, papel='VISUALIZADOR').exists())
r = cs.post('/rotina-adm/acessos/', {'acao': 'adicionar', 'alvo': f'u:{gestor.id}', 'papel': 'GESTOR'})
t('e uma pessoa como Gestor', AcessoRotinaAdm.objects.filter(user=gestor, papel='GESTOR').exists())
t('acessos: só o SUPERADMIN', cg.get('/rotina-adm/acessos/').status_code == 302 and ca.get('/rotina-adm/acessos/').status_code == 302)
h = cs.get('/rotina-adm/acessos/').content.decode()
t('a tela de acessos lista os dois', 'ZZ ADMs de loja' in h and 'ZZRA gestor' in h)

r = ca.get('/rotina-adm/', {'loja': serra.id})
h = r.content.decode()
t('Visualizador entra pela liberação do grupo', r.status_code == 200)
t('Visualizador fica na loja dele mesmo pedindo outra', 'Central operacional da loja · Loja Glória' in h, h[h.find('ra-titulo'):h.find('ra-titulo') + 300])
t('Visualizador não tem as abas de gestão', 'Check-list das lojas' not in h)
t('Visualizador não monta check-list', ca.get('/rotina-adm/checklist/').status_code == 302)
r = cg.get('/rotina-adm/', {'loja': serra.id})
t('Gestor (pessoa) vence o Visualizador (grupo) e escolhe a loja', r.status_code == 200
  and 'Central operacional da loja · Loja Serra Sede' in r.content.decode())

print('Chamados em aberto')
Ticket.objects.create(title='ZZ impressora', description='x', sector=escritorio, created_by=vendedor, status='ABERTO')
Ticket.objects.create(title='ZZ ar', description='x', sector=escritorio, created_by=adm, status='EM_ANDAMENTO')
Ticket.objects.create(title='ZZ resolvido', description='x', sector=escritorio, created_by=adm, status='RESOLVIDO')
Ticket.objects.create(title='ZZ rejeitado', description='x', sector=escritorio, created_by=adm, status='REJEITADO')
Ticket.objects.create(title='ZZ outra loja', description='x', sector=escritorio, created_by=outro, status='ABERTO')
t('conta só os abertos por gente da loja', servicos.chamados_abertos(gloria).count() == 2)
h = ca.get('/rotina-adm/').content.decode()
t('o cartão mostra 2 e a lista traz os títulos', '<p class="ra-numero">2</p>' in h and 'ZZ impressora' in h and 'ZZ ar' in h
  and 'ZZ resolvido' not in h and 'ZZ outra loja' not in h)

print('Pendências fiscais (Visão SAP)')
hoje = timezone.localdate()
base = dict(data_venda=hoje, ativa=True)
LinhaAuditoria.objects.create(pdv='GLÓRIA', tipo_erro='SEM NF', chave='zz1', **base)
LinhaAuditoria.objects.create(pdv='GLÓRIA', tipo_erro='SEM NF', chave='zz2', data_venda=date(2025, 1, 5), ativa=True)
LinhaAuditoria.objects.create(pdv='GLÓRIA', tipo_erro='SEM NF', chave='zz3', resolvida=True, **base)
LinhaAuditoria.objects.create(pdv='GLÓRIA', tipo_erro='SEM NF', chave='zz4', data_venda=hoje, ativa=False)
LinhaAuditoria.objects.create(pdv='GLÓRIA', tipo_erro='SERIAL ERRADO NO VIVO GO', chave='zz5', **base)
LinhaAuditoria.objects.create(pdv='SERRA SEDE', tipo_erro='SEM NF', chave='zz6', **base)
f = servicos.pendencias_fiscais(gloria)
t('"Loja Glória" casa com o PDV "GLÓRIA" do SAP', f['pdvs'] == ['GLÓRIA'], f)
t('conta só Sem NF aberta e ainda no SAP (2, 1 deste mês)', f['total'] == 2 and f['mes'] == 1, f)
h = ca.get('/rotina-adm/').content.decode()
t('o link abre a Visão SAP na aba Sem NF da loja', '/sap/?grupo=sem_nf&mes=todos&loja=GL%C3%93RIA' in h, h[h.find('/sap/'):h.find('/sap/') + 80])

print('Renova da loja (/renova/ e /sap/renova/)')
from auditoria_sap.models import VendaRenova  # noqa: E402
from renova import checklist as rn_checklist  # noqa: E402
from renova.models import Renova  # noqa: E402
from tickets.models import Ticket  # noqa: E402


def avaliacao(loja, **kw):
    dados = dict(criado_por=adm, marca='APPLE', modelo='iPhone 13', armazenamento='128GB', imei1='356938035643809',
                 loja=loja, parecer=rn_checklist.APROVADO, vendedor_nome='ZZ Vendedor Renova')
    dados.update(kw)
    return Renova.objects.create(**dados)


r_aprovar = avaliacao(gloria, aprovacao=Renova.AGUARDANDO_GERENTE)
r_chegando = avaliacao(gloria, aprovacao=Renova.APROVADA, recebimento=Renova.PENDENTE)
r_chegou = avaliacao(gloria, aprovacao=Renova.APROVADA, recebimento=Renova.CHEGOU)
r_nao = avaliacao(gloria, aprovacao=Renova.APROVADA, recebimento=Renova.NAO_CHEGOU)
r_serra = avaliacao(serra, aprovacao=Renova.AGUARDANDO_GERENTE, modelo='Galaxy Serra')
rn = servicos.renova_da_loja(gloria)
t('Renova: números só da loja', rn['disponivel'] and rn['no_mes'] == 4 and rn['a_aprovar'] == 1 and rn['aguardando'] == 1
  and rn['nao_chegaram'] == 1, rn)
sit = {i['obj'].pk: i['situacao'] for i in rn['recentes']}
t('Renova: situação de cada avaliação', sit == {r_aprovar.pk: 'Aguardando gerente', r_chegando.pk: 'Aguardando chegada',
                                                 r_chegou.pk: 'Chegou', r_nao.pk: 'Não chegou'}, sit)
tk = Ticket.objects.create(title='ZZ Renova', description='x', sector=escritorio, created_by=adm)
venda = dict(setor=gloria, loja='GLÓRIA', ativa=True)
VendaRenova.objects.create(ordem='9000001', cliente_nome='ZZ Cliente Sem', data_fatura=hoje, material='IPHONE 15',
                           valor_faturado=4999, **venda)
VendaRenova.objects.create(ordem='9000002', cliente_nome='ZZ Cliente Antigo', data_fatura=date(2025, 1, 5), **venda)
VendaRenova.objects.create(ordem='9000003', cliente_nome='ZZ Com Chamado', data_fatura=hoje, chamado=tk, **venda)
VendaRenova.objects.create(ordem='9000004', cliente_nome='ZZ Dispensada', data_fatura=hoje, dispensada=True, **venda)
VendaRenova.objects.create(ordem='9000005', cliente_nome='ZZ Saiu do SAP', data_fatura=hoje, setor=gloria, ativa=False)
VendaRenova.objects.create(ordem='9000006', cliente_nome='ZZ Outra Loja', data_fatura=hoje, setor=serra, ativa=True)
sr = servicos.sap_renova_da_loja(gloria)
t('SAP: sem chamado (2), no mês (1); do mês, 1 de 3 com chamado', sr['disponivel'] and sr['sem_chamado'] == 2
  and sr['sem_chamado_mes'] == 1 and sr['mes_total'] == 3 and sr['mes_com_chamado'] == 1, sr)
t('SAP: lista só as sem chamado da loja, mais recentes primeiro', [v.ordem for v in sr['recentes']] == ['9000001', '9000002'])
h = ca.get('/rotina-adm/').content.decode()
t('painel: as duas seções da loja', 'id="raRenova"' in h and 'id="raSapRenova"' in h and r_aprovar.codigo in h
  and 'Galaxy Serra' not in h and 'ZZ Cliente Sem' in h and 'ZZ Com Chamado' not in h and 'ZZ Outra Loja' not in h)
t('painel: links já filtrados pela loja', f'/renova/?loja={gloria.id}' in h and f'/sap/renova/?loja={gloria.id}' in h
  and f'/renova/{r_aprovar.pk}/' in h and f'/sap/renova/?loja={gloria.id}&q=9000001' in h)
t('painel: valor em reais', 'R$ 4.999,00' in h, h[h.find('9000001'):h.find('9000001') + 300])
h = ca.get('/rotina-adm/', {'loja': serra.id}).content.decode()
h2 = cs.get('/rotina-adm/', {'loja': serra.id}).content.decode()
t('SUPERADMIN troca a loja e vê a Renova dela', 'Galaxy Serra' in h2 and 'ZZ Outra Loja' in h2 and 'ZZ Cliente Sem' not in h2)
t('ADM não escolhe outra loja (continua a dele)', 'Galaxy Serra' not in h)
Renova.objects.all().delete()
VendaRenova.objects.all().delete()
h = cs.get('/rotina-adm/', {'loja': gloria.id}).content.decode()
t('loja sem nada: avisos de vazio', 'Nenhuma avaliação feita na loja' in h and 'Nenhuma venda com Renova sem chamado' in h)

print('Check-list')
r = cg.post('/rotina-adm/checklist/', {'loja': gloria.id, 'acao': 'adicionar', 'titulo': 'Abertura da loja', 'horario': '08:15'})
cg.post('/rotina-adm/checklist/', {'loja': gloria.id, 'acao': 'adicionar', 'titulo': 'Conferência de estoque', 'horario': ''})
cg.post('/rotina-adm/checklist/', {'loja': gloria.id, 'acao': 'adicionar', 'titulo': 'Limpeza da área de vendas'})
itens = list(ItemChecklist.objects.filter(loja=gloria, ativo=True))
t('Gestor adiciona itens na loja escolhida', [i.titulo for i in itens] == ['Abertura da loja', 'Conferência de estoque', 'Limpeza da área de vendas']
  and itens[0].horario == time(8, 15) and itens[1].horario is None)
cg.post('/rotina-adm/checklist/', {'loja': gloria.id, 'acao': 'descer', 'item': itens[0].id})
t('reordena', [i.titulo for i in ItemChecklist.objects.filter(loja=gloria, ativo=True)][:2] == ['Conferência de estoque', 'Abertura da loja'])
cg.post('/rotina-adm/checklist/', {'loja': gloria.id, 'acao': 'salvar', 'item': itens[2].id, 'titulo': 'Limpeza e organização', 'horario': '09:00'})
t('edita', ItemChecklist.objects.get(pk=itens[2].id).titulo == 'Limpeza e organização')

r = ca.post(f'/rotina-adm/checklist/{itens[0].id}/marcar/', json.dumps({'feito': True}), content_type='application/json')
t('ADM marca o item da loja dele', r.json().get('ok') and MarcacaoChecklist.objects.filter(item=itens[0], data=hoje, feito_por=adm).exists(), r.content)
r = ca.post(f'/rotina-adm/checklist/{itens[0].id}/marcar/', json.dumps({'feito': True}), content_type='application/json')
t('marcar de novo não duplica', MarcacaoChecklist.objects.filter(item=itens[0]).count() == 1)
h = ca.get('/rotina-adm/').content.decode()
t('a central mostra 1 de 3 e quem fez', '1 de 3' in h and 'por ZZRA adm' in h)
r = ca.post(f'/rotina-adm/checklist/{itens[0].id}/marcar/', json.dumps({'feito': False}), content_type='application/json')
t('desmarca', r.json().get('ok') and not MarcacaoChecklist.objects.filter(item=itens[0]).exists())

ItemChecklist.objects.create(loja=serra, titulo='Item da Serra')
r = ca.post(f'/rotina-adm/checklist/{ItemChecklist.objects.get(titulo="Item da Serra").id}/marcar/', json.dumps({'feito': True}),
            content_type='application/json')
t('Visualizador não marca o check-list de outra loja', r.status_code == 403)

r = cg.post('/rotina-adm/checklist/', {'loja': serra.id, 'acao': 'copiar', 'origem': gloria.id})
t('copia da Glória para a Serra somando', sorted(ItemChecklist.objects.filter(loja=serra, ativo=True).values_list('titulo', flat=True))
  == sorted(['Item da Serra', 'Abertura da loja', 'Conferência de estoque', 'Limpeza e organização']))
cg.post('/rotina-adm/checklist/', {'loja': serra.id, 'acao': 'copiar', 'origem': gloria.id})
t('copiar de novo não duplica', ItemChecklist.objects.filter(loja=serra, ativo=True).count() == 4)
cg.post('/rotina-adm/checklist/', {'loja': serra.id, 'acao': 'copiar', 'origem': gloria.id, 'substituir': 'on'})
t('"substituir" troca a lista (o item antigo vira inativo)', ItemChecklist.objects.filter(loja=serra, ativo=True).count() == 3
  and ItemChecklist.objects.filter(loja=serra, titulo='Item da Serra', ativo=False).exists())
cg.post('/rotina-adm/checklist/', {'loja': gloria.id, 'acao': 'remover', 'item': itens[1].id})
t('remover tira da lista', ItemChecklist.objects.filter(loja=gloria, ativo=True).count() == 2)
h = cg.get('/rotina-adm/checklist/', {'loja': gloria.id}).content.decode()
t('tela de montar abre com as lojas e a contagem', 'Copiar de outra loja' in h and 'Loja Serra Sede' in h and '3 itens' in h)

print('Agenda (Rotina Gerencial)')
segunda = hoje - timedelta(days=hoje.weekday())
rotina = RotinaGerencial.objects.create(user=adm)
a1 = AtividadeRotina.objects.create(rotina=rotina, dia_semana=0, inicio=time(9), fim=time(10), titulo='Contagem de estoque LVUT')
AtividadeRotina.objects.create(rotina=rotina, dia_semana=2, inicio=time(14), fim=time(15), titulo='Organização da loja')
ConclusaoAtividade.objects.create(atividade=a1, user=adm, data=segunda)
ag = servicos.agenda_da_semana(adm, segunda)
t('agenda traz as atividades por dia e a conclusão', ag and ag['total'] == 2 and ag['feitas'] == 1
  and ag['dias'][0]['itens'][0]['feita'] and ag['dias'][2]['itens'][0]['atividade'].titulo == 'Organização da loja')
h = ca.get('/rotina-adm/').content.decode()
t('a central mostra a agenda da pessoa', 'Contagem de estoque LVUT' in h and 'Organização da loja' in h and '1 de 2 concluída' in h)
import re as _re  # noqa: E402
_n_hoje = min(hoje.weekday(), len(ag['dias']) - 1)
t('agenda abre no dia de hoje (11/11)', ag['foco'] == (hoje.weekday() if hoje.weekday() < len(ag['dias']) else 0)
  and f'class="ra-dia hoje foco" data-dia="{hoje.weekday()}"' in h if hoje.weekday() < len(ag['dias']) else ag['foco'] == 0,
  ag['foco'])
t('agenda: botões dos dias e "Ver semana"', 'id="raAgendaExpandir"' in h and 'Ver semana' in h
  and len(_re.findall(r'role="tab" data-dia=', h)) == len(ag['dias']))
t('as atividades dos outros dias continuam na página (para expandir)', 'Organização da loja' in h)
h = ca.get('/rotina-adm/', {'semana': (segunda + timedelta(days=7)).isoformat()}).content.decode()
t('navega para a próxima semana (nada concluído ainda)', '0 de 2 concluída' in h)
t('outra semana abre no primeiro dia', 'class="ra-dia  foco" data-dia="0"' in h or 'class="ra-dia foco" data-dia="0"' in h)
t('sem Rotina Gerencial: aviso', servicos.agenda_da_semana(gestor, segunda) is None
  and 'ainda não tem Rotina Gerencial' in cg.get('/rotina-adm/').content.decode())
h = cg.get('/rotina-adm/', {'pessoa': adm.id}).content.decode()
t('Gestor olha a agenda de outra pessoa', 'Contagem de estoque LVUT' in h and 'de ZZRA adm' in h)
h = ca.get('/rotina-adm/', {'pessoa': gestor.id}).content.decode()
t('Visualizador não troca a pessoa', 'Contagem de estoque LVUT' in h)

print('LVUT e remoção de acesso')
t('LVUT marcada como exemplo', 'Dados de exemplo' in h)
t('LVUT com as 4 categorias (09/11)', all(n in h for n in ('Smartphones', 'Eletrônicos e Essenciais',
                                                         'SIM Card e Cartão de Recarga', 'Películas'))
  and 'Acessórios' not in h and '>Chips<' not in h)
t('sem "Informar ocorrência"', 'Informar ocorrência' not in h)
t('grupo sem link: aviso apagado para quem não é SUPERADMIN', 'Grupo dos ADMs no WhatsApp' in h
  and 'ainda não foi configurado' in h and 'chat.whatsapp.com' not in h)
t('SUPERADMIN sem link: atalho para configurar', 'Cole o link do grupo' in cs.get('/rotina-adm/').content.decode())
cs.post('/rotina-adm/acessos/', {'acao': 'link_grupo', 'link_grupo_whatsapp': 'chat.whatsapp.com/abc'})
from rotina_adm.models import ConfiguracaoRotinaAdm
t('link sem https é recusado', ConfiguracaoRotinaAdm.get().link_grupo_whatsapp == '')
cg.post('/rotina-adm/acessos/', {'acao': 'link_grupo', 'link_grupo_whatsapp': 'https://chat.whatsapp.com/Gestor'})
t('só o SUPERADMIN configura o link', ConfiguracaoRotinaAdm.get().link_grupo_whatsapp == '')
cs.post('/rotina-adm/acessos/', {'acao': 'link_grupo', 'link_grupo_whatsapp': 'https://chat.whatsapp.com/ZZgrupoAdm'})
h = ca.get('/rotina-adm/').content.decode()
t('com link: o Visualizador abre o grupo em nova aba', 'href="https://chat.whatsapp.com/ZZgrupoAdm" target="_blank"' in h, h.count('ZZgrupoAdm'))
t('acessos mostram o link salvo', 'value="https://chat.whatsapp.com/ZZgrupoAdm"' in cs.get('/rotina-adm/acessos/').content.decode())
cs.post('/rotina-adm/acessos/', {'acao': 'link_grupo', 'link_grupo_whatsapp': ''})
t('apagar o link', ConfiguracaoRotinaAdm.get().link_grupo_whatsapp == '')
acesso = AcessoRotinaAdm.objects.get(grupo=grupo)
cs.post('/rotina-adm/acessos/', {'acao': 'remover', 'acesso': acesso.id})
adm2 = User.objects.get(pk=adm.pk); c2 = Client(); c2.force_login(adm2)
t('tirar o grupo tira o acesso', c2.get('/rotina-adm/').status_code == 302)

print(f'\n{ok} OK, {fail} falha(s)')
