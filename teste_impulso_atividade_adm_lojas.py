"""Impulso: qualquer pessoa do Impulso cria atividade para quem está em ADM's LOJAS.

Pedido: "Permita qualquer pessoa com acesso ao impulso poder criar atividade para
as pessoas do grupo ADM's LOJAS (ID 32), onde poderá selecionar também qual o
gestor que irá aprovar e avaliar a atividade, entre qualquer um dos gestores."

- quem não é gestor escolhe "para mim" ou alguém de ADM's LOJAS; para outra
  pessoa, escolhe qualquer gestor do Impulso, e a atividade espera a aprovação
  dele antes de entrar no Kanban do ADM — é ele também quem avalia a entrega;
- o gestor que cria para ADM de loja de outra área escolhe o mesmo: sendo ele
  mesmo, a meta entra direto; sendo outro gestor, espera a aprovação dele;
- ADM de loja pedindo para si também escolhe qualquer gestor;
- para quem não é de ADM's LOJAS nada muda (colega do escritório continua fora,
  e a demanda para outra área continua indo para o gestor de lá).

Roda dentro de uma transação desfeita no fim: não grava nada no banco. Os avisos
do Impulso são só notificações dentro do portal (sem push, WhatsApp ou e-mail).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

import json

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import timezone

from communications.models import CommunicationGroup
from core.models import Notification
from impulso.models import GRUPO_ADM, GRUPO_ADM_LOJAS, GRUPO_GESTOR, Meta
from impulso.utils import _q_grupo, get_adms_lojas
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


marcador = transaction.atomic()
marcador.__enter__()
try:
    escritorio_g = CommunicationGroup.objects.filter(_q_grupo('name', GRUPO_ADM)).first()
    lojas_g = CommunicationGroup.objects.filter(_q_grupo('name', GRUPO_ADM_LOJAS)).first()
    gestores_g = CommunicationGroup.objects.filter(_q_grupo('name', GRUPO_GESTOR)).first()
    assert escritorio_g and lojas_g and gestores_g, 'grupos do Impulso não encontrados'

    print("== O GRUPO ==")
    t("ADM's LOJAS é o grupo 32 do pedido (achado pelo nome, com o espaço no fim)",
      lojas_g.id == 32, (lojas_g.id, repr(lojas_g.name)))

    loja = Sector.objects.create(name='ZZ Loja Teste ADM')
    escritorio_a = Sector.objects.create(name='ZZ Escritorio A')
    escritorio_b = Sector.objects.create(name='ZZ Escritorio B')
    sem_gestor = Sector.objects.create(name='ZZ Setor Sem Gestor')

    def novo(username, setor, grupos, extras=()):
        u = User.objects.create_user(
            username=username, email=f'{username}@exemplo-teste.local', password='S3nha!teste',
            sector=setor, first_name=username.split('.')[1].title(), last_name='Teste')
        for g in grupos:
            u.communication_groups.add(g)
        for s in extras:
            u.sectors.add(s)
        return u

    adm1 = novo('zzadm.lojaum', loja, [lojas_g])
    adm2 = novo('zzadm.lojadois', loja, [lojas_g])
    escr = novo('zzadm.escritorio', escritorio_a, [escritorio_g])
    escr_sem = novo('zzadm.semgestor', sem_gestor, [escritorio_g])
    colega = novo('zzadm.colega', escritorio_a, [escritorio_g])
    gestor_loja = novo('zzadm.gestorloja', escritorio_b, [escritorio_g, gestores_g], extras=[loja])
    gestor_fora = novo('zzadm.gestorfora', escritorio_b, [escritorio_g, gestores_g])
    gestor_a = novo('zzadm.gestora', escritorio_a, [escritorio_g, gestores_g])
    gestor_outro = novo('zzadm.gestoroutro', escritorio_b, [escritorio_g, gestores_g])

    hoje = timezone.localdate()
    prazo = hoje.isoformat()                 # hoje: cai no mês que o Kanban abre por padrão

    def cliente(u):
        c = Client()
        c.force_login(u)
        return c

    def nome(u):
        return u.get_full_name()

    def avisos(u, titulo):
        return list(Notification.objects.filter(user=u, title=titulo).values_list('message', flat=True))

    def criar(c, titulo, **extra):
        dados = {'titulo': titulo, 'descricao': 'descrição', 'prazo': prazo,
                 'recorrencia': Meta.Recorrencia.UNICA}
        dados.update(extra)
        r = c.post('/impulso/metas/nova/', dados, follow=True)
        return Meta.objects.filter(titulo=titulo).first(), r.content.decode()

    c_escr = cliente(escr)
    c_adm1 = cliente(adm1)
    c_gfora = cliente(gestor_fora)

    print('\n== A TELA DE QUEM NÃO É GESTOR ==')
    html = c_escr.get('/impulso/metas/nova/').content.decode()
    t('pergunta para quem é a atividade: para mim ou alguém de ADM\'s LOJAS',
      'id="impParaQuem"' in html and '<option value="">Para mim</option>' in html
      and f'value="{adm1.id}" data-nome="{nome(adm1)}">{nome(adm1)} — {loja.name}</option>' in html)
    t("lista todo mundo de ADM's LOJAS (os seis reais e os de teste)",
      all(f'value="{u.id}" data-nome=' in html for u in get_adms_lojas()), get_adms_lojas().count())
    t('mas não oferece colega do escritório como "outra pessoa"', f'data-nome="{nome(colega)}"' not in html)
    bloco_adm = re.search(r'<div id="impBlocoAdm".*?</div>', html, flags=re.S).group(0)
    t('para ADM, o gestor aprovador sai de TODOS os gestores do Impulso',
      all(f'<option value="{g.id}">{nome(g)}</option>' in bloco_adm
          for g in (gestor_loja, gestor_fora, gestor_a, gestor_outro)) and 'name="gestor_aprovador"' in bloco_adm)
    t('e começa escondido e desabilitado (o padrão é "para mim")',
      '<div id="impBlocoAdm" class="hidden">' in html and 'required disabled' in bloco_adm)
    t('explica que o gestor escolhido aprova e avalia',
      'Gestor que vai aprovar e avaliar' in html and 'é ele quem dá a nota na entrega' in html)

    html = cliente(escr_sem).get('/impulso/metas/nova/').content.decode()
    t('sem gestor no próprio setor, a tela abre mesmo assim (dá para criar para um ADM)',
      'Ainda não há gestor do Impulso no seu setor' not in html and 'id="impParaQuem"' in html
      and 'Para mim</option>' not in html and '<div id="impBlocoAdm">' in html
      and 'id="impBlocoParaMim" class="space-y-4 hidden"' in html)

    html = c_adm1.get('/impulso/metas/nova/').content.decode()
    bloco_mim = re.search(r'<div id="impBlocoParaMim".*?<div id="impBlocoAdm"', html, flags=re.S).group(0)
    t('ADM de loja pedindo para si escolhe qualquer gestor, não só os da loja',
      f'<option value="{gestor_fora.id}">' in bloco_mim and f'<option value="{gestor_loja.id}">' in bloco_mim
      and 'Pode ser qualquer gestor do Impulso.' in bloco_mim)
    t('e vê os outros ADMs como "outra pessoa" (ele mesmo não)',
      f'data-nome="{nome(adm2)}"' in html and f'data-nome="{nome(adm1)}"' not in html)

    node = shutil.which('node')
    if node:
        scripts = [s for s in re.findall(r'<script>(.*?)</script>', html, flags=re.S) if s.strip()]
        erros = []
        for s in scripts:
            with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as arq:
                arq.write(s)
            r = subprocess.run([node, '--check', arq.name], capture_output=True, text=True, timeout=30)
            os.unlink(arq.name)
            if r.returncode:
                erros.append(r.stderr[-300:])
        t('o JS da tela é válido (node --check)', scripts and not erros, erros)

    print('\n== QUEM NÃO É GESTOR CRIA PARA UM ADM DE LOJA ==')
    meta, html = criar(c_escr, 'ZZ ADM atividade', colaborador=adm1.id, gestor_aprovador=gestor_fora.id,
                       precisa_aprovacao='nao')
    t('a atividade é criada para o ADM', meta is not None and meta.colaborador_id == adm1.id)
    t('com o gestor escolhido (de outra área, sem setor em comum) para aprovar e avaliar',
      meta and meta.gestor_id == gestor_fora.id)
    t('esperando a aprovação dele — mesmo com "não precisa de aprovação" no POST',
      meta and meta.aprovacao == Meta.Aprovacao.PENDENTE)
    t('registra quem pediu e quem criou', meta and meta.solicitada_por_id == escr.id and meta.created_by_id == escr.id)
    t('a tela diz para quem foi e onde acompanhar',
      f'Atividade enviada para {nome(gestor_fora)} aprovar' in html and f'Kanban de {nome(adm1)}' in html)
    t('só o gestor escolhido é avisado', avisos(gestor_fora, 'Nova atividade para aprovar') == [
        f'{nome(escr)} criou "ZZ ADM atividade" para {nome(adm1)}, com você para aprovar e avaliar. Aprove ou recuse.'])
    t('o gestor da loja e o ADM ainda não recebem nada',
      not Notification.objects.filter(user__in=[gestor_loja, adm1]).exists())
    t('não entra no Kanban do ADM antes da aprovação',
      'ZZ ADM atividade' not in c_adm1.get('/impulso/metas/').content.decode())

    html = c_escr.get('/impulso/metas/solicitacoes/').content.decode()
    t('quem pediu acompanha em Solicitações (para quem, para qual gestor, e pode cancelar)',
      'ZZ ADM atividade' in html and f'Para <strong>{nome(adm1)}</strong>' in html
      and f'Enviada para <strong>{nome(gestor_fora)}</strong>' in html
      and f'/impulso/metas/{meta.id}/cancelar-solicitacao/' in html)
    html = c_gfora.get('/impulso/metas/solicitacoes/').content.decode()
    t('o gestor escolhido vê o pedido com quem pediu e para quem, e os botões de decidir',
      f'Pedida por <strong>{nome(escr)}</strong>' in html and f'para <strong>{nome(adm1)}</strong>' in html
      and f'/impulso/metas/{meta.id}/decidir/' in html)
    html = c_escr.get(f'/impulso/metas/{meta.id}/').content.decode()
    t('a tela da atividade explica o pedido',
      re.search(rf'Pedida por {nome(escr)}\s+para {nome(adm1)} — aguardando a aprovação de\s+{nome(gestor_fora)}\.', html)
      is not None)

    r = cliente(gestor_outro).post(f'/impulso/metas/{meta.id}/decidir/', {'decisao': 'aprovar'}, follow=True)
    meta.refresh_from_db()
    t('outro gestor (nem o escolhido, nem da loja) não decide', meta.aprovacao == Meta.Aprovacao.PENDENTE
      and 'Apenas o gestor escolhido' in r.content.decode())
    t('quem pediu também não decide o próprio pedido', not meta.pode_decidir(escr))
    t('o gestor da loja pode decidir, se o escolhido não puder (regra que já existia)', meta.pode_decidir(gestor_loja))

    c_gfora.post(f'/impulso/metas/{meta.id}/decidir/', {'decisao': 'aprovar'})
    meta.refresh_from_db()
    t('o gestor escolhido aprova', meta.aprovacao == Meta.Aprovacao.APROVADA and meta.decidida_por_id == gestor_fora.id)
    t('o ADM é avisado de que a atividade é dele agora (dizendo quem pediu)', avisos(adm1, 'Solicitação aprovada') == [
        f'"ZZ ADM atividade", pedida por {nome(escr)} para você, foi aprovada e já está no seu Kanban.'])
    t('quem pediu recebe a resposta', avisos(escr, 'Pedido aprovado') == [
        f'"ZZ ADM atividade", que você pediu para {nome(adm1)}, foi aprovada por {nome(gestor_fora)} e já está no Kanban.'])
    t('entra no Kanban do ADM', 'ZZ ADM atividade' in c_adm1.get('/impulso/metas/').content.decode())
    t('e no Kanban do gestor escolhido', 'ZZ ADM atividade' in c_gfora.get('/impulso/metas/').content.decode())

    c_adm1.post(f'/impulso/metas/{meta.id}/entregar/', {'entrega_link': ''})
    meta.refresh_from_db()
    t('o ADM entrega e o gestor escolhido é avisado', meta.status == Meta.Status.ENTREGUE
      and Notification.objects.filter(user=gestor_fora, title='Meta entregue').exists())
    cliente(gestor_loja).post(f'/impulso/metas/{meta.id}/avaliar/', {'nota_qualidade': 1, 'nota_prazo': 1})
    meta.refresh_from_db()
    t('quem avalia é o gestor escolhido — o da loja não', meta.status == Meta.Status.ENTREGUE)
    c_gfora.post(f'/impulso/metas/{meta.id}/avaliar/', {'nota_qualidade': 5, 'nota_prazo': 4,
                                                         'avaliacao_comentario': 'ótimo'})
    meta.refresh_from_db()
    t('o gestor escolhido avalia e conclui', meta.status == Meta.Status.CONCLUIDA and meta.nota_qualidade == 5
      and meta.nota_prazo == 4 and meta.avaliado_por_id == gestor_fora.id)

    print('\n== RECUSA ==')
    recusada, _ = criar(c_escr, 'ZZ ADM recusada', colaborador=adm1.id, gestor_aprovador=gestor_fora.id)
    c_gfora.post(f'/impulso/metas/{recusada.id}/decidir/', {'decisao': 'recusar', 'motivo_recusa': 'já existe'})
    recusada.refresh_from_db()
    t('o gestor escolhido recusa', recusada.aprovacao == Meta.Aprovacao.RECUSADA)
    t('quem pediu recebe a recusa com o motivo', avisos(escr, 'Pedido recusado') == [
        f'"ZZ ADM recusada", que você pediu para {nome(adm1)}, foi recusada por {nome(gestor_fora)}. Motivo: já existe'])
    t('e o ADM recebe o aviso de sempre, sem o "sua meta" de quem pediu',
      avisos(adm1, 'Solicitação recusada') == [
          f'"ZZ ADM recusada", que {nome(escr)} pediu para você, foi recusada. Motivo: já existe'])

    print('\n== O QUE CONTINUA BARRADO ==')
    antes = Meta.objects.count()
    _, html = criar(c_escr, 'ZZ para colega', colaborador=colega.id, gestor_aprovador=gestor_fora.id)
    t("para colega do escritório (fora de ADM's LOJAS), não", Meta.objects.count() == antes
      and "só pode ser criada para quem está em ADM&#x27;s LOJAS" in html)
    _, html = criar(c_escr, 'ZZ sem gestor', colaborador=adm1.id)
    t('sem escolher o gestor, não', Meta.objects.count() == antes
      and 'Escolha o gestor que vai aprovar e avaliar a atividade.' in html)
    _, html = criar(c_escr, 'ZZ gestor falso', colaborador=adm1.id, gestor_aprovador=colega.id)
    t('com alguém que não é gestor do Impulso no lugar do gestor, não', Meta.objects.count() == antes)
    _, html = criar(c_escr, 'ZZ para mim fora', gestor=gestor_fora.id)
    t('para si mesmo, quem não é ADM continua pedindo ao gestor do próprio setor', Meta.objects.count() == antes
      and 'Escolha um gestor do seu setor.' in html)
    propria, _ = criar(c_escr, 'ZZ para mim', gestor=gestor_a.id)
    t('(e com o gestor do setor funciona como antes)', propria and propria.colaborador_id == escr.id
      and propria.aprovacao == Meta.Aprovacao.PENDENTE and propria.solicitada_por_id == escr.id)
    c_ga = cliente(gestor_a)
    c_ga.post(f'/impulso/metas/{propria.id}/decidir/', {'decisao': 'aprovar'})
    t('pedido para si mesmo aprovado: o aviso de sempre para quem pediu',
      avisos(escr, 'Solicitação aprovada') == ['Sua meta "ZZ para mim" foi aprovada e já está no Kanban.']
      and len(avisos(escr, 'Pedido aprovado')) == 1)          # o único "Pedido aprovado" é o do ADM, lá em cima

    print('\n== ADM DE LOJA ==')
    de_adm, _ = criar(c_adm1, 'ZZ ADM para ADM', colaborador=adm2.id, gestor_aprovador=gestor_outro.id)
    t('ADM cria para outro ADM, com qualquer gestor aprovando', de_adm and de_adm.colaborador_id == adm2.id
      and de_adm.gestor_id == gestor_outro.id and de_adm.aprovacao == Meta.Aprovacao.PENDENTE
      and de_adm.solicitada_por_id == adm1.id)
    para_si, _ = criar(c_adm1, 'ZZ ADM para si', gestor=gestor_fora.id)
    t('ADM pede para si a um gestor de fora da loja', para_si and para_si.colaborador_id == adm1.id
      and para_si.gestor_id == gestor_fora.id and para_si.aprovacao == Meta.Aprovacao.PENDENTE)
    sem_aprovacao, _ = criar(c_adm1, 'ZZ ADM para si direto', gestor=gestor_fora.id, precisa_aprovacao='nao')
    t('e, sendo atividade dele, pode dispensar a aprovação (como qualquer pedido para si)',
      sem_aprovacao and sem_aprovacao.aprovacao == Meta.Aprovacao.APROVADA)

    print('\n== GESTOR ==')
    html = c_gfora.get('/impulso/metas/nova/').content.decode()
    fora = json.loads(re.search(r'<script id="impForaJson" type="application/json">(.*?)</script>', html, flags=re.S).group(1))
    t('na tela do gestor, ADM de loja de outra área não vai para o gestor da loja: usa o gestor escolhido',
      fora.get(str(adm1.id)) == {'area': loja.name, 'adm_loja': True})
    t('colega do escritório de outra área continua indo para o gestor de lá',
      fora.get(str(escr.id), {}).get('gestores') == [{'id': gestor_a.id, 'nome': nome(gestor_a)}])
    t('o rótulo vira "Gestor que aprova e avalia" para ADM de loja',
      "adm ? 'Gestor que aprova e avalia' : rotulo.dataset.padrao" in html
      and 'data-padrao="Gestor responsável"' in html and 'id="impAjudaGestorAdm"' in html)

    direto, html = criar(c_gfora, 'ZZ gestor para ADM', colaborador=adm1.id, gestor=gestor_fora.id)
    t('gestor de fora da loja escolhendo a si mesmo: entra direto, sem pedido',
      direto and direto.aprovacao == Meta.Aprovacao.APROVADA and direto.gestor_id == gestor_fora.id
      and direto.solicitada_por_id is None)
    t('e o ADM é avisado', any('"ZZ gestor para ADM" foi atribuída a você.' == m for m in avisos(adm1, 'Nova meta atribuída')))

    pedido, html = criar(c_gfora, 'ZZ gestor pede para ADM', colaborador=adm1.id, gestor=gestor_outro.id)
    t('escolhendo outro gestor: espera a aprovação dele', pedido and pedido.aprovacao == Meta.Aprovacao.PENDENTE
      and pedido.gestor_id == gestor_outro.id and pedido.solicitada_por_id == gestor_fora.id)
    t('avisa o gestor escolhido — não o gestor da loja',
      avisos(gestor_outro, 'Nova atividade para aprovar') and not avisos(gestor_loja, 'Demanda de outra área para aprovar'))
    html = c_gfora.get('/impulso/metas/solicitacoes/').content.decode()
    t('o gestor que pediu acompanha o próprio pedido em Solicitações, sem botão de decidir',
      'ZZ gestor pede para ADM' in html and f'Para <strong>{nome(adm1)}</strong>' in html
      and f'/impulso/metas/{pedido.id}/decidir/' not in html
      and f'/impulso/metas/{pedido.id}/cancelar-solicitacao/' in html)
    c_gout = cliente(gestor_outro)
    html = c_gout.get('/impulso/metas/solicitacoes/').content.decode()
    t('e o escolhido decide por lá', f'/impulso/metas/{pedido.id}/decidir/' in html
      and f'Pedida por <strong>{nome(gestor_fora)}</strong>' in html)
    c_gout.post(f'/impulso/metas/{pedido.id}/decidir/', {'decisao': 'aprovar'})
    t('aprovado, o gestor que pediu é avisado',
      avisos(gestor_fora, 'Pedido aprovado') and Meta.objects.get(id=pedido.id).vale_pontos)

    da_loja, _ = criar(cliente(gestor_loja), 'ZZ gestor da loja', colaborador=adm1.id, gestor=gestor_fora.id)
    t('o gestor da própria loja continua criando direto, com outro gestor avaliando (regra que já existia)',
      da_loja and da_loja.aprovacao == Meta.Aprovacao.APROVADA and da_loja.gestor_id == gestor_fora.id
      and avisos(gestor_fora, 'Meta criada no seu nome'))

    outra_area, _ = criar(c_gfora, 'ZZ gestor outra area', colaborador=escr.id, gestor_aprovador=gestor_a.id)
    t('para o escritório de outra área, nada mudou: vai para o gestor de lá',
      outra_area and outra_area.aprovacao == Meta.Aprovacao.PENDENTE and outra_area.gestor_id == gestor_a.id
      and avisos(gestor_a, 'Demanda de outra área para aprovar'))
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
