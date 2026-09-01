"""Quatro mudanças: comissionamento, cadastro de usuário, metas por PDV e edição no Impulso.

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
from impulso.models import (GRUPO_ADM, GRUPO_GESTOR, ConteudoConectar, Meta)
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
    gerentes = CommunicationGroup.objects.filter(name__icontains='GERENTES').first()
    coord = CommunicationGroup.objects.filter(name__icontains='COORDENADORES').first()
    assert adm and ges and gerentes, 'grupos não encontrados'

    def novo(username, grupos=(), **kw):
        kw.setdefault('first_name', username.split('.')[1].title())
        kw.setdefault('last_name', 'Teste')
        u = User.objects.create_user(
            username=username, email=f'{username}@exemplo-teste.local',
            password='S3nha!teste', **kw)
        for g in grupos:
            u.communication_groups.add(g)
        return u

    print('== COMISSIONAMENTO: SÓ GERENTE ENTRE OS PADRÃO ==')
    from users.commission_views import pode_ver_comissionamento

    consultor = novo('pe.consultor')
    gerente = novo('pe.gerente', [gerentes])
    coordenador = novo('pe.coord', [coord]) if coord else None
    administrativo = novo('pe.adm', hierarchy='ADMINISTRATIVO')
    chefe = novo('pe.chefe', hierarchy='SUPERADMIN')

    t('consultor PADRÃO não vê', not pode_ver_comissionamento(consultor))
    t('PADRÃO no grupo GERENTES vê', pode_ver_comissionamento(gerente))
    if coordenador:
        t('coordenador PADRÃO continua vendo', pode_ver_comissionamento(coordenador))
    t('ADMINISTRATIVO não é PADRÃO, segue vendo', pode_ver_comissionamento(administrativo))
    t('SUPERADMIN vê', pode_ver_comissionamento(chefe))
    t('anônimo não vê', not pode_ver_comissionamento(None))

    cc = Client()
    cc.force_login(consultor)
    r = cc.get('/commission/', follow=True)
    t('a tela recusa o consultor',
      'disponível para gerentes' in r.content.decode())

    r = cc.get('/commission/api/')
    t('a API também recusa (403)', r.status_code == 403, r.status_code)
    r = cc.get('/commission/refresh/')
    t('o refresh também recusa (403)', r.status_code == 403, r.status_code)
    r = cc.get('/commission/export/', follow=True)
    t('a exportação também recusa',
      'disponível para gerentes' in r.content.decode())

    cg = Client()
    cg.force_login(gerente)
    r = cg.get('/commission/')
    t('o gerente abre normalmente', r.status_code == 200, r.status_code)

    # A palavra "Comissionamento" também aparece num comunicado da home; o que
    # importa é o link do menu.
    from django.urls import reverse
    link_menu = f'href="{reverse("commission")}"'
    t('o link do menu some para quem não pode',
      link_menu not in cc.get('/').content.decode())
    t('e continua para quem pode', link_menu in cg.get('/').content.decode())

    print('\n== AS OUTRAS PORTAS DO MÓDULO ==')
    for caminho, rotulo in (('/users/commission/', 'a rota sob /users/'),
                            ('/users/commission/projecao/', 'a projeção'),
                            ('/users/commission/export/', 'a exportação sob /users/')):
        r = cc.get(caminho, follow=True)
        t(f'{rotulo} recusa o consultor',
          'disponível para gerentes' in r.content.decode(), caminho)
    r = cc.get('/users/api/commission/')
    t('a API sob /users/ recusa (403)', r.status_code == 403, r.status_code)
    r = cc.get('/users/api/vendas-pilar/?pilar=x')
    t('a API de vendas por pilar recusa (403)', r.status_code == 403, r.status_code)
    r = cg.get('/users/commission/projecao/', follow=True)
    t('e o gerente continua abrindo a projeção',
      'disponível para gerentes' not in r.content.decode())

    print('\n== CADASTRO DE FUNCIONÁRIO: SÓ SUPERADMIN ==')
    alvo = novo('pe.alvo')
    t('SUPERADMIN edita', chefe.can_edit_users())
    t('ADMINISTRATIVO não edita mais', not administrativo.can_edit_users())
    t('SUPERVISOR não edita', not novo('pe.sup', hierarchy='SUPERVISOR').can_edit_users())
    t('ADMIN não edita', not novo('pe.admin', hierarchy='ADMIN').can_edit_users())
    t('mas continuam abrindo a lista', administrativo.can_manage_users())

    ca = Client()
    ca.force_login(administrativo)
    r = ca.get(f'/users/manage/users/{alvo.id}/edit/', follow=True)
    t('a tela de edição recusa quem não é SUPERADMIN',
      'Somente o SUPERADMIN' in r.content.decode())

    nome_antes = alvo.first_name
    r = ca.post(f'/users/manage/users/{alvo.id}/edit/',
                {'first_name': 'INVASOR', 'email': alvo.email,
                 'username': alvo.username}, follow=True)
    alvo.refresh_from_db()
    t('e o POST direto não altera nada', alvo.first_name == nome_antes, alvo.first_name)

    r = ca.post(f'/users/manage/users/{alvo.id}/change-password/',
                {'new_password': 'Xx123456!'}, follow=True)
    t('trocar senha alheia também recusa', 'Somente o SUPERADMIN' in r.content.decode())

    r = ca.post('/users/manage/users/import-excel/', {}, follow=True)
    t('importação em massa recusa', 'Somente o SUPERADMIN' in r.content.decode())

    html = ca.get('/users/manage/users/').content.decode()
    t('o botão de editar some da lista', f'/users/manage/users/{alvo.id}/edit/' not in html)
    # `toggleImportModal()` também é o nome da função no script da página;
    # o que não pode existir é o botão que a chama.
    t('e os botões de importar somem',
      'onclick="toggleImportModal()"' not in html
      and 'onclick="toggleImportColaboradoresModal()"' not in html)

    cs = Client()
    cs.force_login(chefe)
    html = cs.get('/users/manage/users/').content.decode()
    t('SUPERADMIN continua vendo os botões',
      f'/users/manage/users/{alvo.id}/edit/' in html
      and 'onclick="toggleImportModal()"' in html)

    print('\n== METAS: NOME DA PLANILHA x CADASTRO ==')
    from power_bi.views import _encontrar_usuario_por_nome, _normalize_text

    indice = {}
    for u in [novo('pe.hemilly', first_name='HEMILLY', last_name='NUNES FERREIRA'),
              novo('pe.arthur', first_name='ARTHUR', last_name='GONÇALVES MILED MONTEIRO'),
              novo('pe.well', first_name='WELLINGTON', last_name='BOTELHO MOTA')]:
        indice[_normalize_text(f'{u.first_name} {u.last_name}')] = u

    achou = _encontrar_usuario_por_nome('HEMILLY NUNES FERREIRA', indice)
    t('nome igual casa', achou and achou.username == 'pe.hemilly')
    achou = _encontrar_usuario_por_nome('HEMILY NUNES FERREIRA', indice)
    t('erro de digitação casa', achou and achou.username == 'pe.hemilly', achou)
    achou = _encontrar_usuario_por_nome('ARTHUR MILED MONTEIRO', indice)
    t('sobrenome a menos na planilha casa', achou and achou.username == 'pe.arthur')
    achou = _encontrar_usuario_por_nome('WELLINGTON BOTELHO MOTA LOPES', indice)
    t('sobrenome a mais na planilha casa', achou and achou.username == 'pe.well')
    t('nome curto e genérico NÃO casa',
      _encontrar_usuario_por_nome('ARTHUR', indice) is None)
    t('quem não existe devolve nada',
      _encontrar_usuario_por_nome('FULANO DE TAL SILVA', indice) is None)

    # Ambiguidade: dois parecidos não podem casar com nenhum.
    ambiguo = dict(indice)
    outro = novo('pe.well2', first_name='WELLINGTON', last_name='BOTELHO MOTA SILVA')
    ambiguo[_normalize_text('WELLINGTON BOTELHO MOTA SILVA')] = outro
    t('dois candidatos parecidos não casam com nenhum',
      _encontrar_usuario_por_nome('WELLINGTON BOTELHO MOTA LOPES SILVA', ambiguo) is None)

    print('\n== EDITAR ATIVIDADE DO CONFIAR ==')
    area = Sector.objects.create(name='ZZ Area Edicao')
    gestor_imp = novo('pe.gestorimp', [adm, ges], sector=area)
    colab = novo('pe.colabimp', [adm], sector=area)
    hoje = timezone.localdate()

    meta = Meta.objects.create(
        titulo='ZZ Atividade original', descricao='antes', colaborador=colab,
        gestor=gestor_imp, prazo=hoje + timedelta(days=5),
        aprovacao=Meta.Aprovacao.APROVADA, created_by=gestor_imp)

    t('gestor pode editar', meta.pode_editar(gestor_imp))
    t('colaborador não pode editar', not meta.pode_editar(colab))

    pendente = Meta.objects.create(
        titulo='ZZ Solicitacao', colaborador=colab, gestor=gestor_imp,
        prazo=hoje + timedelta(days=5), aprovacao=Meta.Aprovacao.PENDENTE,
        solicitada_por=colab, created_by=colab)
    t('solicitação pendente PODE ser editada (corrigir antes de aprovar)',
      pendente.pode_editar(gestor_imp))
    t('mas continua não podendo ser apagada', not pendente.pode_excluir(gestor_imp))

    cgi = Client()
    cgi.force_login(gestor_imp)
    r = cgi.post(f'/impulso/metas/{meta.id}/editar/', {
        'titulo': 'ZZ Atividade editada', 'descricao': 'depois',
        'prazo': (hoje + timedelta(days=10)).isoformat(),
        'recorrencia': meta.recorrencia}, follow=True)
    meta.refresh_from_db()
    t('gestor edita pela tela', meta.titulo == 'ZZ Atividade editada', r.status_code)
    t('o prazo mudou', meta.prazo == hoje + timedelta(days=10))
    t('a mudança vira comentário na atividade',
      meta.comentarios.filter(mensagem__contains='editou a atividade').exists())

    ccol = Client()
    ccol.force_login(colab)
    r = ccol.post(f'/impulso/metas/{meta.id}/editar/', {
        'titulo': 'ZZ Invadida', 'prazo': hoje.isoformat()}, follow=True)
    meta.refresh_from_db()
    t('colaborador não edita pela tela', meta.titulo == 'ZZ Atividade editada')
    t('a tela explica', 'não pode editar esta atividade' in r.content.decode())

    r = cgi.post(f'/impulso/metas/{meta.id}/editar/',
                 {'titulo': '', 'prazo': ''}, follow=True)
    meta.refresh_from_db()
    t('título e prazo continuam obrigatórios',
      meta.titulo == 'ZZ Atividade editada'
      and 'obrigatórios' in r.content.decode())

    html = cgi.get(f'/impulso/metas/{meta.id}/').content.decode()
    t('o botão de editar aparece para o gestor',
      f'/impulso/metas/{meta.id}/editar/' in html)
    html = ccol.get(f'/impulso/metas/{meta.id}/').content.decode()
    t('e não aparece para o colaborador',
      f'/impulso/metas/{meta.id}/editar/' not in html)

    print('\n== EDITAR CURSO/POP DO CONECTAR ==')
    conteudo = ConteudoConectar.objects.create(
        tipo=ConteudoConectar.Tipo.CURSO, titulo='ZZ Curso original',
        descricao='antes', url='https://exemplo.local/a', obrigatorio=True,
        criado_por=gestor_imp)

    r = cgi.post(f'/impulso/conectar/{conteudo.id}/editar/', {
        'tipo': ConteudoConectar.Tipo.VIDEO, 'titulo': 'ZZ Curso editado',
        'descricao': 'depois', 'url': 'https://exemplo.local/b',
        'obrigatorio': 'on'}, follow=True)
    conteudo.refresh_from_db()
    t('gestor edita o conteúdo', conteudo.titulo == 'ZZ Curso editado', r.status_code)
    t('muda o tipo', conteudo.tipo == ConteudoConectar.Tipo.VIDEO)
    t('muda o link', conteudo.url == 'https://exemplo.local/b')

    r = ccol.post(f'/impulso/conectar/{conteudo.id}/editar/', {
        'titulo': 'ZZ Invadido'}, follow=True)
    conteudo.refresh_from_db()
    t('colaborador não edita conteúdo', conteudo.titulo == 'ZZ Curso editado')
    t('a tela explica', 'Só o SUPERADMIN ou um gestor' in r.content.decode())

    r = cgi.post(f'/impulso/conectar/{conteudo.id}/editar/',
                 {'titulo': ''}, follow=True)
    conteudo.refresh_from_db()
    t('título continua obrigatório', conteudo.titulo == 'ZZ Curso editado')

    html = cgi.get(f'/impulso/conectar/{conteudo.id}/').content.decode()
    t('o botão de editar aparece no detalhe',
      f'/impulso/conectar/{conteudo.id}/editar/' in html)
    html = cgi.get('/impulso/conectar/').content.decode()
    t('e na lista também', f'/impulso/conectar/{conteudo.id}/editar/' in html)
    html = ccol.get('/impulso/conectar/').content.decode()
    t('colaborador não vê o lápis',
      f'/impulso/conectar/{conteudo.id}/editar/' not in html)

    html = cgi.get(f'/impulso/conectar/{conteudo.id}/editar/').content.decode()
    t('o formulário abre preenchido',
      'ZZ Curso editado' in html and 'Salvar alterações' in html)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
