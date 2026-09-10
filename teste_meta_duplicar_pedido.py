"""Impulso: usuário PADRÃO duplica atividade — e a cópia vai para o gestor aprovar.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import sys
from datetime import timedelta

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
from django.utils import timezone

from communications.models import CommunicationGroup
from core.models import Notification
from impulso.models import GRUPO_ADM, GRUPO_GESTOR, Meta, MetaItem
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


def proximo_sabado():
    d = timezone.localdate() + timedelta(days=1)
    while d.weekday() != 5:
        d += timedelta(days=1)
    return d


marcador = transaction.atomic()
marcador.__enter__()
try:
    adm = CommunicationGroup.objects.filter(name__iexact=GRUPO_ADM).first()
    ges = CommunicationGroup.objects.filter(name__iexact=GRUPO_GESTOR).first()
    assert adm and ges, 'grupos do Impulso não encontrados'
    area = Sector.objects.create(name='ZZ Area Pedido Duplicar')
    outra_area = Sector.objects.create(name='ZZ Outra Area Duplicar')

    def novo(u, setor, grupos=(), **kw):
        x = User.objects.create_user(
            username=u, email=f'{u}@exemplo-teste.local', password='S3nha!teste',
            sector=setor, first_name=u.split('.')[1].title(), last_name='T', **kw)
        for g in grupos:
            x.communication_groups.add(g)
        return x

    gestor = novo('pd.gestor', area, [adm, ges])
    gestor2 = novo('pd.gestor2', area, [adm, ges])
    gestor_fora = novo('pd.fora', outra_area, [adm, ges])
    colab = novo('pd.colab', area, [adm])
    part = novo('pd.part', area, [adm])
    estranho = novo('pd.estranho', area, [adm])
    hoje = timezone.localdate()

    original = Meta.objects.create(
        titulo='ZZ Relatório mensal', descricao='Montar e enviar o relatório.',
        colaborador=colab, gestor=gestor, prazo=hoje + timedelta(days=5),
        recorrencia=Meta.Recorrencia.MENSAL, apenas_dias_uteis=True,
        aprovacao=Meta.Aprovacao.APROVADA, status=Meta.Status.CONCLUIDA,
        created_by=gestor, nota_qualidade=5, nota_prazo=5)
    original.participantes.add(part)
    for n, texto in enumerate(['Levantar dados', 'Montar planilha', 'Enviar']):
        MetaItem.objects.create(meta=original, texto=texto, ordem=n, criado_por=gestor, concluido=True)

    print('== QUEM PODE PEDIR ==')
    t('o dono da atividade (PADRÃO) pode pedir', original.pode_solicitar_duplicacao(colab))
    t('o participante também', original.pode_solicitar_duplicacao(part))
    t('quem não responde pela atividade não', not original.pode_solicitar_duplicacao(estranho))
    t('o gestor não pede: duplica direto', not original.pode_solicitar_duplicacao(gestor))
    pendente = Meta.objects.create(
        titulo='ZZ Ainda pendente', descricao='x', colaborador=colab, gestor=gestor,
        prazo=hoje + timedelta(days=3), aprovacao=Meta.Aprovacao.PENDENTE,
        solicitada_por=colab, created_by=colab)
    t('solicitação ainda não aprovada não se duplica', not pendente.pode_solicitar_duplicacao(colab))

    cc = Client(); cc.force_login(colab)
    cg = Client(); cg.force_login(gestor)

    print('\n== O BOTÃO APARECE PARA O PADRÃO ==')
    kanban = cc.get('/impulso/metas/').content.decode()
    t('no card: link de pedir a duplicação',
      f'href="/impulso/metas/{original.id}/duplicar/solicitar/"' in kanban)
    t('e não o botão de duplicar direto', 'class="imp-duplicar ' not in kanban)
    t('o link não dispara o arrastar do card', '.imp-duplicar-pedido' in kanban)
    html = cc.get(f'/impulso/metas/{original.id}/').content.decode()
    t('no detalhe também', f'/impulso/metas/{original.id}/duplicar/solicitar/' in html)

    kanban_g = cg.get('/impulso/metas/').content.decode()
    t('o gestor continua com o botão de duplicar direto', 'class="imp-duplicar ' in kanban_g)

    print('\n== A TELA DO PEDIDO ==')
    antes = Meta.objects.count()
    r = cc.post(f'/impulso/metas/{original.id}/duplicar/')
    t('POST direto do PADRÃO não cria nada', Meta.objects.count() == antes)
    t('e leva para a tela do pedido',
      r.status_code == 302 and '/duplicar/solicitar/' in r['Location'], r.status_code)

    r = cc.get(f'/impulso/metas/{original.id}/duplicar/solicitar/')
    html = r.content.decode()
    t('a tela abre', r.status_code == 200, r.status_code)
    t('explica que vai para aprovação', 'solicitação' in html and 'aprovar' in html)
    t('sugere o título "Cópia de…"', 'value="Cópia de ZZ Relatório mensal"' in html)
    t('diz quantos passos vão junto', '3 passos do to-do' in html)
    t('e o que não vai', 'outros responsáveis' in html)
    t('o gestor da atividade vem selecionado',
      f'value="{gestor.id}" selected' in html)
    t('só gestores do setor aparecem', f'value="{gestor_fora.id}"' not in html)

    ce = Client(); ce.force_login(estranho)
    r = ce.get(f'/impulso/metas/{original.id}/duplicar/solicitar/', follow=True)
    t('quem não responde pela atividade não abre a tela', 'Cópia de ZZ' not in r.content.decode())

    r = cg.get(f'/impulso/metas/{original.id}/duplicar/solicitar/')
    t('o gestor é mandado de volta (ele duplica direto)',
      r.status_code == 302 and f'/impulso/metas/{original.id}/' in r['Location'])

    print('\n== VALIDAÇÕES ==')
    url = f'/impulso/metas/{original.id}/duplicar/solicitar/'
    base = {'titulo': 'ZZ Relatório de outubro', 'prazo': (hoje + timedelta(days=10)).isoformat(),
            'gestor': gestor.id}
    cc.post(url, {**base, 'prazo': (hoje - timedelta(days=1)).isoformat()})
    t('prazo no passado é recusado', Meta.objects.count() == antes)
    cc.post(url, {**base, 'gestor': gestor_fora.id})
    t('gestor de outra área é recusado', Meta.objects.count() == antes)
    cc.post(url, {**base, 'titulo': '   '})
    t('título vazio é recusado', Meta.objects.count() == antes)

    print('\n== O PEDIDO ==')
    sabado = proximo_sabado()
    r = cc.post(url, {**base, 'prazo': sabado.isoformat(), 'gestor': gestor2.id}, follow=True)
    copia = Meta.objects.filter(titulo='ZZ Relatório de outubro').first()
    t('cria a cópia', copia is not None, r.status_code)
    t('como SOLICITAÇÃO pendente', copia and copia.aprovacao == Meta.Aprovacao.PENDENTE)
    t('para o próprio colaborador', copia and copia.colaborador_id == colab.id)
    t('pedida por ele', copia and copia.solicitada_por_id == colab.id)
    t('para o gestor escolhido', copia and copia.gestor_id == gestor2.id)
    t('sabendo de onde veio', copia and copia.duplicada_de_id == original.id)
    t('leva a descrição', copia and copia.descricao == original.descricao)
    t('leva a recorrência e os dias úteis',
      copia and copia.recorrencia == original.recorrencia and copia.apenas_dias_uteis)
    t('prazo no sábado anda para a segunda (somente dias úteis)',
      copia and copia.prazo == sabado + timedelta(days=2), copia and copia.prazo)
    passos = list(copia.itens.order_by('ordem')) if copia else []
    t('leva os passos na ordem', [p.texto for p in passos] == ['Levantar dados', 'Montar planilha', 'Enviar'])
    t('desmarcados', all(not p.concluido for p in passos))
    t('NÃO leva os outros responsáveis', copia and copia.participantes.count() == 0)
    t('nasce A fazer e sem nota', copia and copia.status == Meta.Status.A_FAZER and copia.nota_qualidade is None)
    t('a original fica intacta',
      Meta.objects.filter(id=original.id, status=Meta.Status.CONCLUIDA).exists()
      and original.participantes.filter(id=part.id).exists())
    html = r.content.decode()
    t('a tela confirma para quem foi', 'Pedido de duplicação enviado' in html)
    t('o gestor escolhido é avisado',
      Notification.objects.filter(user=gestor2, title='Pedido de duplicação de meta').exists())

    print('\n== ENQUANTO NÃO APROVA ==')
    kanban = cc.get('/impulso/metas/').content.decode()
    t('a cópia não entra no Kanban', 'ZZ Relatório de outubro' not in kanban)
    html = cc.get('/impulso/metas/solicitacoes/').content.decode()
    t('aparece nas solicitações do colaborador', 'ZZ Relatório de outubro' in html)
    t('marcada como duplicação', 'duplicação' in html)

    r = cc.post(url, base, follow=True)
    t('um segundo pedido da mesma atividade é barrado',
      Meta.objects.filter(duplicada_de=original, solicitada_por=colab).count() == 1)
    t('e a tela explica', 'já pediu a duplicação' in r.content.decode())
    html = cc.get(url).content.decode()
    t('a tela do pedido mostra o pedido que já existe', 'Ver o pedido' in html)

    print('\n== O GESTOR DECIDE ==')
    cg2 = Client(); cg2.force_login(gestor2)
    html = cg2.get('/impulso/metas/solicitacoes/').content.decode()
    t('está na fila do gestor escolhido', 'ZZ Relatório de outubro' in html)
    t('com o selo de duplicação', 'duplicação' in html)
    t('e de qual atividade', 'Cópia de: ZZ Relatório mensal' in html)
    html = cg2.get(f'/impulso/metas/{copia.id}/').content.decode()
    t('o detalhe diz que é pedido de duplicação', 'pedido de <strong>duplicação</strong>' in html)
    t('com link para a original', f'/impulso/metas/{original.id}/' in html)

    r = cc.post(f'/impulso/metas/{copia.id}/decidir/', {'decisao': 'aprovar'}, follow=True)
    copia.refresh_from_db()
    t('quem pediu não aprova o próprio pedido', copia.aprovacao == Meta.Aprovacao.PENDENTE)

    r = cg2.post(f'/impulso/metas/{copia.id}/decidir/', {'decisao': 'aprovar'}, follow=True)
    copia.refresh_from_db()
    t('o gestor aprova', copia.aprovacao == Meta.Aprovacao.APROVADA)
    t('o aviso diz que foi a duplicação',
      Notification.objects.filter(user=colab, title='Duplicação aprovada').exists())
    kanban = Client()
    kanban.force_login(colab)
    t('agora a cópia está no Kanban', 'ZZ Relatório de outubro' in kanban.get('/impulso/metas/').content.decode())

    print('\n== RECUSA ==')
    cc.post(url, {**base, 'titulo': 'ZZ Pedido que será recusado'}, follow=True)
    recusar = Meta.objects.filter(titulo='ZZ Pedido que será recusado').first()
    t('um novo pedido pode ser feito depois do anterior decidido', recusar is not None)
    cg.post(f'/impulso/metas/{recusar.id}/decidir/',
            {'decisao': 'recusar', 'motivo_recusa': 'ZZ já existe essa tarefa'}, follow=True)
    recusar.refresh_from_db()
    t('o gestor recusa', recusar.aprovacao == Meta.Aprovacao.RECUSADA)
    aviso = Notification.objects.filter(user=colab, title='Duplicação recusada').first()
    t('o colaborador é avisado da recusa', aviso is not None)
    t('com o motivo', aviso and 'ZZ já existe essa tarefa' in aviso.message)

    print('\n== O PARTICIPANTE TAMBÉM PEDE ==')
    cp = Client(); cp.force_login(part)
    cp.post(url, {**base, 'titulo': 'ZZ Cópia do participante'}, follow=True)
    dele = Meta.objects.filter(titulo='ZZ Cópia do participante').first()
    t('cria o pedido', dele is not None)
    t('e a cópia é dele, não do dono original', dele and dele.colaborador_id == part.id)

    print('\n== O GESTOR CONTINUA DUPLICANDO DIRETO ==')
    r = cg.post(f'/impulso/metas/{original.id}/duplicar/', follow=True)
    direta = Meta.objects.filter(titulo='Cópia de ZZ Relatório mensal', solicitada_por__isnull=True).first()
    t('cria a cópia na hora', direta is not None)
    t('já aprovada', direta and direta.aprovacao == Meta.Aprovacao.APROVADA)
    t('também registra de onde veio', direta and direta.duplicada_de_id == original.id)
    t('e abre na edição', bool(r.redirect_chain) and '/editar/' in r.redirect_chain[-1][0])

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
