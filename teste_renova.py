"""Vini Renova: checklist só Apple, padrão pelas avarias, aprovação do gerente, chamado, etiqueta e gestão.

Pedidos:
- módulo em que o SUPERADMIN vê tudo e define quem faz; checklist do impresso; a
  pessoa vê a tabela e o passo a passo; quem é do setor da categoria do chamado
  marca se o aparelho chegou; etiqueta com a loja e a logo do portal;
- o preço é calculado pelas avarias sinalizadas (ninguém escolhe padrão nem valor);
- depois da avaliação do vendedor, o gerente da loja (grupo GERENTES) aprova ou não;
- só Apple, sem nº de série e sem parecer do aparelho na tela;
- quadro de gestão para o financeiro acompanhar o que chegou ou não;
- a tela vem em etapas, com os itens obrigatórios na última; se o cliente segue
  com a troca, as fotos do aparelho são obrigatórias (gravadas no armazenamento).

Nada sai daqui: avisos do chamado (sinais, push, webhooks) são dublês, os avisos
do Renova vão só para o sino (registro no banco), a categoria e o setor que
recebe são de teste, as fotos vão para um armazenamento em memória (nada sobe
para o MinIO) e tudo roda numa transação desfeita no fim.
"""
import base64
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.db import transaction
from django.test import Client, RequestFactory
from django.test.utils import setup_test_environment
from django.utils import timezone
from PIL import Image

from notifications.models import UserNotification
from renova import checklist
from renova.context_processors import renova_menu
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile

from renova.models import CATEGORIA_PADRAO_ID, ConfiguracaoRenova, FotoRenova, PrecoAparelho, Renova
from renova.padrao import calcular_padrao
from renova.permissoes import grupo_gerentes
from renova.validacao import imei_valido, ler_checklist
from tickets.models import Category, Ticket, TicketComment, TicketLog
from users.models import Sector

# Guarda o contexto dos templates nas respostas (e troca o e-mail por um de memória).
setup_test_environment()

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


def com_digito(corpo):
    """IMEI de 15 números a partir de 14, com o dígito verificador certo."""
    soma = 0
    for posicao, digito in enumerate(int(c) for c in corpo):
        if posicao % 2 == 1:
            digito *= 2
            if digito > 9:
                digito -= 9
        soma += digito
    return corpo + str((10 - soma % 10) % 10)


def assinatura():
    buffer = io.BytesIO()
    Image.effect_noise((90, 30), 60).convert('L').save(buffer, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()


def foto(nome='foto.jpg', cor=(102, 0, 153), tamanho=(2400, 1800), formato='JPEG'):
    buffer = io.BytesIO()
    Image.new('RGB', tamanho, cor).save(buffer, format=formato)
    return SimpleUploadedFile(nome, buffer.getvalue(), content_type='image/jpeg')


def fotos_obrigatorias():
    return {f'foto_{chave}': foto(f'{chave}.jpg') for chave, _, _, _, obrigatoria in checklist.FOTOS if obrigatoria}


IMEI_1 = com_digito('35693803564380')
IMEI_2 = com_digito('01234567890123')
HOJE = timezone.localdate().isoformat()
ASSINATURA = assinatura()


def todos_ok(**nada):
    return {**{f'func_{c}': 'OK' for c, _, _, _ in checklist.FUNCIONALIDADES},
            **{f'est_{c}': 'OK' for c, _, _, _ in checklist.ESTETICA}}


marcador = transaction.atomic()
marcador.__enter__()
try:
    caches['local'].clear()
    padrao_cfg = ConfiguracaoRenova.get()
    t('a configuração nasce apontando para a categoria 89 (RENOVA VINI)',
      padrao_cfg.categoria_id == CATEGORIA_PADRAO_ID and Category.objects.filter(pk=CATEGORIA_PADRAO_ID).exists(),
      padrao_cfg.categoria_id)
    t('a tabela inicial veio do impresso (34 iPhones)',
      PrecoAparelho.objects.filter(marca='APPLE').count() >= 34
      and PrecoAparelho.objects.filter(modelo='iPhone 17 Pro Max', armazenamento='512GB', valor_excelente=5900).exists())

    loja = Sector.objects.create(name='ZZ Loja Renova Teste')
    outra_loja = Sector.objects.create(name='ZZ Outra Loja Renova')
    setor_recebe = Sector.objects.create(name='ZZ Setor Recebe Renova')
    categoria = Category.objects.create(sector=setor_recebe, name='ZZ RENOVA TESTE', default_solution_time_hours=24)
    cfg = ConfiguracaoRenova.get()
    cfg.categoria = categoria
    cfg.desconto_b, cfg.desconto_c, cfg.desconto_d = 20, 40, 60
    cfg.save()

    def novo(apelido, **extra):
        return User.objects.create_user(
            username=f'zzren.{apelido}', email=f'zzren.{apelido}@exemplo-teste.local', password='S3nha!teste',
            first_name='ZZRenova', last_name=apelido.title(), **extra)

    admin = novo('admin', hierarchy='SUPERADMIN', sector=loja)
    vendedor = novo('vendedor', sector=loja)
    gerente = novo('gerente', sector=loja)
    gerente_outra = novo('gerenteoutra', sector=outra_loja)
    financeiro = novo('financeiro')
    recebe = novo('recebe')                              # está no setor só pelo vínculo (M2M)
    recebe.sectors.add(setor_recebe)
    estranho = novo('estranho', sector=loja)
    cfg.habilitados.add(vendedor)
    cfg.financeiro.add(financeiro)
    grupo = grupo_gerentes()
    assert grupo is not None, 'o grupo GERENTES não existe no banco'
    grupo.members.add(gerente, gerente_outra)
    preco = PrecoAparelho.objects.get(marca='APPLE', modelo='iPhone 15 Pro', armazenamento='256GB')

    def checklist_completo(**extra):
        dados = {
            'modelo': 'iPhone 15 Pro', 'cor': 'Titânio natural', 'armazenamento': '256GB',
            'imei1': IMEI_1, 'imei2': IMEI_2, 'data_avaliacao': HOJE, 'loja': str(loja.pk),
            'saude_bateria': '90', 'observacoes': '',
            'vendedor_nome': 'ZZ Vendedor Renova', 'matricula': 'M123', 'assinatura': ASSINATURA,
            'data_responsavel': HOJE, 'cliente_segue': 'SIM',
        }
        dados.update(fotos_obrigatorias())
        for chave, _, _, _ in checklist.ITENS_OBRIGATORIOS:
            dados[f'obrig_{chave}'] = 'on'
        dados.update(todos_ok())
        dados.update(extra)
        return dados

    def cliente(user):
        c = Client()
        c.force_login(user)
        return c

    c_admin, c_vendedor, c_recebe, c_estranho = cliente(admin), cliente(vendedor), cliente(recebe), cliente(estranho)
    c_gerente, c_gerente_outra, c_fin = cliente(gerente), cliente(gerente_outra), cliente(financeiro)
    pedido = RequestFactory().get('/')

    def menu(user):
        caches['local'].clear()
        pedido.user = user
        return renova_menu(pedido)

    with mock.patch('notifications.services.notification_service.notify_ticket_created') as aviso_criado, \
            mock.patch('notifications.services.notification_service.notify_ticket_comment') as aviso_comentario, \
            mock.patch('notifications.services.notification_service.notify_ticket_status_changed'), \
            mock.patch('notifications.push_utils.send_push_notification_to_user') as push, \
            mock.patch.object(Ticket, 'trigger_webhooks') as webhooks, \
            mock.patch.object(Ticket, 'trigger_webhook'), \
            mock.patch.object(FotoRenova._meta.get_field('arquivo'), 'storage', InMemoryStorage()) as fotos_memoria:

        print('== QUEM ENTRA ==')
        t('quem não foi habilitado nem recebe não entra', c_estranho.get('/renova/').status_code == 302)
        html = c_vendedor.get('/renova/').content.decode()
        t('quem foi habilitado abre as avaliações, com a aba de nova avaliação',
          'href="/renova/nova/"' in html and 'Vini Renova</span>' in html)
        html = c_recebe.get('/renova/').content.decode()
        t('o setor da categoria entra pelo vínculo, mas não faz avaliação',
          'Avaliações e recebimento' in html and 'href="/renova/nova/"' not in html
          and c_recebe.get('/renova/nova/').status_code == 302)
        r = c_gerente.get('/renova/')
        t('o gerente da loja entra (para aprovar), sem fazer avaliação', r.status_code == 200
          and 'href="/renova/nova/"' not in r.content.decode(), r.status_code)
        html = c_fin.get('/renova/').content.decode()
        t('o financeiro entra e tem a aba do quadro de gestão', 'href="/renova/gestao/"' in html)
        t('quem não é financeiro não tem a aba', 'href="/renova/gestao/"' not in c_vendedor.get('/renova/').content.decode())
        t('configuração é só do SUPERADMIN', c_admin.get('/renova/configuracao/').status_code == 200
          and c_vendedor.get('/renova/configuracao/').status_code == 302)
        t('menu: liberado para quem faz, para o gerente e para o financeiro',
          menu(vendedor)['renova_liberado'] and menu(gerente)['renova_liberado'] and menu(financeiro)['renova_liberado'])
        t('menu: escondido de quem não tem acesso', menu(estranho)['renova_liberado'] is False)

        print('\n== TABELA E PASSO A PASSO ==')
        html = c_vendedor.get('/renova/tabela/').content.decode()
        t('a tabela abre com os padrões A/B/C/D e seus critérios',
          all(p in html for p in ('Excelente', 'Abaixo do padrão', 'Bateria abaixo de 70%')))
        t('com os valores calculados (17 Pro Max 512GB: 5.900 / 4.720 / 3.540 / 2.360)',
          all(v in html for v in ('5.900', '4.720', '3.540', '2.360')))
        t('e o impresso em imagem', 'renova/tabela-de-avaliacao.jpg' in html and 'Impresso' in html)
        html = c_recebe.get('/renova/passo-a-passo/').content.decode()
        t('o passo a passo abre com os 12 passos', html.count('class="rn-passo-num"') == 12, html.count('class="rn-passo-num"'))
        t('com os alertas e as faixas de bateria', 'Nunca aceite o aparelho com iCloud ativo!' in html and '80% a 84%' in html)

        print('\n== O CHECKLIST ==')
        html_nova = c_vendedor.get('/renova/nova/').content.decode()
        t('com as seções do impresso, sem o card de parecer', all(s in html_nova for s in (
            'Dados do aparelho', 'Itens obrigatórios para concluir a troca', 'Funcionalidades', 'Condição estética',
            'Observações gerais', 'Responsável pela avaliação')) and 'Parecer final do aparelho' not in html_nova)
        etapas = re.findall(r'data-etapa="(\d)" data-nome="([^"]+)"', html_nova)
        t('em 7 etapas, uma por vez', [n for n, _ in etapas] == list('1234567')
          and html_nova.count('data-ir=') == 7, etapas)
        t('os itens obrigatórios (a etapa 2 do impresso) ficam no fim, depois do responsável',
          dict(etapas).get('7') == 'Itens obrigatórios e envio'
          and html_nova.index('Responsável pela avaliação') < html_nova.index('name="obrig_capa"'))
        t('o cliente decide se segue depois de ver o valor, antes das fotos',
          html_nova.index('id="rn-valor"') < html_nova.index('name="cliente_segue" value="SIM"')
          < html_nova.index('name="foto_frente"'))
        t('fotos: envio de arquivo, uma por posição e as avarias',
          'enctype="multipart/form-data"' in html_nova
          and all(f'name="foto_{c}"' in html_nova for c, *_ in checklist.FOTOS) and 'name="foto_avaria"' in html_nova)
        itens = [titulo for _, titulo, _, _ in checklist.ITENS_OBRIGATORIOS + checklist.FUNCIONALIDADES + checklist.ESTETICA]
        t('e todos os itens', all(titulo in html_nova for titulo in itens), [x for x in itens if x not in html_nova])
        t('só Apple: sem escolha de marca', 'fa-brands fa-apple' in html_nova and 'name="marca"' not in html_nova
          and 'XIAOMI' not in html_nova and 'Samsung' not in html_nova)
        t('modelo em lista, tirada da tabela de avaliação', 'id="rn-modelo"' in html_nova
          and '<option value="iPhone 15 Pro"' in html_nova and 'id="rn-armazenamento"' in html_nova)
        t('sem nº de série', 'numero_serie' not in html_nova and 'Nº de série' not in html_nova)
        t('padrão e valor só aparecem: não há campo de valor, padrão nem parecer', 'id="rn-valor"' in html_nova
          and 'id="rn-padrao-letra"' in html_nova and all(f'name="{c}"' not in html_nova for c in ('valor_estimado', 'padrao', 'parecer')))
        t('a tela recebe a tabela e as mesmas regras do servidor', 'id="rn-precos"' in html_nova and 'id="rn-regras"' in html_nova)
        t('e o botão manda para a aprovação do gerente', 'Enviar para aprovação do gerente' in html_nova)
        t('já vem com o nome do vendedor e a loja dele', 'value="ZZRenova Vendedor"' in html_nova
          and f'<option value="{loja.pk}" selected>' in html_nova)

        node = shutil.which('node')
        script = next((s for s in re.findall(r'<script>(.*?)</script>', html_nova, flags=re.S) if 'calcularPadrao' in s), '')
        if node and script:
            with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as arquivo:
                arquivo.write(script)
            checagem = subprocess.run([node, '--check', arquivo.name], capture_output=True, text=True, timeout=30)
            os.unlink(arquivo.name)
            t('o JS da tela é válido (node --check)', checagem.returncode == 0, checagem.stderr[-300:])
        else:
            print('  (node não encontrado ou script ausente: sintaxe do JS não conferida)')

        antes = Renova.objects.count()
        sem_fotos = {k: v for k, v in checklist_completo().items() if not k.startswith('foto_')}
        html = c_vendedor.post('/renova/nova/', sem_fotos).content.decode()
        t('sem as fotos obrigatórias não grava e volta na etapa das fotos',
          'Tire as fotos obrigatórias: Frente, Traseira, Laterais.' in html and 'data-etapa-inicial="5"' in html
          and Renova.objects.count() == antes)
        falsa = SimpleUploadedFile('frente.jpg', b'<html>nao sou foto</html>' * 30, content_type='image/jpeg')
        html = c_vendedor.post('/renova/nova/', checklist_completo(foto_frente=falsa)).content.decode()
        t('arquivo que só diz ser foto é recusado (e pede para escolher as fotos de novo)',
          'Frente: não abriu como imagem' in html and 'escolha as fotos de novo' in html and Renova.objects.count() == antes)
        html = c_vendedor.post('/renova/nova/', checklist_completo(cliente_segue='NAO')).content.decode()
        t('cliente que não quer seguir: nada é gravado', 'quando o cliente quer seguir' in html
          and 'data-etapa-inicial="4"' in html and Renova.objects.count() == antes)
        r = c_vendedor.post('/renova/nova/', checklist_completo(imei1='12345', obrig_chip='', func_bateria='', saude_bateria=''))
        html = r.content.decode()
        t('incompleto: a tela volta com os erros e não grava', r.status_code == 200 and 'Faltou pouco' in html
          and Renova.objects.count() == antes)
        t('aponta IMEI, item obrigatório, funcionalidade e saúde da bateria', 'IMEI inválido' in html
          and 'Conferir se o chip foi removido' in html and 'faltou Bateria' in html and 'Informe a saúde da bateria' in html)
        t('e mantém o modelo escolhido', '<option value="iPhone 15 Pro" selected>' in html)
        html = c_vendedor.post('/renova/nova/', checklist_completo(modelo='iPhone 99 Ultra')).content.decode()
        t('modelo fora da tabela é recusado', 'não está na tabela de avaliação' in html and Renova.objects.count() == antes)

        print('\n== ENVIO: VAI PARA O GERENTE ==')
        r = c_vendedor.post('/renova/nova/', checklist_completo(
            saude_bateria='82', est_laterais='OBS', observacoes='Risco na lateral esquerda', padrao='A',
            valor_estimado='99999', parecer=checklist.APROVADO, marca='SAMSUNG', numero_serie='X1',
            foto_tela_ligada=foto('sobre.png', formato='PNG', tamanho=(900, 1600)),
            foto_avaria=[foto('risco1.jpg'), foto('risco2.jpg')]))
        renova = Renova.objects.filter(criado_por=vendedor).order_by('-pk').first()
        fotos = renova.fotos_em_ordem() if renova else []
        t('as fotos ficam na avaliação, na ordem do checklist (fixas e depois as avarias)',
          [(f.tipo, f.ordem) for f in fotos] == [('frente', 0), ('traseira', 0), ('laterais', 0), ('tela_ligada', 0),
                                                 ('avaria', 1), ('avaria', 2)], [(f.tipo, f.ordem) for f in fotos])
        with Image.open(fotos_memoria.open(fotos[0].arquivo.name)) as gravada:
            t('gravadas em JPEG, reduzidas a 1920 px, com nome sem dados do cliente',
              gravada.format == 'JPEG' and max(gravada.size) == 1920 and fotos[0].arquivo.name.startswith('renova/fotos/')
              and IMEI_1 not in fotos[0].arquivo.name, (gravada.format, gravada.size, fotos[0].arquivo.name))
        with Image.open(fotos_memoria.open(fotos[3].arquivo.name)) as gravada:
            t('PNG vira JPEG também', gravada.format == 'JPEG' and gravada.size == (900, 1600), gravada.size)
        t('grava a avaliação e abre o detalhe dela', renova is not None and r.status_code == 302
          and r['Location'] == f'/renova/{renova.pk}/', r.get('Location'))
        t('fica aguardando a aprovação do gerente, sem chamado', renova.aprovacao == Renova.AGUARDANDO_GERENTE
          and renova.chamado_id is None and not aviso_criado.called)
        t('o padrão sai das avarias: bateria 82% e lateral com observação = B',
          renova.padrao == 'B' and [m['texto'] for m in renova.padrao_motivos] == ['Bateria em 82%', 'Laterais com observação'],
          (renova.padrao, renova.padrao_motivos))
        t('o valor sai da tabela no padrão B (A − 20%), e o que veio da tela é ignorado',
          renova.valor_estimado == 2080 and renova.preco_tabela_id == preco.pk and renova.marca == 'APPLE'
          and renova.numero_serie == '', (renova.valor_estimado, renova.marca))
        t('o gerente da loja é avisado no sino; o da outra loja não',
          UserNotification.objects.filter(user=gerente, notification__title__contains=renova.codigo).exists()
          and not UserNotification.objects.filter(user=gerente_outra, notification__title__contains=renova.codigo).exists())
        t('menu do gerente mostra a troca a aprovar', menu(gerente)['renova_aguardando'] == 1, menu(gerente))

        t('antes da aprovação não há etiqueta', c_vendedor.get(f'/renova/{renova.pk}/etiqueta/').status_code == 302)
        c_recebe.post(f'/renova/{renova.pk}/recebimento/', {'situacao': Renova.CHEGOU})
        renova.refresh_from_db()
        t('nem marcação de chegada', renova.recebimento == Renova.PENDENTE)
        html = c_vendedor.get(f'/renova/{renova.pk}/').content.decode()
        t('o vendedor vê quem aprova, sem poder decidir', 'ZZRenova Gerente' in html and 'name="decisao"' not in html
          and 'Aguardando aprovação do gerente' in html)
        html = c_gerente.get(f'/renova/{renova.pk}/').content.decode()
        t('o gerente vê os motivos do padrão e os botões de aprovar e reprovar',
          'value="aprovar"' in html and 'value="reprovar"' in html and 'Laterais com observação' in html)
        t('e as fotos do aparelho', html.count('data-galeria') == 6 and fotos[0].arquivo.url in html)
        html = c_gerente.get('/renova/').content.decode()
        t('e a lista dele leva direto para aprovar', f'/renova/{renova.pk}/#aprovacao' in html
          and c_gerente.get('/renova/').context['kpis']['a_aprovar'] == 1)

        c_gerente_outra.post(f'/renova/{renova.pk}/aprovacao/', {'decisao': 'aprovar'})
        c_vendedor.post(f'/renova/{renova.pk}/aprovacao/', {'decisao': 'aprovar'})
        renova.refresh_from_db()
        t('nem o gerente de outra loja nem o vendedor aprovam', renova.aprovacao == Renova.AGUARDANDO_GERENTE)
        t('o gerente de outra loja nem vê a avaliação', c_gerente_outra.get(f'/renova/{renova.pk}/').status_code == 302)
        c_gerente.post(f'/renova/{renova.pk}/aprovacao/', {'decisao': 'reprovar', 'observacao': ''})
        renova.refresh_from_db()
        t('reprovar sem dizer o motivo não vale', renova.aprovacao == Renova.AGUARDANDO_GERENTE)

        print('\n== APROVAÇÃO: ABRE O CHAMADO ==')
        r = c_gerente.post(f'/renova/{renova.pk}/aprovacao/', {'decisao': 'aprovar', 'observacao': 'Pode trocar'}, follow=True)
        renova.refresh_from_db()
        chamado = renova.chamado
        t('aprovada, com quem e quando', renova.aprovacao == Renova.APROVADA and renova.aprovacao_por_id == gerente.pk
          and renova.aprovacao_em is not None and renova.aprovacao_obs == 'Pode trocar')
        t('o chamado abre na categoria configurada, em nome do vendedor', chamado is not None
          and chamado.category_id == categoria.pk and chamado.sector_id == setor_recebe.pk and chamado.created_by_id == vendedor.pk)
        t('com o checklist, o padrão e a aprovação', chamado is not None and renova.codigo in chamado.title
          and IMEI_1 in chamado.description and 'Por que este padrão: Bateria em 82%' in chamado.description
          and 'Aprovada pelo gerente por ZZRenova Gerente' in chamado.description and 'Nº de série' not in chamado.description
          and 'Fotos do aparelho: 6 — Frente, Traseira, Laterais, Tela ligada, Avaria, Avaria' in chamado.description)
        t('e o histórico e os avisos de sempre do chamado', TicketLog.objects.filter(ticket=chamado, new_status='ABERTO').exists()
          and aviso_criado.called and webhooks.called)
        t('o vendedor é avisado no sino', UserNotification.objects.filter(
            user=vendedor, notification__title__contains=renova.codigo).exists())
        c_gerente.post(f'/renova/{renova.pk}/aprovacao/', {'decisao': 'reprovar', 'observacao': 'mudei de ideia'})
        renova.refresh_from_db()
        t('decisão tomada não muda mais', renova.aprovacao == Renova.APROVADA)
        html = c_vendedor.get(f'/renova/{renova.pk}/etiqueta/?novo=1').content.decode()
        t('a etiqueta sai, com quem aprovou, a loja, a logo e o IMEI', 'Aprovado por' in html and 'ZZRenova Gerente' in html
          and 'images/logo.png' in html and 'ZZ Loja Renova Teste' in html and IMEI_1 in html and 'Nº de série' not in html)

        print('\n== RECEBIMENTO ==')
        html = c_recebe.get(f'/renova/{renova.pk}/').content.decode()
        t('o setor que recebe vê os botões', 'value="CHEGOU"' in html and 'value="NAO_CHEGOU"' in html)
        t('e a troca aprovada aparece aguardando', renova.codigo in c_recebe.get('/renova/?recebimento=AGUARDANDO').content.decode())
        r = c_recebe.post(f'/renova/{renova.pk}/recebimento/',
                          {'situacao': Renova.CHEGOU, 'observacao': 'Caixa ok', 'voltar': 'https://exemplo.com/fora'})
        renova.refresh_from_db()
        t('o setor marca que chegou', renova.recebimento == Renova.CHEGOU and renova.recebido_por_id == recebe.pk
          and renova.recebimento_obs == 'Caixa ok')
        t('sem redirecionar para fora do portal', r['Location'] == f'/renova/{renova.pk}/', r.get('Location'))
        comentario = TicketComment.objects.filter(ticket=chamado).order_by('-pk').first()
        t('e isso fica no chamado (e avisa quem abriu)', comentario is not None and 'Chegou' in comentario.comment
          and aviso_comentario.called)
        c_recebe.post(f'/renova/{renova.pk}/recebimento/', {'situacao': Renova.PENDENTE})
        renova.refresh_from_db()
        t('dá para voltar para "aguardando"', renova.recebimento == Renova.PENDENTE and renova.recebido_por_id is None)

        print('\n== REPROVAÇÃO: SEM TROCA ==')
        c_vendedor.post('/renova/nova/', checklist_completo(imei1=com_digito('35693803564384'), saude_bateria='65',
                                                            func_cameras='NAO', observacoes='Câmera não foca'))
        reprovada = Renova.objects.filter(criado_por=vendedor).order_by('-pk').first()
        t('bateria 65% e câmera que não funciona = D',
          reprovada.padrao == 'D' and reprovada.valor_estimado == 1040, (reprovada.padrao, reprovada.valor_estimado))
        c_gerente.post(f'/renova/{reprovada.pk}/aprovacao/', {'decisao': 'reprovar', 'observacao': 'Cliente não aceitou o valor'})
        reprovada.refresh_from_db()
        t('reprovada: sem chamado e marcada como não aprovada', reprovada.aprovacao == Renova.REPROVADA
          and reprovada.parecer == checklist.NAO_APROVADO and reprovada.chamado_id is None)
        t('o vendedor fica sabendo o motivo', UserNotification.objects.filter(
            user=vendedor, notification__message__contains='Cliente não aceitou o valor').exists())
        html = c_recebe.get(f'/renova/{reprovada.pk}/').content.decode()
        t('aparece como "Sem troca", sem os botões de chegada', 'Sem troca' in html and 'value="CHEGOU"' not in html)
        c_recebe.post(f'/renova/{reprovada.pk}/recebimento/', {'situacao': Renova.CHEGOU})
        reprovada.refresh_from_db()
        t('ninguém marca chegada nem imprime etiqueta', reprovada.recebimento == Renova.PENDENTE
          and c_vendedor.get(f'/renova/{reprovada.pk}/etiqueta/').status_code == 302)

        print('\n== SEM CATEGORIA / CHAMADO QUE NÃO ABRIU ==')
        cfg.categoria = None
        cfg.save()
        html = c_vendedor.get('/renova/nova/').content.decode()
        t('sem categoria, a tela avisa e não deixa enviar', 'não está configurada' in html
          and re.search(r'id="rn-enviar"[^>]*disabled', html) is not None)
        antes = Renova.objects.count()
        r = c_vendedor.post('/renova/nova/', checklist_completo(imei1=com_digito('35693803564381')))
        t('e o envio é recusado sem gravar', r.status_code == 200 and Renova.objects.count() == antes)
        cfg.categoria = categoria
        cfg.save()
        c_vendedor.post('/renova/nova/', checklist_completo(imei1=com_digito('35693803564382')))
        sem_chamado = Renova.objects.filter(criado_por=vendedor).order_by('-pk').first()
        with mock.patch('renova.servicos.abrir_chamado', side_effect=RuntimeError('fora do ar')):
            r = c_gerente.post(f'/renova/{sem_chamado.pk}/aprovacao/', {'decisao': 'aprovar'}, follow=True)
        sem_chamado.refresh_from_db()
        t('se o chamado falhar na aprovação, a aprovação vale e o gerente é avisado',
          sem_chamado.aprovada and sem_chamado.chamado_id is None and 'o chamado não abriu' in r.content.decode())
        t('e dá para abrir o chamado de novo pela avaliação',
          'Abrir o chamado agora' in c_vendedor.get(f'/renova/{sem_chamado.pk}/').content.decode())
        c_vendedor.post(f'/renova/{sem_chamado.pk}/abrir-chamado/')
        sem_chamado.refresh_from_db()
        t('abrindo de verdade na segunda tentativa', sem_chamado.chamado_id is not None)

        print('\n== VISIBILIDADE ==')
        outro = Renova.objects.create(criado_por=admin, marca='APPLE', modelo='iPhone 13', armazenamento='128GB',
                                      imei1=com_digito('35693803564383'), loja=outra_loja, parecer=checklist.APROVADO,
                                      vendedor_nome='ZZ Admin')
        t('quem faz vê só as próprias', outro.codigo not in c_vendedor.get('/renova/').content.decode()
          and c_vendedor.get(f'/renova/{outro.pk}/').status_code == 302)
        t('o gerente vê as da loja dele, não as de outra', renova.codigo in c_gerente.get('/renova/').content.decode()
          and outro.codigo not in c_gerente.get('/renova/').content.decode()
          and outro.codigo in c_gerente_outra.get('/renova/').content.decode())
        t('SUPERADMIN, financeiro e quem recebe veem todas',
          all(outro.codigo in c.get('/renova/').content.decode() for c in (c_admin, c_fin, c_recebe)))
        t('busca por código RN-', outro.codigo in c_admin.get(f'/renova/?q={outro.codigo}').content.decode())

        print('\n== QUADRO DE GESTÃO ==')
        t('só o financeiro e o SUPERADMIN abrem', c_fin.get('/renova/gestao/').status_code == 200
          and c_admin.get('/renova/gestao/').status_code == 200 and c_vendedor.get('/renova/gestao/').status_code == 302
          and c_gerente.get('/renova/gestao/').status_code == 302)
        r = c_fin.get(f'/renova/gestao/?loja={loja.pk}')
        esperado = Counter(x.situacao[0] for x in Renova.objects.filter(loja=loja))
        resumo = {item['codigo']: item['quantidade'] for item in r.context['resumo']}
        t('os totais por situação batem com as trocas da loja', all(resumo[c] == esperado.get(c, 0) for c in resumo)
          and resumo['REPROVADA'] >= 1 and resumo['A_CAMINHO'] >= 2, (resumo, esperado))
        valor = next(item['valor'] for item in r.context['resumo'] if item['codigo'] == 'REPROVADA')
        t('com o valor somado de cada situação', valor == 1040, valor)
        html = c_fin.get(f'/renova/gestao/?loja={loja.pk}&situacao=REPROVADA').content.decode()
        t('o filtro de situação mostra só aquelas trocas', reprovada.codigo in html and renova.codigo not in html)
        r = c_fin.get(f'/renova/gestao/?loja={loja.pk}&formato=csv')
        csv_texto = r.content.decode('utf-8')
        t('exporta planilha (CSV com acentos certos no Excel)', r['Content-Type'].startswith('text/csv')
          and csv_texto.startswith('﻿') and 'Código;Avaliado em;Loja' in csv_texto and renova.codigo in csv_texto
          and 'Reprovada — sem troca' in csv_texto)

        print('\n== CONFIGURAÇÃO ==')
        c_admin.post('/renova/configuracao/', {
            'secao': 'acesso', 'habilitados': [vendedor.pk, estranho.pk], 'financeiro': [financeiro.pk, recebe.pk],
            'categoria': categoria.pk, 'desconto_b': '25', 'desconto_c': '45', 'desconto_d': '65'})
        cfg.refresh_from_db()
        t('o SUPERADMIN define quem faz e quem é do financeiro',
          set(cfg.habilitados.values_list('pk', flat=True)) == {vendedor.pk, estranho.pk}
          and set(cfg.financeiro.values_list('pk', flat=True)) == {financeiro.pk, recebe.pk})
        caches['local'].clear()
        t('e a pessoa nova já entra', c_estranho.get('/renova/nova/').status_code == 200)
        t('descontos salvos e usados no cálculo', (cfg.desconto_b, cfg.desconto_c, cfg.desconto_d) == (25, 45, 65)
          and cfg.valor_do_padrao(1000, 'B') == 750)
        linha = PrecoAparelho.objects.get(marca='APPLE', modelo='iPhone 13', armazenamento='128GB')
        c_admin.post('/renova/configuracao/', {
            'secao': 'tabela', 'preco_id': [linha.pk], f'modelo_{linha.pk}': 'iPhone 13',
            f'armazenamento_{linha.pk}': '128gb', f'valor_{linha.pk}': '1050.00', f'ordem_{linha.pk}': str(linha.ordem),
            f'ativo_{linha.pk}': 'on', 'novo_marca': ['APPLE', 'APPLE'], 'novo_modelo': ['iPhone 17 Air', 'iPhone 13'],
            'novo_armazenamento': ['256', '128GB'], 'novo_valor': ['3900', '999']})
        linha.refresh_from_db()
        t('edita um valor da tabela', linha.valor_excelente == 1050 and linha.armazenamento == '128GB')
        t('inclui modelo novo (256 vira 256GB) e ele entra na lista do formulário',
          PrecoAparelho.objects.filter(modelo='iPhone 17 Air', armazenamento='256GB').exists()
          and '<option value="iPhone 17 Air"' in c_vendedor.get('/renova/nova/').content.decode())
        t('e recusa linha repetida', PrecoAparelho.objects.filter(modelo='iPhone 13', armazenamento='128GB').count() == 1)
        r = c_vendedor.post('/renova/configuracao/', {'secao': 'acesso', 'habilitados': [], 'categoria': ''})
        cfg.refresh_from_db()
        t('quem não é SUPERADMIN não muda nada', r.status_code == 302 and cfg.habilitados.count() == 2
          and cfg.financeiro.count() == 2 and cfg.categoria_id == categoria.pk)

        print('\n== MATERIAIS IMPRESSOS ==')
        from django.core.files.storage import InMemoryStorage
        from django.core.files.uploadedfile import SimpleUploadedFile
        memoria = InMemoryStorage()   # nada vai para o MinIO
        with mock.patch.object(ConfiguracaoRenova._meta.get_field('imagem_tabela'), 'storage', memoria):
            falso = SimpleUploadedFile('tabela.html', b'<script>alert(1)</script>' * 20, content_type='image/png')
            c_admin.post('/renova/configuracao/', {'secao': 'materiais', 'imagem_tabela': falso})
            cfg.refresh_from_db()
            t('arquivo que só diz ser imagem é recusado', not cfg.imagem_tabela, cfg.imagem_tabela.name)
            buf = io.BytesIO()
            Image.new('RGB', (40, 30), (102, 0, 153)).save(buf, format='PNG')
            real = SimpleUploadedFile('tabela.html', buf.getvalue(), content_type='image/png')
            c_admin.post('/renova/configuracao/', {'secao': 'materiais', 'imagem_tabela': real})
            cfg.refresh_from_db()
            t('imagem de verdade entra, gravada com a extensão do conteúdo', cfg.imagem_tabela.name.endswith('tabela.png')
              and memoria.exists(cfg.imagem_tabela.name), cfg.imagem_tabela.name)
            c_admin.post('/renova/configuracao/', {'secao': 'materiais', 'remover_imagem_tabela': 'on'})
            cfg.refresh_from_db()
            t('e dá para voltar ao impresso padrão', not cfg.imagem_tabela, cfg.imagem_tabela.name)

    print('\n== PADRÃO PELAS AVARIAS ==')
    ok_func = {c: 'OK' for c, _, _, _ in checklist.FUNCIONALIDADES}
    ok_est = {c: 'OK' for c, _, _, _ in checklist.ESTETICA}

    def letra(bateria=90, func=None, est=None):
        return calcular_padrao({**ok_func, **(func or {})}, {**ok_est, **(est or {})}, bateria)[0]

    t('tudo OK e bateria acima de 85%: A, sem motivos', calcular_padrao(ok_func, ok_est, 86) == ('A', []))
    t('bateria: 85% e 80% = B, 79% e 70% = C, 69% = D',
      [letra(b) for b in (85, 80, 79, 70, 69)] == ['B', 'B', 'C', 'C', 'D'], [letra(b) for b in (85, 80, 79, 70, 69)])
    t('função com observação = B; função que não funciona = D',
      letra(func={'audio': 'OBS'}) == 'B' and letra(func={'audio': 'NAO'}) == 'D')
    t('estética com observação = B; não OK = C', letra(est={'traseira': 'OBS'}) == 'B' and letra(est={'traseira': 'NAO'}) == 'C')
    t('trinco com observação = C; trinco não OK = D', letra(est={'trincos': 'OBS'}) == 'C' and letra(est={'trincos': 'NAO'}) == 'D')
    t('três itens estéticos não OK = D (dois ainda são C)',
      letra(est={'tela': 'NAO', 'traseira': 'NAO', 'laterais': 'NAO'}) == 'D'
      and letra(est={'tela': 'NAO', 'traseira': 'NAO'}) == 'C')
    final, motivos = calcular_padrao({**ok_func, 'wifi': 'OBS'}, {**ok_est, 'laterais': 'NAO'}, 82)
    t('vale o pior, e os motivos vêm do pior para o melhor',
      final == 'C' and [m['letra'] for m in motivos] == ['C', 'B', 'B'], (final, motivos))

    print('\n== VALIDAÇÃO ==')
    t('IMEI com dígito verificador certo passa', imei_valido(IMEI_1) and imei_valido('490154203237518'))
    t('IMEI digitado errado não passa', not imei_valido(IMEI_1[:-1] + str((int(IMEI_1[-1]) + 1) % 10)) and not imei_valido('abc'))
    lojas = {str(loja.pk): loja}
    tabela = {str(p.pk): p for p in PrecoAparelho.objects.filter(ativo=True)}
    config_atual = ConfiguracaoRenova.get()
    dados, erros = ler_checklist(checklist_completo(saude_bateria='75', padrao='A', valor_estimado='99999'), lojas=lojas, precos=tabela)
    t('padrão e valor calculados: bateria 75% = C, pelo desconto configurado',
      not erros and dados['padrao'] == 'C' and dados['valor_estimado'] == config_atual.valor_do_padrao(preco.valor_excelente, 'C'),
      (erros, dados.get('padrao'), dados.get('valor_estimado')))
    dados, _ = ler_checklist(checklist_completo(modelo='  iphone 15  PRO '), lojas=lojas, precos=tabela)
    t('o modelo casa com a tabela mesmo escrito de outro jeito', dados['preco_tabela'] == preco)
    _, erros = ler_checklist(checklist_completo(armazenamento='1TB'), lojas=lojas, precos=tabela)
    t('armazenamento que a tabela não tem para o modelo é recusado', 'modelo' in erros, erros)
    _, erros = ler_checklist(checklist_completo(saude_bateria=''), lojas=lojas, precos=tabela)
    t('sem saúde da bateria não conclui', 'saude_bateria' in erros, erros)
    _, erros = ler_checklist(checklist_completo(func_bateria='OBS', observacoes=''), lojas=lojas, precos=tabela)
    t('item com observação pede a observação', 'observacoes' in erros, erros)
    dados, erros = ler_checklist(checklist_completo(parecer=checklist.NAO_APROVADO), lojas=lojas, precos=tabela)
    t('parecer enviado pela tela é ignorado', not erros and dados['parecer'] == checklist.APROVADO, erros)
    _, erros = ler_checklist(checklist_completo(data_avaliacao='2999-01-01'), lojas=lojas, precos=tabela)
    t('data da avaliação no futuro é recusada', 'data_avaliacao' in erros, erros)
    _, erros = ler_checklist(checklist_completo(assinatura=''), lojas=lojas, precos=tabela)
    t('sem assinatura não conclui', 'assinatura' in erros, erros)
    _, erros = ler_checklist(checklist_completo(cliente_segue=''), lojas=lojas, precos=tabela)
    t('sem o cliente dizer que segue não conclui', 'cliente_segue' in erros, erros)
    _, erros = ler_checklist(checklist_completo(loja='999999999'), lojas=lojas, precos=tabela)
    t('loja fora da lista é recusada', 'loja' in erros, erros)
    t('o push do comentário do chamado foi sempre dublê (nada saiu)', isinstance(push, mock.MagicMock))
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    caches['local'].clear()
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
