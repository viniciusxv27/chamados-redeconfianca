"""Impulso: filtro por nome e setor em todas as telas + POP e vídeo num card só.

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
from django.test import Client, RequestFactory
from django.utils import timezone

from communications.models import CommunicationGroup
from impulso import filtros
from impulso.models import (GRUPO_ADM, GRUPO_GESTOR, ConclusaoConteudo,
                            ConteudoConectar, Meta)
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
    loja_a = Sector.objects.create(name='ZZ Loja A')
    loja_b = Sector.objects.create(name='ZZ Loja B')

    def novo(u, nome, sobrenome, setor, grupos=(), **kw):
        x = User.objects.create_user(
            username=u, email=f'{u}@exemplo-teste.local', password='S3nha!teste',
            sector=setor, first_name=nome, last_name=sobrenome, **kw)
        for g in grupos:
            x.communication_groups.add(g)
        return x

    chefe = novo('zz.chefe', 'Zelia', 'Chefe', loja_a, [adm, ges],
                 is_superuser=True, is_staff=True)
    ana = novo('zz.ana', 'ZZAna', 'Lima', loja_a, [adm])
    bruno = novo('zz.bruno', 'ZZBruno', 'Costa', loja_b, [adm])

    print('== O QUE A BUSCA ACEITA ==')
    def f(qs=''):
        return filtros.ler(RequestFactory().get(f'/x/?{qs}'))

    pessoas = User.objects.filter(id__in=[ana.id, bruno.id])
    t('acha pelo primeiro nome',
      list(filtros.pessoas(pessoas, f('q=ZZAna'))) == [ana])
    t('acha pelo sobrenome',
      list(filtros.pessoas(pessoas, f('q=Costa'))) == [bruno])
    t('nome composto vira E, não OU',
      list(filtros.pessoas(pessoas, f('q=ZZAna Lima'))) == [ana])
    t('"ZZAna Costa" não existe e devolve vazio',
      list(filtros.pessoas(pessoas, f('q=ZZAna Costa'))) == [])
    t('acha pelo login', list(filtros.pessoas(pessoas, f('q=zz.bruno'))) == [bruno])
    t('não diferencia maiúscula',
      list(filtros.pessoas(pessoas, f('q=zzana'))) == [ana])
    t('filtra por setor',
      list(filtros.pessoas(pessoas, f(f'setor={loja_b.id}'))) == [bruno])
    t('nome + setor juntos são E',
      list(filtros.pessoas(pessoas, f(f'q=ZZAna&setor={loja_b.id}'))) == [])
    t('setor inválido não quebra', filtros.ler(
        RequestFactory().get('/x/?setor=abc'))['setor'] is None)
    t('sem filtro devolve tudo',
      set(filtros.pessoas(pessoas, f())) == {ana, bruno})

    ana.sectors.add(loja_b)
    t('setor vinculado também conta',
      ana in filtros.pessoas(pessoas, f(f'setor={loja_b.id}')))
    ana.sectors.clear()

    print('\n== O FILTRO CHEGA NAS TELAS ==')
    hoje = timezone.localdate()
    m_ana = Meta.objects.create(titulo='ZZ Meta da Ana', colaborador=ana,
                                gestor=chefe, prazo=hoje,
                                aprovacao=Meta.Aprovacao.APROVADA)
    m_bruno = Meta.objects.create(titulo='ZZ Meta do Bruno', colaborador=bruno,
                                  gestor=chefe, prazo=hoje,
                                  aprovacao=Meta.Aprovacao.APROVADA)

    c = Client(); c.force_login(chefe)

    TELAS = [
        ('/impulso/metas/', 'Kanban de Metas'),
        ('/impulso/atividades/', 'Próximas Atividades'),
        ('/impulso/feedbacks/', 'Feedbacks'),
        ('/impulso/assiduidade/', 'Assiduidade'),
        ('/impulso/metas/solicitacoes/', 'Solicitações'),
        ('/impulso/conectar/', 'Cursos, Vídeos e POPs'),
        ('/impulso/conectar/projetos/', 'Projetos Foco'),
        ('/impulso/minhas-tarefas/', 'Minhas Tarefas'),
        ('/impulso/acompanhamento/', 'Ranking do mês'),
    ]
    for url, nome in TELAS:
        r = c.get(url)
        corpo = r.content.decode() if r.status_code == 200 else ''
        t(f'{nome}: a barra de filtro aparece',
          r.status_code == 200 and 'name="q"' in corpo and 'name="setor"' in corpo,
          r.status_code)
        t(f'{nome}: o seletor traz os setores',
          'Todos os setores' in corpo and 'ZZ Loja A' in corpo)

    print('\n== FILTRAR MUDA O RESULTADO ==')
    html = c.get('/impulso/metas/?q=ZZAna').content.decode()
    t('kanban: só a meta da Ana',
      'ZZ Meta da Ana' in html and 'ZZ Meta do Bruno' not in html)
    html = c.get(f'/impulso/metas/?setor={loja_b.id}').content.decode()
    t('kanban por setor: só a do Bruno',
      'ZZ Meta do Bruno' in html and 'ZZ Meta da Ana' not in html)
    html = c.get('/impulso/metas/').content.decode()
    t('sem filtro: as duas', 'ZZ Meta da Ana' in html and 'ZZ Meta do Bruno' in html)

    html = c.get('/impulso/acompanhamento/?q=ZZAna').content.decode()
    t('ranking: só a Ana', 'ZZAna' in html and 'ZZBruno' not in html)

    html = c.get('/impulso/atividades/?q=ZZBruno').content.decode()
    t('próximas atividades: o gestor vê a da equipe filtrada',
      'ZZ Meta do Bruno' in html and 'ZZ Meta da Ana' not in html)

    print('\n== O FILTRO NÃO ATROPELA OS OUTROS PARÂMETROS ==')
    html = c.get('/impulso/assiduidade/?mes=3&ano=2026&q=ZZAna').content.decode()
    t('mês e ano sobrevivem como hidden',
      'name="mes" value="3"' in html and 'name="ano" value="2026"' in html)
    t('e o Limpar mantém o mês', 'mes=3' in html)

    print('\n== O KANBAN LEMBRA O FILTRO NO VOLTAR ==')
    c.get(f'/impulso/metas/?q=ZZAna&setor={loja_a.id}')
    html = c.get(f'/impulso/metas/{m_ana.id}/').content.decode()
    t('o Voltar da meta leva o filtro junto',
      'q=ZZAna' in html and f'setor={loja_a.id}' in html)

    print('\n== O COLABORADOR NÃO VÊ A EQUIPE ==')
    cc = Client(); cc.force_login(ana)
    html = cc.get('/impulso/atividades/').content.decode()
    t('atividades: a Ana só vê a dela',
      'ZZ Meta da Ana' in html and 'ZZ Meta do Bruno' not in html)
    html = cc.get('/impulso/atividades/?q=ZZBruno').content.decode()
    t('e nem filtrando alcança a do Bruno', 'ZZ Meta do Bruno' not in html)

    print('\n== INOVAR CONTINUA ANÔNIMO ==')
    html = c.get('/impulso/inovar/').content.decode()
    t('a tela de ideias NÃO ganhou filtro por nome',
      'name="setor"' not in html)

    print('\n== POP E VÍDEO NO MESMO CARD ==')
    pdf = SimpleUploadedFile('zz_pop.pdf', b'%PDF-1.4 zz', content_type='application/pdf')
    mp4 = SimpleUploadedFile('zz_video.mp4', b'\x00\x00\x00\x18ftypmp42',
                             content_type='video/mp4')
    junto = ConteudoConectar.objects.create(
        tipo=ConteudoConectar.Tipo.POP, titulo='ZZ POP com vídeo',
        arquivo=pdf, video=mp4, criado_por=chefe)

    t('o card tem documento', junto.documento is not None)
    t('e vídeo', junto.arquivo_de_video is not None)
    t('o vídeo é o campo vídeo, não o PDF',
      junto.arquivo_de_video.name.endswith('.mp4'))
    t('o documento é o PDF, não o vídeo',
      junto.documento.name.endswith('.pdf'))
    t('é tocável', junto.video_reproduzivel)
    t('e o rótulo diz os dois', junto.rotulo == 'Vídeo e POP', junto.rotulo)
    t('está no grupo Vídeo e POP',
      junto.grupo == ConteudoConectar.GRUPO_POP_VIDEO)

    so_pdf = ConteudoConectar.objects.create(
        tipo=ConteudoConectar.Tipo.POP, titulo='ZZ POP sozinho',
        arquivo=SimpleUploadedFile('zz2.pdf', b'%PDF zz', content_type='application/pdf'),
        criado_por=chefe)
    t('POP sem vídeo não vira obrigatório de assistir',
      not so_pdf.video_reproduzivel)
    t('e o rótulo é POP', so_pdf.rotulo == 'POP', so_pdf.rotulo)

    # Vídeo antigo, subido antes do campo existir: mora no `arquivo`.
    legado = ConteudoConectar.objects.create(
        tipo=ConteudoConectar.Tipo.VIDEO, titulo='ZZ Vídeo antigo',
        arquivo=SimpleUploadedFile('zz3.mp4', b'\x00ftyp', content_type='video/mp4'),
        criado_por=chefe)
    t('vídeo antigo continua tocando', legado.video_reproduzivel)
    t('e não vira documento', legado.documento is None)
    t('rótulo Vídeo', legado.rotulo == 'Vídeo', legado.rotulo)

    curso = ConteudoConectar.objects.create(
        tipo=ConteudoConectar.Tipo.CURSO, titulo='ZZ Curso',
        url='https://exemplo.test/curso', criado_por=chefe)
    t('curso fica no grupo dele',
      curso.grupo == ConteudoConectar.GRUPO_CURSO)
    t('rótulo Curso', curso.rotulo == 'Curso')

    print('\n== A TELA MOSTRA DUAS SEÇÕES, NÃO TRÊS ==')
    html = c.get('/impulso/conectar/').content.decode()
    t('tem "Cursos"', '>Cursos<' in html or 'Cursos</h3>' in html)
    t('tem "Vídeos e POPs"', 'Vídeos e POPs' in html)
    t('não tem mais a seção "POPs" separada', '>POPs</h3>' not in html)
    t('não tem mais a seção "Vídeos" separada',
      'fa-video mr-1"></i>Vídeos</h3>' not in html)
    t('o card avisa que tem vídeo e POP',
      'ZZ POP com vídeo' in html)

    print('\n== O DETALHE ABRE OS DOIS ==')
    html = c.get(f'/impulso/conectar/{junto.id}/').content.decode()
    t('toca o vídeo', 'id="impVideo"' in html)
    t('a fonte é o mp4', '.mp4' in html)
    t('e oferece o POP junto', 'Abrir o POP' in html)
    t('o selo diz Vídeo e POP', 'Vídeo e POP' in html)

    print('\n== O FORMULÁRIO OFERECE OS DOIS ANEXOS ==')
    html = c.get('/impulso/conectar/novo/').content.decode()
    t('campo de documento', 'name="arquivo"' in html)
    t('campo de vídeo', 'name="video"' in html)
    t('o seletor é por grupo', 'name="grupo"' in html and 'name="tipo"' not in html)
    t('com as duas opções', 'Vídeo e POP' in html and '>Curso<' in html)

    r = c.post('/impulso/conectar/novo/', {
        'grupo': 'POP_VIDEO', 'titulo': 'ZZ Criado pela tela',
        'descricao': '', 'url': '',
        'arquivo': SimpleUploadedFile('zz4.pdf', b'%PDF x', content_type='application/pdf'),
        'video': SimpleUploadedFile('zz4.mp4', b'\x00ftyp', content_type='video/mp4'),
    }, follow=True)
    criado = ConteudoConectar.objects.filter(titulo='ZZ Criado pela tela').first()
    t('criou pelo formulário', criado is not None)
    t('com os dois anexos',
      criado and criado.documento and criado.arquivo_de_video)
    t('guardado como POP (tem documento)',
      criado and criado.tipo == ConteudoConectar.Tipo.POP, criado and criado.tipo)

    r = c.post('/impulso/conectar/novo/', {
        'grupo': 'POP_VIDEO', 'titulo': 'ZZ Só vídeo', 'descricao': '', 'url': '',
        'video': SimpleUploadedFile('zz5.mp4', b'\x00ftyp', content_type='video/mp4'),
    }, follow=True)
    sovideo = ConteudoConectar.objects.filter(titulo='ZZ Só vídeo').first()
    t('só vídeo é guardado como VIDEO',
      sovideo and sovideo.tipo == ConteudoConectar.Tipo.VIDEO,
      sovideo and sovideo.tipo)
    t('e cai no mesmo grupo',
      sovideo and sovideo.grupo == ConteudoConectar.GRUPO_POP_VIDEO)

    print('\n== A PONTUAÇÃO CONTINUA VENDO OS DOIS JUNTOS ==')
    from decimal import Decimal

    from impulso.scoring import _nota_conteudos, periodo_do_mes
    inicio, fim = periodo_do_mes()

    def total(tipos):
        _n, _m, d = _nota_conteudos(ana, tipos, Decimal('10'), inicio, fim)
        return d.get('total', 0)

    juntos = total([ConteudoConectar.Tipo.VIDEO, ConteudoConectar.Tipo.POP])
    t('vídeo e POP somam no mesmo balde da nota',
      juntos == total([ConteudoConectar.Tipo.VIDEO]) + total([ConteudoConectar.Tipo.POP]),
      juntos)
    t('e o curso fica num balde separado',
      total([ConteudoConectar.Tipo.CURSO]) != juntos or juntos == 0)

    print('\n== CONCLUIR UM POP COM VÍDEO EXIGE ASSISTIR ==')
    ca = Client(); ca.force_login(ana)
    r = ca.post(f'/impulso/conectar/{junto.id}/concluir/', follow=True)
    conc = ConclusaoConteudo.objects.filter(conteudo=junto, user=ana).first()
    t('não conclui sem ver o vídeo', not (conc and conc.concluido))
    t('e a tela explica', 'Assista o vídeo até o fim' in r.content.decode())

    r = ca.post(f'/impulso/conectar/{so_pdf.id}/concluir/', follow=True)
    conc2 = ConclusaoConteudo.objects.filter(conteudo=so_pdf, user=ana).first()
    t('POP sem vídeo conclui normal', conc2 and conc2.concluido)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
