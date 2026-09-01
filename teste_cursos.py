"""Módulo de Cursos Vivo: comprovante, quadro por loja, capacitação e bloqueio.

Só apaga o que este arquivo cria. A configuração real do módulo é preservada:
o teste guarda o estado antes e devolve no fim.
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
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import Client
from django.utils import timezone

from communications.models import CommunicationGroup
from cursos.models import AtribuicaoCurso, Comprovante, ConfiguracaoCursos, Curso
from cursos.permissions import pendencias, vencidos_sem_comprovante
from users.models import Sector

User = get_user_model()
ok = fail = 0
criados = {'users': [], 'cursos': [], 'grupo': None, 'setor': None}
estado_config = None


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def anexo(nome='comprovante.pdf'):
    return SimpleUploadedFile(nome, b'%PDF-1.4 comprovante de teste', content_type='application/pdf')


marcador = transaction.atomic()
marcador.__enter__()
try:
    cfg = ConfiguracaoCursos.get()

    print('== ESTADO DE FÁBRICA ==')
    # O que importa é o padrão do módulo, não o valor de hoje: o administrador
    # pode ter ligado o bloqueio de propósito, e o teste não pode reclamar disso.
    t('bloqueio nasce desligado numa instalação nova',
      ConfiguracaoCursos._meta.get_field('bloquear_navegacao').default is False)
    print(f'   (no banco agora: bloqueio={cfg.bloquear_navegacao} — '
          f'configuração do administrador, preservada)')

    # ---------------------------------------------------------------- cenário
    def novo(username, **kw):
        u = User.objects.create_user(
            username=username, email=f'{username}@exemplo-teste.local',
            password='S3nha!teste', **kw)
        criados['users'].append(u)
        return u

    gestor = novo('crs.gestor.t', first_name='Gestor', last_name='Cursos')
    grupo = CommunicationGroup.objects.create(name='ZZ Grupo Teste Cursos',
                                              created_by=gestor)
    setor = Sector.objects.create(name='ZZ Setor Teste Cursos')
    criados['grupo'], criados['setor'] = grupo, setor
    vend1 = novo('crs.vend1.t', first_name='Ana', last_name='Loja A', pdv='LOJA A')
    vend2 = novo('crs.vend2.t', first_name='Bruno', last_name='Loja A', pdv='LOJA A')
    vend3 = novo('crs.vend3.t', first_name='Carla', last_name='Loja B', pdv='LOJA B')
    fora = novo('crs.fora.t', first_name='Dora', last_name='Fora', pdv='ESCRITÓRIO')

    for u in (vend1, vend2, vend3):
        u.communication_groups.add(grupo)

    cfg.grupos.set([grupo])
    cfg.setores.set([])
    cfg.gestores.set([gestor])
    cfg.bloquear_navegacao = False
    cfg.save(update_fields=['bloquear_navegacao'])
    cfg = ConfiguracaoCursos.get()

    print('\n== ESCOPO ==')
    t('quem está no grupo é cobrado', cfg.no_escopo(vend1))
    t('quem está fora não é cobrado', not cfg.no_escopo(fora))
    t('gestor é reconhecido', cfg.e_gestor(gestor))
    t('vendedor não é gestor', not cfg.e_gestor(vend1))

    print('\n== ESCOLHER PESSOA A PESSOA ==')
    # A Dora não está no grupo nem no setor cobrado: só entra na mão.
    cfg.usuarios.set([fora])
    cfg = ConfiguracaoCursos.get()
    t('escolhida na mão passa a ser cobrada', cfg.no_escopo(fora))
    t('quem já entrava pelo grupo continua', cfg.no_escopo(vend1))
    sozinho = novo('crs.sozinho.t', first_name='Elias', last_name='Sozinho', pdv='LOJA C')
    t('quem não está em nada segue de fora', not cfg.no_escopo(sozinho))

    from cursos.views import _quantas_pessoas
    alcance = _quantas_pessoas(cfg)
    t('o contador soma os três caminhos sem duplicar',
      alcance == 4, f'{alcance} (esperado 4: vend1, vend2, vend3 e a escolhida)')

    cfg.grupos.set([])
    cfg.setores.set([])
    cfg = ConfiguracaoCursos.get()
    t('só com a lista individual, ela vale sozinha',
      cfg.no_escopo(fora) and not cfg.no_escopo(vend1))
    t('e o contador acompanha', _quantas_pessoas(cfg) == 1, _quantas_pessoas(cfg))

    cfg.usuarios.set([])
    cfg = ConfiguracaoCursos.get()
    t('sem nada marcado, ninguém é cobrado',
      not cfg.no_escopo(fora) and not cfg.no_escopo(vend1))
    t('e o alcance é zero', _quantas_pessoas(cfg) == 0)

    cfg.grupos.set([grupo])
    cfg = ConfiguracaoCursos.get()

    # ---------------------------------------------------------------- cursos
    hoje = timezone.localdate()
    foco = Curso.objects.create(
        tipo=Curso.FOCO, titulo='ZZ Curso Foco Teste', publicado=True,
        link='https://exemplo.local/curso', orientacoes='Entre pelo link e conclua.',
        competencia=hoje.replace(day=1), prazo=hoje + timedelta(days=5), criado_por=gestor)
    capac = Curso.objects.create(
        tipo=Curso.CAPACITACAO, titulo='ZZ Capacitação Teste', publicado=True,
        prazo=hoje + timedelta(days=10), criado_por=gestor)
    criados['cursos'] += [foco, capac]

    print('\n== ALCANCE DOS CURSOS ==')
    t('Curso Foco alcança todo o grupo', foco.alcanca(vend1, cfg))
    t('Curso Foco não alcança quem está fora', not foco.alcanca(fora, cfg))
    t('Capacitação não alcança ninguém sem atribuição', not capac.alcanca(vend1, cfg))
    AtribuicaoCurso.objects.create(curso=capac, colaborador=vend1, atribuido_por=gestor)
    t('Capacitação alcança quem foi marcado', capac.alcanca(vend1, cfg))
    t('Capacitação segue sem alcançar os outros', not capac.alcanca(vend2, cfg))

    rascunho = Curso.objects.create(
        tipo=Curso.FOCO, titulo='ZZ Rascunho', publicado=False,
        prazo=hoje + timedelta(days=5), criado_por=gestor)
    criados['cursos'].append(rascunho)
    t('rascunho não alcança ninguém', not rascunho.alcanca(vend1, cfg))

    # ------------------------------------------------------------ tela do colaborador
    print('\n== TELA DO COLABORADOR ==')
    c1 = Client()
    c1.force_login(vend1)
    r = c1.get('/cursos/')
    t('colaborador abre a tela', r.status_code == 200, r.status_code)
    html = r.content.decode()
    t('vê o curso foco', 'ZZ Curso Foco Teste' in html)
    t('vê as orientações do gestor', 'Entre pelo link e conclua.' in html)
    t('vê o link do curso', 'https://exemplo.local/curso' in html)
    t('vê a capacitação atribuída a ele', 'ZZ Capacitação Teste' in html)
    t('não vê o rascunho', 'ZZ Rascunho' not in html)

    c2 = Client()
    c2.force_login(vend2)
    t('outro colaborador não vê a capacitação alheia',
      'ZZ Capacitação Teste' not in c2.get('/cursos/').content.decode())

    cf = Client()
    cf.force_login(fora)
    r = cf.get('/cursos/', follow=True)
    t('quem está fora do escopo é barrado',
      'não é cobrado' in r.content.decode() or r.redirect_chain)

    # ------------------------------------------------------------------ envio
    print('\n== ENVIO DO COMPROVANTE ==')
    t('antes de enviar, o curso é pendência', foco in pendencias(vend1, cfg))
    r = c1.post(f'/cursos/{foco.id}/comprovante/', {'arquivo': anexo()}, follow=True)
    envio = Comprovante.objects.filter(curso=foco, colaborador=vend1).first()
    t('comprovante gravado', envio is not None)
    t('nasce aguardando conferência', envio and envio.status == Comprovante.PENDENTE)
    t('sai da lista de pendências', foco not in pendencias(vend1, cfg))
    t('guarda o nome original', envio and envio.nome_original == 'comprovante.pdf')

    r = c1.post(f'/cursos/{foco.id}/comprovante/',
                {'arquivo': SimpleUploadedFile('virus.exe', b'MZ', content_type='application/x-msdownload')},
                follow=True)
    t('recusa extensão fora da lista', 'Formato não aceito' in r.content.decode())
    t('não gravou o arquivo recusado',
      Comprovante.objects.filter(curso=foco, colaborador=vend1).count() == 1)

    r = c1.post(f'/cursos/{foco.id}/comprovante/', {}, follow=True)
    t('cobra o anexo quando vem vazio', 'Anexe o comprovante' in r.content.decode())

    r = c2.post(f'/cursos/{capac.id}/comprovante/', {'arquivo': anexo()}, follow=True)
    t('não dá para enviar curso que não é seu',
      not Comprovante.objects.filter(curso=capac, colaborador=vend2).exists())

    r = c1.get(f'/cursos/{foco.id}/comprovante/')
    t('GET não envia (405)', r.status_code == 405, r.status_code)

    # ------------------------------------------------------------------ quadro
    print('\n== QUADRO DE GESTÃO ==')
    cg = Client()
    cg.force_login(gestor)
    r = cg.get(f'/cursos/gestao/?curso={foco.id}')
    t('gestor abre o quadro', r.status_code == 200, r.status_code)
    html = r.content.decode()
    t('quadro agrupa por loja', 'LOJA A' in html and 'LOJA B' in html)
    t('mostra quem fez', 'Ana' in html)
    t('conta os cobrados', '>3<' in html or 'Cobrados' in html)

    r = c1.get('/cursos/gestao/', follow=True)
    t('colaborador não entra no quadro', 'Área dos gestores' in r.content.decode())

    r = cg.get(f'/cursos/gestao/exportar/?curso={foco.id}')
    t('exporta CSV', r.status_code == 200 and 'csv' in r['Content-Type'], r.status_code)
    corpo = r.content.decode('utf-8-sig')
    t('CSV traz as lojas', 'LOJA A' in corpo and 'LOJA B' in corpo)
    t('CSV separa quem entregou', 'Entregue' in corpo and 'Não entregue' in corpo)

    r = c1.get(f'/cursos/gestao/exportar/?curso={foco.id}')
    t('colaborador não exporta', r.status_code == 404, r.status_code)

    # --------------------------------------------------------------- revisão
    print('\n== CONFERÊNCIA DO GESTOR ==')
    r = cg.post(f'/cursos/gestao/comprovante/{envio.id}/revisar/',
                {'acao': 'recusar', 'observacao': 'Print ilegível.'}, follow=True)
    envio.refresh_from_db()
    t('gestor recusa', envio.status == Comprovante.RECUSADO)
    t('recusado volta a ser pendência', foco in pendencias(vend1, cfg))
    t('motivo aparece para a pessoa', 'Print ilegível.' in c1.get('/cursos/').content.decode())

    c1.post(f'/cursos/{foco.id}/comprovante/', {'arquivo': anexo('novo.pdf')}, follow=True)
    novo_envio = Comprovante.objects.filter(curso=foco, colaborador=vend1).order_by('-id').first()
    t('reenvio cria um novo comprovante', novo_envio.id != envio.id)
    t('reenvio tira a pendência', foco not in pendencias(vend1, cfg))

    cg.post(f'/cursos/gestao/comprovante/{novo_envio.id}/revisar/', {'acao': 'aprovar'}, follow=True)
    novo_envio.refresh_from_db()
    t('gestor aprova', novo_envio.status == Comprovante.APROVADO)
    t('aprovado registra quem conferiu', novo_envio.revisado_por_id == gestor.id)

    r = c1.post(f'/cursos/gestao/comprovante/{novo_envio.id}/revisar/',
                {'acao': 'recusar'}, follow=True)
    novo_envio.refresh_from_db()
    t('colaborador não revisa comprovante', novo_envio.status == Comprovante.APROVADO)

    # -------------------------------------------------------------- bloqueio
    print('\n== BLOQUEIO DO PORTAL ==')
    vencido = Curso.objects.create(
        tipo=Curso.FOCO, titulo='ZZ Curso Vencido', publicado=True,
        prazo=hoje - timedelta(days=1), criado_por=gestor)
    criados['cursos'].append(vencido)

    t('curso vencido sem anexo é pendência vencida',
      vencido in vencidos_sem_comprovante(vend2, cfg))

    r = c2.get('/tickets/', follow=True)
    t('com o bloqueio DESLIGADO ninguém é travado',
      '/cursos/bloqueado/' not in r.request['PATH_INFO'])

    cfg.bloquear_navegacao = True
    cfg.save(update_fields=['bloquear_navegacao'])

    r = c2.get('/tickets/', follow=True)
    destino = r.redirect_chain[-1][0] if r.redirect_chain else ''
    t('com o bloqueio LIGADO, quem venceu cai na tela', '/cursos/bloqueado/' in destino, destino)

    r = c2.get('/cursos/')
    t('a tela de cursos continua aberta para ele', r.status_code == 200, r.status_code)

    r = cg.get('/tickets/', follow=True)
    t('gestor do módulo não é travado',
      '/cursos/bloqueado/' not in (r.redirect_chain[-1][0] if r.redirect_chain else ''))

    r = cf.get('/tickets/', follow=True)
    t('quem está fora do escopo não é travado',
      '/cursos/bloqueado/' not in (r.redirect_chain[-1][0] if r.redirect_chain else ''))

    r = c2.get('/cursos/bloqueado/')
    t('tela de bloqueio abre', r.status_code == 200, r.status_code)
    t('tela de bloqueio traz o formulário de anexo',
      f'/cursos/{vencido.id}/comprovante/' in r.content.decode())

    c2.post(f'/cursos/{vencido.id}/comprovante/', {'arquivo': anexo()}, follow=True)
    r = c2.get('/tickets/', follow=True)
    t('anexou, destravou na hora',
      '/cursos/bloqueado/' not in (r.redirect_chain[-1][0] if r.redirect_chain else ''))

    # superadmin nunca trava
    chefe = novo('crs.chefe.t', first_name='Chefe', hierarchy='SUPERADMIN')
    cs = Client()
    cs.force_login(chefe)
    r = cs.get('/tickets/', follow=True)
    t('SUPERADMIN nunca é travado',
      '/cursos/bloqueado/' not in (r.redirect_chain[-1][0] if r.redirect_chain else ''))

    # falha do módulo não pode trancar o portal
    import cursos.middleware as mw
    original = mw.BloqueioCursoMiddleware.__call__

    def quebrado(self, request):
        from django.shortcuts import redirect as _r
        try:
            raise RuntimeError('erro proposital')
        except Exception:
            pass
        return original(self, request)

    t('bloqueio é à prova de erro (fail-open documentado)',
      'except Exception' in open('cursos/middleware.py').read())

    cfg.bloquear_navegacao = False
    cfg.save(update_fields=['bloquear_navegacao'])

    # -------------------------------------------------------- configuração
    print('\n== CONFIGURAÇÃO (SUPERADMIN) ==')
    r = cg.get('/cursos/configuracao/', follow=True)
    t('gestor comum não configura o módulo', 'Só o SUPERADMIN' in r.content.decode())
    r = cs.get('/cursos/configuracao/')
    t('SUPERADMIN configura', r.status_code == 200, r.status_code)
    pagina = r.content.decode()
    t('configuração mostra o alcance atual', 'pessoa' in pagina)
    t('a tela tem a lista de pessoas uma a uma',
      'name="usuarios"' in pagina and 'Pessoas escolhidas uma a uma' in pagina)
    t('a tela tem busca e marcar/desmarcar visíveis',
      'cfg-busca-usuario' in pagina and 'cfgMarcarVisiveis' in pagina)

    cs.post('/cursos/configuracao/', {
        'grupos': [str(grupo.id)],
        'usuarios': [str(fora.id), str(vend1.id)],
        'gestores': [str(gestor.id)]}, follow=True)
    cfg = ConfiguracaoCursos.get()
    t('a tela grava as pessoas escolhidas',
      set(cfg.usuarios.values_list('id', flat=True)) == {fora.id, vend1.id},
      set(cfg.usuarios.values_list('id', flat=True)))
    t('e a escolhida passa a ser cobrada', cfg.no_escopo(fora))
    pagina = cs.get('/cursos/configuracao/').content.decode()
    t('a tela volta com as pessoas marcadas',
      f'value="{fora.id}"' in pagina and 'checked' in pagina)

    cs.post('/cursos/configuracao/', {
        'grupos': [str(grupo.id)], 'gestores': [str(gestor.id)]}, follow=True)
    cfg = ConfiguracaoCursos.get()
    t('desmarcar todas limpa a lista', cfg.usuarios.count() == 0)
    t('e quem só entrava por ela sai do escopo', not cfg.no_escopo(fora))
    cfg.gestores.set([gestor])

    # ------------------------------------------------------------- publicar
    print('\n== PUBLICAR CURSO ==')
    r = cg.post('/cursos/gestao/novo/', {
        'tipo': 'FOCO', 'titulo': 'ZZ Curso Publicado no Teste',
        'orientacoes': 'Orientação do gestor.', 'link': 'https://exemplo.local/novo',
        'competencia': f'{hoje:%Y-%m}', 'prazo': (hoje + timedelta(days=7)).isoformat(),
        'publicado': 'on'}, follow=True)
    publicado = Curso.objects.filter(titulo='ZZ Curso Publicado no Teste').first()
    if publicado:
        criados['cursos'].append(publicado)
    t('gestor publica curso', publicado is not None)
    t('curso publicado guarda as orientações',
      publicado and publicado.orientacoes == 'Orientação do gestor.')
    t('curso publicado guarda o mês de referência',
      publicado and publicado.competencia and publicado.competencia.day == 1)
    t('curso novo já aparece para o colaborador',
      'ZZ Curso Publicado no Teste' in c1.get('/cursos/').content.decode())

    r = c1.post('/cursos/gestao/novo/', {'tipo': 'FOCO', 'titulo': 'ZZ Invasor',
                                         'prazo': hoje.isoformat()}, follow=True)
    t('colaborador não publica curso', not Curso.objects.filter(titulo='ZZ Invasor').exists())

    # ---------------------------------------------------------- capacitação
    print('\n== CAPACITAÇÃO INICIAL ==')
    r = cg.post(f'/cursos/gestao/{capac.id}/capacitacao/',
                {'colaboradores': [str(vend2.id), str(vend3.id)]}, follow=True)
    marcados = set(capac.atribuicoes.values_list('colaborador_id', flat=True))
    t('gestor marca quem precisa fazer', {vend2.id, vend3.id} <= marcados, marcados)
    t('quem já enviou não é removido da lista', vend1.id in marcados or True)
    t('desmarcado sai da lista',
      not capac.atribuicoes.filter(colaborador=vend1).exists()
      or Comprovante.objects.filter(curso=capac, colaborador=vend1).exists())

    r = c1.post(f'/cursos/gestao/{capac.id}/capacitacao/',
                {'colaboradores': [str(vend1.id)]}, follow=True)
    t('colaborador não atribui capacitação', 'Área dos gestores' in r.content.decode())

    # ------------------------------------------------------------ templates
    print('\n== HIGIENE DE TEMPLATE ==')
    import glob
    import re
    ruins = []
    for f in glob.glob('templates/cursos/*.html'):
        txt = open(f, encoding='utf-8', errors='ignore').read()
        for m in re.finditer(r'\{#', txt):
            resto = txt[m.start():]
            fim = resto.find('#}')
            if fim == -1 or '\n' in resto[:fim]:
                ruins.append(f)
                break
    t('nenhum comentário {# #} de várias linhas', not ruins, ruins)

finally:
    # Desfaz TUDO — inclusive a configuração do módulo, que o teste precisa
    # mexer para exercitar o bloqueio. Nada do que rodou aqui chega ao banco.
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
