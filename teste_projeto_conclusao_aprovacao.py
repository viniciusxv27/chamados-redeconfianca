"""Impulso: a conclusão de projeto foco passa por um SUPERADMIN.

Pedido: "em /impulso/conectar/projetos/{id}/ — quando concluir o projeto,
algum SUPERADMIN deve aprovar ou reprovar a conclusão".

O que este teste cobre:

- concluir virou pedido: o projeto fica aguardando, e a metade dos pontos pela
  conclusão só entra depois da aprovação;
- quem decide é só o SUPERADMIN (nem o gestor que concluiu, nem a equipe);
- reprovar exige motivo, devolve o projeto para "em andamento" e avisa quem
  concluiu; aprovar avisa quem tem tarefa no projeto;
- concluir de novo recomeça limpo, e reabrir cancela a conclusão pendente;
- a fila de aprovação na lista de projetos, os selos das telas;
- INSERT antigo (sem as colunas novas) continua funcionando — os outros
  servidores rodam o código já commitado contra este mesmo banco.

Caches em memória e transação desfeita no fim: nada é gravado, nada sai.
"""
import os
import sys
from decimal import Decimal

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-proj'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-proj-local'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import Client
from django.utils import timezone

from communications.models import CommunicationGroup
from core.models import Notification
from impulso.models import GRUPO_ADM, GRUPO_GESTOR, ProjetoFoco, TarefaProjeto
from impulso.scoring import _nota_projeto_foco, periodo_do_mes
from impulso.utils import e_superadmin, is_impulso_member
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


def avisado(user, titulo):
    return Notification.objects.filter(user=user, title=titulo).exists()


marcador = transaction.atomic()
marcador.__enter__()
try:
    assert not User.objects.filter(username__startswith='zzap.').exists(), 'usuários do teste já existem'
    adm = CommunicationGroup.objects.filter(name__iexact=GRUPO_ADM).first()
    ges = CommunicationGroup.objects.filter(name__iexact=GRUPO_GESTOR).first()
    assert adm and ges, 'grupos do Impulso não encontrados'
    area = Sector.objects.create(name='ZZ Area Aprovacao')
    hoje = timezone.localdate()
    inicio, fim = periodo_do_mes()

    def novo(u, nome, grupos=(), **kw):
        x = User.objects.create_user(
            username=u, email=f'{u}@exemplo-teste.local', password='S3nha!teste',
            sector=area, first_name=nome, last_name='Teste', **kw)
        for g in grupos:
            x.communication_groups.add(g)
        return x

    chefe = novo('zzap.chefe', 'ZZAPChefe', [adm, ges])          # gestor, sem ser SUPERADMIN
    # De propósito sem grupo do Impulso: dos dez SUPERADMIN do portal, só três
    # são superuser e entrariam no módulo. O aviso aponta para a tela — ela
    # precisa abrir para ele.
    boss = novo('zzap.boss', 'ZZAPBoss', hierarchy='SUPERADMIN')
    dev = novo('zzap.dev', 'ZZAPDev', [adm])

    c_chefe, c_boss, c_dev = Client(), Client(), Client()
    c_chefe.force_login(chefe); c_boss.force_login(boss); c_dev.force_login(dev)

    projeto = ProjetoFoco.objects.create(nome='ZZAP Vitrine', criado_por=chefe)
    projeto.membros.add(dev)
    tarefa = TarefaProjeto.objects.create(projeto=projeto, titulo='ZZAP T1',
                                          responsavel=dev, prazo=hoje,
                                          status=TarefaProjeto.Status.CONCLUIDA)

    print('== QUEM É SUPERADMIN ==')
    t('a hierarquia SUPERADMIN decide', e_superadmin(boss))
    t('mesmo sem acesso ao módulo Impulso', not is_impulso_member(boss))
    t('o gestor do Impulso, sozinho, não', not e_superadmin(chefe))
    t('nem o colaborador', not e_superadmin(dev))
    t('projeto novo nasce sem conclusão pendente',
      projeto.aprovacao == ProjetoFoco.Aprovacao.PENDENTE
      and not projeto.aguardando_aprovacao and not projeto.entregue
      and not projeto.conclusao_reprovada)

    print('\n== CONCLUIR VIROU PEDIDO ==')
    nota_antes, maximo, det = _nota_projeto_foco(dev, inicio, fim)
    r = c_chefe.post(f'/impulso/conectar/projetos/{projeto.id}/concluir/', follow=True)
    projeto.refresh_from_db()
    t('o gestor conclui e o projeto fica aguardando',
      projeto.concluido and projeto.aguardando_aprovacao and not projeto.entregue)
    t('registra quem concluiu e quando',
      projeto.concluido_por_id == chefe.id and projeto.concluido_em is not None)
    t('a tela avisa que foi para aprovação', 'enviado para aprovação' in r.content.decode())
    t('o SUPERADMIN é avisado', avisado(boss, 'Conclusão de projeto para aprovar'))
    t('e quem tem tarefa ainda não', not avisado(dev, 'Projeto foco concluído'))

    nota_esperando, _, det = _nota_projeto_foco(dev, inicio, fim)
    t('a metade da entrega continua valendo', nota_esperando == nota_antes and nota_esperando > 0, nota_esperando)
    t('mas a conclusão ainda não paga', det['pontos_conclusao'] == Decimal('0.00'), det)
    t('e nenhum projeto conta como concluído', det['projetos_concluidos'] == 0, det)

    print('\n== SÓ O SUPERADMIN DECIDE ==')
    r = c_chefe.post(f'/impulso/conectar/projetos/{projeto.id}/decidir/', {'decisao': 'aprovar'}, follow=True)
    projeto.refresh_from_db()
    t('quem concluiu não aprova a própria conclusão',
      projeto.aguardando_aprovacao and 'não aprova a conclusão' in r.content.decode())
    r = c_dev.post(f'/impulso/conectar/projetos/{projeto.id}/decidir/', {'decisao': 'aprovar'}, follow=True)
    projeto.refresh_from_db()
    t('nem a equipe', projeto.aguardando_aprovacao)
    r = c_boss.get(f'/impulso/conectar/projetos/{projeto.id}/decidir/')
    t('GET não decide (405)', r.status_code == 405, r.status_code)
    r = c_boss.post(f'/impulso/conectar/projetos/{projeto.id}/decidir/', {'decisao': 'talvez'}, follow=True)
    projeto.refresh_from_db()
    t('decisão inválida não muda nada',
      projeto.aguardando_aprovacao and 'Decisão inválida' in r.content.decode())

    print('\n== A TELA DE QUEM DECIDE ==')
    html = c_boss.get(f'/impulso/conectar/projetos/{projeto.id}/').content.decode()
    t('o SUPERADMIN vê os botões', 'Aprovar conclusão' in html and 'Reprovar' in html)
    t('e o aviso de que os pontos não entraram', 'esperando aprovação' in html)
    html = c_chefe.get(f'/impulso/conectar/projetos/{projeto.id}/').content.decode()
    t('o gestor vê o aviso, sem os botões',
      'esperando aprovação' in html and 'Aprovar conclusão' not in html)
    html = c_boss.get('/impulso/conectar/projetos/').content.decode()
    t('a lista mostra a fila para o SUPERADMIN',
      'esperando sua aprovação' in html and 'ZZAP Vitrine' in html)
    html = c_chefe.get('/impulso/conectar/projetos/').content.decode()
    t('e não mostra para o gestor', 'esperando sua aprovação' not in html)
    t('o card marca o projeto como aguardando', 'Aguardando' in html)

    t('e ele abre a tela do projeto sem ser do módulo',
      c_boss.get(f'/impulso/conectar/projetos/{projeto.id}/').status_code == 200)

    print('\n== REPROVAR ==')
    r = c_boss.post(f'/impulso/conectar/projetos/{projeto.id}/decidir/', {'decisao': 'reprovar'}, follow=True)
    projeto.refresh_from_db()
    t('reprovar sem motivo é recusado',
      projeto.aguardando_aprovacao and 'motivo da reprovação' in r.content.decode())

    r = c_boss.post(f'/impulso/conectar/projetos/{projeto.id}/decidir/',
                    {'decisao': 'reprovar', 'observacao': 'ZZAP A vitrine não foi montada na loja 2.'},
                    follow=True)
    projeto.refresh_from_db()
    t('reprovada, a conclusão cai e o projeto volta a andar',
      projeto.conclusao_reprovada and not projeto.concluido and not projeto.entregue)
    t('guarda o motivo, quem decidiu e quando',
      'loja 2' in projeto.observacao and projeto.decidida_por_id == boss.id
      and projeto.decidida_em is not None)
    t('quem concluiu é avisado', avisado(chefe, 'Conclusão de projeto reprovada'))
    t('e a equipe não recebe aviso de conclusão', not avisado(dev, 'Projeto foco concluído'))
    _, _, det = _nota_projeto_foco(dev, inicio, fim)
    t('nada de pontos pela conclusão', det['pontos_conclusao'] == Decimal('0.00'), det)
    html = c_chefe.get(f'/impulso/conectar/projetos/{projeto.id}/').content.decode()
    t('o gestor lê o motivo na tela', 'Conclusão reprovada' in html and 'loja 2' in html)
    t('e o botão de concluir volta', 'Concluir projeto' in html)
    t('a decisão já tomada não pode ser decidida de novo', not projeto.pode_decidir(boss))

    print('\n== CONCLUIR DE NOVO ==')
    c_chefe.post(f'/impulso/conectar/projetos/{projeto.id}/concluir/', follow=True)
    projeto.refresh_from_db()
    t('volta a aguardar, com a ficha limpa',
      projeto.aguardando_aprovacao and projeto.observacao == ''
      and projeto.decidida_por_id is None and projeto.decidida_em is None)

    print('\n== APROVAR ==')
    r = c_boss.post(f'/impulso/conectar/projetos/{projeto.id}/decidir/', {'decisao': 'aprovar'}, follow=True)
    projeto.refresh_from_db()
    t('aprovada, o projeto está entregue',
      projeto.entregue and projeto.concluido
      and projeto.aprovacao == ProjetoFoco.Aprovacao.APROVADA)
    t('registra quem aprovou', projeto.decidida_por_id == boss.id and projeto.decidida_em is not None)
    t('agora sim a equipe é avisada', avisado(dev, 'Projeto foco concluído'))
    nota_aprovada, _, det = _nota_projeto_foco(dev, inicio, fim)
    t('e a metade da conclusão entra',
      nota_aprovada > nota_esperando and det['projetos_concluidos'] == 1, det)
    html = c_dev.get(f'/impulso/conectar/projetos/{projeto.id}/').content.decode()
    t('a tela mostra o selo de concluído', 'concluído' in html and 'esperando aprovação' not in html)
    t('e quem já decidiu não decide de novo', not projeto.pode_decidir(boss))

    print('\n== REABRIR ==')
    r = c_chefe.post(f'/impulso/conectar/projetos/{projeto.id}/concluir/', {'reabrir': '1'}, follow=True)
    projeto.refresh_from_db()
    t('reabrir derruba a conclusão e a aprovação',
      not projeto.concluido and projeto.aprovacao == ProjetoFoco.Aprovacao.PENDENTE
      and not projeto.entregue)
    t('e a tela avisa o efeito na pontuação', 'sai da pontuação' in r.content.decode())
    c_chefe.post(f'/impulso/conectar/projetos/{projeto.id}/concluir/', follow=True)
    r = c_chefe.post(f'/impulso/conectar/projetos/{projeto.id}/concluir/', {'reabrir': '1'}, follow=True)
    projeto.refresh_from_db()
    t('reabrir uma conclusão pendente cancela o pedido',
      not projeto.concluido and 'cancelada' in r.content.decode())

    print('\n== O BANCO COMPARTILHADO ==')
    # Outro servidor, rodando o código de antes desta migração, insere projeto
    # sem as colunas novas: com db_default isso continua entrando.
    with connection.cursor() as cur:
        cur.execute("""
            INSERT INTO impulso_projetofoco (nome, descricao, ativo, concluido, criado_em)
            VALUES ('ZZAP Servidor antigo', '', true, false, NOW()) RETURNING id
        """)
        antigo_id = cur.fetchone()[0]
    antigo = ProjetoFoco.objects.get(pk=antigo_id)
    t('INSERT sem as colunas novas funciona (db_default)',
      antigo.aprovacao == ProjetoFoco.Aprovacao.PENDENTE and antigo.observacao == '')
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
