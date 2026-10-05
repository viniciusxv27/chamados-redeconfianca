"""Cobrança dos cursos no grupo de gestão das lojas (WhatsApp).

Tudo numa transação desfeita no fim. O envio passa por um dublê de
`core.evolution.enviar_texto` — além da trava de `teste_*.py` —, então nenhum
grupo de verdade recebe mensagem.
"""
import os
import sys
from datetime import datetime, time, timedelta

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')
for nome in ('EVOLUTION_API_URL', 'EVOLUTION_API_KEY', 'EVOLUTION_INSTANCE'):
    setattr(settings, nome, getattr(settings, nome, '') or 'teste')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import timezone

import core.evolution
from cursos import cobranca
from cursos.models import (
    CobrancaWhatsapp, Comprovante, ConfiguracaoCursos, Curso, GrupoWhatsappLoja, jid_do_grupo,
)
from users.models import Sector

User = get_user_model()
ok = fail = 0
enviados = []


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def duble(numero, texto, timeout=10):
    enviados.append((numero, texto))
    return True, 'ok (dublê)'


core.evolution.enviar_texto = duble

marcador = transaction.atomic()
marcador.__enter__()
try:
    hoje = timezone.localdate()
    print('== ID DO GRUPO ==')
    t('grupo novo ganha @g.us', jid_do_grupo('120363406942610139') == '120363406942610139@g.us')
    t('grupo antigo ganha @g.us', jid_do_grupo('5527997784149-1612196334') == '5527997784149-1612196334@g.us')
    t('formato Z-API (-group) é aceito', jid_do_grupo('120363406942610139-group') == '120363406942610139@g.us')
    t('vazio continua vazio', jid_do_grupo('  ') == '')

    print('== GRUPOS CADASTRADOS PELA MIGRAÇÃO ==')
    t('as 20 lojas têm grupo', GrupoWhatsappLoja.objects.count() >= 20,
      GrupoWhatsappLoja.objects.count())
    t('Masterplace com o ID antigo',
      GrupoWhatsappLoja.objects.filter(setor__name='Loja Masterplace',
                                       grupo='5527997784149-1612196334').exists())

    # ------------------------------------------------------------- cenário
    # Um curso de capacitação: só vale para quem for atribuído, então o cenário
    # não depende de quem a configuração real do módulo cobra.
    cfg = ConfiguracaoCursos.get()
    loja_a = Sector.objects.create(name='Loja ZZ A Cobrança')
    loja_b = Sector.objects.create(name='Loja ZZ B Cobrança')
    GrupoWhatsappLoja.objects.create(setor=loja_a, grupo='120363000000000001')

    def novo(username, setor, **kw):
        return User.objects.create_user(username=username, email=f'{username}@exemplo-teste.local',
                                        password='S3nha!teste', sector=setor, **kw)

    gestor = novo('crs.cob.gestor', None, first_name='Gestor', last_name='Cobrança',
                  hierarchy='SUPERADMIN')
    ana = novo('crs.cob.ana', loja_a, first_name='ANA', last_name='DA SILVA')
    bia = novo('crs.cob.bia', loja_a, first_name='BIA', last_name='SOUZA')
    caio = novo('crs.cob.caio', loja_a, first_name='CAIO', last_name='LIMA')
    davi = novo('crs.cob.davi', loja_b, first_name='DAVI', last_name='ROCHA')
    sem = novo('crs.cob.sem', None, first_name='SEM', last_name='LOJA')

    curso = Curso.objects.create(tipo=Curso.CAPACITACAO, titulo='ZZ Curso Cobrança',
                                 prazo=hoje + timedelta(days=3), publicado=True)
    for p in (ana, bia, caio, davi, sem):
        curso.atribuicoes.create(colaborador=p)
    # Só o nome do arquivo: nada sobe para o MinIO (que é compartilhado).
    Comprovante.objects.create(curso=curso, colaborador=bia, arquivo='zz/teste.pdf',
                               status=Comprovante.APROVADO)
    Comprovante.objects.create(curso=curso, colaborador=caio, arquivo='zz/teste.pdf',
                               status=Comprovante.RECUSADO)

    print('== PRÉVIA ==')
    dados = cobranca.previa([curso], cfg)
    por_setor = {l['setor'].id: l for l in dados['lojas']}
    la, lb = por_setor.get(loja_a.id), por_setor.get(loja_b.id)
    t('loja A tem 2 pendentes (Ana e Caio recusado)', la and la['pessoas'] == 2, la and la['pessoas'])
    t('quem mandou e foi aprovado não aparece', la and 'Bia' not in la['texto'])
    t('recusado aparece avisando', la and 'Caio Lima _(comprovante recusado' in la['texto'], la and la['texto'])
    t('nome legível com partícula minúscula', la and '• Ana da Silva' in la['texto'])
    t('prazo com dias restantes', la and 'faltam 3 dias' in la['texto'])
    t('link do portal no fim', la and la['texto'].rstrip().endswith('/cursos/'))
    t('loja B sem grupo fica marcada', lb and lb['grupo'] is None)
    t('pessoa sem setor contada à parte', dados['sem_loja'] == 1, dados['sem_loja'])

    print('== ENVIO ==')
    r = cobranca.cobrar([curso], CobrancaWhatsapp.MANUAL, user=gestor, setores={loja_a.id, loja_b.id},
                        cfg=cfg, em_segundo_plano=False)
    t('gravou uma cobrança (só a loja com grupo)', len(r['gravadas']) == 1, r)
    t('loja sem grupo listada', loja_b in r['sem_grupo'])
    t('saiu para o JID do grupo', enviados and enviados[-1][0] == '120363000000000001@g.us', enviados)
    c = CobrancaWhatsapp.objects.get(setor=loja_a)
    t('registro marcado como enviado', c.enviado is True and c.pessoas == 2)
    t('registro guarda o curso', list(c.cursos.all()) == [curso])

    antes = len(enviados)
    r = cobranca.cobrar([curso], CobrancaWhatsapp.MANUAL, user=gestor, setores={loja_a.id},
                        cfg=cfg, em_segundo_plano=False)
    t('segunda cobrança em seguida não sai', not r['gravadas'] and loja_a in r['recentes']
      and len(enviados) == antes)

    CobrancaWhatsapp.objects.filter(setor=loja_a).update(enviado=False)
    r = cobranca.cobrar([curso], CobrancaWhatsapp.MANUAL, user=gestor, setores={loja_a.id},
                        cfg=cfg, em_segundo_plano=False)
    t('cobrança que falhou pode ser repetida', len(r['gravadas']) == 1)

    print('== AGENDA ==')
    cfg.cobranca_automatica = False
    cfg.cobranca_dias = ','.join(str(d) for d in range(7))
    cfg.cobranca_hora = time(9, 0)
    cfg.ultima_cobranca_automatica = None
    cfg.save()
    tz = timezone.get_current_timezone()
    dez = timezone.make_aware(datetime.combine(hoje, time(10, 0)), tz)
    oito = timezone.make_aware(datetime.combine(hoje, time(8, 0)), tz)
    t('desligada não roda', not cobranca.esta_na_hora(cfg, dez))
    cfg.cobranca_automatica = True
    t('ligada, depois da hora: roda', cobranca.esta_na_hora(cfg, dez))
    t('antes da hora: não', not cobranca.esta_na_hora(cfg, oito))
    cfg.cobranca_dias = str((hoje.weekday() + 1) % 7)
    t('dia não marcado: não', not cobranca.esta_na_hora(cfg, dez))
    cfg.cobranca_dias = str(hoje.weekday())
    cfg.ultima_cobranca_automatica = dez
    t('já rodou hoje: não', not cobranca.esta_na_hora(cfg, dez))
    t('processo de teste nunca dispara o automático', cobranca.disparar_se_esta_na_hora() is False)

    print('== TELAS ==')
    cli = Client()
    cli.force_login(gestor)
    resp = cli.get(f'/cursos/gestao/?curso={curso.id}')
    t('quadro tem o botão Cobrar no WhatsApp', b'/cursos/gestao/cobrar/' in resp.content)
    resp = cli.get(f'/cursos/gestao/cobrar/?curso={curso.id}')
    t('prévia abre', resp.status_code == 200, resp.status_code)
    t('prévia mostra o texto da loja', 'Loja ZZ A Cobrança' in resp.content.decode())
    t('loja sem grupo não pode ser marcada', 'sem grupo de WhatsApp' in resp.content.decode())

    CobrancaWhatsapp.objects.filter(setor=loja_a).update(enviado=False)
    antes = len(enviados)
    resp = cli.post('/cursos/gestao/cobrar/', {'curso': curso.id, 'setores': [loja_a.id]})
    t('POST volta para a prévia', resp.status_code == 302, resp.status_code)
    t('gravou a cobrança do POST', CobrancaWhatsapp.objects.filter(setor=loja_a, enviado__isnull=True).exists()
      or CobrancaWhatsapp.objects.filter(setor=loja_a, enviado=True).count() >= 1)

    resp = cli.get('/cursos/configuracao/')
    t('configuração mostra a loja com o grupo', '120363000000000001' in resp.content.decode())
    t('configuração mostra a loja sem grupo', f'name="grupo_{loja_b.id}"' in resp.content.decode())

    leve = Client()
    comum = novo('crs.cob.comum', loja_a, first_name='Comum', last_name='X')
    leve.force_login(comum)
    resp = leve.get('/cursos/gestao/cobrar/')
    t('quem não é gestor não vê a cobrança', resp.status_code == 302)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)

print(f'\n{ok} OK / {fail} falhas — rollback: nada deste teste ficou no banco.')
sys.exit(1 if fail else 0)
