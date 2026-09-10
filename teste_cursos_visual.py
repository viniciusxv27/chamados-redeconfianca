"""Cursos: saber quem está pendente sem abrir loja por loja.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import os
import re
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

from cursos.models import ConfiguracaoCursos, Comprovante, Curso
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


def pdf(nome='zz.pdf'):
    return SimpleUploadedFile(nome, b'%PDF-1.4 zz', content_type='application/pdf')


marcador = transaction.atomic()
marcador.__enter__()
try:
    area = Sector.objects.create(name='ZZ Setor Cursos')
    cfg = ConfiguracaoCursos.get()
    cfg.setores.add(area)

    def novo(u, nome, loja, **kw):
        return User.objects.create_user(
            username=u, email=f'{u}@exemplo-teste.local', password='S3nha!teste',
            sector=area, first_name=nome, last_name='ZZT', pdv=loja, **kw)

    chefe = novo('zzc.chefe', 'ZZChefe', '', is_superuser=True, is_staff=True,
                 hierarchy='SUPERADMIN')
    # Loja A: um esperando aprovação, um que não fez.
    a_pend = novo('zzc.apend', 'ZZAnaPend', 'ZZ LOJA A')
    a_nada = novo('zzc.anada', 'ZZAlanNada', 'ZZ LOJA A')
    # Loja B: tudo aprovado.
    b_ok = novo('zzc.bok', 'ZZBiaOk', 'ZZ LOJA B')
    # Loja C: um recusado.
    c_rec = novo('zzc.crec', 'ZZCaioRec', 'ZZ LOJA C')
    # Sem loja: um esperando aprovação (vai para a aba Geral).
    sem = novo('zzc.sem', 'ZZSemLoja', '')
    # Sem PDV e sem setor é o que faz cair na aba "Geral"; para continuar no
    # público do curso, entra pela lista de usuários avulsos da configuração.
    sem.sector = None
    sem.save()
    cfg.usuarios.add(sem)

    curso = Curso.objects.create(
        titulo='ZZ Curso de teste', tipo=Curso.FOCO,
        prazo=timezone.localdate() + timezone.timedelta(days=10),
        publicado=True)

    Comprovante.objects.create(curso=curso, colaborador=a_pend, arquivo=pdf(),
                               status=Comprovante.PENDENTE)
    Comprovante.objects.create(curso=curso, colaborador=b_ok, arquivo=pdf(),
                               status=Comprovante.APROVADO)
    Comprovante.objects.create(curso=curso, colaborador=c_rec, arquivo=pdf(),
                               status=Comprovante.RECUSADO,
                               observacao='ZZ certificado ilegível')
    Comprovante.objects.create(curso=curso, colaborador=sem, arquivo=pdf(),
                               status=Comprovante.PENDENTE)

    c = Client(); c.force_login(chefe)
    r = c.get(f'/cursos/gestao/?curso={curso.id}')
    html = r.content.decode()
    t('o quadro abre', r.status_code == 200, r.status_code)

    print('== O INDICADOR QUE FALTAVA ==')
    t('a loja com fila anuncia no cabeçalho',
      'pendente de aprovação nesta loja' in html)
    t('a chamada do topo soma os pendentes',
      'aguardando a sua aprovação' in html)
    t('e diz quantos', re.search(r'2 comprovantes? aguardando', html) is not None)
    t('a aba Geral tem o próprio contador', 'pendente(s) de aprovação' in html)
    t('e o texto do grupo é "neste grupo"', 'neste grupo' in html)

    print('\n== A LOJA COM FILA ABRE SOZINHA ==')
    bloco_a = html.split('ZZ LOJA A')[1].split('crs-bloco')[0] if 'ZZ LOJA A' in html else ''
    # O painel da Loja A não pode nascer com hidden; o da Loja B (tudo ok) pode.
    paineis = dict(re.findall(r'id="(crs-bloco-\d+)"\s+class="([^"]*)"', html))
    abertos = [k for k, v in paineis.items() if 'hidden' not in v.split()]
    t('pelo menos um painel nasce aberto', len(abertos) >= 1, paineis)
    t('e pelo menos um nasce fechado',
      any('hidden' in v.split() for v in paineis.values()), paineis)

    print('\n== A ORDEM RESPONDE "DE QUEM EU PRECISO?" ==')
    pos_a = html.find('ZZ LOJA A')
    pos_b = html.find('ZZ LOJA B')
    pos_c = html.find('ZZ LOJA C')
    t('a loja com fila vem antes das outras',
      pos_a < pos_b and pos_a < pos_c, (pos_a, pos_b, pos_c))

    dentro = html[pos_a:pos_b] if pos_a < pos_b else ''
    t('dentro da loja, quem espera decisão vem primeiro',
      dentro.find('ZZAnaPend') < dentro.find('ZZAlanNada'),
      (dentro.find('ZZAnaPend'), dentro.find('ZZAlanNada')))

    print('\n== CADA PESSOA MOSTRA O ESTADO SEM ABRIR NADA ==')
    t('quem espera aprovação tem o selo', 'Pendente de aprovação' in html)
    t('quem não fez tem o selo', 'Não fez' in html)
    t('quem foi recusado tem o selo', '>Recusado' in html or 'Recusado\n' in html)
    t('quem foi aprovado tem o selo', 'Aprovado' in html)
    t('o motivo da recusa fica no title', 'ZZ certificado ilegível' in html)

    print('\n== O FILTRO TEM O QUE FILTRAR ==')
    t('cada linha carrega a situação', 'data-situacao="CONFERIR"' in html)
    t('e o nome para a busca', 'data-nome="zzanapend zzt"' in html)
    t('o bloco carrega quantos esperam', 'data-conferir="1"' in html)
    for chave in ('TODOS', 'CONFERIR', 'FALTAM', 'APROVADO'):
        t(f'existe o filtro {chave}', f'data-crs-filtro="{chave}"' in html)
    t('tem busca por pessoa ou loja', 'id="crsBusca"' in html)
    t('e o abrir/fechar todas', 'id="crsExpandir"' in html)

    print('\n== OS NÚMEROS DO TOPO ==')
    # `response.context` não vem preenchido com o Client fora do runner, então
    # a conferência é feita no que a pessoa realmente lê na tela.
    def numero(rotulo, corpo):
        m = re.search(rotulo + r'</p>\s*<p[^>]*>\s*([0-9]+)', corpo)
        return int(m.group(1)) if m else None

    # O curso FOCO cobra o público inteiro configurado, e o banco de dev tem
    # gente de verdade nele — então o que se afirma aqui é o que este teste
    # controla (os ZZ) e a coerência da conta, não um total absoluto.
    cobrados = numero('Cobrados', html)
    conferir = numero('Pendentes de aprovação', html)
    aprovados = numero('Aprovados', html)
    nao_fez = numero('Não fizeram', html)

    t('os 4 números aparecem no topo',
      None not in (cobrados, conferir, aprovados, nao_fez),
      (cobrados, conferir, aprovados, nao_fez))
    t('pendentes de aprovação: os 2 deste teste', conferir == 2, conferir)
    t('aprovados: o 1 deste teste', aprovados == 1, aprovados)
    t('recusados aparecem à parte', '1 recusado' in html)
    t('a conta fecha: cobrados = conferir + aprovados + recusados + não fez',
      cobrados == conferir + aprovados + 1 + nao_fez,
      (cobrados, conferir, aprovados, nao_fez))
    t('a aba Por loja conta só as lojas (1)',
      're de aprovação nas lojas">1<' in html or 'nas lojas">1<' in html)
    t('e o Geral fica com o resto (1)', 'de aprovação">1<' in html)

    print('\n== APROVAR TIRA DA FILA ==')
    envio = Comprovante.objects.get(curso=curso, colaborador=a_pend)
    r = c.post(f'/cursos/gestao/comprovante/{envio.id}/revisar/',
               {'acao': 'aprovar', 'voltar': f'/cursos/gestao/?curso={curso.id}'},
               follow=True)
    html2 = r.content.decode()
    t('a loja A some do indicador',
      html2.count('pendente de aprovação nesta loja') == 0)
    t('o total cai para 1', numero('Pendentes de aprovação', html2) == 1,
      numero('Pendentes de aprovação', html2))
    t('e vira aprovado', numero('Aprovados', html2) == 2,
      numero('Aprovados', html2))

    print('\n== A TELA DO COLABORADOR FALA A MESMA LÍNGUA ==')
    ca = Client(); ca.force_login(a_pend)
    html = ca.get('/cursos/').content.decode()
    t('mostra o resumo por estado', 'Em conferência' in html and 'Aprovados' in html)
    t('aprovado aparece como aprovado', 'Aprovado' in html)

    cs = Client(); cs.force_login(sem)
    html = cs.get('/cursos/').content.decode()
    t('quem enviou e espera vê "Em conferência"', 'Em conferência' in html)
    t('e NÃO vê "Aprovado" como se estivesse resolvido',
      'fa-circle-check"></i>Aprovado' not in html)
    t('a tela explica que está na fila do gestor',
      'fila do gestor' in html)
    t('e mostra o anexo que ele mandou', 'zz.pdf' in html or 'comprovante' in html)

    cr = Client(); cr.force_login(c_rec)
    html = cr.get('/cursos/').content.decode()
    t('recusado pede reenvio', 'Reenviar' in html)
    t('com o motivo', 'ZZ certificado ilegível' in html)

    cn = Client(); cn.force_login(a_nada)
    html = cn.get('/cursos/').content.decode()
    t('quem não fez vê o prazo', 'Faltam' in html or 'Vence hoje' in html)
    t('e o aviso de pendência', 'sem comprovante' in html)

    print('\n== NADA DE COMENTÁRIO VAZANDO PARA A TELA ==')
    for pagina, nome in ((c.get(f'/cursos/gestao/?curso={curso.id}').content.decode(), 'gestão'),
                         (ca.get('/cursos/').content.decode(), 'colaborador')):
        t(f'{nome}: nenhum {{# #}} na tela', '{#' not in pagina)
        t(f'{nome}: nenhum comment cru', '{% comment' not in pagina)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
