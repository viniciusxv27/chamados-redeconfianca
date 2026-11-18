"""Módulo Cursos (Vivo) no app: meus cursos, detalhe, enviar o comprovante e o quadro do gestor (leitura).

Regras de onde vieram (app ``cursos``):
- Entrada: ``cursos.permissions.pode_ver`` (gestor, no escopo, curso disponível ou liberação
  individual) — é o gate do menu (cursos/context_processors.py:13), de ``GATES['cursos']``
  (users/module_access.py:225) e da tela ``meus_cursos`` (cursos/views.py:66).
- Lista: os mesmos cursos de ``meus_cursos`` (``cursos_do_usuario``, views.py:70), com a mesma
  situação (``_situacao``, views.py:180) e a mesma ordem (``ORDEM_MEUS``, views.py:175).
- Detalhe: só curso que está na lista da pessoa; fora disso → 404.
- Enviar comprovante: a mesma régua de ``enviar_comprovante`` (views.py:113-133): curso publicado,
  ``curso.alcanca(user)``, arquivo obrigatório, qualquer tipo, até ``TAMANHO_MAXIMO``.
- Quadro do gestor (só leitura): ``e_gestor`` (views.py:219), as mesmas pessoas
  (``_pessoas_do_curso``, views.py:190) e a mesma loja (``_loja``, views.py:43).
- Arquivo do comprovante (link assinado): o dono ou um gestor — quem a web deixa ver o link
  (meus_cursos.html:153 e _pessoa_row.html:78).

Fica no portal: publicar/editar curso, capacitação inicial, aprovar/recusar comprovante,
exportar CSV, cobrança no WhatsApp das lojas e a configuração do módulo.
"""
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils import timezone
from rest_framework.decorators import api_view, authentication_classes, parser_classes, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from .. import dados
from ..arquivos import link, registrar
from ..autenticacao import TokenMobileAuthentication
from . import permissao


def pode_entrar(user):
    from cursos.permissions import pode_ver
    return bool(user and getattr(user, 'is_authenticated', False)) and pode_ver(user)


MODULO = {'rotulo': 'Cursos', 'descricao': 'Cursos Vivo e comprovantes',
          'icone': 'school-outline', 'cor': '#8B5CF6', 'pode_entrar': pode_entrar,
          # Como na web: o bloqueio por curso vencido deixa /cursos/ aberto (cursos/middleware.py
          # LIBERADOS) — mandar o comprovante é justamente a saída da trava.
          'libera_no_bloqueio': ('curso',)}

_PERM = [permissao('cursos')]
_AUTH = [TokenMobileAuthentication]

SITUACAO_NOME = {'NAO_FEZ': 'A enviar', 'RECUSADO': 'Recusado', 'CONFERIR': 'Em conferência', 'APROVADO': 'Aprovado'}


# ── Formato ──────────────────────────────────────────────────────────────────

def _envio(request, envio):
    if envio is None:
        return None
    return {
        'id': envio.pk,
        'status': envio.status,
        'status_nome': envio.get_status_display(),
        'nome': envio.nome_original or 'comprovante',
        'tamanho': envio.tamanho,
        'enviado_em': dados.iso(envio.enviado_em),
        'observacao': envio.observacao or None,
        'revisado_em': dados.iso(envio.revisado_em),
        # `.url`/arquivo vazio derrubava a tela da web (meus_cursos.html:152): aqui vira None.
        'url': link(request, 'cursos_comprovante', envio.pk) if envio.arquivo else None,
    }


def _linha(request, curso, envio, hoje):
    """Uma linha de meus_cursos (cursos/views.py:75-89)."""
    from cursos.views import _situacao
    entregue = bool(envio and envio.vale_como_entregue)
    situacao = _situacao(envio)
    return {
        'id': curso.pk,
        'tipo': curso.tipo,
        'tipo_nome': curso.get_tipo_display(),
        'titulo': curso.titulo,
        'competencia': curso.competencia.isoformat() if curso.competencia else None,
        'prazo': curso.prazo.isoformat(),
        'dias': (curso.prazo - hoje).days,
        'tem_link': bool(curso.link),
        'situacao': situacao,
        'situacao_nome': SITUACAO_NOME[situacao],
        'entregue': entregue,
        'atrasado': (not entregue) and curso.prazo < hoje,
        'ultimo_envio': _envio(request, envio),
    }


def _curso_da_pessoa(user, pk, cfg):
    """Só o que está na lista de meus_cursos; o resto é 404."""
    from cursos.permissions import cursos_do_usuario
    curso = cursos_do_usuario(user, cfg).filter(pk=pk).first()
    if curso is None:
        raise Http404('Curso não encontrado.')
    return curso


def _detalhe(request, curso, cfg):
    from cursos.models import Comprovante
    from cursos.views import EXTENSOES_TEXTO, TAMANHO_MAXIMO
    envios = list(Comprovante.objects.filter(colaborador=request.user, curso=curso).order_by('-enviado_em'))
    linha = _linha(request, curso, envios[0] if envios else None, timezone.localdate())
    return {
        **linha,
        'orientacoes': curso.orientacoes or None,
        'link': curso.link or None,
        'envios': [_envio(request, e) for e in envios],
        # A web sempre deixa mandar outro (meus_cursos.html:163); o servidor confere o alcance.
        'pode_enviar': curso.alcanca(request.user, cfg),
        'tamanho_maximo': TAMANHO_MAXIMO,
        'extensoes': EXTENSOES_TEXTO,
        'trava_portal': cfg.bloquear_navegacao,
        'caminho_web': '/cursos/',
    }


@registrar('cursos_comprovante')
def _arquivo_comprovante(user, obj_id):
    from cursos.models import Comprovante
    from cursos.permissions import e_gestor
    envio = Comprovante.objects.select_related('curso').get(pk=int(obj_id))
    if not pode_entrar(user) or not (envio.colaborador_id == user.pk or e_gestor(user)):
        raise PermissionDenied
    if not envio.arquivo:
        raise Http404
    return envio.arquivo, envio.nome_original or 'comprovante', None


# ── Endpoints ────────────────────────────────────────────────────────────────

@api_view(['GET'])
@authentication_classes(_AUTH)
@permission_classes(_PERM)
def meus(request):
    """Meus cursos (cursos/views.py:meus_cursos, linha 64)."""
    from cursos.models import ConfiguracaoCursos
    from cursos.permissions import cursos_do_usuario, e_gestor
    from cursos.views import EXTENSOES_TEXTO, ORDEM_MEUS, TAMANHO_MAXIMO, _comprovantes_por_curso
    cfg = ConfiguracaoCursos.get()
    cursos = list(cursos_do_usuario(request.user, cfg))
    envios = _comprovantes_por_curso(request.user, cursos)
    hoje = timezone.localdate()
    linhas = [_linha(request, c, envios.get(c.id), hoje) for c in cursos]
    linhas.sort(key=lambda l: (ORDEM_MEUS[l['situacao']], l['prazo']))
    return Response({
        'cursos': linhas,
        'resumo': {
            'nao_fez': sum(1 for l in linhas if l['situacao'] == 'NAO_FEZ'),
            'recusados': sum(1 for l in linhas if l['situacao'] == 'RECUSADO'),
            'conferir': sum(1 for l in linhas if l['situacao'] == 'CONFERIR'),
            'aprovados': sum(1 for l in linhas if l['situacao'] == 'APROVADO'),
            'atrasados': sum(1 for l in linhas if l['atrasado']),
            'pendentes': sum(1 for l in linhas if not l['entregue']),
            'total': len(linhas),
        },
        'gestor': e_gestor(request.user, cfg),
        'trava_portal': cfg.bloquear_navegacao,
        'tamanho_maximo': TAMANHO_MAXIMO,
        'extensoes': EXTENSOES_TEXTO,
    })


@api_view(['GET'])
@authentication_classes(_AUTH)
@permission_classes(_PERM)
def detalhe(request, pk):
    from cursos.models import ConfiguracaoCursos
    cfg = ConfiguracaoCursos.get()
    return Response(_detalhe(request, _curso_da_pessoa(request.user, pk, cfg), cfg))


@api_view(['POST'])
@authentication_classes(_AUTH)
@permission_classes(_PERM)
@parser_classes([MultiPartParser, FormParser])
def enviar(request, pk):
    """Envia o comprovante: a régua de cursos/views.py:enviar_comprovante (linhas 113-133)."""
    from cursos.models import Comprovante, ConfiguracaoCursos, Curso
    from cursos.views import TAMANHO_MAXIMO, _tamanho_legivel
    cfg = ConfiguracaoCursos.get()
    curso = get_object_or_404(Curso, id=pk, publicado=True)
    if not curso.alcanca(request.user, cfg):
        raise Http404('Curso não encontrado.')
    arquivo = request.FILES.get('arquivo')
    if not arquivo:
        return Response({'erro': 'Anexe o comprovante do curso.'}, status=400)
    if arquivo.size > TAMANHO_MAXIMO:
        return Response({'erro': f'Arquivo de {_tamanho_legivel(arquivo.size)} — '
                                 f'o limite é {_tamanho_legivel(TAMANHO_MAXIMO)}.'}, status=400)
    Comprovante.objects.create(curso=curso, colaborador=request.user, arquivo=arquivo,
                               nome_original=arquivo.name[:255], tamanho=arquivo.size)
    # Liberou o curso vencido? O bloqueio do app (cache de 60 s) reavalia na hora.
    from django.core.cache import caches
    caches['local'].delete(f'api_mobile:bloqueio:{request.user.pk}')
    saida = _detalhe(request, curso, cfg)
    saida['mensagem'] = 'Comprovante enviado. O gestor vai conferir; enquanto isso o portal fica liberado.'
    return Response(saida, status=201)


@api_view(['GET'])
@authentication_classes(_AUTH)
@permission_classes(_PERM)
def gestao(request):
    """Quadro por loja, só leitura (cursos/views.py:gestao, linha 216)."""
    from cursos.models import Comprovante, ConfiguracaoCursos, Curso
    from cursos.permissions import e_gestor
    from cursos.views import ORDEM_SITUACAO, SEM_LOJA, _loja, _pessoas_do_curso, _situacao
    cfg = ConfiguracaoCursos.get()
    if not e_gestor(request.user, cfg):
        raise PermissionDenied('O quadro é dos gestores do módulo de cursos.')

    cursos = list(Curso.objects.filter(publicado=True).order_by('-prazo', '-id')[:60])
    escolhido = str(request.query_params.get('curso') or '')
    curso = next((c for c in cursos if str(c.id) == escolhido), None) or (cursos[0] if cursos else None)

    lojas, totais = [], {'pessoas': 0, 'entregues': 0, 'conferir': 0, 'aprovados': 0, 'recusados': 0, 'nao_fez': 0}
    contador = {'CONFERIR': 'conferir', 'RECUSADO': 'recusados', 'NAO_FEZ': 'nao_fez', 'APROVADO': 'aprovados'}
    if curso:
        pessoas = list(_pessoas_do_curso(curso, cfg).select_related('sector'))
        envios = {}
        for c in (Comprovante.objects.filter(curso=curso, colaborador__in=pessoas)
                  .order_by('colaborador_id', '-enviado_em')):
            envios.setdefault(c.colaborador_id, c)
        por_loja = {}
        for p in pessoas:
            envio = envios.get(p.id)
            situacao = _situacao(envio)
            entregue = bool(envio and envio.vale_como_entregue)
            por_loja.setdefault(_loja(p), []).append({
                'id': p.pk,
                'nome': p.get_full_name() or p.username,
                'situacao': situacao,
                'situacao_nome': SITUACAO_NOME[situacao],
                'entregue': entregue,
                'envio': _envio(request, envio),
            })
            totais['pessoas'] += 1
            totais['entregues'] += int(entregue)
            totais[contador[situacao]] += 1
        for nome, itens in por_loja.items():
            itens.sort(key=lambda i: (ORDEM_SITUACAO[i['situacao']], i['nome'].lower()))
            feitos = sum(1 for i in itens if i['entregue'])
            lojas.append({
                'nome': nome,
                'geral': nome == SEM_LOJA,
                'total': len(itens),
                'feitos': feitos,
                'faltam': len(itens) - feitos,
                'conferir': sum(1 for i in itens if i['situacao'] == 'CONFERIR'),
                'percentual': round(feitos * 100 / len(itens)) if itens else 0,
                'pessoas': itens,
            })
        # Como a web (views.py:286): fila de aprovação primeiro, depois alfabética; "Sem loja" no fim.
        lojas.sort(key=lambda b: (b['geral'], 0 if b['conferir'] else 1, b['nome']))

    return Response({
        'cursos': [{'id': c.id, 'titulo': c.titulo, 'prazo': c.prazo.isoformat(), 'tipo_nome': c.get_tipo_display()}
                   for c in cursos],
        'curso': ({'id': curso.id, 'titulo': curso.titulo, 'tipo': curso.tipo, 'tipo_nome': curso.get_tipo_display(),
                   'prazo': curso.prazo.isoformat(), 'vencido': curso.vencido} if curso else None),
        'totais': totais,
        'percentual': round(totais['entregues'] * 100 / totais['pessoas']) if totais['pessoas'] else 0,
        'lojas': lojas,
        'caminho_web': f'/cursos/gestao/?curso={curso.id}' if curso else '/cursos/gestao/',
    })


urlpatterns = [
    path('', meus, name='cursos_meus'),
    path('gestao/', gestao, name='cursos_gestao'),
    path('<int:pk>/', detalhe, name='cursos_detalhe'),
    path('<int:pk>/comprovante/', enviar, name='cursos_enviar'),
]
