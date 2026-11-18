"""Importar planilha no Quadro de HC: entrada → prévia (nada gravado) → aplicar só o marcado.

Roda numa transação desfeita no fim, com cache em memória (o default é o Redis
remoto). Nada sai: o aviso de notificação é trocado por um dublê que só anota e
o download do Google é simulado.

A planilha real (aba baixada em CSV) é lida do scratchpad quando existe:
    venv/bin/python teste_quadro_hc_importar.py [/caminho/aba.csv]
"""
import io
import os
import sys
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
import redeconfianca.settings as _cfg  # noqa: E402

_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'teste-{n}'}
               for n in _cfg.CACHES}
import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model  # noqa: E402
from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.db import transaction  # noqa: E402
from django.test import Client  # noqa: E402
from django.utils import timezone  # noqa: E402

User = get_user_model()
ok = fail = 0
CSV_REAL = (sys.argv[1] if len(sys.argv) > 1 else
            '/private/tmp/claude-501/-Users-teste-Downloads-chamados-redeconfianca/'
            '60786f8d-df7c-4ce4-9157-d1b83b7f8cd1/scratchpad/planilha_hc/aba.csv')


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


CAB = ',Cargo,Nome,CIDADE ONDE RESIDE,Login,Nascimento,Contratação,Experiência 1º Janela,Experiência 2º Janela,OBS,NÚMERO DO COLABORADOR'
PLANILHA = '\n'.join([
    'j,,,,,,,,,,',
    ',ZZCART - COORDENAÇÃO - ZZCOORD,,,,,,,,,',
    ',,,,,,,,,,',
    ',ZZ ALFA - ES,,,,,,,,,',
    CAB,
    ',Consultor(a) ,ZZIMP OUTRO NOME,JACARAIPE - SERRA,89990001.0,5/3/1999,01/02/2025,,,NOVATO,27 99999-0001',
    ',Consultor(a),ZZIMP FULANO  DA SILVA,,89990002,,,,,,',
    ',Consultor(a),ZZIMP BELTRANO MUDA,,,,,,,,',
    ',Quer ser gerente.,ZZIMP CICLANO CARGO,,aguardando,,,,,,27 98888-7777',
    ',Consultor(a),ZZIMP NINGUEM EXISTE,,,,,,,,',
    ',Consultor(a),ZZIMP HOMONIMO,,,,,,,,',
    ',Administrativo,VAGA EM ABERTO (REBECCA),,,,,,,VIVÊNCIA 19/11,',
    ',Consultor(a),VAGA EM ABERTO,,,,,,,,',
    ',Consultor(a),VAGA EM ABERTO ,,,,,,,,',
    ',,,,,,,,,,',
    ',ZZ BETA - ES,,,,,,,,,',
    CAB,
    ',Gerente Geral,ZZIMP GERENTE BETA,,,,,,,,',
    ',Consultor(a),ZZIMP PLENO VOLTA,,,,,,,,',
    ',Consultor(a) Pleno,ZZIMP PLENO FICA,,,,,,,,',
    ',,,,,,,,,,',
    ',,ESCRITÓRIO,,,,,,,,',
    ',,Cargo,Nome,Setor,,,,,,',
    ',,AUXILIAR ZZTESTE,ZZIMP ESCRITORIO,ZZ DEPTO,,,,,,',
]) + '\n'


class _Resposta:
    def __init__(self, corpo, status=200, tipo='text/csv'):
        self.status_code, self.headers = status, {'Content-Type': tipo}
        self.raw = io.BytesIO(corpo)
        self.raw.read = (lambda f: (lambda n=-1, decode_content=True: f(n)))(self.raw.read)

    def close(self):
        pass


marcador = transaction.atomic()
marcador.__enter__()
avisos_enviados = []
try:
    from quadro_hc import importacao, servicos
    from quadro_hc.models import AcessoQuadroHC, Carteira, MovimentacaoHC, ObservacaoHC, VagaHC
    from users.models import Sector, UserChangeLog

    dublê = mock.patch('quadro_hc.servicos._avisar', side_effect=lambda *a, **k: avisos_enviados.append(a) or True)
    dublê.start()
    try:
        from notifications.services import notification_service
        dublê2 = mock.patch.object(notification_service, 'send_notification',
                                   side_effect=lambda *a, **k: avisos_enviados.append(a))
        dublê2.start()
    except Exception:  # noqa: BLE001
        dublê2 = None

    A = Sector.objects.create(name='Loja ZZ Alfa', adabas='ZZI001')
    B = Sector.objects.create(name='Loja ZZ Beta', adabas='ZZI002')
    D = Sector.objects.create(name='ZZ Depto')

    def pessoa(username, setor, cargo, first='ZZIMP', last='', **kw):
        u = User.objects.create_user(username=username, email=f'{username}@exemplo-teste.local', password='S3nha!teste',
                                     first_name=first, last_name=last or username, sector=setor, job_title=cargo, **kw)
        if setor:
            u.sectors.add(setor)
        return u

    for lg in ('89990001', '89990002'):
        assert not User.objects.filter(login_code__startswith=lg).exists(), f'login {lg} já existe no banco'
    chefe = pessoa('zzimp.super', None, 'DIRETOR', first='ZZIMP', last='SUPER', hierarchy='SUPERADMIN')
    gerencia = pessoa('zzimp.gerencia', None, 'ANALISTA', last='GERENCIA')
    ve = pessoa('zzimp.ve', None, 'ANALISTA', last='SO VE')
    comum = pessoa('zzimp.comum', None, 'ANALISTA', last='COMUM')
    AcessoQuadroHC.objects.create(user=gerencia, papel=AcessoQuadroHC.GERENCIAR)
    AcessoQuadroHC.objects.create(user=ve, papel=AcessoQuadroHC.VER)
    u1 = pessoa('zzimp.u1', A, 'CONSULTOR DE VENDAS', last='PRIMEIRO', login_code='89990001')
    u2 = pessoa('zzimp.u2', A, 'CONSULTOR DE VENDAS', last='FULANO DA SILVA', city='VITORIA')
    u3 = pessoa('zzimp.u3', B, 'CONSULTOR DE VENDAS', last='BELTRANO MUDA')
    u4 = pessoa('zzimp.u4', A, 'CONSULTOR DE VENDAS', last='CICLANO CARGO')
    h1 = pessoa('zzimp.h1', A, 'CONSULTOR DE VENDAS', last='HOMONIMO')
    h2 = pessoa('zzimp.h2', B, 'CONSULTOR DE VENDAS', last='HOMONIMO')
    u5 = pessoa('zzimp.u5', A, 'CONSULTOR DE VENDAS', last='AUSENTE')
    g = pessoa('zzimp.g', B, 'CONSULTOR DE VENDAS', last='GERENTE BETA')
    pl1 = pessoa('zzimp.pl1', B, 'CONSULTOR DE VENDAS PL', last='PLENO VOLTA')
    pl2 = pessoa('zzimp.pl2', B, 'CONSULTOR DE VENDAS PL', last='PLENO FICA')
    esc = pessoa('zzimp.esc', A, 'AUXILIAR ZZTESTE', last='ESCRITORIO')
    coord = pessoa('zzimp.coord', None, 'COORDENADOR DE VENDAS', first='ZZCOORD', last='TESTE')
    VagaHC.objects.create(setor=A, cargo='CONSULTOR DE VENDAS', criado_por=chefe)

    ca, cg, cv, cc = Client(), Client(), Client(), Client()
    ca.force_login(chefe)
    cg.force_login(gerencia)
    cv.force_login(ve)
    cc.force_login(comum)
    URL = '/quadro-hc/importar/'

    print('\n1. Acesso')
    t('sem acesso: GET 403', cc.get(URL).status_code == 403)
    t('só ver: GET 403', cv.get(URL).status_code == 403)
    t('só ver: POST 403', cv.post(URL, {'acao': 'ler', 'link': 'x'}).status_code == 403)
    t('gerenciar (AcessoQuadroHC): GET 200', cg.get(URL).status_code == 200)
    t('SUPERADMIN: GET 200', ca.get(URL).status_code == 200)
    t('botão "Importar planilha" para quem gerencia', 'data-importar' in ca.get('/quadro-hc/').content.decode())
    t('sem botão para quem só vê', 'data-importar' not in cv.get('/quadro-hc/').content.decode())

    print('\n2. Leitura do layout (planilha de teste)')
    dados = importacao.ler_planilha(importacao.linhas_csv(PLANILHA.encode('utf-8')), 'teste')
    t('3 blocos (Alfa, Beta, escritório·ZZ DEPTO)', [b['titulo'] for b in dados['blocos']]
      == ['ZZ ALFA - ES', 'ZZ BETA - ES', 'ESCRITÓRIO · ZZ DEPTO'], [b['titulo'] for b in dados['blocos']])
    t('carteira ZZCART / ZZCOORD com os dois blocos de loja',
      dados['carteiras'] and dados['carteiras'][0]['nome'] == 'ZZCART' and dados['carteiras'][0]['coord'] == 'ZZCOORD'
      and dados['carteiras'][0]['blocos'] == [0, 1], dados['carteiras'])
    l0 = dados['blocos'][0]['linhas'][0]
    t('login "89990001.0" → 89990001', l0['login'] == '89990001')
    t('cargo "Consultor(a) " sem espaço sobrando', l0['cargo'] == 'Consultor(a)')
    t('data 5/3/1999 → 1999-03-05', l0['nascimento'] == '1999-03-05')
    t('nome com espaço duplo normalizado', dados['blocos'][0]['linhas'][1]['nome'] == 'ZZIMP FULANO DA SILVA')
    t('login "aguardando" não vira código', dados['blocos'][0]['linhas'][3]['login'] == ''
      and dados['blocos'][0]['linhas'][3]['login_txt'] == 'aguardando')
    vagas = [x for x in dados['blocos'][0]['linhas'] if x['vaga']]
    t('3 vagas no Alfa; a 1ª com obs "REBECCA · VIVÊNCIA 19/11"', len(vagas) == 3 and vagas[0]['vaga_obs'] == 'REBECCA · VIVÊNCIA 19/11',
      vagas)
    t('o "j" solto no topo não vira bloco', all(b['titulo'] != 'j' for b in dados['blocos']))

    print('\n3. Planilha real (aba baixada)')
    if os.path.exists(CSV_REAL):
        real = importacao.ler_planilha(importacao.linhas_csv(open(CSV_REAL, 'rb').read()), 'real')
        lojas = [b for b in real['blocos'] if not b['escritorio']]
        escrit = [b for b in real['blocos'] if b['escritorio']]
        t('40 blocos (25 da operação + 15 setores do escritório)', len(real['blocos']) == 40 and len(lojas) == 25
          and len(escrit) == 15, (len(real['blocos']), len(lojas), len(escrit)))
        t('182 pessoas e 13 linhas de vaga', real['pessoas'] == 182 and real['vagas'] == 13, (real['pessoas'], real['vagas']))
        t('4 coordenações (SERRÃO/JOÃO, VITÓRIA VELHA/PEDRO, INTERIOR RJ/ES/LUIZ, SPC/THAYANDRA)',
          [(c['nome'], c['coord']) for c in real['carteiras']] == [('SERRÃO', 'JOÃO'), ('VITÓRIA VELHA', 'PEDRO'),
                                                                    ('INTERIOR RJ/ES', 'LUIZ'), ('SPC', 'THAYANDRA')],
          [(c['nome'], c['coord']) for c in real['carteiras']])
        t('SNIPERS e MOVIMENTOS ESTRATÉGICOS sem carteira', [b['carteira'] for b in lojas[:2]] == [None, None])
        ns = next(b for b in real['blocos'] if b['titulo'] == 'NORTE SUL - ES')
        t('NORTE SUL: 7 pessoas e 3 vagas', sum(not x['vaga'] for x in ns['linhas']) == 7
          and sum(x['vaga'] for x in ns['linhas']) == 3)
        setores = list(Sector.objects.order_by('name'))
        casadas = {b['titulo']: importacao.casar_setor(b['busca'], setores)[0] for b in lojas[2:]}
        t('as 23 lojas casam com um setor "Loja …"', all(s and s.name.startswith('Loja') for s in casadas.values()),
          {k: (s.name if s else None) for k, s in casadas.items()})
        t('CENTRO DE VITÓRIA → Loja Centro VIX; ITACIBÁ - CARIACICA → Loja Itaciba',
          casadas['CENTRO DE VITÓRIA - ES'].name == 'Loja Centro VIX' and casadas['ITACIBÁ - CARIACICA - ES'].name == 'Loja Itaciba')
        antes = (UserChangeLog.objects.count(), VagaHC.objects.count(), MovimentacaoHC.objects.count(),
                 ObservacaoHC.objects.count(), Carteira.objects.count())
        pr = importacao.montar_previa(real)
        depois = (UserChangeLog.objects.count(), VagaHC.objects.count(), MovimentacaoHC.objects.count(),
                  ObservacaoHC.objects.count(), Carteira.objects.count())
        t('prévia da planilha real não grava nada', antes == depois, (antes, depois))
        print(f'       (prévia real: {pr["totais"]})')
    else:
        print(f'  PULADO: {CSV_REAL} não existe')

    print('\n4. Entrada pela tela (csv, xlsx, link) e prévia sem gravar')
    r = ca.post(URL, {'acao': 'ler', 'arquivo': SimpleUploadedFile('quadro.csv', PLANILHA.encode('utf-8'), 'text/csv')})
    t('ler csv → redireciona para a prévia', r.status_code == 302, r.status_code)
    cont = lambda: (UserChangeLog.objects.count(), VagaHC.objects.count(), MovimentacaoHC.objects.count(),  # noqa: E731
                    ObservacaoHC.objects.count(), Carteira.objects.count())
    antes = cont()
    r = ca.get(URL)
    html = r.content.decode()
    t('prévia abre (200) com os blocos', r.status_code == 200 and 'ZZ ALFA - ES' in html and 'Loja ZZ Alfa' in html)
    t('prévia não gravou nada', cont() == antes, (antes, cont()))
    u1.refresh_from_db()
    t('cadastro intocado depois da prévia', u1.city == '' and u1.birth_date is None)

    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Outra'
    ws2 = wb.create_sheet('Quadro')
    for linha in PLANILHA.strip().split('\n'):
        cel = linha.split(',')
        if cel[4:5] == ['89990001.0']:
            cel[4] = 89990001.0
            cel[5] = datetime(1999, 3, 5)
        ws2.append([c if c != '' else None for c in cel])
    buf = io.BytesIO()
    wb.save(buf)
    dx = importacao.ler_entrada(arquivo=SimpleUploadedFile('quadro.xlsx', buf.getvalue()))
    lx = dx['blocos'][0]['linhas'][0]
    t('xlsx: acha a aba do quadro, login float e data datetime', len(dx['blocos']) == 3 and lx['login'] == '89990001'
      and lx['nascimento'] == '1999-03-05', (len(dx['blocos']), lx['login'], lx['nascimento']))

    link = 'https://docs.google.com/spreadsheets/d/1VynVoBTpp22-Hz-SbG4VHvtekkzwKf05lbdl5rk0310/edit?gid=453600524#gid=453600524'
    t('link → export CSV da aba do gid', importacao.link_de_exportacao(link) ==
      'https://docs.google.com/spreadsheets/d/1VynVoBTpp22-Hz-SbG4VHvtekkzwKf05lbdl5rk0310/export?format=csv&gid=453600524')
    try:
        importacao.link_de_exportacao('https://exemplo-teste.local/spreadsheets/d/abc')
        t('link de outro site é recusado', False)
    except servicos.ErroQuadro:
        t('link de outro site é recusado', True)
    with mock.patch('requests.get', return_value=_Resposta(PLANILHA.encode('utf-8'))) as falso:
        dl = importacao.ler_entrada(link=link)
    t('link: baixa (simulado) só o export do docs.google.com', falso.call_args[0][0].startswith(
        'https://docs.google.com/spreadsheets/d/') and len(dl['blocos']) == 3)
    with mock.patch('requests.get', return_value=_Resposta(b'<!DOCTYPE html><html>login', tipo='text/html')):
        r = ca.post(URL, {'acao': 'ler', 'link': link})
    t('planilha privada (HTML de login) → erro na tela, sem trocar a prévia', r.status_code == 400
      and 'não está pública' in r.content.decode())

    print('\n5. Casamentos e propostas')
    pr = importacao.montar_previa(dados)
    alfa = pr['blocos'][0]
    por_nome = {p['nome']: p for p in alfa['pessoas']}
    p1, p2, p3, p4 = (por_nome['ZZIMP OUTRO NOME'], por_nome['ZZIMP FULANO DA SILVA'], por_nome['ZZIMP BELTRANO MUDA'],
                      por_nome['ZZIMP CICLANO CARGO'])
    t('bloco Alfa casou com Loja ZZ Alfa', alfa['setor'] == A)
    t('pelo login (nome diferente, com aviso)', p1['user'] == u1 and p1['como'] == 'login'
      and any('Casou pelo login' in a for a in p1['avisos']))
    t('pelo nome normalizado', p2['user'] == u2 and p2['como'] == 'nome')
    campos1 = {c['campo']: c for c in p1['campos']}
    t('u1: cidade, bairro, nascimento, contratação, telefone e observação propostos',
      set(campos1) == {'city', 'neighborhood', 'birth_date', 'admission_date', 'phone', 'observacao'}, set(campos1))
    t('"JACARAIPE - SERRA" → bairro JACARAIPE, cidade SERRA',
      campos1['neighborhood']['depois'] == 'JACARAIPE' and campos1['city']['depois'] == 'SERRA')
    campos2 = {c['campo'] for c in p2['campos']}
    t('u2: só o login (cidade vazia na planilha não apaga VITORIA)', campos2 == {'login_code'}, campos2)
    t('u3 (na Beta, listado no Alfa) → movimentação de loja marcada', p3['mov'] and p3['mov']['novo_setor'] == A
      and p3['mov']['marcado'])
    t('u4: cargo desconhecido não vira movimentação e vai para pendência', p4['mov'] is None and any(
        x['tipo'] == 'cargo' and 'ZZIMP CICLANO CARGO' == x['nome'] for x in pr['pendencias']))
    t('u4: telefone segue proposto mesmo com cargo pendente', any(c['campo'] == 'phone' for c in p4['campos']))
    t('u4: login "aguardando" só avisa', any('aguardando' in a for a in p4['avisos']))
    t('não achado → pendência', por_nome['ZZIMP NINGUEM EXISTE']['user'] is None
      and 'Não achei' in por_nome['ZZIMP NINGUEM EXISTE']['pendencia'])
    t('homônimo → pendência ambígua', por_nome['ZZIMP HOMONIMO']['user'] is None
      and 'mais de um' in por_nome['ZZIMP HOMONIMO']['pendencia'])
    vg = alfa['vagas']
    t('vagas: admin abre; 1 consultor já aberta, 1 consultor abre',
      [bool(v['chave']) for v in vg] == [True, False, True] and vg[0]['cargo'] == 'AUXILIAR ADMINISTRATIVO', vg)
    t('ausente do Alfa (u5) só como aviso; homônimo citado não entra', [a['nome'] for a in alfa['ausentes']]
      == ['ZZIMP AUSENTE'], alfa['ausentes'])
    beta = pr['blocos'][1]
    pg = beta['pessoas'][0]
    pv, pf = beta['pessoas'][1], beta['pessoas'][2]
    t('"Consultor(a)" × CONSULTOR DE VENDAS PL → movimentação de cargo (o grupo muda)',
      pv['user'] == pl1 and pv['mov'] and pv['mov']['novo_cargo'] and
      servicos.grupo_do_cargo(pv['mov']['novo_cargo'])[1] == 'Consultor', pv['mov'])
    t('"Consultor(a) Pleno" × CONSULTOR DE VENDAS PL → nada muda', pf['user'] == pl2 and pf['mov'] is None, pf['mov'])
    t('Gerente Geral × CONSULTOR → movimentação de cargo para o cargo de gerente do cadastro',
      pg['mov'] and pg['mov']['novo_setor'] is None and servicos.grupo_do_cargo(pg['mov']['novo_cargo'])[1] == 'Gerente',
      pg['mov'])
    pe = pr['blocos'][2]['pessoas'][0]
    t('escritório: setor ZZ DEPTO casou e a movimentação vem desmarcada', pr['blocos'][2]['setor'] == D and pe['mov']
      and not pe['mov']['marcado'])
    ct = pr['carteiras'][0]
    t('carteira nova ZZCART com coordenador ligado e as duas lojas', ct['chave'] and ct['existente'] is None
      and ct['coord_user'] == coord and {s.id for s in ct['lojas']} == {A.id, B.id})
    sem = importacao.montar_previa(dados, {'0': '0'})
    t('escolher "sem loja" no bloco: sem movimentação nem vaga', all(p['mov'] is None or p['mov']['novo_setor'] is None
      for p in sem['blocos'][0]['pessoas']) and not any(v['chave'] for v in sem['blocos'][0]['vagas']))

    print('\n6. Aplicar só o marcado')
    ca.post(URL, {'acao': 'ler', 'arquivo': SimpleUploadedFile('quadro.csv', PLANILHA.encode('utf-8'), 'text/csv')})
    token = ca.session['quadro_hc_importacao']['token']
    escolher = [campos1['city']['chave'], campos1['neighborhood']['chave'], campos1['phone']['chave'],
                next(c['chave'] for c in p2['campos'] if c['campo'] == 'login_code'), p3['mov']['chave'],
                vg[0]['chave'], vg[2]['chave'], ct['chave']]
    logs_antes = UserChangeLog.objects.count()
    vagas_antes = VagaHC.objects.filter(setor=A, fechada_em__isnull=True).count()
    r = ca.post(URL, {'acao': 'aplicar', 'token': token, 'item': escolher})
    html = r.content.decode()
    t('aplicar → 200 com resumo', r.status_code == 200 and 'Importação aplicada' in html, r.status_code)
    for u in (u1, u2, u3, u4, g, esc):
        u.refresh_from_db()
    t('u1: cidade/bairro/telefone gravados', (u1.city, u1.neighborhood, u1.phone) == ('SERRA', 'JACARAIPE', '(27) 99999-0001'),
      (u1.city, u1.neighborhood, u1.phone))
    t('u1: nascimento/contratação/observação desmarcados não gravaram',
      u1.birth_date is None and u1.admission_date is None and not ObservacaoHC.objects.filter(colaborador=u1).exists())
    log = UserChangeLog.objects.filter(target=u1, field='city').first()
    t('UserChangeLog da cidade, por quem aplicou', log is not None and log.changed_by_id == chefe.id
      and log.old_value == '—' and log.new_value == 'SERRA')
    t('u2: login gravado com log', u2.login_code == '89990002'
      and UserChangeLog.objects.filter(target=u2, field='login_code', new_value='89990002').exists())
    t('UserChangeLog: só os campos marcados (3 de u1 + 1 de u2 + movimentação)',
      UserChangeLog.objects.count() - logs_antes >= 4
      and not UserChangeLog.objects.filter(target=u1, field__in=['birth_date', 'admission_date']).exists())
    m = MovimentacaoHC.objects.filter(colaborador=u3).first()
    t('u3: mudança de loja virou movimentação aplicada hoje', m is not None and m.situacao == MovimentacaoHC.APLICADA
      and m.novo_setor_id == A.id and m.data == timezone.localdate() and u3.sector_id == A.id and A in u3.sectors.all(),
      (m and m.situacao, u3.sector_id))
    t('u3: a loja mudou pelo fluxo (log de setor junto)',
      UserChangeLog.objects.filter(target=u3, field='sector', new_value='Loja ZZ Alfa').exists())
    t('g: movimentação de cargo desmarcada não aplicou', g.job_title == 'CONSULTOR DE VENDAS'
      and not MovimentacaoHC.objects.filter(colaborador=g).exists())
    t('esc: movimentação do escritório (desmarcada) não aplicou', esc.sector_id == A.id)
    t('u4: cargo pendente não mudou', u4.job_title == 'CONSULTOR DE VENDAS')
    t('vagas: +2 no Alfa (admin + 1 consultor)',
      VagaHC.objects.filter(setor=A, fechada_em__isnull=True).count() == vagas_antes + 2)
    cart = Carteira.objects.filter(nome='ZZCART').first()
    t('carteira ZZCART criada pelo salvar_carteira, com coordenador e lojas',
      cart is not None and cart.coordenador_id == coord.id and set(cart.lojas.values_list('id', flat=True)) == {A.id, B.id})
    t('resumo lista pendências', 'ZZIMP NINGUEM EXISTE' in html and 'não reconhecido' in html)
    t('a prévia sai da sessão depois de aplicar', 'quadro_hc_importacao' not in ca.session)
    r = ca.post(URL, {'acao': 'aplicar', 'token': token, 'item': escolher})
    t('reenviar o mesmo token não aplica de novo', r.status_code == 302
      and VagaHC.objects.filter(setor=A, fechada_em__isnull=True).count() == vagas_antes + 2)

    print('\n7. Ler de novo: o que já foi aplicado some da prévia')
    pr2 = importacao.montar_previa(dados)
    alfa2 = pr2['blocos'][0]
    t('vagas não duplicam: nenhuma vaga a abrir no Alfa', not any(v['chave'] for v in alfa2['vagas']),
      [(v['cargo'], v['situacao']) for v in alfa2['vagas']])
    p1b = next(p for p in alfa2['pessoas'] if p['user'] == u1)
    t('u1: cidade/bairro/telefone não aparecem mais', not {'city', 'neighborhood', 'phone'} & {c['campo'] for c in p1b['campos']})
    p3b = next(p for p in alfa2['pessoas'] if p['user'] == u3)
    t('u3: sem movimentação (já está no Alfa)', p3b['mov'] is None)
    t('carteira já está assim', not pr2['carteiras'][0]['chave'])
    t('nenhum aviso/notificação saiu', not avisos_enviados, avisos_enviados)

    print('\n8. Carteira pela tela continua igual (view → servicos.salvar_carteira)')
    import json
    r = ca.post('/quadro-hc/carteira/', json.dumps({'id': cart.id, 'nome': 'ZZCART 2', 'coordenador_nome': 'X',
                                                   'lojas': [A.id]}), content_type='application/json')
    cart.refresh_from_db()
    t('carteira_salvar salva', r.status_code == 200 and cart.nome == 'ZZCART 2'
      and list(cart.lojas.values_list('id', flat=True)) == [A.id] and cart.coordenador_id is None, r.content[:200])
finally:
    try:
        dublê.stop()
        if dublê2:
            dublê2.stop()
    except Exception:  # noqa: BLE001
        pass
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
