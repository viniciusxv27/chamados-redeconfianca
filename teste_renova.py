"""Vini Renova: checklist do aparelho, chamado automático, etiqueta, recebimento e configuração.

Pedido: módulo novo em que o SUPERADMIN vê todos os Renovas e define quem pode
fazer; o checklist é o do impresso e, ao enviar, abre sozinho o chamado na
categoria do Renova com as informações; quem é do setor dessa categoria marca
se o aparelho chegou; a pessoa vê a tabela e o passo a passo em imagem e em
HTML; e, concluído, aparece a etiqueta para completar e imprimir, com a loja de
origem e a logo do portal.

Nada sai daqui: avisos do chamado (sinais, push, webhooks) são dublês, a
categoria e o setor que recebe são de teste e tudo roda numa transação desfeita
no fim.
"""
import base64
import io
import os
import re
import sys
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
from django.utils import timezone
from PIL import Image

from renova import checklist
from renova import views as renova_views
from renova.context_processors import renova_menu
from renova.models import CATEGORIA_PADRAO_ID, ConfiguracaoRenova, PrecoAparelho, Renova
from renova.validacao import imei_valido, ler_checklist
from tickets.models import Category, Ticket, TicketComment, TicketLog
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


IMEI_1 = com_digito('35693803564380')
IMEI_2 = com_digito('01234567890123')
HOJE = timezone.localdate().isoformat()
ASSINATURA = assinatura()


marcador = transaction.atomic()
marcador.__enter__()
try:
    caches['local'].clear()
    padrao = ConfiguracaoRenova.get()
    t('a configuração nasce apontando para a categoria 89 (RENOVA VINI)',
      padrao.categoria_id == CATEGORIA_PADRAO_ID and Category.objects.filter(pk=CATEGORIA_PADRAO_ID).exists(),
      padrao.categoria_id)
    t('a tabela inicial veio do impresso (34 iPhones)',
      PrecoAparelho.objects.filter(marca='APPLE').count() >= 34
      and PrecoAparelho.objects.filter(modelo='iPhone 17 Pro Max', armazenamento='512GB', valor_excelente=5900).exists())

    loja = Sector.objects.create(name='ZZ Loja Renova Teste')
    setor_recebe = Sector.objects.create(name='ZZ Setor Recebe Renova')
    categoria = Category.objects.create(sector=setor_recebe, name='ZZ RENOVA TESTE', default_solution_time_hours=24)
    cfg = ConfiguracaoRenova.get()
    cfg.categoria = categoria
    cfg.save()

    def novo(apelido, **extra):
        return User.objects.create_user(
            username=f'zzren.{apelido}', email=f'zzren.{apelido}@exemplo-teste.local', password='S3nha!teste',
            first_name='ZZRenova', last_name=apelido.title(), **extra)

    admin = novo('admin', hierarchy='SUPERADMIN', sector=loja)
    vendedor = novo('vendedor', sector=loja)
    recebe = novo('recebe')                              # está no setor só pelo vínculo (M2M)
    recebe.sectors.add(setor_recebe)
    estranho = novo('estranho', sector=loja)
    cfg.habilitados.add(vendedor)
    preco = PrecoAparelho.objects.get(marca='APPLE', modelo='iPhone 15 Pro', armazenamento='256GB')

    def checklist_completo(**extra):
        dados = {
            'marca': 'APPLE', 'modelo': 'iPhone 15 Pro', 'cor': 'Titânio natural', 'armazenamento': '256GB',
            'imei1': IMEI_1, 'imei2': IMEI_2, 'numero_serie': 'ZZSERIE123', 'data_avaliacao': HOJE,
            'loja': str(loja.pk), 'padrao': 'B', 'preco_tabela': str(preco.pk), 'valor_estimado': '2080.00',
            'saude_bateria': '88', 'observacoes': '', 'parecer': checklist.APROVADO,
            'vendedor_nome': 'ZZ Vendedor Renova', 'matricula': 'M123', 'assinatura': ASSINATURA,
            'data_responsavel': HOJE,
        }
        for chave, _, _, _ in checklist.ITENS_OBRIGATORIOS:
            dados[f'obrig_{chave}'] = 'on'
        for chave, _, _, _ in checklist.FUNCIONALIDADES:
            dados[f'func_{chave}'] = 'OK'
        for chave, _, _, _ in checklist.ESTETICA:
            dados[f'est_{chave}'] = 'OK'
        dados.update(extra)
        return dados

    def cliente(user):
        c = Client()
        c.force_login(user)
        return c

    c_admin, c_vendedor, c_recebe, c_estranho = cliente(admin), cliente(vendedor), cliente(recebe), cliente(estranho)

    with mock.patch('notifications.services.notification_service.notify_ticket_created') as aviso_criado, \
            mock.patch('notifications.services.notification_service.notify_ticket_comment') as aviso_comentario, \
            mock.patch('notifications.services.notification_service.notify_ticket_status_changed'), \
            mock.patch('notifications.push_utils.send_push_notification_to_user') as push, \
            mock.patch.object(Ticket, 'trigger_webhooks') as webhooks, \
            mock.patch.object(Ticket, 'trigger_webhook'):

        print('== QUEM ENTRA ==')
        r = c_estranho.get('/renova/')
        t('quem não foi habilitado nem recebe não entra', r.status_code == 302, r.status_code)
        r = c_vendedor.get('/renova/')
        html = r.content.decode()
        t('quem foi habilitado abre as avaliações', r.status_code == 200 and 'Minhas avaliações' in html, r.status_code)
        t('com a aba de nova avaliação e o item no menu', 'href="/renova/nova/"' in html and 'Vini Renova</span>' in html)
        r = c_recebe.get('/renova/')
        html = r.content.decode()
        t('o setor da categoria entra pelo vínculo (sem ser o setor principal)',
          r.status_code == 200 and 'Avaliações e recebimento' in html, r.status_code)
        t('mas não faz avaliação', 'href="/renova/nova/"' not in html
          and c_recebe.get('/renova/nova/').status_code == 302)
        t('configuração é só do SUPERADMIN', c_admin.get('/renova/configuracao/').status_code == 200
          and c_vendedor.get('/renova/configuracao/').status_code == 302)
        pedido = RequestFactory().get('/')
        pedido.user = vendedor
        t('menu: liberado para quem faz', renova_menu(pedido)['renova_liberado'] is True)
        pedido.user = estranho
        t('menu: escondido de quem não tem acesso', renova_menu(pedido)['renova_liberado'] is False)

        print('\n== TABELA E PASSO A PASSO ==')
        r = c_vendedor.get('/renova/tabela/')
        html = r.content.decode()
        t('a tabela abre', r.status_code == 200, r.status_code)
        t('com os padrões A/B/C/D e seus critérios', all(p in html for p in ('Excelente', 'Abaixo do padrão', 'Bateria abaixo de 70%')))
        t('com os valores calculados (17 Pro Max 512GB: 5.900 / 4.720 / 3.540 / 2.360)',
          all(v in html for v in ('5.900', '4.720', '3.540', '2.360')))
        t('e o impresso em imagem', 'renova/tabela-de-avaliacao.jpg' in html and 'Impresso' in html)
        r = c_recebe.get('/renova/passo-a-passo/')
        html = r.content.decode()
        t('o passo a passo abre com os 12 passos', r.status_code == 200 and html.count('class="rn-passo-num"') == 12,
          html.count('class="rn-passo-num"'))
        t('com os alertas e as faixas de bateria', 'Nunca aceite o aparelho com iCloud ativo!' in html and '80% a 84%' in html)
        t('e o impresso em imagem', 'renova/passo-a-passo-da-avaliacao.jpg' in html)

        print('\n== O CHECKLIST ==')
        r = c_vendedor.get('/renova/nova/')
        html = r.content.decode()
        t('o formulário abre', r.status_code == 200, r.status_code)
        t('com as 7 seções do impresso', all(s in html for s in (
            'Dados do aparelho', 'Itens obrigatórios antes da avaliação', 'Funcionalidades', 'Condição estética',
            'Observações gerais', 'Parecer final do aparelho', 'Responsável pela avaliação')))
        itens = [titulo for _, titulo, _, _ in checklist.ITENS_OBRIGATORIOS + checklist.FUNCIONALIDADES + checklist.ESTETICA]
        t('e todos os itens', all(titulo in html for titulo in itens), [x for x in itens if x not in html])
        t('marca, armazenamento, IMEI, série, loja e valor estimado', all(c in html for c in (
            'name="marca"', 'value="XIAOMI"', 'value="1TB"', 'name="imei1"', 'name="imei2"', 'name="numero_serie"',
            'name="loja"', 'name="valor_estimado"')))
        t('parecer com as três opções e o quadro de assinatura', 'Aprovado com observações' in html
          and 'Não aprovado' in html and 'id="rn-assinatura"' in html)
        t('a tabela vai junto para sugerir o valor', 'id="rn-precos"' in html and 'iPhone 15 Pro' in html)
        t('já vem com o nome do vendedor e a loja dele', 'value="ZZRenova Vendedor"' in html
          and f'<option value="{loja.pk}" selected>' in html)
        t('o impresso do checklist está a um clique', 'renova/checklist-de-avaliacao.jpg' in html)

        antes = Renova.objects.count()
        incompleto = checklist_completo(imei1='12345', obrig_chip='', func_bateria='')
        r = c_vendedor.post('/renova/nova/', incompleto)
        html = r.content.decode()
        t('incompleto: a tela volta com os erros', r.status_code == 200 and 'Faltou pouco' in html, r.status_code)
        t('aponta IMEI, item obrigatório e funcionalidade', 'IMEI inválido' in html
          and 'Conferir se o chip foi removido' in html and 'Marque todas as funcionalidades: faltou Bateria' in html)
        t('mantém o que já foi preenchido', 'value="iPhone 15 Pro"' in html and 'value="ZZSERIE123"' in html)
        t('e não grava nada', Renova.objects.count() == antes)

        r = c_vendedor.post('/renova/nova/', checklist_completo())
        renova = Renova.objects.filter(criado_por=vendedor).order_by('-pk').first()
        t('completo: grava a avaliação', renova is not None and renova.imei1 == IMEI_1 and renova.loja_id == loja.pk)
        t('e vai para a etiqueta', r.status_code == 302 and renova is not None
          and r['Location'] == f'/renova/{renova.pk}/etiqueta/?novo=1', r.get('Location'))
        t('respostas guardadas item a item', renova is not None and renova.funcionalidades.get('bateria') == 'OK'
          and all(renova.itens_obrigatorios.values()) and renova.valor_estimado == 2080 and renova.padrao == 'B')
        chamado = renova.chamado if renova else None
        t('o chamado abre sozinho na categoria configurada', chamado is not None and chamado.category_id == categoria.pk
          and chamado.sector_id == setor_recebe.pk and chamado.created_by_id == vendedor.pk)
        t('com as informações do checklist', chamado is not None and renova.codigo in chamado.title
          and IMEI_1 in chamado.description and 'FUNCIONALIDADES' in chamado.description
          and 'ZZ Loja Renova Teste' in chamado.description and '/renova/' in chamado.description)
        t('e o histórico de abertura, com os avisos de sempre do chamado',
          chamado is not None and TicketLog.objects.filter(ticket=chamado, new_status='ABERTO').exists()
          and aviso_criado.called and webhooks.called)

        r = c_vendedor.get(f'/renova/{renova.pk}/etiqueta/?novo=1')
        html = r.content.decode()
        t('a etiqueta abre com o aviso de chamado aberto', r.status_code == 200
          and f'Avaliação {renova.codigo} concluída' in html and f'#{chamado.pk}' in html)
        t('com a logo do portal, a loja de origem e os dados do aparelho', 'images/logo.png' in html
          and 'ZZ Loja Renova Teste' in html and IMEI_1 in html and 'Titânio natural' in html)
        t('e os campos para completar antes de imprimir', html.count('contenteditable="true"') >= 10
          and 'id="rn-imprimir"' in html)

        print('\n== RECEBIMENTO ==')
        r = c_vendedor.post(f'/renova/{renova.pk}/recebimento/', {'situacao': Renova.CHEGOU})
        renova.refresh_from_db()
        t('quem fez não marca a chegada', r.status_code == 302 and renova.recebimento == Renova.PENDENTE)
        html = c_recebe.get(f'/renova/{renova.pk}/').content.decode()
        t('o setor que recebe vê o detalhe com os botões', 'value="CHEGOU"' in html and 'value="NAO_CHEGOU"' in html)
        t('e o item aparece aguardando na lista dele', renova.codigo in c_recebe.get('/renova/?recebimento=AGUARDANDO').content.decode())
        r = c_recebe.post(f'/renova/{renova.pk}/recebimento/',
                          {'situacao': Renova.CHEGOU, 'observacao': 'Caixa ok', 'voltar': 'https://exemplo.com/fora'})
        renova.refresh_from_db()
        t('o setor marca que chegou', renova.recebimento == Renova.CHEGOU and renova.recebido_por_id == recebe.pk
          and renova.recebimento_obs == 'Caixa ok')
        t('sem redirecionar para fora do portal', r.status_code == 302 and r['Location'] == f'/renova/{renova.pk}/',
          r.get('Location'))
        comentario = TicketComment.objects.filter(ticket=chamado).order_by('-pk').first()
        t('e isso fica no chamado (e avisa quem abriu)', comentario is not None and comentario.comment_type == 'FOLLOW_UP'
          and 'Chegou' in comentario.comment and aviso_comentario.called)
        kpis = c_recebe.get('/renova/').context
        html = c_recebe.get('/renova/?recebimento=AGUARDANDO').content.decode()
        t('sai da fila de aguardando', renova.codigo not in html)
        c_recebe.post(f'/renova/{renova.pk}/recebimento/', {'situacao': Renova.PENDENTE})
        renova.refresh_from_db()
        t('dá para voltar para "aguardando"', renova.recebimento == Renova.PENDENTE and renova.recebido_por_id is None)

        print('\n== SEM CATEGORIA / CHAMADO QUE NÃO ABRIU ==')
        cfg.categoria = None
        cfg.save()
        html = c_vendedor.get('/renova/nova/').content.decode()
        t('sem categoria, a tela avisa e não deixa concluir', 'não está configurada' in html
          and re.search(r'id="rn-enviar"[^>]*disabled', html) is not None)
        antes = Renova.objects.count()
        r = c_vendedor.post('/renova/nova/', checklist_completo(imei1=com_digito('35693803564381')))
        t('e o envio é recusado sem gravar', r.status_code == 200 and Renova.objects.count() == antes)
        cfg.categoria = categoria
        cfg.save()
        with mock.patch.object(renova_views, 'abrir_chamado', side_effect=RuntimeError('fora do ar')):
            r = c_vendedor.post('/renova/nova/', checklist_completo(imei1=com_digito('35693803564382')), follow=True)
        sem_chamado = Renova.objects.filter(criado_por=vendedor, chamado__isnull=True).order_by('-pk').first()
        t('se o chamado falhar, a avaliação fica salva e a pessoa é avisada', sem_chamado is not None
          and 'o chamado não abriu' in r.content.decode())
        html = c_vendedor.get(f'/renova/{sem_chamado.pk}/').content.decode()
        t('e dá para abrir o chamado de novo pela avaliação', 'Abrir o chamado agora' in html)
        c_vendedor.post(f'/renova/{sem_chamado.pk}/abrir-chamado/')
        sem_chamado.refresh_from_db()
        t('abrindo de verdade na segunda tentativa', sem_chamado.chamado_id is not None)

        print('\n== VISIBILIDADE ==')
        outro = Renova.objects.create(criado_por=admin, marca='SAMSUNG', modelo='Galaxy S23', armazenamento='256GB',
                                      imei1=com_digito('35693803564383'), loja=loja, parecer=checklist.NAO_APROVADO,
                                      vendedor_nome='ZZ Admin', observacoes='Tela quebrada')
        t('quem faz vê só as próprias', outro.codigo not in c_vendedor.get('/renova/').content.decode()
          and c_vendedor.get(f'/renova/{outro.pk}/').status_code == 302)
        t('o SUPERADMIN vê todas', outro.codigo in c_admin.get('/renova/').content.decode()
          and renova.codigo in c_admin.get('/renova/').content.decode())
        t('não aprovado não entra na fila de chegada', outro.codigo not in c_recebe.get('/renova/?recebimento=AGUARDANDO').content.decode())
        html = c_recebe.get(f'/renova/{outro.pk}/').content.decode()
        t('não aprovado aparece como "Sem troca", sem os botões de chegada', 'Sem troca' in html and 'value="CHEGOU"' not in html)
        c_recebe.post(f'/renova/{outro.pk}/recebimento/', {'situacao': Renova.CHEGOU})
        outro.refresh_from_db()
        t('e ninguém marca chegada de aparelho não aprovado', outro.recebimento == Renova.PENDENTE)
        html = c_admin.get(f'/renova/{outro.pk}/etiqueta/').content.decode()
        t('a etiqueta já traz a observação e a logo no selo', 'Tela quebrada' in html and 'rn-etiqueta-logo' in html)
        t('busca por código RN-', outro.codigo in c_admin.get(f'/renova/?q={outro.codigo}').content.decode())

        print('\n== CONFIGURAÇÃO ==')
        r = c_admin.post('/renova/configuracao/', {
            'secao': 'acesso', 'habilitados': [vendedor.pk, estranho.pk], 'categoria': categoria.pk,
            'desconto_b': '25', 'desconto_c': '45', 'desconto_d': '65'})
        cfg.refresh_from_db()
        t('o SUPERADMIN define quem pode fazer', set(cfg.habilitados.values_list('pk', flat=True)) == {vendedor.pk, estranho.pk})
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
        t('inclui modelo novo (256 vira 256GB)', PrecoAparelho.objects.filter(modelo='iPhone 17 Air', armazenamento='256GB').exists())
        t('e recusa linha repetida', PrecoAparelho.objects.filter(modelo='iPhone 13', armazenamento='128GB').count() == 1)
        r = c_vendedor.post('/renova/configuracao/', {'secao': 'acesso', 'habilitados': [], 'categoria': ''})
        cfg.refresh_from_db()
        t('quem não é SUPERADMIN não muda nada', r.status_code == 302 and cfg.habilitados.count() == 2 and cfg.categoria_id == categoria.pk)

        print('\n== MATERIAIS IMPRESSOS ==')
        from io import BytesIO
        from django.core.files.storage import InMemoryStorage
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image as PILImage
        from renova.models import ConfiguracaoRenova
        memoria = InMemoryStorage()   # nada vai para o MinIO
        with mock.patch.object(ConfiguracaoRenova._meta.get_field('imagem_tabela'), 'storage', memoria):
            falso = SimpleUploadedFile('tabela.html', b'<script>alert(1)</script>' * 20, content_type='image/png')
            c_admin.post('/renova/configuracao/', {'secao': 'materiais', 'imagem_tabela': falso})
            cfg.refresh_from_db()
            t('arquivo que só diz ser imagem é recusado', not cfg.imagem_tabela, cfg.imagem_tabela.name)
            buf = BytesIO()
            PILImage.new('RGB', (40, 30), (102, 0, 153)).save(buf, format='PNG')
            real = SimpleUploadedFile('tabela.html', buf.getvalue(), content_type='image/png')
            c_admin.post('/renova/configuracao/', {'secao': 'materiais', 'imagem_tabela': real})
            cfg.refresh_from_db()
            t('imagem de verdade entra, gravada com a extensão do conteúdo', cfg.imagem_tabela.name.endswith('tabela.png')
              and memoria.exists(cfg.imagem_tabela.name), cfg.imagem_tabela.name)
            c_admin.post('/renova/configuracao/', {'secao': 'materiais', 'remover_imagem_tabela': 'on'})
            cfg.refresh_from_db()
            t('e dá para voltar ao impresso padrão', not cfg.imagem_tabela, cfg.imagem_tabela.name)

    print('\n== VALIDAÇÃO ==')
    t('IMEI com dígito verificador certo passa', imei_valido(IMEI_1) and imei_valido('490154203237518'))
    t('IMEI digitado errado não passa', not imei_valido(IMEI_1[:-1] + str((int(IMEI_1[-1]) + 1) % 10)) and not imei_valido('abc'))
    lojas = {str(loja.pk): loja}
    _, erros = ler_checklist(checklist_completo(parecer=checklist.APROVADO_OBS, observacoes=''), lojas=lojas, precos={})
    t('aprovado com observações pede a observação', 'observacoes' in erros, erros)
    _, erros = ler_checklist(checklist_completo(marca='OUTROS', marca_outra=''), lojas=lojas, precos={})
    t('marca "Outros" pede qual', 'marca_outra' in erros, erros)
    _, erros = ler_checklist(checklist_completo(data_avaliacao='2999-01-01'), lojas=lojas, precos={})
    t('data da avaliação no futuro é recusada', 'data_avaliacao' in erros, erros)
    _, erros = ler_checklist(checklist_completo(assinatura=''), lojas=lojas, precos={})
    t('sem assinatura não conclui', 'assinatura' in erros, erros)
    _, erros = ler_checklist(checklist_completo(loja='999999999'), lojas=lojas, precos={})
    t('loja fora da lista é recusada', 'loja' in erros, erros)
    t('push de verdade nunca foi chamado (só o dublê)', isinstance(push, mock.MagicMock))
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    caches['local'].clear()
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
