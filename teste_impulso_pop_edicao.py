"""Impulso — Conectar: quem enviou o POP edita o arquivo enviado.

Quem enviou troca o arquivo e corrige título, descrição e link. Obrigatoriedade,
público, período e exclusão continuam com o gestor/SUPERADMIN.

Roda dentro de uma transação desfeita no fim: não grava nada no banco. Os
arquivos vão para um armazenamento em memória (nada chega ao MinIO).
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
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import Client
from django.utils import timezone

from communications.models import CommunicationGroup
from impulso.models import GRUPO_ADM, GRUPO_GESTOR, ConteudoConectar
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


memoria = InMemoryStorage()
for campo in ('arquivo', 'video'):
    ConteudoConectar._meta.get_field(campo).storage = memoria


def pdf(nome='pop.pdf', texto=b'%PDF-1.4 versao 1'):
    return SimpleUploadedFile(nome, texto, content_type='application/pdf')


marcador = transaction.atomic()
marcador.__enter__()
try:
    adm = CommunicationGroup.objects.filter(name__iexact=GRUPO_ADM).first()
    ges = CommunicationGroup.objects.filter(name__iexact=GRUPO_GESTOR).first()
    area = Sector.objects.create(name='ZZ Area POP Edicao')

    def novo(u, nome, grupos=(), **kw):
        x = User.objects.create_user(
            username=u, email=f'{u}@exemplo-teste.local', password='S3nha!teste',
            sector=area, first_name=nome, last_name='Teste', **kw)
        for g in grupos:
            x.communication_groups.add(g)
        return x

    gestor = novo('zzpop.gestor', 'ZZPopGestor', [adm, ges])
    autor = novo('zzpop.autor', 'ZZPopAutor', [adm])
    colega = novo('zzpop.colega', 'ZZPopColega', [adm])
    superadmin = novo('zzpop.super', 'ZZPopSuper', [adm], hierarchy='SUPERADMIN')

    ca, cg, cc, cs = Client(), Client(), Client(), Client()
    ca.force_login(autor)
    cg.force_login(gestor)
    cc.force_login(colega)
    cs.force_login(superadmin)

    print('== A EQUIPE ENVIA O POP ==')
    r = ca.post('/impulso/conectar/novo/', {'grupo': 'POP_VIDEO', 'titulo': 'ZZPOP Abertura de loja',
                                            'descricao': 'versão 1', 'arquivo': pdf()})
    pop = ConteudoConectar.objects.filter(titulo='ZZPOP Abertura de loja').first()
    t('POP criado pela equipe', pop is not None and pop.criado_por_id == autor.pk and pop.criado_por_equipe)
    arquivo_v1 = pop.arquivo.name
    t('com o arquivo guardado', bool(arquivo_v1) and memoria.exists(arquivo_v1), arquivo_v1)

    # O gestor torna obrigatório para alguém, com período — isso o autor não pode desfazer.
    inicio = timezone.localdate()
    r = cg.post(f'/impulso/conectar/{pop.id}/editar/', {
        'grupo': 'POP_VIDEO', 'titulo': pop.titulo, 'descricao': pop.descricao,
        'obrigatorio': 'on', 'obrigatorio_para': [colega.pk],
        'inicio': inicio.isoformat(), 'fim': (inicio + timedelta(days=30)).isoformat()})
    pop.refresh_from_db()
    t('o gestor edita tudo (obrigatório, público, período)',
      pop.obrigatorio and list(pop.obrigatorio_para.values_list('pk', flat=True)) == [colega.pk]
      and pop.inicio == inicio, (pop.obrigatorio, pop.inicio))

    print('\n== QUEM ENVIOU VÊ ONDE EDITAR ==')
    html = ca.get(f'/impulso/conectar/{pop.id}/').content.decode()
    t('o detalhe mostra "Editar meu POP"', 'Editar meu POP' in html)
    t('e não mostra excluir', 'Excluir conteúdo' not in html)
    html = ca.get('/impulso/conectar/').content.decode()
    t('a lista mostra o lápis no POP dele', f'/impulso/conectar/{pop.id}/editar/' in html
      and 'Editar o POP que você enviou' in html)
    t('e não mostra a lixeira dele', f'data-id="{pop.id}"' not in html)

    html = ca.get(f'/impulso/conectar/{pop.id}/editar/').content.decode()
    t('o formulário abre para quem enviou', 'Salvar alterações' in html and 'Você enviou este POP' in html)
    t('sem os campos do gestor', 'name="obrigatorio_para"' not in html and 'name="obrigatorio"' not in html
      and 'name="inicio"' not in html and 'name="grupo"' not in html)
    t('com o campo para trocar o arquivo', 'name="arquivo"' in html and '<strong>substitui</strong>' in html)

    print('\n== QUEM ENVIOU TROCA O ARQUIVO ==')
    r = ca.post(f'/impulso/conectar/{pop.id}/editar/', {
        'titulo': 'ZZPOP Abertura de loja (revisado)', 'descricao': 'versão 2', 'url': 'https://exemplo.com/pop',
        'arquivo': pdf('pop-v2.pdf', b'%PDF-1.4 versao 2'),
        # Tentativa de mexer no que é do gestor: tem que ser ignorada.
        'grupo': 'CURSO', 'obrigatorio_para': [autor.pk], 'inicio': '', 'fim': ''})
    pop.refresh_from_db()
    t('salvar redireciona para o detalhe', r.status_code == 302 and r['Location'].endswith(f'/impulso/conectar/{pop.id}/'),
      (r.status_code, r.get('Location')))
    t('o arquivo foi trocado', pop.arquivo.name != arquivo_v1 and memoria.open(pop.arquivo.name).read() == b'%PDF-1.4 versao 2')
    t('o arquivo antigo foi apagado', not memoria.exists(arquivo_v1))
    t('título, descrição e link atualizados', pop.titulo == 'ZZPOP Abertura de loja (revisado)'
      and pop.descricao == 'versão 2' and pop.url == 'https://exemplo.com/pop')
    t('continua POP (não virou curso)', pop.tipo == ConteudoConectar.Tipo.POP)
    t('obrigatoriedade mantida', pop.obrigatorio is True)
    t('público mantido', list(pop.obrigatorio_para.values_list('pk', flat=True)) == [colega.pk])
    t('período mantido', pop.inicio == inicio and pop.fim == inicio + timedelta(days=30))

    r = ca.post(f'/impulso/conectar/{pop.id}/editar/', {
        'titulo': pop.titulo, 'descricao': pop.descricao, 'url': pop.url,
        'video': SimpleUploadedFile('aula.mp4', b'\x00\x00\x00\x18ftypmp42', content_type='video/mp4')})
    pop.refresh_from_db()
    t('quem enviou também anexa o vídeo', pop.video_reproduzivel and pop.documento is not None)
    video_antigo = pop.video.name
    ca.post(f'/impulso/conectar/{pop.id}/editar/', {
        'titulo': pop.titulo, 'descricao': pop.descricao, 'url': pop.url, 'remover_video': 'on'})
    pop.refresh_from_db()
    t('e remove o vídeo', not pop.video and not memoria.exists(video_antigo))

    print('\n== QUEM NÃO ENVIOU NÃO EDITA ==')
    r = cc.get(f'/impulso/conectar/{pop.id}/editar/')
    t('o colega é mandado de volta', r.status_code == 302 and r['Location'].endswith(f'/impulso/conectar/{pop.id}/'))
    r = cc.post(f'/impulso/conectar/{pop.id}/editar/', {'titulo': 'ZZPOP invadido', 'arquivo': pdf('x.pdf')})
    pop.refresh_from_db()
    t('e o POST dele não muda nada', pop.titulo == 'ZZPOP Abertura de loja (revisado)')
    html = cc.get(f'/impulso/conectar/{pop.id}/').content.decode()
    t('o detalhe não mostra editar para o colega', f'/impulso/conectar/{pop.id}/editar/' not in html)

    r = ca.post(f'/impulso/conectar/{pop.id}/excluir/')
    t('quem enviou não exclui', ConteudoConectar.objects.filter(pk=pop.pk).exists())

    curso = ConteudoConectar.objects.create(tipo=ConteudoConectar.Tipo.CURSO, titulo='ZZPOP Curso do autor',
                                            criado_por=autor, obrigatorio=False)
    r = ca.get(f'/impulso/conectar/{curso.id}/editar/')
    t('curso não entra na regra (só POP e vídeo)', r.status_code == 302)
    t('pode_editar do modelo concorda', not curso.pode_editar(autor) and pop.pode_editar(autor)
      and not pop.pode_editar(colega) and pop.pode_editar(gestor))

    print('\n== SUPERADMIN SEM SER GESTOR ==')
    html = cs.get(f'/impulso/conectar/{pop.id}/editar/').content.decode()
    t('o SUPERADMIN vê os campos do gestor', 'name="obrigatorio_para"' in html and 'name="grupo"' in html)
    t('e não recebe o aviso de quem enviou', 'Você enviou este POP' not in html)

    html = cg.get(f'/impulso/conectar/{pop.id}/').content.decode()
    t('o gestor continua com editar e excluir', 'Excluir conteúdo' in html
      and f'/impulso/conectar/{pop.id}/editar/' in html and 'Editar meu POP' not in html)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
