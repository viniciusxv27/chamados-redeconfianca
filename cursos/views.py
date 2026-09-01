"""Telas do módulo de cursos da Vivo."""
import logging

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from communications.models import CommunicationGroup
from users.models import Sector

from .models import AtribuicaoCurso, Comprovante, ConfiguracaoCursos, Curso
from .permissions import (
    cursos_do_usuario, e_gestor, e_superadmin, no_escopo, pendencias, pode_ver,
)

logger = logging.getLogger(__name__)
User = get_user_model()

TAMANHO_MAXIMO = 25 * 1024 * 1024          # 25 MB — comprovante é print ou PDF
EXTENSOES = ('.pdf', '.png', '.jpg', '.jpeg', '.webp', '.heic')


def _tamanho_legivel(bytes_):
    for unidade in ('B', 'KB', 'MB'):
        if bytes_ < 1024 or unidade == 'MB':
            return f'{bytes_:.0f} {unidade}' if unidade == 'B' else f'{bytes_:.1f} {unidade}'
        bytes_ /= 1024
    return f'{bytes_:.1f} MB'


def _loja(user):
    """Nome da loja da pessoa. O PDV é o campo que o RH mantém preenchido."""
    return (user.pdv or '').strip() or (user.sector.name if user.sector_id else 'Sem loja')


def _comprovantes_por_curso(user, cursos):
    """O último envio de cada curso, para a tela do colaborador."""
    if not cursos:
        return {}
    mapa = {}
    for c in (Comprovante.objects
              .filter(colaborador=user, curso__in=cursos)
              .order_by('curso_id', '-enviado_em')):
        mapa.setdefault(c.curso_id, c)
    return mapa


# ---------------------------------------------------------------------------
# Colaborador
# ---------------------------------------------------------------------------
@login_required
def meus_cursos(request):
    cfg = ConfiguracaoCursos.get()
    if not pode_ver(request.user, cfg):
        messages.error(request, 'Você não é cobrado pelos cursos da Vivo.')
        return redirect('home')

    cursos = list(cursos_do_usuario(request.user, cfg))
    envios = _comprovantes_por_curso(request.user, cursos)
    hoje = timezone.localdate()

    linhas = []
    for c in cursos:
        envio = envios.get(c.id)
        entregue = bool(envio and envio.vale_como_entregue)
        linhas.append({
            'curso': c,
            'envio': envio,
            'entregue': entregue,
            'atrasado': (not entregue) and c.prazo < hoje,
            'dias': (c.prazo - hoje).days,
        })

    return render(request, 'cursos/meus_cursos.html', {
        'linhas': linhas,
        'is_gestor': e_gestor(request.user, cfg),
        'pendentes': sum(1 for l in linhas if not l['entregue']),
        'extensoes': ', '.join(EXTENSOES),
        'tamanho_maximo': _tamanho_legivel(TAMANHO_MAXIMO),
    })


@require_POST
@login_required
def enviar_comprovante(request, curso_id):
    cfg = ConfiguracaoCursos.get()
    curso = get_object_or_404(Curso, id=curso_id, publicado=True)

    if not curso.alcanca(request.user, cfg):
        messages.error(request, 'Este curso não é cobrado de você.')
        return redirect('cursos:meus_cursos')

    arquivo = request.FILES.get('arquivo')
    if not arquivo:
        messages.error(request, 'Anexe o comprovante do curso.')
        return redirect('cursos:meus_cursos')

    nome = (arquivo.name or '').lower()
    if not nome.endswith(EXTENSOES):
        messages.error(request, f'Formato não aceito. Envie {", ".join(EXTENSOES)}.')
        return redirect('cursos:meus_cursos')
    if arquivo.size > TAMANHO_MAXIMO:
        messages.error(request, f'Arquivo de {_tamanho_legivel(arquivo.size)} — '
                                f'o limite é {_tamanho_legivel(TAMANHO_MAXIMO)}.')
        return redirect('cursos:meus_cursos')

    Comprovante.objects.create(
        curso=curso, colaborador=request.user, arquivo=arquivo,
        nome_original=arquivo.name[:255], tamanho=arquivo.size)

    messages.success(request, 'Comprovante enviado. O gestor vai conferir; '
                              'enquanto isso o portal fica liberado.')
    return redirect('cursos:meus_cursos')


@login_required
def bloqueado(request):
    """Tela de quem passou do prazo. A saída é anexar o comprovante."""
    cfg = ConfiguracaoCursos.get()
    hoje = timezone.localdate()
    atrasados = [c for c in pendencias(request.user, cfg) if c.prazo < hoje]
    if not atrasados:
        return redirect('cursos:meus_cursos')

    ultimos = _comprovantes_por_curso(request.user, atrasados)
    linhas = []
    for c in atrasados:
        envio = ultimos.get(c.id)
        recusa = envio if (envio and envio.status == Comprovante.RECUSADO) else None
        linhas.append({'curso': c, 'recusa': recusa})

    return render(request, 'cursos/bloqueado.html', {
        'linhas': linhas,
        'extensoes': ', '.join(EXTENSOES),
    })


# ---------------------------------------------------------------------------
# Gestão
# ---------------------------------------------------------------------------
def _pessoas_do_curso(curso, cfg):
    """Quem é cobrado por este curso, já com a loja."""
    if curso.tipo == Curso.CAPACITACAO:
        ids = curso.atribuicoes.values_list('colaborador_id', flat=True)
        qs = User.objects.filter(id__in=ids)
    else:
        grupos = list(cfg.grupos.values_list('id', flat=True))
        setores = list(cfg.setores.values_list('id', flat=True))
        escolhidos = list(cfg.usuarios.values_list('id', flat=True))
        if not grupos and not setores and not escolhidos:
            return User.objects.none()
        filtro = None
        if grupos:
            filtro = User.objects.filter(communication_groups__id__in=grupos)
        if setores:
            porsetor = User.objects.filter(sector_id__in=setores) | \
                       User.objects.filter(sectors__id__in=setores)
            filtro = porsetor if filtro is None else (filtro | porsetor)
        if escolhidos:
            um_a_um = User.objects.filter(id__in=escolhidos)
            filtro = um_a_um if filtro is None else (filtro | um_a_um)
        qs = filtro.filter(is_active=True)
    return qs.filter(is_active=True).distinct().order_by('pdv', 'first_name', 'last_name')


@login_required
def gestao(request):
    """Quadro por loja: quem fez e quem não fez."""
    cfg = ConfiguracaoCursos.get()
    if not e_gestor(request.user, cfg):
        messages.error(request, 'Área dos gestores do módulo.')
        return redirect('cursos:meus_cursos')

    cursos = list(Curso.objects.filter(publicado=True).order_by('-prazo', '-id')[:60])
    curso = None
    escolhido = request.GET.get('curso')
    if escolhido:
        curso = next((c for c in cursos if str(c.id) == str(escolhido)), None)
    if curso is None:
        curso = cursos[0] if cursos else None

    lojas, totais = [], {'pessoas': 0, 'entregues': 0, 'pendentes': 0, 'conferir': 0}
    if curso:
        pessoas = list(_pessoas_do_curso(curso, cfg))
        envios = {}
        for c in (Comprovante.objects
                  .filter(curso=curso, colaborador__in=pessoas)
                  .select_related('colaborador').order_by('colaborador_id', '-enviado_em')):
            envios.setdefault(c.colaborador_id, c)

        por_loja = {}
        for p in pessoas:
            envio = envios.get(p.id)
            entregue = bool(envio and envio.vale_como_entregue)
            item = {'pessoa': p, 'envio': envio, 'entregue': entregue,
                    'conferir': bool(envio and envio.status == Comprovante.PENDENTE)}
            por_loja.setdefault(_loja(p), []).append(item)
            totais['pessoas'] += 1
            totais['entregues' if entregue else 'pendentes'] += 1
            totais['conferir'] += 1 if item['conferir'] else 0

        for nome in sorted(por_loja):
            itens = sorted(por_loja[nome], key=lambda i: (i['entregue'], i['pessoa'].first_name))
            feitos = sum(1 for i in itens if i['entregue'])
            lojas.append({
                'nome': nome, 'itens': itens, 'total': len(itens), 'feitos': feitos,
                'faltam': len(itens) - feitos,
                'percentual': round(feitos * 100 / len(itens)) if itens else 0,
            })

    return render(request, 'cursos/gestao.html', {
        'cursos': cursos, 'curso': curso, 'lojas': lojas, 'totais': totais,
        'percentual_geral': (round(totais['entregues'] * 100 / totais['pessoas'])
                             if totais['pessoas'] else 0),
        'is_superadmin': e_superadmin(request.user),
    })


@login_required
def exportar(request):
    """O mesmo quadro em CSV, para mandar para a Vivo ou para a coordenação."""
    import csv

    cfg = ConfiguracaoCursos.get()
    if not e_gestor(request.user, cfg):
        raise Http404

    curso = get_object_or_404(Curso, id=request.GET.get('curso') or 0)
    pessoas = list(_pessoas_do_curso(curso, cfg))
    envios = {}
    for c in (Comprovante.objects.filter(curso=curso, colaborador__in=pessoas)
              .order_by('colaborador_id', '-enviado_em')):
        envios.setdefault(c.colaborador_id, c)

    resposta = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    nome = f'cursos-{curso.id}-{timezone.localdate():%Y-%m-%d}.csv'
    resposta['Content-Disposition'] = f'attachment; filename="{nome}"'
    escritor = csv.writer(resposta, delimiter=';')
    escritor.writerow(['Loja', 'Colaborador', 'Situação', 'Enviado em', 'Conferência'])
    for p in pessoas:
        envio = envios.get(p.id)
        escritor.writerow([
            _loja(p), p.get_full_name() or p.username,
            'Entregue' if (envio and envio.vale_como_entregue) else 'Não entregue',
            timezone.localtime(envio.enviado_em).strftime('%d/%m/%Y %H:%M') if envio else '',
            envio.get_status_display() if envio else '',
        ])
    return resposta


@login_required
def curso_form(request, curso_id=None):
    """Publica ou edita um curso: link, orientações e prazo."""
    cfg = ConfiguracaoCursos.get()
    if not e_gestor(request.user, cfg):
        messages.error(request, 'Área dos gestores do módulo.')
        return redirect('cursos:meus_cursos')

    curso = get_object_or_404(Curso, id=curso_id) if curso_id else None

    if request.method == 'POST':
        tipo = request.POST.get('tipo') or Curso.FOCO
        titulo = (request.POST.get('titulo') or '').strip()
        prazo = (request.POST.get('prazo') or '').strip()
        if not titulo or not prazo:
            messages.error(request, 'Título e prazo são obrigatórios.')
        else:
            dados = {
                'tipo': tipo if tipo in (Curso.FOCO, Curso.CAPACITACAO) else Curso.FOCO,
                'titulo': titulo[:200],
                'orientacoes': (request.POST.get('orientacoes') or '').strip(),
                'link': (request.POST.get('link') or '').strip()[:500],
                'prazo': prazo,
                'publicado': request.POST.get('publicado') == 'on',
            }
            competencia = (request.POST.get('competencia') or '').strip()
            dados['competencia'] = f'{competencia}-01' if competencia else None

            if curso:
                for campo, valor in dados.items():
                    setattr(curso, campo, valor)
                curso.save()
                messages.success(request, 'Curso atualizado.')
            else:
                curso = Curso.objects.create(criado_por=request.user, **dados)
                messages.success(request, 'Curso publicado.' if dados['publicado']
                                 else 'Curso salvo como rascunho.')
            return redirect('cursos:gestao')

    return render(request, 'cursos/curso_form.html', {
        'curso': curso,
        'tipos': Curso.TIPOS,
        'hoje': timezone.localdate(),
    })


@login_required
def capacitacao(request, curso_id):
    """Sinaliza quem precisa fazer a capacitação inicial."""
    cfg = ConfiguracaoCursos.get()
    if not e_gestor(request.user, cfg):
        messages.error(request, 'Área dos gestores do módulo.')
        return redirect('cursos:meus_cursos')

    curso = get_object_or_404(Curso, id=curso_id, tipo=Curso.CAPACITACAO)

    if request.method == 'POST':
        try:
            ids = {int(x) for x in request.POST.getlist('colaboradores')}
        except (TypeError, ValueError):
            messages.error(request, 'Seleção inválida.')
            return redirect('cursos:capacitacao', curso_id=curso.id)

        atuais = set(curso.atribuicoes.values_list('colaborador_id', flat=True))
        # Quem saiu da lista perde a atribuição, menos quem já mandou comprovante:
        # apagar aí apagaria o histórico de quem cumpriu.
        com_envio = set(Comprovante.objects.filter(curso=curso)
                        .values_list('colaborador_id', flat=True))
        remover = (atuais - ids) - com_envio
        if remover:
            curso.atribuicoes.filter(colaborador_id__in=remover).delete()
        novos = [AtribuicaoCurso(curso=curso, colaborador_id=i, atribuido_por=request.user)
                 for i in (ids - atuais)
                 if User.objects.filter(id=i, is_active=True).exists()]
        if novos:
            AtribuicaoCurso.objects.bulk_create(novos, ignore_conflicts=True)

        messages.success(request, f'{len(ids)} pessoa(s) na capacitação inicial.')
        return redirect('cursos:gestao')

    marcados = set(curso.atribuicoes.values_list('colaborador_id', flat=True))
    pessoas = (User.objects.filter(is_active=True)
               .order_by('pdv', 'first_name', 'last_name'))
    return render(request, 'cursos/capacitacao.html', {
        'curso': curso,
        'pessoas': pessoas,
        'marcados': marcados,
        'com_envio': set(Comprovante.objects.filter(curso=curso)
                         .values_list('colaborador_id', flat=True)),
    })


@require_POST
@login_required
def revisar_comprovante(request, comprovante_id):
    cfg = ConfiguracaoCursos.get()
    if not e_gestor(request.user, cfg):
        messages.error(request, 'Área dos gestores do módulo.')
        return redirect('cursos:meus_cursos')

    envio = get_object_or_404(Comprovante, id=comprovante_id)
    acao = request.POST.get('acao')
    if acao not in ('aprovar', 'recusar'):
        messages.error(request, 'Ação inválida.')
        return redirect('cursos:gestao')

    envio.status = Comprovante.APROVADO if acao == 'aprovar' else Comprovante.RECUSADO
    envio.observacao = (request.POST.get('observacao') or '').strip()
    envio.revisado_por = request.user
    envio.revisado_em = timezone.now()
    envio.save(update_fields=['status', 'observacao', 'revisado_por', 'revisado_em'])

    if envio.status == Comprovante.RECUSADO:
        _avisar(envio.colaborador, 'Comprovante de curso recusado',
                f'O comprovante de "{envio.curso.titulo}" foi recusado. '
                f'{envio.observacao or "Envie novamente."}')
    messages.success(request, 'Comprovante ' +
                     ('aprovado.' if acao == 'aprovar' else 'recusado.'))
    return redirect(request.POST.get('voltar') or 'cursos:gestao')


def _avisar(destinatario, titulo, texto):
    """Notificação no portal. Nunca derruba a ação por causa do aviso."""
    try:
        from core.models import Notification
        Notification.objects.create(
            user=destinatario, title=titulo, message=texto,
            notification_type='SYSTEM', related_url='/cursos/')
    except Exception as exc:                                    # noqa: BLE001
        logger.warning('Aviso de curso não enviado: %s', exc)


# ---------------------------------------------------------------------------
# Configuração (SUPERADMIN)
# ---------------------------------------------------------------------------
@login_required
def configuracao(request):
    if not e_superadmin(request.user):
        messages.error(request, 'Só o SUPERADMIN configura o módulo de cursos.')
        return redirect('cursos:meus_cursos')

    cfg = ConfiguracaoCursos.get()

    if request.method == 'POST':
        cfg.bloquear_navegacao = request.POST.get('bloquear_navegacao') == 'on'
        cfg.save(update_fields=['bloquear_navegacao'])
        cfg.grupos.set(CommunicationGroup.objects.filter(
            id__in=request.POST.getlist('grupos')))
        cfg.setores.set(Sector.objects.filter(id__in=request.POST.getlist('setores')))
        cfg.usuarios.set(User.objects.filter(
            id__in=request.POST.getlist('usuarios'), is_active=True))
        cfg.gestores.set(User.objects.filter(
            id__in=request.POST.getlist('gestores'), is_active=True))
        messages.success(request, 'Configuração salva.')
        return redirect('cursos:configuracao')

    return render(request, 'cursos/configuracao.html', {
        'cfg': cfg,
        'grupos': CommunicationGroup.objects.all().order_by('name'),
        'setores': Sector.objects.all().order_by('name'),
        'pessoas': User.objects.filter(is_active=True).order_by('first_name', 'last_name'),
        'grupos_marcados': set(cfg.grupos.values_list('id', flat=True)),
        'setores_marcados': set(cfg.setores.values_list('id', flat=True)),
        'usuarios_marcados': set(cfg.usuarios.values_list('id', flat=True)),
        'gestores_marcados': set(cfg.gestores.values_list('id', flat=True)),
        'alcance': _quantas_pessoas(cfg),
    })


def _quantas_pessoas(cfg):
    """Quantos seriam cobrados com a configuração atual — antes de ligar o bloqueio.

    Soma os três caminhos sem contar ninguém duas vezes: quem está num grupo
    cobrado E foi escolhido na mão continua sendo uma pessoa só.
    """
    grupos = list(cfg.grupos.values_list('id', flat=True))
    setores = list(cfg.setores.values_list('id', flat=True))
    escolhidos = list(cfg.usuarios.values_list('id', flat=True))
    if not grupos and not setores and not escolhidos:
        return 0

    qs = None
    if grupos:
        qs = User.objects.filter(communication_groups__id__in=grupos)
    if setores:
        porsetor = (User.objects.filter(sector_id__in=setores)
                    | User.objects.filter(sectors__id__in=setores))
        qs = porsetor if qs is None else (qs | porsetor)
    if escolhidos:
        um_a_um = User.objects.filter(id__in=escolhidos)
        qs = um_a_um if qs is None else (qs | um_a_um)
    return qs.filter(is_active=True).distinct().count()
