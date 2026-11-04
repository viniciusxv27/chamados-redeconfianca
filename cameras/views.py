import json
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Q
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from users.models import Sector

from . import servicos
from .importacao import importar_planilha
from .indicadores import indicadores_do_periodo
from .models import AcessoCamera, AlertaCamera, Canal, ConfigCameras, Gravador
from .permissoes import exige_superadmin


def _registrar(request, acao, gravador=None, detalhe=''):
    AcessoCamera.objects.create(user=request.user, acao=acao, gravador=gravador, detalhe=detalhe[:160])


def _alerta_json(a):
    return {'id': a.id, 'loja': a.gravador.loja, 'canal': a.canal.titulo if a.canal else '', 'tipo': a.get_tipo_display(),
            'confianca': a.confianca, 'quando': timezone.localtime(a.criado_em).strftime('%d/%m %H:%M')}


@exige_superadmin
def painel(request):
    gravadores = list(Gravador.objects.prefetch_related('canais')
                      .annotate(novos=Count('alertas', filter=Q(alertas__status=AlertaCamera.Status.NOVO))))
    for g in gravadores:
        g.canais_ativos = [c for c in g.canais.all() if c.ativo]
        g.principal = g.canais_ativos[0] if g.canais_ativos else None
    com_equipamento = [g for g in gravadores if g.tem_equipamento]
    sem_contato = [g for g in com_equipamento if not g.ultimo_contato]
    servicos.marcar_vendo_mosaico()
    _registrar(request, 'ver_mosaico')
    return render(request, 'cameras/painel.html', {
        'aba': 'painel', 'gravadores': gravadores, 'config': ConfigCameras.atual(),
        'sem_contato': len(sem_contato),
        'total_lojas': len(com_equipamento), 'online': sum(g.online for g in com_equipamento),
        'total_cameras': sum(len(g.canais_ativos) for g in com_equipamento),
        'novos': sum(g.novos for g in gravadores),
        'recentes': AlertaCamera.objects.select_related('gravador', 'canal').filter(status__in=['NOVO', 'ANALISE'])[:8],
    })


@exige_superadmin
def estado(request):
    """Pedido leve das telas abertas: situação das lojas e alertas novos."""
    novos = AlertaCamera.objects.filter(status=AlertaCamera.Status.NOVO).select_related('gravador', 'canal')
    return JsonResponse({
        'novos': novos.count(), 'ultimo': _alerta_json(novos[0]) if novos[:1] else None,
        'lojas': {g.id: g.situacao for g in Gravador.objects.all()},
    })


@exige_superadmin
def fotos(request):
    """As fotos em um pedido só. Marca quem está olhando: sem ninguém, a loja não manda imagem."""
    try:
        ids = [int(i) for i in request.GET.get('canais', '').split(',') if i][:64]
        desde = float(request.GET.get('desde') or 0)
        foco = int(request.GET.get('foco') or 0)
        loja = int(request.GET.get('loja') or 0)
    except ValueError:
        return JsonResponse({'erro': 'Parâmetro inválido.'}, status=400)
    if loja:
        servicos.marcar_vendo_loja(loja, foco or None)
    else:
        servicos.marcar_vendo_mosaico()
    return JsonResponse({'fotos': servicos.fotos_dos_canais(ids, desde)})


@exige_superadmin
def loja(request, gravador_id):
    gravador = get_object_or_404(Gravador, pk=gravador_id)
    canais = list(gravador.canais.filter(ativo=True))
    servicos.marcar_vendo_loja(gravador.id)
    _registrar(request, 'ver_loja', gravador)
    return render(request, 'cameras/loja.html', {
        'aba': 'painel', 'gravador': gravador, 'canais': canais, 'config': ConfigCameras.atual(),
        'zonas_json': {c.id: c.zonas for c in canais},
        'alertas': gravador.alertas.select_related('canal')[:10],
    })


TIPOS_DE_ZONA = {'MOSTRUARIO': 'Mostruário', 'PORTA': 'Porta', 'MESA': 'Mesa', 'TELA': 'Tela'}
SENTIDOS = ('direita', 'esquerda', 'cima', 'baixo')


def _zonas_validas(bruto):
    zonas, nomes = [], set()
    for z in bruto if isinstance(bruto, list) else []:
        try:
            x, y, w, h = (round(max(0.0, min(1.0, float(z[k]))), 4) for k in 'xywh')
        except (KeyError, TypeError, ValueError):
            continue
        if w <= 0.01 or h <= 0.01:
            continue
        tipo = z.get('tipo') if z.get('tipo') in TIPOS_DE_ZONA else 'MOSTRUARIO'
        base = str(z.get('nome') or TIPOS_DE_ZONA[tipo])[:36]
        # O agente mede por nome: duas mesas com o mesmo nome virariam uma só.
        nome, n = base, 1
        while nome in nomes:
            n += 1
            nome = f'{base} {n}'
        nomes.add(nome)
        zona = {'nome': nome, 'tipo': tipo, 'x': x, 'y': y, 'w': w, 'h': h}
        if tipo == 'PORTA':
            zona['sentido'] = z.get('sentido') if z.get('sentido') in SENTIDOS else 'baixo'
        zonas.append(zona)
    return zonas[:12]


@exige_superadmin
@require_POST
def canal_salvar(request, canal_id):
    canal = get_object_or_404(Canal.objects.select_related('gravador'), pk=canal_id)
    try:
        corpo = json.loads(request.body or '{}')
    except ValueError:
        return JsonResponse({'erro': 'Dados inválidos.'}, status=400)
    if 'zonas' in corpo:
        canal.zonas = _zonas_validas(corpo['zonas'])
    if 'nome' in corpo:
        canal.nome = str(corpo['nome'] or '').strip()[:80]
    if 'ia_ativa' in corpo:
        canal.ia_ativa = bool(corpo['ia_ativa'])
    canal.save()
    return JsonResponse({'ok': True, 'zonas': canal.zonas, 'titulo': canal.titulo})


def _data_do_get(request, campo, padrao):
    try:
        return datetime.strptime(request.GET.get(campo) or '', '%Y-%m-%d').date()
    except ValueError:
        return padrao


@exige_superadmin
def indicadores(request):
    hoje = timezone.localdate()
    de = _data_do_get(request, 'de', hoje)
    ate = max(_data_do_get(request, 'ate', de), de)
    ate = min(ate, de + timedelta(days=92))
    loja_id = request.GET.get('loja', '')
    dados = indicadores_do_periodo(de, ate, int(loja_id) if loja_id.isdigit() else None)
    _registrar(request, 'ver_indicadores')
    return render(request, 'cameras/indicadores.html', {
        'aba': 'indicadores', 'de': de, 'ate': ate, 'loja_id': loja_id, 'um_dia': de == ate, 'hoje': hoje,
        'lojas': Gravador.objects.filter(tem_equipamento=True), **dados,
    })


@exige_superadmin
def alertas(request):
    status = request.GET.get('status', 'abertos')
    lista = AlertaCamera.objects.select_related('gravador', 'canal', 'tratado_por')
    if status == 'abertos':
        lista = lista.filter(status__in=['NOVO', 'ANALISE'])
    elif status in AlertaCamera.Status.values:
        lista = lista.filter(status=status)
    loja_id = request.GET.get('loja', '')
    if loja_id.isdigit():
        lista = lista.filter(gravador_id=int(loja_id))
    return render(request, 'cameras/alertas.html', {
        'aba': 'alertas', 'alertas': lista[:200], 'status': status, 'loja_id': loja_id,
        'status_opcoes': AlertaCamera.Status.choices, 'lojas': Gravador.objects.filter(tem_equipamento=True),
    })


@exige_superadmin
def alerta_detalhe(request, alerta_id):
    alerta = get_object_or_404(AlertaCamera.objects.select_related('gravador', 'canal', 'tratado_por'), pk=alerta_id)
    if request.method == 'POST':
        novo = request.POST.get('status')
        if novo in AlertaCamera.Status.values:
            alerta.status = novo
            alerta.observacao = request.POST.get('observacao', '').strip()
            alerta.tratado_por, alerta.tratado_em = request.user, timezone.now()
            alerta.save(update_fields=['status', 'observacao', 'tratado_por', 'tratado_em'])
            _registrar(request, 'tratar_alerta', alerta.gravador, f'#{alerta.id} {novo}')
            messages.success(request, f'Alerta marcado como "{alerta.get_status_display()}".')
        return redirect('cameras:alertas')
    _registrar(request, 'ver_alerta', alerta.gravador, f'#{alerta.id}')
    return render(request, 'cameras/alerta_detalhe.html', {'aba': 'alertas', 'alerta': alerta, 'status_opcoes': AlertaCamera.Status.choices})


@exige_superadmin
def alerta_foto(request, alerta_id):
    alerta = get_object_or_404(AlertaCamera, pk=alerta_id)
    if not alerta.foto:
        raise Http404
    resposta = FileResponse(alerta.foto.open('rb'), content_type='image/jpeg')
    resposta['Cache-Control'] = 'private, max-age=600'
    return resposta


@exige_superadmin
def configuracao(request):
    config = ConfigCameras.atual()
    if request.method == 'POST':
        config.ativo = request.POST.get('ativo') == '1'
        config.alterado_por = request.user
        config.save()
        _registrar(request, 'ligar' if config.ativo else 'desligar')
        messages.success(request, 'Módulo ligado: as lojas com agente começam a mandar imagem.' if config.ativo
                         else 'Módulo desligado: as lojas pararam de mandar imagem e alerta.')
        return redirect('cameras:configuracao')
    return render(request, 'cameras/configuracao.html', {
        'aba': 'configuracao', 'config': config,
        'gravadores': Gravador.objects.select_related('setor').annotate(canais_ativos_qtd=Count('canais', filter=Q(canais__ativo=True))),
        'token_novo': request.session.pop('cameras_token_novo', None),
        'acessos': AcessoCamera.objects.select_related('user', 'gravador')[:30],
        'endereco': request.build_absolute_uri('/').rstrip('/'),
    })


@exige_superadmin
@require_POST
def importar(request):
    arquivo = request.FILES.get('planilha')
    if not arquivo:
        messages.error(request, 'Escolha a planilha.')
        return redirect('cameras:configuracao')
    try:
        r = importar_planilha(arquivo)
    except Exception as erro:  # noqa: BLE001 — arquivo que não é xlsx, por exemplo
        messages.error(request, f'Não consegui ler a planilha: {erro}')
        return redirect('cameras:configuracao')
    _registrar(request, 'importar', detalhe=f"{r['criados']} novas, {r['atualizados']} atualizadas")
    texto = f"{r['criados']} loja(s) nova(s) e {r['atualizados']} atualizada(s)."
    if r['sem_equipamento']:
        texto += ' Sem gravador: ' + ', '.join(r['sem_equipamento']) + '.'
    if r['sem_setor']:
        texto += ' Sem setor do portal: ' + ', '.join(r['sem_setor']) + '.'
    messages.success(request, texto)
    return redirect('cameras:configuracao')


@exige_superadmin
def gravador_editar(request, gravador_id):
    gravador = get_object_or_404(Gravador, pk=gravador_id)
    if request.method == 'POST':
        for campo in ('loja', 'coordenador', 'serial', 'usuario', 'ip_local', 'caminho_rtsp'):
            setattr(gravador, campo, request.POST.get(campo, '').strip())
        gravador.loja = gravador.loja or 'Sem nome'
        gravador.caminho_rtsp = gravador.caminho_rtsp or Gravador._meta.get_field('caminho_rtsp').default
        gravador.tem_equipamento = request.POST.get('tem_equipamento') == '1'
        setor = request.POST.get('setor', '')
        gravador.setor_id = int(setor) if setor.isdigit() else None
        try:
            gravador.porta_rtsp = int(request.POST.get('porta_rtsp') or 554)
            quantos = max(1, min(64, int(request.POST.get('canais') or 1)))
        except ValueError:
            messages.error(request, 'Porta e quantidade de câmeras precisam ser números.')
            return redirect('cameras:gravador_editar', gravador.id)
        gravador.abre_as = request.POST.get('abre_as') or gravador.abre_as
        gravador.fecha_as = request.POST.get('fecha_as') or gravador.fecha_as
        gravador.matinal_inicio = request.POST.get('matinal_inicio') or gravador.matinal_inicio
        gravador.matinal_fim = request.POST.get('matinal_fim') or gravador.matinal_fim
        try:
            gravador.matinal_minutos = max(1, min(120, int(request.POST.get('matinal_minutos') or gravador.matinal_minutos)))
        except ValueError:
            pass
        # Em branco mantém a senha atual: ela nunca volta para a tela.
        if request.POST.get('senha'):
            gravador.definir_senha(request.POST['senha'])
        gravador.save()
        for numero in range(1, quantos + 1):
            Canal.objects.get_or_create(gravador=gravador, numero=numero)
        gravador.canais.filter(numero__gt=quantos).update(ativo=False)
        gravador.canais.filter(numero__lte=quantos).update(ativo=True)
        _registrar(request, 'editar_gravador', gravador)
        messages.success(request, f'{gravador.loja} salva.')
        return redirect('cameras:configuracao')
    return render(request, 'cameras/gravador_form.html', {
        'aba': 'configuracao', 'gravador': gravador, 'setores': Sector.objects.order_by('name'),
        'quantos': gravador.canais.filter(ativo=True).count() or 1,
    })


@exige_superadmin
@require_POST
def gravador_instalador(request, gravador_id):
    """O mesmo .exe para todas as lojas: o que muda é o código no nome do arquivo baixado."""
    gravador = get_object_or_404(Gravador, pk=gravador_id, tem_equipamento=True)
    arquivo = ConfigCameras.atual().instalador_arquivo
    if not arquivo:
        messages.error(request, 'O instalador ainda não foi publicado neste portal.')
        return redirect('cameras:configuracao')
    nome = servicos.nome_do_instalador(gravador, gravador.gerar_codigo())
    _registrar(request, 'baixar_instalador', gravador)
    if settings.STORAGES['default']['BACKEND'].endswith('FileSystemStorage'):
        return FileResponse(default_storage.open(arquivo, 'rb'), as_attachment=True, filename=nome)
    return redirect(servicos.link_do_instalador(arquivo, nome))


@exige_superadmin
@require_POST
def gravador_token(request, gravador_id):
    gravador = get_object_or_404(Gravador, pk=gravador_id)
    request.session['cameras_token_novo'] = {'loja': gravador.loja, 'token': gravador.gerar_token()}
    _registrar(request, 'gerar_token', gravador)
    return redirect('cameras:configuracao')
