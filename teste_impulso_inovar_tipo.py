"""Inovar: ideia de Melhoria contínua ou Projeto; só projeto aprovado vale os pontos.

Pedido (07/10/2026), em /impulso/inovar: "O colaborador pode dar ideia de
Melhoria Contínua ou Projeto (menu suspenso para escolher qual); com 3 ideias,
independente de ser M.C ou Projeto, garante os 10 pontos; se tiver 1 ideia de
projeto aprovado, garante os outros 10 pontos. Caso seja projeto, precisamos
induzir o colaborador a especificar mais: resumo do projeto, o motivo, como
funciona hoje e como deveria ser."

Tudo numa transação desfeita. A pontuação é conferida num mês distante
(06/2031), onde nenhuma ideia real cai.
"""
import os
import re
import sys
from datetime import date, datetime
from decimal import Decimal

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-it'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-it2'},
}
django.setup()

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import timezone

from communications.models import CommunicationGroup
from impulso import scoring
from impulso.models import Ideia
from impulso.scoring import calcular_pontuacao, linhas_detalhadas
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


NOVA = '/impulso/inovar/nova/'
INI, FIM = date(2031, 6, 1), date(2031, 6, 30)

marcador = transaction.atomic()
marcador.__enter__()
try:
    setor = Sector.objects.create(name='ZZ Setor Inovar Tipo')
    chefe = User.objects.create_user(
        username='zzit.chefe', email='zzit.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN', is_superuser=True, sector=setor)
    adm, _ = CommunicationGroup.objects.get_or_create(name='ESCRITÓRIO (ADM)', defaults={'created_by': chefe})
    gestores, _ = CommunicationGroup.objects.get_or_create(name='GESTORES (IMPULSO)', defaults={'created_by': chefe})
    chefe.communication_groups.add(adm, gestores)

    def pessoa(nome):
        u = User.objects.create_user(
            username=f'zzit.{nome}', email=f'zzit.{nome}@exemplo-teste.local', password='S3nha!teste',
            first_name=nome.capitalize(), last_name='Inovar', hierarchy='PADRAO', sector=setor)
        u.communication_groups.add(adm)
        return u

    ana, bia = pessoa('ana'), pessoa('bia')
    ca = Client()
    ca.force_login(ana)

    print('== O FORMULÁRIO ==')
    html = ca.get(NOVA).content.decode()
    t('tem o menu suspenso de tipo', 'name="tipo"' in html and 'value="MELHORIA"' in html and 'value="PROJETO"' in html)
    t('a escolha começa em branco (a pessoa escolhe)', 'Escolha: melhoria contínua ou projeto' in html)
    t('o projeto pede como funciona hoje e como deveria ser',
      'name="como_funciona_hoje"' in html and 'name="como_deveria_ser"' in html)
    t('e troca os rótulos para resumo e por que propor',
      'data-rotulo-projeto="Resumo do projeto *"' in html
      and 'data-rotulo-projeto="Por que este projeto está sendo proposto? *"' in html)

    projeto = {'tipo': 'PROJETO', 'descricao': 'ZZIT Painel único de pedidos',
               'setor_impacto': 'Logística', 'motivo': 'ZZIT planilhas paralelas causam erro'}
    antes = Ideia.objects.count()
    r = ca.post(NOVA, projeto)
    html = r.content.decode()
    t('projeto sem o antes e depois é recusado', Ideia.objects.count() == antes and r.status_code == 200, r.status_code)
    t('e diz o que falta', 'como funciona hoje e como deveria ser' in html)
    t('e devolve o formulário com o que foi digitado (nada se perde)',
      'ZZIT Painel único de pedidos' in html and 'ZZIT planilhas paralelas' in html
      and re.search(r'value="PROJETO"\s+selected', html))

    r = ca.post(NOVA, dict(projeto, como_funciona_hoje='ZZIT cada loja manda uma planilha',
                           como_deveria_ser='ZZIT um painel com a situação de cada pedido',
                           participantes=[bia.id]))
    proj = Ideia.objects.filter(descricao='ZZIT Painel único de pedidos').first()
    t('com tudo preenchido, o projeto é salvo', r.status_code == 302 and proj is not None, r.status_code)
    t('com o tipo e os dois campos novos',
      proj and proj.tipo == Ideia.Tipo.PROJETO and proj.como_funciona_hoje.startswith('ZZIT cada loja')
      and proj.como_deveria_ser.startswith('ZZIT um painel'))

    r = ca.post(NOVA, {'tipo': 'MELHORIA', 'descricao': 'ZZIT Relatório automático', 'setor_impacto': 'Vendas',
                       'motivo': 'ZZIT ganha tempo', 'como_funciona_hoje': 'ZZIT sobrou da troca de tipo'})
    mc = Ideia.objects.filter(descricao='ZZIT Relatório automático').first()
    t('melhoria contínua é salva sem os campos do projeto',
      mc and mc.tipo == Ideia.Tipo.MELHORIA and mc.como_funciona_hoje == '')

    r = ca.post(NOVA, {'descricao': 'ZZIT Sem tipo', 'setor_impacto': 'TI', 'motivo': 'ZZIT veio do assistente antigo'})
    sem_tipo = Ideia.objects.filter(descricao='ZZIT Sem tipo').first()
    t('sem tipo no envio, vira melhoria contínua', sem_tipo and sem_tipo.tipo == Ideia.Tipo.MELHORIA)

    print('\n== EDIÇÃO ==')
    editar = f'/impulso/inovar/{proj.id}/editar/'
    html = ca.get(editar).content.decode()
    t('a edição abre no tipo da ideia, com os textos', re.search(r'value="PROJETO"\s+selected', html)
      and 'ZZIT cada loja manda uma planilha' in html)
    # O assistente edita mandando só o texto: o projeto não pode virar melhoria.
    r = ca.post(editar, {'descricao': 'ZZIT Painel único de pedidos v2', 'setor_impacto': 'Logística',
                         'motivo': 'ZZIT planilhas paralelas', 'participantes': [bia.id]})
    proj.refresh_from_db()
    t('editar sem mandar o tipo mantém o projeto e o antes e depois',
      r.status_code == 302 and proj.tipo == Ideia.Tipo.PROJETO and proj.descricao.endswith('v2')
      and proj.como_funciona_hoje.startswith('ZZIT cada loja'), (r.status_code, proj.tipo))
    r = ca.post(f'/impulso/inovar/{mc.id}/editar/', {'tipo': 'PROJETO', 'descricao': mc.descricao,
                                                      'setor_impacto': mc.setor_impacto, 'motivo': mc.motivo})
    mc.refresh_from_db()
    t('virar projeto sem o antes e depois é recusado', r.status_code == 200 and mc.tipo == Ideia.Tipo.MELHORIA)

    print('\n== A LISTA ==')
    html = ca.get('/impulso/inovar/').content.decode()
    t('o card mostra o tipo', '>Projeto' in html and 'Melhoria contínua' in html)
    t('e o antes e depois do projeto', 'Como funciona hoje' in html and 'ZZIT um painel com a situação' in html)
    t('o motivo do projeto vira "por que este projeto"', 'Por que este projeto' in html)
    html = ca.get('/impulso/inovar/?tipo=PROJETO').content.decode()
    t('o filtro por tipo funciona', 'ZZIT Painel único' in html and 'ZZIT Relatório automático' not in html)
    cc = Client()
    cc.force_login(chefe)
    html = cc.get('/impulso/inovar/').content.decode()
    t('o gestor é avisado de que melhoria aprovada não vale os pontos de projeto',
      'É melhoria contínua: aprovar não vale os pontos de' in html)

    print('\n== A PONTUAÇÃO (06/2031) ==')
    def ideia(descricao, tipo, status=Ideia.Status.NOVA, autor=ana):
        i = Ideia.objects.create(autor=autor, tipo=tipo, descricao=f'ZZIT {descricao}', setor_impacto='ZZ',
                                 motivo='ZZ', status=status)
        Ideia.objects.filter(pk=i.pk).update(criado_em=timezone.make_aware(datetime(2031, 6, 10, 10, 0)))
        return i

    m1 = ideia('melhoria 1', Ideia.Tipo.MELHORIA)
    m2 = ideia('melhoria 2', Ideia.Tipo.MELHORIA)
    dados = calcular_pontuacao(ana, inicio=INI, fim=FIM)
    t('2 ideias não valem os 10 pontos', dados['p_ideias'] == 0)
    p1 = ideia('projeto 1', Ideia.Tipo.PROJETO)
    p1.participantes.add(bia)
    dados = calcular_pontuacao(ana, inicio=INI, fim=FIM)
    t('3 ideias, misturando melhoria e projeto, valem os 10', dados['p_ideias'] == Decimal('10'))
    t('sem nada aprovado, nada de projeto aprovado', dados['p_ideia_aprovada'] == 0)

    Ideia.objects.filter(pk=m1.pk).update(status=Ideia.Status.APROVADA)
    dados = calcular_pontuacao(ana, inicio=INI, fim=FIM)
    t('melhoria contínua aprovada não vale os pontos de projeto aprovado', dados['p_ideia_aprovada'] == 0)
    linha = next(l for l in linhas_detalhadas(dados) if l['bloco'] == 'INOVAR' and 'aprovado' in l['item'])
    t('e o detalhamento explica por quê', linha['item'] == 'Projeto aprovado'
      and 'melhoria(s) aprovada(s) não conta(m)' in linha['info'], linha)

    Ideia.objects.filter(pk=p1.pk).update(status=Ideia.Status.APROVADA)
    dados = calcular_pontuacao(ana, inicio=INI, fim=FIM)
    t('projeto aprovado vale os outros 10', dados['p_ideia_aprovada'] == Decimal('10'))
    t('o desempate continua contando toda ideia aprovada', dados['desempate']['ideias_aprovadas'] == 2,
      dados['desempate'])
    linha = next(l for l in linhas_detalhadas(dados) if l['item'].startswith('Propor'))
    t('o detalhamento das ideias separa melhoria e projeto',
      '3 ideia(s) proposta(s) · 2 melhoria(s) contínua(s) · 1 projeto(s)' == linha['info'], linha['info'])
    dados_bia = calcular_pontuacao(bia, inicio=INI, fim=FIM)
    t('quem participa do projeto aprovado pontua junto', dados_bia['p_ideia_aprovada'] == Decimal('10'))
    t('mês fechado antes do tipo existir continua legível',
      scoring._info_ideias({'propostas': 3}) == '3 ideia(s) proposta(s)'
      and scoring._info_projeto_aprovado({'aprovadas': 2}) == '2 aprovada(s)')

    print('\n== APROVAR VIRA ATIVIDADE ==')
    texto = proj.descricao_da_atividade()
    t('a atividade do projeto leva o antes e depois',
      'Como funciona hoje: ZZIT cada loja' in texto and 'Como deveria ser: ZZIT um painel' in texto
      and '(projeto ·' in texto, texto[:200])

    print('\n== O ASSISTENTE ==')
    from assistente.comum import Invalido
    from assistente.ferramentas_impulso import _previa_ideia_criar, _previa_ideia_decidir, _previa_ideia_editar
    try:
        _previa_ideia_criar(ana, {'tipo': 'projeto', 'descricao': 'x', 'setor_impacto': 'y', 'motivo': 'z'})
        t('projeto pelo assistente sem o antes e depois é recusado', False)
    except Invalido as exc:
        t('projeto pelo assistente sem o antes e depois é recusado', 'como_funciona_hoje' in str(exc), str(exc))
    texto, dados_post = _previa_ideia_criar(ana, {
        'tipo': 'Projeto', 'descricao': 'x', 'setor_impacto': 'y', 'motivo': 'z',
        'como_funciona_hoje': 'hoje', 'como_deveria_ser': 'depois'})
    t('com tudo, a prévia mostra o projeto', 'Enviar o projeto' in texto and 'Como deveria ser: depois' in texto
      and dados_post['tipo'] == 'PROJETO')
    texto, dados_post = _previa_ideia_criar(ana, {'tipo': 'melhoria contínua', 'descricao': 'x',
                                                  'setor_impacto': 'y', 'motivo': 'z'})
    t('"melhoria contínua" por extenso é entendido', dados_post['tipo'] == 'MELHORIA')
    proj.status = Ideia.Status.NOVA
    proj.save(update_fields=['status'])
    _texto, dados_post = _previa_ideia_editar(ana, {'ideia_id': proj.id, 'motivo': 'ZZIT motivo novo'})
    t('editar pelo assistente sem tipo mantém o projeto e o antes e depois',
      dados_post['post']['tipo'] == 'PROJETO'
      and dados_post['post']['como_funciona_hoje'].startswith('ZZIT cada loja'), dados_post['post'])
    texto, _d = _previa_ideia_decidir(chefe, {'ideia_id': mc.id, 'status': 'APROVADA'})
    t('decidir uma melhoria avisa que ela não vale os pontos de projeto', 'É melhoria contínua' in texto, texto)
    texto, _d = _previa_ideia_decidir(chefe, {'ideia_id': proj.id, 'status': 'APROVADA'})
    t('decidir um projeto avisa que ele vale os pontos', 'Projeto aprovado vale os pontos' in texto, texto)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)

print(f'\n{ok} OK / {fail} falhas — rollback: nada deste teste ficou no banco.')
sys.exit(1 if fail else 0)
