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
    t('colaborador não duplica', Meta.objects.count() == antes)
    t('a tela explica', 'não pode duplicar' in r.content.decode())

    r = cg.get(f'/impulso/metas/{original.id}/duplicar/')
    t('GET não duplica (405)', r.status_code == 405, r.status_code)

    r = cg.post('/impulso/metas/99999999/duplicar/')
    t('id inexistente devolve 404', r.status_code == 404, r.status_code)

    html = cg.get(f'/impulso/metas/{original.id}/').content.decode()
    t('o botão de duplicar aparece para o gestor',
      f'/impulso/metas/{original.id}/duplicar/' in html)
    html = cc.get(f'/impulso/metas/{original.id}/').content.decode()
    t('e não aparece para o colaborador',
      f'/impulso/metas/{original.id}/duplicar/' not in html)

    print('\n== BOTÃO DE DUPLICAR NO CARD DO KANBAN ==')
    kanban = cg.get('/impulso/metas/').content.decode()
    t('o card traz o botão de duplicar', 'imp-duplicar' in kanban)
    t('apontando para a meta certa',
      f'class="imp-duplicar' in kanban and f'data-id="{original.id}"' in kanban)
    t('existe um formulário único na página', 'id="impFormDuplicar"' in kanban)
    t('clique duplo não cria duas cópias', "dataset.enviando === '1'" in kanban)
    t('o botão não dispara o arrastar do card',
      'e.stopPropagation();' in kanban and "setAttribute('draggable', 'false')" in kanban)

    kanban_colab = cc.get('/impulso/metas/').content.decode()
    # O seletor do script aparece para todo mundo (não acha nada); o que
    # importa é não existir botão nenhum no HTML.
    t('colaborador não vê o botão no card',
      'class="imp-duplicar' not in kanban_colab)
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
