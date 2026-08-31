"""Impulso/Conectar: SUPERADMIN e gestor apagam cursos, vídeos e POPs.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import Client
from django.utils import timezone

from communications.models import CommunicationGroup
from impulso.models import (GRUPO_ADM, GRUPO_GESTOR, ConclusaoConteudo,
                            ConteudoConectar)

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

    def novo(username, grupos=(), **kw):
        u = User.objects.create_user(
            username=username, email=f'{username}@exemplo-teste.local',
            password='S3nha!teste', first_name=username.split('.')[1].title(),
            last_name='Teste', **kw)
        for g in grupos:
            u.communication_groups.add(g)
        return u

    gestor = novo('cx.gestor', [adm, ges])
    outro_gestor = novo('cx.gestor2', [adm, ges])
    chefe = novo('cx.chefe', [adm], hierarchy='SUPERADMIN')
    colab = novo('cx.colab', [adm])
    colab2 = novo('cx.colab2', [adm])

    def novo_conteudo(titulo, autor, tipo=ConteudoConectar.Tipo.CURSO):
        return ConteudoConectar.objects.create(
            tipo=tipo, titulo=titulo, descricao='ZZ conteúdo de teste',
            url='https://exemplo.local/curso', obrigatorio=True, criado_por=autor)

    print('== QUEM PODE APAGAR ==')
    curso = novo_conteudo('ZZ Curso do gestor', gestor)
    t('quem criou apaga', curso.pode_excluir(gestor))
    t('outro gestor também apaga', curso.pode_excluir(outro_gestor))
    t('SUPERADMIN apaga', curso.pode_excluir(chefe))
    t('colaborador não apaga', not curso.pode_excluir(colab))

    pop = novo_conteudo('ZZ POP da equipe', colab, tipo=ConteudoConectar.Tipo.POP)
    t('nem o POP que ele mesmo subiu', not pop.pode_excluir(colab))
    t('mas o gestor apaga o POP da equipe', pop.pode_excluir(gestor))

    print('\n== O QUE SOME JUNTO ==')
    ConclusaoConteudo.objects.create(conteudo=curso, user=colab, concluido=True,
                                     concluido_em=timezone.now())
    ConclusaoConteudo.objects.create(
        conteudo=curso, user=colab2, concluido=True, concluido_em=timezone.now(),
        certificado=SimpleUploadedFile('cert.pdf', b'%PDF-1.4 x'))
    impacto = curso.impacto_da_exclusao
    t('conta as conclusões', impacto['conclusoes'] == 2, impacto)
    t('conta quem concluiu', impacto['concluidos'] == 2)
    t('conta os certificados', impacto['certificados'] == 1, impacto)

    print('\n== EXCLUSÃO PELA TELA ==')
    cg = Client()
    cg.force_login(gestor)
    r = cg.post(f'/impulso/conectar/{curso.id}/excluir/', follow=True)
    t('gestor apaga pela tela', not ConteudoConectar.objects.filter(id=curso.id).exists(),
      r.status_code)
    t('as conclusões caem junto',
      not ConclusaoConteudo.objects.filter(conteudo_id=curso.id).exists())
    t('a tela avisa que a pontuação mudou',
      'pontuação do mês delas foi recalculada' in r.content.decode())

    try:
        from core.models import Notification
        t('quem tinha concluído é avisado',
          Notification.objects.filter(user=colab,
                                      title='Conteúdo removido do Conectar').exists())
    except Exception as exc:                                    # noqa: BLE001
        t('quem tinha concluído é avisado', False, exc)

    sem_conclusao = novo_conteudo('ZZ Curso sem conclusao', gestor)
    r = cg.post(f'/impulso/conectar/{sem_conclusao.id}/excluir/', follow=True)
    # A palavra "recalculada" também aparece no texto da confirmação da tela;
    # o que interessa é a mensagem do sistema, não o script.
    corpo = r.content.decode()
    # As aspas do título saem como &quot; no HTML — comparar o texto cru falharia.
    t('sem conclusão, a mensagem é simples',
      'ZZ Curso sem conclusao' in corpo and 'excluído.' in corpo
      and 'pontuação do mês delas foi recalculada' not in corpo)

    print('\n== SEGURANÇA ==')
    protegido = novo_conteudo('ZZ Curso protegido', gestor)
    cc = Client()
    cc.force_login(colab)
    r = cc.post(f'/impulso/conectar/{protegido.id}/excluir/', follow=True)
    t('colaborador não apaga pela tela',
      ConteudoConectar.objects.filter(id=protegido.id).exists())
    t('a tela explica a recusa',
      'Só o SUPERADMIN ou um gestor' in r.content.decode())

    r = Client().post(f'/impulso/conectar/{protegido.id}/excluir/')
    t('anônimo não apaga', ConteudoConectar.objects.filter(id=protegido.id).exists())

    r = cg.get(f'/impulso/conectar/{protegido.id}/excluir/')
    t('GET não apaga (405)', r.status_code == 405, r.status_code)

    r = cg.post('/impulso/conectar/99999999/excluir/')
    t('id inexistente devolve 404', r.status_code == 404, r.status_code)

    print('\n== O BOTÃO APARECE SÓ PARA QUEM PODE ==')
    alvo = f'data-id="{protegido.id}"'
    html_gestor = cg.get('/impulso/conectar/').content.decode()
    t('gestor vê a lixeira na lista',
      alvo in html_gestor and 'imp-conteudo-excluir' in html_gestor)
    html_colab = cc.get('/impulso/conectar/').content.decode()
    t('colaborador não vê lixeira na lista',
      'class="imp-conteudo-excluir' not in html_colab)

    html_gestor = cg.get(f'/impulso/conectar/{protegido.id}/').content.decode()
    t('gestor vê o botão no detalhe',
      f'/impulso/conectar/{protegido.id}/excluir/' in html_gestor)
    html_colab = cc.get(f'/impulso/conectar/{protegido.id}/').content.decode()
    t('colaborador não vê o botão no detalhe',
      f'/impulso/conectar/{protegido.id}/excluir/' not in html_colab)

    print('\n== A CONFIRMAÇÃO DIZ O TAMANHO DO ESTRAGO ==')
    com_gente = novo_conteudo('ZZ Curso com gente', gestor)
    ConclusaoConteudo.objects.create(conteudo=com_gente, user=colab, concluido=True,
                                     concluido_em=timezone.now())
    html = cg.get('/impulso/conectar/').content.decode()
    t('o card leva o número de concluintes',
      f'data-concluidos="1"' in html)
    html = cg.get(f'/impulso/conectar/{com_gente.id}/').content.decode()
    t('o detalhe avisa antes de clicar', 'já concluíram este conteúdo' in html)
    t('e a confirmação existe', 'impConfirmarExclusao' in html)

    print('\n== SUPERADMIN SEM SER GESTOR DO IMPULSO ==')
    do_chefe = novo_conteudo('ZZ Curso do chefe', gestor)
    cs = Client()
    cs.force_login(chefe)
    r = cs.post(f'/impulso/conectar/{do_chefe.id}/excluir/', follow=True)
    t('SUPERADMIN apaga pela tela',
      not ConteudoConectar.objects.filter(id=do_chefe.id).exists(), r.status_code)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
