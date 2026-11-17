"""Módulo Câmeras no app: lojas e câmeras, alertas, indicadores e a última imagem.

Regras de onde vieram (cameras/):
- Entrada: toda view web de /cameras/ tem ``@exige_superadmin`` (cameras/permissoes.py:12), que
  chama ``e_superadmin`` (cameras/permissoes.py:7: is_superuser ou hierarquia SUPERADMIN). O item
  do menu sai do mesmo teste (cameras/context_processors.py:7 → ``cameras_liberado`` em
  templates/base.html:866). Não há liberação individual nem grupo além disso. O switch
  ``ConfigCameras.ativo`` não fecha as telas na web (só o pedido de vídeo ao vivo,
  cameras/views.py:156), então aqui também não fecha: o app só mostra o aviso.
- Recorte: o SUPERADMIN vê todas as lojas (a web não recorta por loja); id que não existe → 404.
- Registro de acesso (LGPD): as mesmas ações da web (``ver_mosaico``, ``ver_loja``, ``ver_alerta``,
  ``ver_indicadores``, ``tratar_alerta``, ``ver_video``) entram em ``AcessoCamera`` com "pelo app".
- Última imagem: igual à web (cameras/views.py:45 e :68), abrir a tela marca "alguém olhando" e o
  agente passa a mandar foto no próximo sinal dele. Isso NÃO é comando ao agente: o portal não chama
  a loja, só responde o sinal que o próprio agente manda (servicos.resposta_do_sinal).
- Tratar alerta: a mesma regra da web (cameras/views.py:306-321): falso alarme exige o motivo.

Fica de fora (no portal): vídeo ao vivo (WebRTC: o app abre a página da loja no navegador),
tratar/apagar em lote, pedir nova análise da IA, marcar áreas, configuração, instalador, token,
importar planilha, CSV e o quadro do aprendizado. Nenhum endpoint daqui fala com a loja.
"""
import base64
import io
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404
from django.urls import path
from django.utils import timezone
from django.views.decorators.http import require_GET
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from cameras import aprendizado, ia, ponto, servicos
from cameras import views as web
from cameras.indicadores import indicadores_do_periodo
from cameras.models import AlertaCamera, Canal, ConfigCameras, Gravador
from cameras.permissoes import e_superadmin

from .. import dados
from ..arquivos import link, registrar
from ..autenticacao import TokenMobileAuthentication
from . import permissao


def pode_entrar(user):
    # cameras/permissoes.py:7 (e_superadmin) — o mesmo teste do @exige_superadmin de todas as views web.
    return bool(user and user.is_active and e_superadmin(user))


MODULO = {'rotulo': 'Câmeras', 'descricao': 'Lojas, alertas e indicadores', 'icone': 'videocam-outline',
          'cor': '#E74C3C', 'pode_entrar': pode_entrar}

_PERM = [permissao('cameras')]
_AUTH = [TokenMobileAuthentication]
POR_PAGINA = 30

SITUACAO = {'online': 'Agente online', 'offline': 'Agente sem contato', 'sem_agente': 'Agente não instalado',
            'sem_equipamento': 'Sem gravador'}


def _registrar(request, acao, gravador=None, detalhe=''):
    """O mesmo registro da web (cameras/views.py:25), marcando que veio do app."""
    web._registrar(request, acao, gravador, f'{detalhe} pelo app'.strip())


# ── Formato das respostas ────────────────────────────────────────────────────

def _quando_foto(epoca):
    return dados.iso(datetime.fromtimestamp(epoca, tz=timezone.get_current_timezone())) if epoca else None


def _problemas(g):
    return [{'nivel': nivel, 'texto': texto} for nivel, texto in servicos.problemas_de_saude(g)] if g.online else []


def _loja_resumo(request, g, novos=0, fotos=None):
    ativos = [c for c in g.canais.all() if c.ativo]
    principal = ativos[0] if ativos else None
    foto = (fotos or {}).get(principal.id) if principal else None
    return {
        'id': g.pk,
        'loja': g.loja,
        'coordenador': g.coordenador or None,
        'situacao': g.situacao,
        'situacao_rotulo': SITUACAO.get(g.situacao, g.situacao),
        'online': g.online,
        'tem_equipamento': g.tem_equipamento,
        'ultimo_contato': dados.iso(g.ultimo_contato),
        'versao_agente': g.versao_agente or None,
        'erro_agente': g.erro_agente or None,
        'cameras': len(ativos),
        'alertas_novos': novos,
        'problemas': _problemas(g),
        'foto': link(request, 'cameras_foto', principal.id) if foto else None,
        'foto_em': _quando_foto(foto['em']) if foto else None,
    }


def _alerta_resumo(request, a):
    return {
        'id': a.pk,
        'loja': {'id': a.gravador_id, 'nome': a.gravador.loja},
        'canal': a.canal.titulo if a.canal else None,
        'tipo': a.tipo,
        'tipo_rotulo': a.get_tipo_display(),
        'status': a.status,
        'status_rotulo': a.get_status_display(),
        'confianca': a.confianca,
        'prioridade': a.prioridade,
        'chance_real': a.chance_real,
        'repeticoes': a.repeticoes,
        'motivo': a.motivo or None,
        'criado_em': dados.iso(a.criado_em),
        'ultimo_em': dados.iso(a.ultimo_em),
        'video_status': a.video_status,
        'video_rotulo': a.get_video_status_display(),
        'ia_status': a.ia_status or None,
        'ia_risco': a.ia_risco,
        'ia_classificacao': ia.CLASSIFICACOES.get(a.ia_classificacao) or None,
        'foto': link(request, 'cameras_alerta_foto', a.pk) if a.foto else None,
    }


def _lista_texto(valor):
    return [str(x) for x in valor] if isinstance(valor, list) else []


def _momentos(valor):
    saida = []
    for m in valor if isinstance(valor, list) else []:
        if isinstance(m, dict):
            try:
                segundo = int(m.get('segundo') or 0)
            except (TypeError, ValueError):
                segundo = 0
            saida.append({'segundo': segundo, 'o_que': str(m.get('o_que') or '')})
    return saida


def _alerta_detalhe(request, a):
    base = _alerta_resumo(request, a)
    analise = a.ia_analise if isinstance(a.ia_analise, dict) else {}
    historico = aprendizado.avaliar(a.tipo, a.gravador_id, a.canal_id)
    tem_video = bool(a.video) and a.video_status == AlertaCamera.Video.RECEBIDO
    base.update({
        'observacao': a.observacao or None,
        'motivo_falso': a.motivo_falso or None,
        'motivo_falso_rotulo': a.get_motivo_falso_display() if a.motivo_falso else None,
        'tratado_por': dados.pessoa(request, a.tratado_por),
        'tratado_em': dados.iso(a.tratado_em),
        'video': _link_video(request, a) if tem_video else None,
        'video_segundos': a.video_segundos,
        'video_evento_segundo': a.video_evento_segundo,
        'video_tamanho': a.video_tamanho,
        # cameras/views.py:333 — o segundo de cada quadro no vídeo.
        'quadros': [{'indice': i, 'segundo': q.get('segundo', 0),
                     'no_video': max(0, a.video_evento_segundo + q.get('segundo', 0)),
                     'imagem': link(request, 'cameras_quadro', f'{a.pk}:{i}')}
                    for i, q in enumerate(a.quadros or []) if isinstance(q, dict)],
        'medidas': [{'rotulo': r, 'valor': str(v)} for r, v in web._medidas(a.detalhes)],
        'historico': {'chance': historico.get('chance'), 'decisoes_canal': historico.get('decisoes_canal', 0),
                      'decisoes_loja': historico.get('decisoes_loja', 0)},
        'motivos_camera': [{'rotulo': r, 'quantos': n} for r, n in aprendizado.motivos_de_falso(a.tipo, a.canal_id)[:3]],
        'rigor': aprendizado.ROTULO_RIGOR[aprendizado.rigor(a.tipo, a.gravador_id, a.canal_id)],
        'ia': {
            'status': a.ia_status or None,
            'status_rotulo': a.get_ia_status_display() if a.ia_status else None,
            'risco': a.ia_risco,
            'classificacao': ia.CLASSIFICACOES.get(a.ia_classificacao) or None,
            'resumo': a.ia_resumo or None,
            'linha_do_tempo': _momentos(analise.get('linha_do_tempo')),
            'sinais_de_risco': _lista_texto(analise.get('sinais_de_risco')),
            'sinais_normais': _lista_texto(analise.get('sinais_normais')),
            'conferir_no_video': _momentos(analise.get('conferir_no_video')),
            'recomendacao': str(analise.get('recomendacao') or '') or None,
            'modelo': a.ia_modelo or None,
            'em': dados.iso(a.ia_em),
            'erro': a.ia_erro or None,
        },
        'equipe': [{'nome': e.get('nome'), 'horarios': list(e.get('horarios') or []), 'como': e.get('como')}
                   for e in ponto.equipe_no_momento(a.gravador, a.criado_em)],
        'na_tolerancia': a.tipo == 'FORA_HORARIO' and a.gravador.na_tolerancia(a.criado_em),
        'caminho_web': f'/cameras/alertas/{a.pk}/',
    })
    return base


def _opcoes(choices):
    return [{'valor': v, 'rotulo': r} for v, r in choices]


def _gravador(pk):
    g = Gravador.objects.prefetch_related('canais').filter(pk=pk).first()
    if g is None:
        raise Http404('Loja não encontrada.')
    return g


def _alerta(pk):
    a = AlertaCamera.objects.select_related('gravador', 'canal', 'tratado_por').filter(pk=pk).first()
    if a is None:
        raise Http404('Alerta não encontrado.')
    return a


# ── Endpoints ────────────────────────────────────────────────────────────────

@api_view(['GET'])
@authentication_classes(_AUTH)
@permission_classes(_PERM)
def inicio(request):
    """O mosaico da web (cameras/views.py:35): lojas com situação, câmeras, alertas novos e a foto principal."""
    gravadores = list(Gravador.objects.prefetch_related('canais')
                      .annotate(novos=Count('alertas', filter=Q(alertas__status=AlertaCamera.Status.NOVO))))
    principais = [c.id for c in (next((c for c in g.canais.all() if c.ativo), None) for g in gravadores) if c]
    fotos = servicos.fotos_dos_canais(principais)
    com_equipamento = [g for g in gravadores if g.tem_equipamento]
    servicos.marcar_vendo_mosaico()
    _registrar(request, 'ver_mosaico')
    lojas = [_loja_resumo(request, g, g.novos, fotos) for g in gravadores]
    recentes = (AlertaCamera.objects.select_related('gravador', 'canal')
                .filter(status__in=['NOVO', 'ANALISE']).order_by('-criado_em')[:8])
    return Response({
        'ativo': ConfigCameras.atual().ativo,
        'total_lojas': len(com_equipamento),
        'online': sum(g.online for g in com_equipamento),
        'sem_agente': sum(1 for g in com_equipamento if not g.ultimo_contato),
        'total_cameras': sum(l['cameras'] for l, g in zip(lojas, gravadores) if g.tem_equipamento),
        'novos': sum(g.novos for g in gravadores),
        'com_problema': sum(1 for l, g in zip(lojas, gravadores) if g.tem_equipamento and l['problemas']),
        'lojas': lojas,
        'recentes': [_alerta_resumo(request, a) for a in recentes],
        'caminho_web': '/cameras/',
    })


@api_view(['GET'])
@authentication_classes(_AUTH)
@permission_classes(_PERM)
def loja(request, pk):
    """A tela da loja (cameras/views.py:85): câmeras com a última imagem, saúde do agente e últimos alertas."""
    g = _gravador(pk)
    canais = [c for c in g.canais.all() if c.ativo]
    saude = g.saude_atual
    saude_cameras = saude.get('cameras') if isinstance(saude.get('cameras'), dict) else {}
    fotos = servicos.fotos_dos_canais([c.id for c in canais])
    servicos.marcar_vendo_loja(g.id)
    _registrar(request, 'ver_loja', g)
    config = ConfigCameras.atual()

    def camera(c):
        s = saude_cameras.get(str(c.numero)) if isinstance(saude_cameras.get(str(c.numero)), dict) else None
        foto = fotos.get(c.id)
        return {
            'id': c.id, 'numero': c.numero, 'titulo': c.titulo, 'analisando': c.ia_ativa,
            'areas': len(c.zonas or []),
            'imagem_ok': None if s is None else bool(s.get('ok')),
            'quadros_por_segundo': s.get('fps') if s else None,
            'erro': (str(s.get('erro')) if s and s.get('erro') else None),
            'foto': link(request, 'cameras_foto', c.id) if foto else None,
            'foto_em': _quando_foto(foto['em']) if foto else None,
        }

    medidas_saude = [
        (rotulo, valor) for rotulo, valor in (
            ('Análise', saude.get('detector')),
            ('Processador', f"{saude['cpu']}%" if saude.get('cpu') is not None else None),
            ('Memória do agente', f"{saude['memoria_mb']} MB" if saude.get('memoria_mb') else None),
            ('Disco livre', f"{saude['disco_livre_gb']} GB" if saude.get('disco_livre_gb') is not None else None),
            ('Medições esperando', saude.get('fila_medicoes')),
            ('Vídeos esperando', saude.get('fila_clipes')),
            ('Agente ligado há', f"{saude['ligado_ha_min']} min" if saude.get('ligado_ha_min') is not None else None),
            ('Windows', saude.get('windows')),
        ) if valor not in (None, '')
    ]  # templates/cameras/loja.html:14-22
    alertas = g.alertas.select_related('gravador', 'canal')[:10]
    novos = g.alertas.filter(status=AlertaCamera.Status.NOVO).count()
    resumo = _loja_resumo(request, g, novos, fotos)
    resumo.update({
        'abre_as': g.abre_as.strftime('%H:%M'),
        'fecha_as': g.fecha_as.strftime('%H:%M'),
        'saude_em': dados.iso(g.saude_em) if saude else None,
        'saude': [{'rotulo': r, 'valor': str(v)} for r, v in medidas_saude],
        'cameras_lista': [camera(c) for c in canais],
        'alertas': [_alerta_resumo(request, a) for a in alertas],
        # Vídeo ao vivo só pelo navegador (WebRTC): a mesma condição da web para tentar (servicos.abrir_sessoes).
        'ao_vivo': {'disponivel': bool(config.ativo and g.online and g.tem_ao_vivo),
                    'motivo': (None if config.ativo and g.online and g.tem_ao_vivo else
                               'Módulo desligado.' if not config.ativo else
                               'Agente da loja sem contato.' if not g.online else
                               'Reinstale o agente desta loja para ter vídeo ao vivo.')},
        'caminho_web': f'/cameras/loja/{g.pk}/',
    })
    return Response(resumo)


@api_view(['GET'])
@authentication_classes(_AUTH)
@permission_classes(_PERM)
def alertas(request):
    """A lista da web (cameras/views.py:247) com o mesmo filtro (_alertas_do_filtro): situação, loja, tipo e período."""
    lista, filtros = web._alertas_do_filtro(request.query_params)
    pagina = Paginator(lista, POR_PAGINA).get_page(request.query_params.get('pagina'))
    abertos = AlertaCamera.objects.filter(status__in=['NOVO', 'ANALISE'])
    resumo = {r['tipo']: r['n'] for r in abertos.values('tipo').annotate(n=Count('id')).order_by()}
    return Response({
        'filtros': filtros,
        'pagina': pagina.number,
        'tem_mais': pagina.has_next(),
        'total': pagina.paginator.count,
        'total_abertos': sum(resumo.values()),
        'por_tipo': [{'valor': v, 'rotulo': r, 'abertos': resumo.get(v, 0)} for v, r in AlertaCamera.Tipo.choices],
        'opcoes': {
            'status': [{'valor': 'abertos', 'rotulo': 'Abertos'}] + _opcoes(AlertaCamera.Status.choices),
            'tipos': _opcoes(AlertaCamera.Tipo.choices),
            'lojas': [{'valor': str(g.pk), 'rotulo': g.loja} for g in Gravador.objects.filter(tem_equipamento=True)],
        },
        'alertas': [_alerta_resumo(request, a) for a in pagina.object_list],
    })


@api_view(['GET'])
@authentication_classes(_AUTH)
@permission_classes(_PERM)
def alerta(request, pk):
    a = _alerta(pk)
    _registrar(request, 'ver_alerta', a.gravador, f'#{a.pk}')
    corpo = _alerta_detalhe(request, a)
    corpo['opcoes'] = {'status': _opcoes(AlertaCamera.Status.choices),
                       'motivos_falso': _opcoes(AlertaCamera.MotivoFalso.choices)}
    return Response(corpo)


@api_view(['POST'])
@authentication_classes(_AUTH)
@permission_classes(_PERM)
def tratar(request, pk):
    """cameras/views.py:306-321: muda a situação; falso alarme exige o motivo (é com ele que a análise aprende)."""
    a = _alerta(pk)
    corpo = request.data if isinstance(request.data, dict) else {}
    novo = str(corpo.get('status') or '')
    motivo = str(corpo.get('motivo_falso') or '')
    if novo not in AlertaCamera.Status.values:
        return Response({'erro': 'Escolha a situação do alerta.'}, status=400)
    if novo == AlertaCamera.Status.FALSO and motivo not in AlertaCamera.MotivoFalso.values:
        return Response({'erro': 'Diga por que foi falso alarme: é com isso que a análise aprende.'}, status=400)
    a.status = novo
    a.motivo_falso = motivo if novo == AlertaCamera.Status.FALSO else ''
    a.observacao = str(corpo.get('observacao') or '').strip()
    a.tratado_por, a.tratado_em = request.user, timezone.now()
    a.save(update_fields=['status', 'motivo_falso', 'observacao', 'tratado_por', 'tratado_em'])
    if novo in (AlertaCamera.Status.CONFIRMADO, AlertaCamera.Status.FALSO):
        aprendizado.esquecer()      # a próxima conta (e o próximo sinal da loja) já usa esta decisão
    _registrar(request, 'tratar_alerta', a.gravador, f'#{a.pk} {novo}')
    resposta = _alerta_detalhe(request, _alerta(pk))
    resposta['opcoes'] = {'status': _opcoes(AlertaCamera.Status.choices),
                          'motivos_falso': _opcoes(AlertaCamera.MotivoFalso.choices)}
    resposta['mensagem'] = (f'Alerta marcado como "{a.get_status_display()}".'
                            + (' A análise vai levar esta decisão em conta nos próximos alertas.'
                               if novo in (AlertaCamera.Status.CONFIRMADO, AlertaCamera.Status.FALSO) else ''))
    return Response(resposta)


def _hora(valor):
    return valor.strftime('%H:%M') if valor else None


@api_view(['GET'])
@authentication_classes(_AUTH)
@permission_classes(_PERM)
def indicadores(request):
    """cameras/views.py:196 — mesmo período (até 92 dias) e a mesma conta (indicadores_do_periodo)."""
    hoje = timezone.localdate()
    de = web._data_do_get(request._request, 'de', hoje)
    ate = max(web._data_do_get(request._request, 'ate', de), de)
    ate = min(ate, de + timedelta(days=92))
    loja_id = request.query_params.get('loja', '')
    d = indicadores_do_periodo(de, ate, int(loja_id) if loja_id.isdigit() else None)
    _registrar(request, 'ver_indicadores')
    uma_loja = loja_id.isdigit()

    def linha(l):
        return {
            'loja': {'id': l['gravador'].pk, 'nome': l['gravador'].loja},
            'cobertura': l['cobertura'], 'cobertura_porta': l['cobertura_porta'], 'cobertura_mesa': l['cobertura_mesa'],
            'tem_ponto': l['tem_ponto'],
            'entradas': l['entradas'], 'entradas_aviso': l['entradas_aviso'] or None,
            'entradas_equipe': l['entradas_equipe'], 'clientes': l['clientes'], 'parcial_entradas': l['parcial_entradas'],
            'pico': l['pico'] or None,
            'atendimentos': l['atendimentos'], 'mesas_aviso': l['mesas_aviso'] or None, 'parcial_mesas': l['parcial_mesas'],
            'longos': l['longos'], 'media': l['media'], 'mediana': l['mediana'], 'maior': l['maior'],
            'por_mesa': l['por_mesa'],
            'matinal_aviso': l['matinal_aviso'] or None, 'matinal_dias': l['matinal_dias'],
            'matinal_medidos': l['matinal_medidos'], 'matinal_sem_equipe': l['matinal_sem_equipe'],
            'matinais': [{'data': m['data'].isoformat(), 'inicio': _hora(timezone.localtime(m['inicio']) if m['inicio'] else None),
                          'duracao': m['duracao'], 'pessoas': m['pessoas']} for m in l['matinais']],
            # A web mostra o dia a dia quando o filtro é uma loja (templates/cameras/indicadores.html:73).
            'por_dia': [{**dia, 'data': dia['data'].isoformat(), 'primeira': _hora(dia['primeira'])}
                        for dia in l['por_dia']] if uma_loja else [],
        }

    anterior = d.get('anterior') or {}
    return Response({
        'de': de.isoformat(), 'ate': ate.isoformat(), 'hoje': hoje.isoformat(), 'um_dia': de == ate,
        'loja': int(loja_id) if uma_loja else None,
        'lojas': [{'valor': str(g.pk), 'rotulo': g.loja} for g in Gravador.objects.filter(tem_equipamento=True)],
        'total_entradas': d['total_entradas'], 'total_clientes': d['total_clientes'],
        'lojas_com_medicao': d['lojas_com_medicao'], 'total_lojas': d['total_lojas'], 'cobertura_media': d['cobertura_media'],
        'matinal_dias': d['matinal_dias'], 'matinal_medidos': d['matinal_medidos'],
        'matinal_com': d['matinal_com'], 'matinal_medidas': d['matinal_medidas'],
        'total_atendimentos': d['total_atendimentos'], 'total_longos': d['total_longos'],
        'media_geral': d['media_geral'], 'mediana_geral': d['mediana_geral'],
        'atendimento_maximo_min': d['atendimento_maximo_min'],
        'por_hora': d['por_hora'],
        'anterior': {'de': anterior['de'].isoformat(), 'ate': anterior['ate'].isoformat(),
                     'entradas': anterior.get('entradas'), 'atendimentos': anterior.get('atendimentos')} if anterior else None,
        'linhas': [linha(l) for l in d['linhas']],
        'caminho_web': f'/cameras/indicadores/?de={de:%Y-%m-%d}&ate={ate:%Y-%m-%d}&loja={loja_id if uma_loja else ""}',
    })


# ── Arquivos: foto ao vivo (cache), foto e quadros do alerta, vídeo do alerta ─────────

def _exigir(user):
    """O link assinado não é passe-livre: confere de novo a regra de entrada com o dono do link."""
    if not pode_entrar(user):
        raise PermissionDenied('Sem acesso às câmeras.')


@registrar('cameras_foto')
def _foto_canal(user, obj_id):
    _exigir(user)
    canal = Canal.objects.get(pk=int(obj_id), ativo=True)
    foto = servicos.fotos_dos_canais([canal.pk]).get(canal.pk)
    if not foto:
        raise Http404('Sem imagem recente.')
    return io.BytesIO(base64.b64decode(foto['b64'])), f'camera-{canal.pk}.jpg', 'image/jpeg'


@registrar('cameras_alerta_foto')
def _foto_alerta(user, obj_id):
    _exigir(user)
    a = AlertaCamera.objects.get(pk=int(obj_id))
    if not a.foto:
        raise Http404
    return a.foto, f'alerta-{a.pk}.jpg', 'image/jpeg'


@registrar('cameras_quadro')
def _quadro(user, obj_id):
    """cameras/views.py:429 — quadro-chave guardado no storage do vídeo."""
    _exigir(user)
    alerta_id, _, indice = str(obj_id).partition(':')
    a = AlertaCamera.objects.get(pk=int(alerta_id))
    quadros = a.quadros or []
    i = int(indice)
    if not 0 <= i < len(quadros):
        raise Http404
    arquivo = AlertaCamera._meta.get_field('video').storage.open(quadros[i]['arquivo'], 'rb')
    return arquivo, f'alerta-{a.pk}-quadro-{i}.jpg', 'image/jpeg'


# O vídeo precisa responder a pedidos de pedaço (Range): sem isso o iPhone não toca MP4. O link
# genérico (api_mobile/arquivos.py) não faz Range, então o vídeo tem link assinado próprio, que chama
# a view da web (cameras/views.py:393) como o dono do link — mesmo pedaço máximo e mesmo registro.
VIDEO_VALIDADE = 15 * 60       # o player pede pedaços enquanto toca os 3 minutos
_SAL_VIDEO = 'api_mobile.cameras.video'


def _link_video(request, a):
    assinatura = signing.dumps({'u': request.user.pk, 'a': a.pk}, salt=_SAL_VIDEO, compress=True)
    return dados.absoluta(request, f'/api/mobile/v1/m/cameras/video/{assinatura}/')


@require_GET
def video(request, assinatura):
    from ..permissoes import bloqueio_do_usuario

    try:
        d = signing.loads(assinatura, salt=_SAL_VIDEO, max_age=VIDEO_VALIDADE)
    except signing.BadSignature:      # inclui o expirado
        raise Http404('Link inválido ou expirado. Abra o vídeo de novo pelo app.')
    user = get_user_model().objects.filter(pk=d.get('u'), is_active=True).first()
    if user is None or bloqueio_do_usuario(user) or not pode_entrar(user):
        raise Http404('Link inválido.')
    request.user = user
    return web.alerta_video(request, int(d.get('a') or 0))


urlpatterns = [
    path('', inicio, name='m_cameras'),
    path('lojas/<int:pk>/', loja, name='m_cameras_loja'),
    path('alertas/', alertas, name='m_cameras_alertas'),
    path('alertas/<int:pk>/', alerta, name='m_cameras_alerta'),
    path('alertas/<int:pk>/tratar/', tratar, name='m_cameras_tratar'),
    path('indicadores/', indicadores, name='m_cameras_indicadores'),
    path('video/<str:assinatura>/', video, name='m_cameras_video'),
]
