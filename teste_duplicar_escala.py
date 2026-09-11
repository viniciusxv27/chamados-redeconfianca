"""Duplicar atividade do Impulso + tema escuro e semana toda na escala.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import sys
from datetime import date, time, timedelta

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
from impulso.models import GRUPO_ADM, GRUPO_GESTOR, Meta, MetaComentario, MetaItem
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
    adm = CommunicationGroup.objects.filter(name__iexact=GRUPO_ADM).first()
    ges = CommunicationGroup.objects.filter(name__iexact=GRUPO_GESTOR).first()
    assert adm and ges, 'grupos do Impulso não encontrados'

    area = Sector.objects.create(name='ZZ Area Duplicar')

    def novo(username, grupos=(), **kw):
        kw.setdefault('first_name', username.split('.')[1].title())
        kw.setdefault('last_name', 'Teste')
        u = User.objects.create_user(
            username=username, email=f'{username}@exemplo-teste.local',
            password='S3nha!teste', sector=area, **kw)
        for g in grupos:
            u.communication_groups.add(g)
        return u

    gestor = novo('dp.gestor', [adm, ges])
    colab = novo('dp.colab', [adm])
    participante = novo('dp.part', [adm])
    hoje = timezone.localdate()

    print('== DUPLICAR ATIVIDADE ==')
    original = Meta.objects.create(
        titulo='ZZ Atividade modelo', descricao='Descrição que deve ser copiada.',
        colaborador=colab, gestor=gestor, prazo=hoje + timedelta(days=10),
        aprovacao=Meta.Aprovacao.APROVADA, status=Meta.Status.CONCLUIDA,
        created_by=gestor, nota_qualidade=5, nota_prazo=4,
        avaliacao_comentario='Boa entrega', entrega_link='https://exemplo.local/x')
    original.participantes.add(participante)
    for n, texto in enumerate(['Passo um', 'Passo dois', 'Passo três']):
        MetaItem.objects.create(meta=original, texto=texto, ordem=n,
                                criado_por=gestor, concluido=True)
    MetaComentario.objects.create(meta=original, autor=gestor, mensagem='ZZ comentário antigo')

    cg = Client()
    cg.force_login(gestor)
    r = cg.post(f'/impulso/metas/{original.id}/duplicar/', follow=True)
    copia = Meta.objects.filter(titulo='Cópia de ZZ Atividade modelo').first()
    t('a cópia foi criada', copia is not None, r.status_code)
    t('abre direto na edição',
      r.redirect_chain and f'/impulso/metas/{copia.id}/editar/' in r.redirect_chain[-1][0],
      r.redirect_chain)

    print('\n== O QUE A CÓPIA LEVA ==')
    t('leva a descrição', copia.descricao == original.descricao)
    t('leva o colaborador', copia.colaborador_id == colab.id)
    t('leva o gestor', copia.gestor_id == gestor.id)
    t('leva a recorrência', copia.recorrencia == original.recorrencia)
    t('leva o prazo (ainda no futuro)', copia.prazo == original.prazo)
    t('leva os responsáveis',
      set(copia.participantes.values_list('id', flat=True)) == {participante.id})

    passos = list(copia.itens.order_by('ordem'))
    t('leva os passos do to-do', len(passos) == 3, len(passos))
    t('na mesma ordem', [p.texto for p in passos] == ['Passo um', 'Passo dois', 'Passo três'])
    t('os passos vêm desmarcados', all(not p.concluido for p in passos))

    print('\n== O QUE A CÓPIA NÃO LEVA ==')
    t('não leva o status: nasce A fazer', copia.status == Meta.Status.A_FAZER, copia.status)
    t('não leva a nota de qualidade', copia.nota_qualidade is None)
    t('não leva a nota de prazo', copia.nota_prazo is None)
    t('não leva o comentário da avaliação', not copia.avaliacao_comentario)
    t('não leva o link da entrega', not copia.entrega_link)
    t('não leva os comentários', copia.comentarios.count() == 0)
    t('nasce aprovada (entra no Kanban)', copia.aprovacao == Meta.Aprovacao.APROVADA)
    t('registra quem duplicou', copia.created_by_id == gestor.id)
    t('a original continua intacta',
      Meta.objects.filter(id=original.id, status=Meta.Status.CONCLUIDA).exists()
      and original.itens.count() == 3)

    print('\n== PRAZO VENCIDO NÃO VIRA CARD ATRASADO ==')
    velha = Meta.objects.create(
        titulo='ZZ Atividade vencida', colaborador=colab, gestor=gestor,
        prazo=hoje - timedelta(days=30), aprovacao=Meta.Aprovacao.APROVADA,
        created_by=gestor)
    cg.post(f'/impulso/metas/{velha.id}/duplicar/', follow=True)
    copia_velha = Meta.objects.filter(titulo='Cópia de ZZ Atividade vencida').first()
    t('prazo no passado vira hoje', copia_velha and copia_velha.prazo == hoje,
      copia_velha.prazo if copia_velha else '')

    print('\n== SEGURANÇA ==')
    cc = Client()
    cc.force_login(colab)
    antes = Meta.objects.count()
    r = cc.post(f'/impulso/metas/{original.id}/duplicar/', follow=True)
    t('colaborador não duplica direto', Meta.objects.count() == antes)
    # Desde que o PADRÃO pode PEDIR a duplicação, o POST direto dele vai para
    # a tela do pedido (que manda para o gestor aprovar) em vez de só negar.
    t('e vai para a tela de pedir a duplicação',
      bool(r.redirect_chain) and '/duplicar/solicitar/' in r.redirect_chain[-1][0],
      r.redirect_chain)

    estranho = novo('dp.estranho', [adm])
    ce = Client()
    ce.force_login(estranho)
    r = ce.post(f'/impulso/metas/{original.id}/duplicar/', follow=True)
    t('quem não responde pela atividade não duplica', Meta.objects.count() == antes)
    t('nem é mandado para o pedido',
      not any('/duplicar/solicitar/' in u for u, _ in r.redirect_chain), r.redirect_chain)

    r = cg.get(f'/impulso/metas/{original.id}/duplicar/')
    t('GET não duplica (405)', r.status_code == 405, r.status_code)

    r = cg.post('/impulso/metas/99999999/duplicar/')
    t('id inexistente devolve 404', r.status_code == 404, r.status_code)

    html = cg.get(f'/impulso/metas/{original.id}/').content.decode()
    t('o botão de duplicar aparece para o gestor',
      f'/impulso/metas/{original.id}/duplicar/' in html)
    html = cc.get(f'/impulso/metas/{original.id}/').content.decode()
    t('o colaborador não recebe o formulário de duplicar direto',
      f'action="/impulso/metas/{original.id}/duplicar/"' not in html)
    t('recebe o link de pedir a duplicação',
      f'/impulso/metas/{original.id}/duplicar/solicitar/' in html)

    print('\n== ESCOLHER O COLABORADOR DA CÓPIA ==')
    from core.models import Notification

    area_b = Sector.objects.create(name='ZZ Area Duplicar B')
    area_orfa = Sector.objects.create(name='ZZ Area Duplicar Sem Gestor')

    def mudar_setor(u, setor):
        u.sector = setor
        u.save(update_fields=['sector'])
        return u

    colab2 = novo('dp.colab2', [adm])
    gestor_b = mudar_setor(novo('dp.gestorb', [adm, ges]), area_b)
    colab_b = mudar_setor(novo('dp.colabb', [adm]), area_b)
    colab_orfao = mudar_setor(novo('dp.colaborfao', [adm]), area_orfa)
    fora_do_impulso = novo('dp.semgrupo')

    def duplicar_para(quem, cliente=cg):
        ja_havia = set(Meta.objects.filter(duplicada_de=original).values_list('id', flat=True))
        r = cliente.post(f'/impulso/metas/{original.id}/duplicar/', {'colaborador': quem.id}, follow=True)
        return r, Meta.objects.filter(duplicada_de=original).exclude(id__in=ja_havia).first()

    r, para_colab2 = duplicar_para(colab2)
    t('a cópia vai para o colaborador escolhido', para_colab2 and para_colab2.colaborador_id == colab2.id)
    t('da própria área: entra no Kanban na hora',
      para_colab2 and para_colab2.aprovacao == Meta.Aprovacao.APROVADA and para_colab2.solicitada_por_id is None)
    t('o gestor continua o da original', para_colab2 and para_colab2.gestor_id == gestor.id)
    t('continua levando os passos e os responsáveis', para_colab2 and para_colab2.itens.count() == 3
      and set(para_colab2.participantes.values_list('id', flat=True)) == {participante.id})
    t('abre direto na edição da cópia', para_colab2 and r.redirect_chain
      and f'/impulso/metas/{para_colab2.id}/editar/' in r.redirect_chain[-1][0], r.redirect_chain)
    t('a mensagem diz para quem foi', 'para Colab2 Teste' in r.content.decode())
    t('quem recebe é avisado', Notification.objects.filter(user=colab2, title='Nova meta atribuída').exists())

    r, para_mesmo = duplicar_para(colab)
    t('o mesmo colaborador da original: o duplicar de sempre', para_mesmo
      and para_mesmo.colaborador_id == colab.id and para_mesmo.aprovacao == Meta.Aprovacao.APROVADA)
    t('sem aviso de meta nova para quem já era o dono',
      not Notification.objects.filter(user=colab, title='Nova meta atribuída').exists())

    r, para_participante = duplicar_para(participante)
    t('um responsável da original pode receber a cópia',
      para_participante and para_participante.colaborador_id == participante.id)
    t('e não fica repetido como "outro responsável"',
      para_participante and not para_participante.participantes.filter(id=participante.id).exists())

    r, para_outra_area = duplicar_para(colab_b)
    t('de outra área: a cópia é criada', para_outra_area is not None)
    t('mas fica aguardando o gestor de lá aprovar',
      para_outra_area and para_outra_area.aprovacao == Meta.Aprovacao.PENDENTE)
    t('quem fica com a meta é o gestor da área do colaborador',
      para_outra_area and para_outra_area.gestor_id == gestor_b.id)
    t('e quem duplicou fica como quem pediu', para_outra_area and para_outra_area.solicitada_por_id == gestor.id)
    t('o gestor da outra área é avisado',
      Notification.objects.filter(user=gestor_b, title='Demanda de outra área para aprovar').exists())
    t('a tela explica que foi para aprovação', 'enviada para o gestor da área' in r.content.decode())
    t('quem duplicou ainda ajusta a cópia antes da aprovação', para_outra_area and r.redirect_chain
      and f'/impulso/metas/{para_outra_area.id}/editar/' in r.redirect_chain[-1][0], r.redirect_chain)

    antes = Meta.objects.count()
    r, nada = duplicar_para(colab_orfao)
    t('área sem gestor do Impulso: não cria a cópia', nada is None and Meta.objects.count() == antes)
    t('e explica o que fazer', 'não há gestor do Impulso cadastrado' in r.content.decode())
    r, nada = duplicar_para(fora_do_impulso)
    t('quem não é colaborador do Impulso não recebe cópia', nada is None and Meta.objects.count() == antes)
    t('e a tela pede outro colaborador', 'Escolha um colaborador do Impulso' in r.content.decode())
    r, nada = duplicar_para(colab2, cliente=cc)
    t('o PADRÃO não escolhe para quem vai: continua indo para o pedido', nada is None
      and bool(r.redirect_chain) and '/duplicar/solicitar/' in r.redirect_chain[-1][0], r.redirect_chain)

    kanban = cg.get('/impulso/metas/?mes=').content.decode()
    t('o Kanban tem a escolha do colaborador', 'id="impModalDuplicar"' in kanban and 'id="impDupColaborador"' in kanban)
    t('com quem é da área, sem marca', f'<option value="{colab2.id}">' in kanban)
    t('e marcando quem é de outra área', f'<option value="{colab_b.id}" data-fora="1">' in kanban)
    t('o card leva o dono da original, que vem marcado', f'data-colaborador="{colab.id}"' in kanban)
    html = cg.get(f'/impulso/metas/{original.id}/').content.decode()
    t('o detalhe também abre a escolha', 'id="impModalDuplicar"' in html and f'data-colaborador="{colab.id}"' in html)
    t('sem o confirm antigo, que duplicava direto', "confirm('Duplicar esta atividade" not in html)
    t('o colaborador não recebe a lista de pessoas',
      'id="impModalDuplicar"' not in cc.get(f'/impulso/metas/{original.id}/').content.decode())

    import re
    import shutil
    import subprocess
    import tempfile
    blocos = [b for b in re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', kanban, flags=re.S)
              if 'impModalDuplicar' in b]
    if shutil.which('node') and blocos:
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as fh:
            fh.write(blocos[0])
        rr = subprocess.run([shutil.which('node'), '--check', fh.name], capture_output=True, text=True)
        os.unlink(fh.name)
        t('o script da escolha tem sintaxe válida (node --check)', rr.returncode == 0, rr.stderr[-400:])

    print('\n== BOTÃO DE DUPLICAR NO CARD DO KANBAN ==')
    kanban = cg.get('/impulso/metas/?mes=').content.decode()
    t('o card traz o botão de duplicar', 'imp-duplicar' in kanban)
    t('apontando para a meta certa',
      f'class="imp-duplicar' in kanban and f'data-id="{original.id}"' in kanban)
    t('existe um formulário único na página', 'id="impFormDuplicar"' in kanban)
    t('clique duplo não cria duas cópias', "dataset.enviando === '1'" in kanban)
    t('o botão não dispara o arrastar do card',
      'e.stopPropagation();' in kanban and "setAttribute('draggable', 'false')" in kanban)

    kanban_colab = cc.get('/impulso/metas/?mes=').content.decode()
    # O seletor do script aparece para todo mundo (não acha nada); o que
    # importa é não existir botão nenhum no HTML.
    t('colaborador não vê o botão de duplicar direto no card',
      'class="imp-duplicar ' not in kanban_colab)
    t('vê o de pedir a duplicação', 'class="imp-duplicar-pedido' in kanban_colab)
    t('e nem o de excluir', 'class="imp-excluir' not in kanban_colab)

    antes_kanban = Meta.objects.count()
    r = cg.post(f'/impulso/metas/{original.id}/duplicar/', follow=True)
    t('duplicar pelo card cria a cópia', Meta.objects.count() == antes_kanban + 1)
    t('e abre a cópia na edição',
      r.redirect_chain and '/editar/' in r.redirect_chain[-1][0], r.redirect_chain)

    print('\n== ESCALA: TEMA ESCURO ==')
    css = open('static/css/tema-escuro.css', encoding='utf-8').read()
    t('a grade tem borda no escuro', 'html.dark .esc-grid th' in css)
    t('o cabeçalho da grade tem fundo escuro', 'html.dark .esc-grid thead th' in css)
    t('a coluna fixa do nome deixa de ser branca', 'html.dark .esc-nome' in css)
    t('os campos de horário ficam escuros', 'html.dark .esc-time' in css)
    t('o dia de hoje continua destacado', 'html.dark .esc-hoje' in css)
    t('as chaves do CSS estão balanceadas', css.count('{') == css.count('}'))

    print('\n== ESCALA: DEFINIR A SEMANA TODA ==')
    gerentes, _ = CommunicationGroup.objects.get_or_create(
        name='GERENTES', defaults={'created_by': gestor})
    gerente = novo('dp.gerente', hierarchy='PADRAO')
    gerente.communication_groups.add(gerentes)
    escalado = novo('dp.escalado', hierarchy='PADRAO')

    ce = Client()
    ce.force_login(gerente)
    seg = hoje - timedelta(days=hoje.weekday())
    html = ce.get(f'/ponto/escala/?inicio={seg.isoformat()}').content.decode()

    t('a linha tem o botão da semana', 'escAbrirSemana' in html and 'Definir a semana toda' in html)
    t('existe o painel', 'escModalSemana' in html)
    t('o painel pede os quatro horários',
      all(x in html for x in ('escSemEntrada', 'escSemSaidaAlmoco',
                              'escSemVoltaAlmoco', 'escSemSaida')))
    t('dá para escolher em quais dias aplicar', 'escSemDias' in html and 'esc-sem-dia' in html)
    t('mostra a prévia das horas', 'escSemPrevia' in html)
    t('folga não é sobrescrita', 'folga.checked) return' in html)
    t('preencher recalcula o total da linha', 'escRecalcular(escLinhaAlvo)' in html)
    t('o colaborador aparece na grade do gerente',
      escalado.get_full_name() in html or 'colaborador' in html)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
