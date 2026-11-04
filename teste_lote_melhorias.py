"""Lote de melhorias (04/11/2026): comissão, grupos, documentos, metas, Inovar, HC, chamados, Mercadinho, Acess, caixa. Banco descartável; nada sai para a rede."""
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
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-lote-'))
(PASTA / 'zz_lote_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-iacomp'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-iacomp-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_lote_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

import datetime as dt
from decimal import Decimal as D
from unittest.mock import patch

import requests
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from acess import servicos as acess_sv
from acess.models import Colaborador, PedidoDesligamento
from communications.models import CommunicationGroup, MembroPeloCargo
from contagem_caixa import servicos as caixa_sv
from contagem_caixa.models import CategoriaSangria, ConfiguracaoContagem, ContagemCaixaDia, Sangria
from contagem_caixa.sangrias import resumo, sincronizar_dia
from impulso.models import Ideia, IdeiaAnexo, Meta
from impulso.views import _por_entrega_e_prioridade
from prizes.models import SugestaoProduto
from quadro_hc.models import AcessoQuadroHC
from tickets.models import Ticket, TicketAttachment
from users.models import Sector, UserChangeLog, UserDocument, inferir_categoria_documento

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

def novo(u, **k):
    k.setdefault('hierarchy', 'PADRAO')
    return User.objects.create_user(username=f'zzl.{u}', email=f'zzl.{u}@exemplo-teste.local', password='S3nha!teste', first_name=u.title(), last_name='ZZ', **k)


def cli(u):
    c = Client(); c.force_login(u); return c


loja = Sector.objects.create(name='Loja ZZ')
rh = Sector.objects.create(name='RH')
chefe = novo('chefe', hierarchy='SUPERADMIN', is_superuser=True)
adm = novo('adm', hierarchy='ADMINISTRATIVO', sector=loja)
pessoa_rh = novo('rh', hierarchy='ADMINISTRATIVO', sector=rh)
ana = novo('ana', sector=loja)
gestor = novo('gestor', sector=loja)
cs, ca, cr, cn = cli(chefe), cli(adm), cli(pessoa_rh), cli(ana)
for nome_grupo, membros in (('ESCRITÓRIO (ADM)', [ana, gestor]), ('GESTORES (IMPULSO)', [gestor])):
    g, _ = CommunicationGroup.objects.get_or_create(name=nome_grupo, defaults={'created_by': chefe})
    g.members.add(*membros)
cg = cli(gestor)

print('comissão')
from django.test import RequestFactory
from django.contrib.messages.storage.cookie import CookieStorage
from users import commission_views as cv


class S(dict):
    modified = False
    session_key = None

    def save(self):
        pass


r = RequestFactory().get('/users/commission/', {'month': '9', 'year': '2026'}, SERVER_NAME='localhost')
r.user, r.session = ana, S()
r._messages = CookieStorage(r)
with patch.object(cv, 'fetch_excel_data', return_value={'success': False, 'error': 'x'}), patch.object(cv, 'fetch_vendas_data', return_value={'success': False}):
    h = cv.commission_cn_view(r).content.decode()
t('CN vê a mesma faixa da visão do superadmin', 'data-faixa-pessoa' in h and 'Seus dados' in h and 'Ana ZZ' in h and 'Consultor' in h)
t('sem o texto de quem olha outra pessoa', 'Visualizando dados de' not in h)

print('grupos na edição')
grupo, _ = CommunicationGroup.objects.get_or_create(name='GRUPO ZZ', defaults={'created_by': chefe})
outro = CommunicationGroup.objects.create(name='OUTRO ZZ', created_by=chefe)
U = f'/users/manage/users/{ana.id}'
grupo.members.add(ana)
h = cs.get(f'{U}/edit/').content.decode()
t('mostra os grupos da pessoa', 'data-grupo="%d"' % grupo.id in h and 'OUTRO ZZ' in h, )
rr = cs.post(f'{U}/grupos/', {'acao': 'adicionar', 'grupo': outro.id})
t('adiciona', rr.json()['ok'] and outro.members.filter(id=ana.id).exists())
rr = cs.post(f'{U}/grupos/', {'acao': 'remover', 'grupo': outro.id})
t('remove', rr.json()['ok'] and not outro.members.filter(id=ana.id).exists())
t('fica no histórico do cadastro', UserChangeLog.objects.filter(target=ana, field='grupos').count() == 2)
t('quem não edita cadastro não mexe', cn.post(f'{U}/grupos/', {'acao': 'adicionar', 'grupo': outro.id}).status_code == 403 and not outro.members.filter(id=ana.id).exists())
t('ADMINISTRAÇÃO não mexe em quem está acima', ca.post(f'/users/manage/users/{chefe.id}/grupos/', {'acao': 'adicionar', 'grupo': outro.id}).status_code == 403)
t('ação inválida', cs.post(f'{U}/grupos/', {'acao': 'x', 'grupo': outro.id}).status_code == 400)
from communications.cargos import sincronizar_usuario
from communications.models import CargoDoGrupo
CargoDoGrupo.objects.create(grupo=outro, cargo='VENDEDOR ZZ')
User.objects.filter(id=ana.id).update(job_title='VENDEDOR ZZ'); ana.refresh_from_db()
sincronizar_usuario(ana)
t('entrou pelo cargo', outro.members.filter(id=ana.id).exists() and MembroPeloCargo.objects.filter(grupo=outro, user=ana).exists())
t('tela marca "pelo cargo"', 'pelo cargo' in cs.get(f'{U}/edit/').content.decode())
cs.post(f'{U}/grupos/', {'acao': 'remover', 'grupo': outro.id})
sincronizar_usuario(ana)
t('tirar à mão: o cargo não põe de volta', not outro.members.filter(id=ana.id).exists())
cs.post(f'{U}/grupos/', {'acao': 'adicionar', 'grupo': outro.id})
t('posto à mão deixa de ser "pelo cargo"', not MembroPeloCargo.objects.filter(grupo=outro, user=ana).exists())
outro.members.remove(ana); CargoDoGrupo.objects.filter(grupo=outro).delete(); User.objects.filter(id=ana.id).update(job_title=''); ana.refresh_from_db()

print('documentos')
for nome_doc, esperado in [('RG', 'PESSOAL'), ('Cargo atual', 'OUTROS'), ('Comprovante de residência', 'PESSOAL'), ('Contrato de trabalho', 'ADMISSIONAL'),
                           ('Atestado médico', 'ATESTADO'), ('Declaração de horas', 'DECLARACAO'), ('Foto 3x4', 'PESSOAL'), ('Coisa qualquer', 'OUTROS')]:
    t(f'"{nome_doc}" → {esperado}', inferir_categoria_documento(nome_doc) == esperado, inferir_categoria_documento(nome_doc))
arq = lambda n: SimpleUploadedFile(n, b'%PDF-1.4 x', 'application/pdf')
for nome_doc, categoria in (('Atestado 1', ''), ('RG Ana', ''), ('Arquivo livre', 'DECLARACAO')):
    cs.post(f'{U}/documents/upload/', {'document_name': nome_doc, 'categoria': categoria, 'file': arq(f'{nome_doc}.pdf')})
atestado = UserDocument.objects.get(document_name='Atestado 1')
t('categoria escolhida vale', UserDocument.objects.get(document_name='Arquivo livre').categoria_efetiva == 'DECLARACAO')
t('atestado reconhecido pelo nome fica restrito', atestado.restrito)
P = f'{U}/profile/'
hs, hr, ha = cs.get(P).content.decode(), cr.get(P).content.decode(), ca.get(P).content.decode()
t('SUPERADMIN vê o atestado', 'Atestado 1' in hs)
t('RH vê o atestado', 'Atestado 1' in hr, cr.get(P).status_code)
t('outro gestor não vê o atestado, e sabe que existe', 'Atestado 1' not in ha and 'data-docs-restritos' in ha and 'RG Ana' in ha)
t('tela em seções por categoria', 'Documentos Pessoais' in hs and 'Documentos Admissionais' in hs and 'Atestados' in hs and 'Declarações' in hs and 'Outros Documentos' in hs)
t('outro gestor não apaga atestado', ca.post(f'/users/manage/user-documents/{atestado.id}/delete/').status_code == 302 and UserDocument.objects.filter(id=atestado.id).exists())
t('outro gestor não tira da categoria restrita', ca.post(f'/users/manage/user-documents/{atestado.id}/categoria/', {'categoria': 'OUTROS'}).status_code == 403)
rg = UserDocument.objects.get(document_name='RG Ana')
t('nem move documento para a restrita', ca.post(f'/users/manage/user-documents/{rg.id}/categoria/', {'categoria': 'ATESTADO'}).status_code == 403)
t('RH reclassifica', cr.post(f'/users/manage/user-documents/{rg.id}/categoria/', {'categoria': 'ADMISSIONAL'}).json()['ok'] and UserDocument.objects.get(id=rg.id).categoria == 'ADMISSIONAL')
import io
import zipfile
zr = ca.get(f'{U}/documents/download-all/')
nomes = zipfile.ZipFile(io.BytesIO(b''.join(zr.streaming_content) if zr.streaming else zr.content)).namelist()
t('zip de outro gestor sem atestado', not any('Atestado' in n for n in nomes) and any('RG' in n for n in nomes), nomes)

print('metas')
hoje = timezone.localdate()
base = dict(gestor=gestor, colaborador=ana, created_by=gestor, aprovacao=Meta.Aprovacao.APROVADA)
m_b = Meta.objects.create(titulo='B', prazo=hoje, prioridade='BAIXA', **base)
m_c = Meta.objects.create(titulo='C', prazo=hoje, prioridade='CRITICA', **base)
m_a = Meta.objects.create(titulo='A', prazo=hoje + dt.timedelta(days=1), prioridade='CRITICA', **base)
t('ordem: entrega, depois prioridade', [m.titulo for m in _por_entrega_e_prioridade([m_a, m_b, m_c])] == ['C', 'B', 'A'])
h = cn.get(f'/impulso/metas/{m_b.id}/').content.decode()
t('detalhe mostra o dia da criação', 'data-criada-em' in h and timezone.localtime(m_b.created_at).strftime('%d/%m/%Y') in h)
t('detalhe mostra a prioridade', 'Baixa' in h and 'data-prioridade' in h)
h = cn.get('/impulso/metas/').content.decode()
t('Kanban: selo no card e ordem', 'data-card-prioridade' in h)
diaria = Meta.objects.create(titulo='Diária', prazo=hoje, recorrencia='DIARIA', **base)
rr = cn.post(f'/impulso/metas/{diaria.id}/status/', {'status': 'EM_ANDAMENTO'})
t('andar para em andamento não abre a próxima', rr.json()['ok'] and not diaria.ocorrencias.exists())
rr = cn.post(f'/impulso/metas/{diaria.id}/status/', {'status': 'ENTREGUE'})
prox = diaria.ocorrencias.first()
t('entregar abre a próxima, sem avaliação', rr.json()['proxima'] and prox and prox.status == 'A_FAZER' and prox.vale_pontos and prox.prazo == hoje + dt.timedelta(days=1), rr.content)
cn.post(f'/impulso/metas/{diaria.id}/status/', {'status': 'EM_ANDAMENTO'}); cn.post(f'/impulso/metas/{diaria.id}/status/', {'status': 'ENTREGUE'})
t('voltar e entregar de novo não duplica', diaria.ocorrencias.count() == 1)
cg.post(f'/impulso/metas/{diaria.id}/avaliar/', {'nota_qualidade': 5, 'nota_prazo': 5})
diaria.refresh_from_db()
t('avaliar depois não duplica', diaria.status == 'CONCLUIDA' and diaria.ocorrencias.count() == 1)
outra_diaria = Meta.objects.create(titulo='Diária 2', prazo=hoje, recorrencia='DIARIA', **base)
cn.post(f'/impulso/metas/{outra_diaria.id}/entregar/', {})
t('o botão de entregar também abre a próxima', outra_diaria.ocorrencias.count() == 1)
minha = Meta.objects.create(titulo='Minha errada', prazo=hoje, gestor=gestor, colaborador=ana, created_by=ana, aprovacao=Meta.Aprovacao.APROVADA)
do_gestor = Meta.objects.create(titulo='Do gestor', prazo=hoje, **base)
t('dono apaga o que cadastrou em A Fazer', minha.pode_excluir(ana))
t('não apaga o que veio do gestor', not do_gestor.pode_excluir(ana))
minha.status = 'EM_ANDAMENTO'
t('depois de começar, não apaga', not minha.pode_excluir(ana))
minha.status = 'A_FAZER'; minha.save()
fim_do_mes = hoje.replace(day=28)
dentro = fim_do_mes if fim_do_mes >= hoje else hoje
fora = (hoje.replace(day=28) + dt.timedelta(days=10))
rr = cn.post(f'/impulso/metas/{minha.id}/editar/', {'titulo': 'Minha certa', 'descricao': '', 'prazo': dentro.isoformat(), 'recorrencia': 'UNICA', 'prioridade': 'ALTA'})
minha.refresh_from_db()
t('dono corrige título, prazo e prioridade', minha.titulo == 'Minha certa' and minha.prazo == dentro and minha.prioridade == 'ALTA', minha.prazo)
cn.post(f'/impulso/metas/{minha.id}/editar/', {'titulo': 'Minha certa', 'prazo': fora.isoformat(), 'recorrencia': 'UNICA'})
minha.refresh_from_db()
t('não joga o prazo para outro mês', minha.prazo == dentro, minha.prazo)
cn.post(f'/impulso/metas/{do_gestor.id}/editar/', {'titulo': 'Texto novo', 'prazo': fora.isoformat(), 'recorrencia': 'UNICA'})
do_gestor.refresh_from_db()
t('na do gestor o dono continua só no texto', do_gestor.titulo == 'Texto novo' and do_gestor.prazo == hoje)
cn.post(f'/impulso/metas/{minha.id}/excluir/')
t('dono exclui', not Meta.objects.filter(id=minha.id).exists())
from core.models import Notification
t('gestor é avisado', Notification.objects.filter(user=gestor, title='Meta removida').exists())
cn.post(f'/impulso/metas/{do_gestor.id}/excluir/')
t('POST direto não apaga a do gestor', Meta.objects.filter(id=do_gestor.id).exists())

print('anexo no Inovar')
dados = {'tipo': 'MELHORIA', 'descricao': 'Ideia com planilha ZZ', 'setor_impacto': 'TI', 'motivo': 'Porque sim'}
cn.post('/impulso/inovar/nova/', {**dados, 'anexos': [SimpleUploadedFile('modelo.xlsx', b'x'), SimpleUploadedFile('foto.png', b'x')]})
ideia = Ideia.objects.get(descricao='Ideia com planilha ZZ')
t('guarda os arquivos', ideia.anexos.count() == 2 and {a.nome for a in ideia.anexos.all()} == {'modelo.xlsx', 'foto.png'})
t('sem marca de quem enviou', not hasattr(IdeiaAnexo, 'enviado_por'))
h = cg.get('/impulso/inovar/').content.decode()
t('gestor vê o anexo, sem autor', 'modelo.xlsx' in h and 'data-ideia-anexos' in h)
antes = Ideia.objects.count()
cn.post('/impulso/inovar/nova/', {**dados, 'descricao': 'Ideia recusada', 'anexos': [SimpleUploadedFile('virus.exe', b'x')]})
t('tipo proibido não cria a ideia', Ideia.objects.count() == antes)
cn.post('/impulso/inovar/nova/', {**dados, 'descricao': 'Muitos', 'anexos': [SimpleUploadedFile(f'{i}.png', b'x') for i in range(6)]})
t('mais de 5 não cria', Ideia.objects.count() == antes)
cn.post(f'/impulso/inovar/{ideia.id}/editar/', {**dados, 'tirar_anexo': [ideia.anexos.get(nome='foto.png').id]})
t('remove anexo na edição', ideia.anexos.count() == 1)

print('Quadro de HC')
t('sem liberação não entra', cn.get('/quadro-hc/').status_code == 302)
t('menu sem o item', '/quadro-hc/' not in cn.get('/impulso/inovar/').content.decode())
t('só SUPERADMIN abre os acessos', cn.get('/quadro-hc/acessos/').status_code == 302 and cs.get('/quadro-hc/acessos/').status_code == 200)
cs.post('/quadro-hc/acessos/', {'acao': 'adicionar', 'user': ana.id, 'papel': 'VER'})
cn = cli(User.objects.get(id=ana.id))
rq = cn.get('/quadro-hc/')
t('liberado para ver entra', rq.status_code == 200 and 'data-so-leitura' in rq.content.decode())
t('menu com o item', '/quadro-hc/' in cn.get('/impulso/inovar/').content.decode())
import json as _j
t('quem só vê não altera', cn.post('/quadro-hc/observacao/', '{}', content_type='application/json').status_code == 403)
cs.post('/quadro-hc/acessos/', {'acao': 'salvar', 'user': ana.id, 'papel': 'GERENCIAR'})
cn = cli(User.objects.get(id=ana.id))
t('quem gerencia passa da trava', cn.post('/quadro-hc/observacao/', '{}', content_type='application/json').status_code != 403)
t('quem gerencia não abre os acessos', cn.get('/quadro-hc/acessos/').status_code == 302)
cs.post('/quadro-hc/acessos/', {'acao': 'remover', 'acesso': AcessoQuadroHC.objects.get(user=ana).id})
cn = cli(User.objects.get(id=ana.id))
t('removido perde o acesso', cn.get('/quadro-hc/').status_code == 302)

print('anexo em chamado')
ticket = Ticket.objects.create(title='ZZ', description='x', sector=loja, created_by=ana)
anexo = lambda dono: TicketAttachment.objects.create(ticket=ticket, file=arq('a.pdf'), original_filename='a.pdf', file_size=1, content_type='application/pdf', uploaded_by=dono)
meu, de_outro, velho = anexo(ana), anexo(gestor), anexo(ana)
TicketAttachment.objects.filter(id=velho.id).update(uploaded_at=timezone.now() - dt.timedelta(hours=25))
D_ = f'/tickets/{ticket.id}/attachment/{{}}/delete/'
h = cn.get(f'/tickets/{ticket.id}/').content.decode()
t('botão só no próprio e dentro do prazo', D_.format(meu.id) in h and D_.format(de_outro.id) not in h and D_.format(velho.id) not in h)
cn.post(D_.format(de_outro.id)); cn.post(D_.format(velho.id))
t('POST direto não apaga o do outro nem o antigo', TicketAttachment.objects.filter(id__in=[de_outro.id, velho.id]).count() == 2)
cn.post(D_.format(meu.id))
t('apaga o próprio, e fica registrado', not TicketAttachment.objects.filter(id=meu.id).exists() and ticket.comments.filter(comment__startswith='Anexo removido').exists())
novo_anexo = anexo(ana)
Ticket.objects.filter(id=ticket.id).update(status='FECHADO')
cn.post(D_.format(novo_anexo.id))
t('chamado fechado: não apaga', TicketAttachment.objects.filter(id=novo_anexo.id).exists())
t('supervisor segue apagando', cs.post(D_.format(velho.id)).status_code == 302 and not TicketAttachment.objects.filter(id=velho.id).exists())

print('Mercadinho')
cn.post('/prizes/sugerir/', {'link': 'javascript:alert(1)'})
t('link que não é http é recusado', not SugestaoProduto.objects.exists())
cn.post('/prizes/sugerir/', {'link': 'https://loja.exemplo/produto', 'nome': 'Fone ZZ', 'observacao': 'Bom'})
sug = SugestaoProduto.objects.get()
t('sugestão entra em análise', sug.status == 'PENDENTE' and sug.user_id == ana.id)
t('a tela tem o campo', 'data-sugerir-produto' in cn.get('/prizes/').content.decode())
t('colaborador não abre a análise', cn.get('/prizes/sugestoes/').status_code == 302)
t('gestor do Mercadinho vê', 'Fone ZZ' in cs.get('/prizes/sugestoes/').content.decode())
cs.post('/prizes/sugestoes/', {'sugestao': sug.id, 'decisao': 'RECUSADA', 'resposta': 'Fora do orçamento'})
sug.refresh_from_db()
t('decisão registra e avisa', sug.status == 'RECUSADA' and sug.analisado_por == chefe and Notification.objects.filter(user=ana, title__icontains='Mercadinho').exists())

print('pedido de desligamento')
dp, _ = CommunicationGroup.objects.get_or_create(name='FINANCEIRO', defaults={'created_by': chefe})
fin = novo('fin'); dp.members.add(fin)
cf = cli(fin)
col = Colaborador.objects.create(nome='Fulano ZZ', cpf='11111111111')
hoje_ = timezone.localdate()
sug_exec = acess_sv.primeiro_dia_do_mes_seguinte(hoje_)
t('primeiro dia do mês seguinte', sug_exec.day == 1 and sug_exec > hoje_)
t('colaborador comum não abre', cn.get('/acess/pedir-desligamento/').status_code == 302)
h = cf.get('/acess/pedir-desligamento/').content.decode()
t('Financeiro abre e a execução já vem no mês seguinte', sug_exec.isoformat() in h and 'Fulano ZZ' in h)
t('menu com o atalho', '/acess/pedir-desligamento/' in h)
pd = {'colaborador': col.id, 'tipo': 'DESLIGAMENTO', 'saida': hoje_.isoformat(), 'execucao': sug_exec.isoformat(), 'observacao': 'Pediu demissão'}
cf.post('/acess/pedir-desligamento/', pd)
ped = PedidoDesligamento.objects.get()
t('pedido criado', ped.status == 'ABERTO' and ped.solicitante == fin)
col.refresh_from_db()
t('pedir não agenda nada sozinho', col.desligamento_em is None)
cf.post('/acess/pedir-desligamento/', pd)
t('não duplica pedido aberto', PedidoDesligamento.objects.count() == 1)
t('Financeiro não decide', cf.post(f'/acess/desligamentos/pedido/{ped.id}/', {'decisao': 'agendar'}).status_code == 302 and PedidoDesligamento.objects.get().status == 'ABERTO')
t('aparece na tela do Acess', 'Fulano ZZ' in cs.get('/acess/desligamentos/').content.decode() and 'id="pedidos"' in cs.get('/acess/desligamentos/').content.decode())
cs.post(f'/acess/desligamentos/pedido/{ped.id}/', {'decisao': 'recusar'})
t('recusar exige motivo', PedidoDesligamento.objects.get().status == 'ABERTO')
cs.post(f'/acess/desligamentos/pedido/{ped.id}/', {'decisao': 'agendar', 'execucao': sug_exec.isoformat()})
col.refresh_from_db()
t('agendar usa o agendamento do Acess', PedidoDesligamento.objects.get().status == 'AGENDADO' and col.desligamento_em == sug_exec, col.desligamento_em)
t('avisa quem pediu', Notification.objects.filter(user=fin, title__icontains='Fulano').exists())

print('caixa')
dep_cat, _ = CategoriaSangria.objects.get_or_create(nome='DEPÓSITO', defaults={'vai_para_deposito': True})
gasto = CategoriaSangria.objects.create(nome='Limpeza ZZ')
data = hoje_ - dt.timedelta(days=2)
dia = ContagemCaixaDia.objects.create(loja=loja, data=data, deposito=D('50.00'))
Sangria.objects.create(loja=loja, data=data, valor=D('100.00'), categoria=dep_cat, descricao='Banco')
Sangria.objects.create(loja=loja, data=data, valor=D('30.00'), categoria=gasto, descricao='Material')
sincronizar_dia(loja.id, data)
dia.refresh_from_db()
t('depósito vai para a coluna Depósito, somado ao digitado', dia.deposito == D('150.00') and dia.deposito_sangrias == D('100.00'), dia.deposito)
t('e não entra na sangria', dia.sangria_registrada == D('30.00'), dia.sangria_registrada)
Sangria.objects.filter(categoria=dep_cat).update(valor=D('80.00'))
sincronizar_dia(loja.id, data); dia.refresh_from_db()
t('alterar refaz sem acumular', dia.deposito == D('130.00'), dia.deposito)
Sangria.objects.filter(categoria=dep_cat).delete()
sincronizar_dia(loja.id, data); dia.refresh_from_db()
t('apagar devolve o digitado', dia.deposito == D('50.00') and dia.deposito_sangrias == 0, dia.deposito)
Sangria.objects.create(loja=loja, data=data, valor=D('100.00'), categoria=dep_cat, descricao='Banco')
t('análise de gastos sem depósito', resumo(Sangria.objects.filter(loja=loja))['total'] == D('30.00'))
sem_dia = hoje_ - dt.timedelta(days=20)
Sangria.objects.create(loja=loja, data=sem_dia, valor=D('10.00'), categoria=dep_cat, descricao='Banco')
d2 = sincronizar_dia(loja.id, sem_dia)
t('depósito em dia sem importação cria o dia', d2 and d2.deposito == D('10.00'))

gerente = novo('gerente', sector=loja, job_title='GERENTE DE LOJA', phone='27999990000')
ContagemCaixaDia.objects.filter(id=dia.id).update(valor_sap=D('100'), valor_vivogo=D('90'), notificado_em=None)
dia.refresh_from_db()
t('dia em Atenção', dia.em_atencao, dia.status if hasattr(dia, 'status') else '')
from django.core.cache import caches
caches['local'].clear()
cger = cli(gerente)
h = cger.get('/impulso/inovar/' if False else '/').content.decode()
t('popup para o gerente', 'caixaDivergencias' in h and dia.data.strftime('%d/%m/%Y') in h)
caches['local'].clear()
t('sem popup para quem não é gerente nem ADM', 'caixaDivergencias' not in cn.get('/').content.decode())
config = ConfiguracaoContagem.get()
t('WhatsApp nasce desligado', config.notificar_whatsapp is False)
config.notificar_gerente = True; config.save()
with patch('core.evolution.enviar_texto', return_value=(True, '')) as env:
    caixa_sv.notificar_atencao(dia)
    t('desligado: não manda', env.call_count == 0)
    ContagemCaixaDia.objects.filter(id=dia.id).update(notificado_em=None); dia.refresh_from_db()
    config.notificar_whatsapp = True; config.save()
    caixa_sv.notificar_atencao(dia)
    t('ligado: manda ao gerente', env.call_count == 1 and env.call_args[0][0] == '27999990000' and 'Divergência' in env.call_args[0][1], env.call_args)
    caixa_sv.notificar_atencao(dia)
    t('uma vez por dia de caixa', env.call_count == 1)
    ContagemCaixaDia.objects.filter(id=dia.id).update(notificado_em=None); dia.refresh_from_db()
    env.side_effect = RuntimeError('caiu')
    caixa_sv.notificar_atencao(dia)
    t('falha de envio não derruba', True)

print(f'\n{ok} OK, {fail} falha(s)')
