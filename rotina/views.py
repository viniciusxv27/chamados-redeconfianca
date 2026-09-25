"""Telas da Rotina Gerencial.

- `minha`: a semana da pessoa, no jeito do Google Agenda (segunda a sábado, ou
  a domingo quando a semana tem domingo).
- `gestao*` e `modelo*`: só o SUPERADMIN — quem tem rotina, os modelos e o
  editor de cada semana (o mesmo calendário, com tudo liberado).

As telas já entregam os dados prontos (json_script); dali em diante o
calendário conversa com a API a cada mudança.
"""
import logging
from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import servicos, whatsapp
from .models import MINUTOS_WHATSAPP_MAXIMO, MINUTOS_WHATSAPP_PADRAO, ModeloRotina, RotinaGerencial
from .permissoes import e_superadmin

logger = logging.getLogger(__name__)
User = get_user_model()


def so_superadmin(view):
    """Telas de gestão: quem não é SUPERADMIN volta para a própria rotina, com um aviso."""
    @wraps(view)
    @login_required
    def envolta(request, *args, **kwargs):
        if not e_superadmin(request.user):
            messages.error(request, 'Só o SUPERADMIN gerencia a rotina gerencial.')
            return redirect('rotina:minha')
        return view(request, *args, **kwargs)
    return envolta


def _numero(valor, minimo=0, maximo=None):
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return None
    if numero < minimo or (maximo is not None and numero > maximo):
        return None
    return numero


def _plural(total, singular, plural):
    return f'{total} {singular if total == 1 else plural}'


def _contexto(request, aba, com_domingo=False, **extra):
    contexto = {
        'aba': aba,
        'e_admin': e_superadmin(request.user),
        'lembrete_minutos': servicos.MINUTOS_LEMBRETE,
        'whatsapp_minutos_padrao': MINUTOS_WHATSAPP_PADRAO,
        'categorias': servicos.categorias(),
        'com_domingo': com_domingo,
        'nome_da_semana': servicos.nome_da_semana(com_domingo),
        'dias_semana': [{'valor': numero, 'nome': nome, 'curto': servicos.DIAS_CURTOS[numero]}
                        for numero, nome in servicos.dias_da_semana(com_domingo)],
    }
    contexto.update(extra)
    return contexto


def _config(request, modo, urls, **extra):
    """Configuração do calendário (vai para o JS por json_script)."""
    config = {
        'modo': modo,
        'podeCriar': False,
        'podeTravar': False,
        'mostrarDatas': modo == 'pessoa',
        'domingo': False,
        'usuario': None,
        'csrf': get_token(request),
        'urls': urls,
        'destaque': {'atividade': None, 'dia': None},
        # Lembrete no WhatsApp de cada atividade: o valor de uma atividade nova e o teto.
        'whatsappPadrao': MINUTOS_WHATSAPP_PADRAO,
        'whatsappMaximo': MINUTOS_WHATSAPP_MAXIMO,
    }
    config.update(extra)
    return config


def _pessoas_para_editar():
    """Todas as pessoas ativas, para o SUPERADMIN abrir a rotina de qualquer uma (quem já tem rotina primeiro)."""
    totais = dict(RotinaGerencial.objects.annotate(total=Count('atividades')).values_list('user_id', 'total'))
    pessoas = list(User.objects.filter(is_active=True).select_related('sector').order_by('first_name', 'last_name'))
    for pessoa in pessoas:
        pessoa.rotina_total = totais.get(pessoa.id)
    pessoas.sort(key=lambda pessoa: pessoa.rotina_total is None)
    return pessoas


def _urls_rotina(usuario_id=None):
    listar = reverse('rotina:api_rotina')
    if usuario_id:
        listar += f'?usuario={usuario_id}'
    return {
        'listar': listar,
        'criar': reverse('rotina:api_rotina_criar'),
        'atualizar': reverse('rotina:api_rotina_atualizar', args=[0]),
        'excluir': reverse('rotina:api_rotina_excluir', args=[0]),
        'concluir': reverse('rotina:api_rotina_concluir', args=[0]),
        'desfazer': reverse('rotina:api_rotina_desfazer', args=[0]),
    }


def _urls_modelo(modelo):
    return {
        'listar': reverse('rotina:api_modelo_atividades', args=[modelo.id]),
        'criar': reverse('rotina:api_modelo_atividades', args=[modelo.id]),
        'atualizar': reverse('rotina:api_modelo_atualizar', args=[0]),
        'excluir': reverse('rotina:api_modelo_excluir', args=[0]),
    }


# ---------------------------------------------------------------------------
# A rotina da pessoa
# ---------------------------------------------------------------------------
@login_required
def minha(request):
    rotina = (RotinaGerencial.objects.select_related('user', 'modelo_origem')
              .filter(user=request.user).first())
    total = rotina.atividades.count() if rotina else 0
    # Rotina vazia só aparece se a pessoa puder criar: senão é uma grade em
    # branco sem nada para fazer com ela.
    mostrar = bool(rotina and rotina.ativa and (total or rotina.pode_criar))
    dados = servicos.payload_rotina(rotina, request.user) if mostrar else None
    com_domingo = bool(dados and dados['semana']['com_domingo'])
    contexto = _contexto(request, 'minha', com_domingo=com_domingo, rotina=rotina, total_atividades=total,
                         mostrar_calendario=mostrar,
                         whatsapp_ligado=bool(rotina and rotina.avisar_whatsapp and servicos.tem_telefone(request.user)),
                         pessoas_para_editar=_pessoas_para_editar() if e_superadmin(request.user) else [])
    if mostrar:
        destaque = _numero(request.GET.get('atividade'), minimo=1)
        if destaque and not any(a['id'] == destaque for a in dados['atividades']):
            destaque = None
        ultimo_dia = dados['semana']['ultimo_dia']
        contexto.update({
            'dados': dados,
            'semana': dados['semana'],
            'config': _config(
                request, 'pessoa', _urls_rotina(),
                podeCriar=rotina.pode_criar, domingo=com_domingo,
                destaque={'atividade': destaque, 'dia': _numero(request.GET.get('dia'), 0, ultimo_dia)}),
        })
    return render(request, 'rotina/minha.html', contexto)


# ---------------------------------------------------------------------------
# Gestão: quem tem rotina
# ---------------------------------------------------------------------------
@so_superadmin
def gestao(request):
    q = (request.GET.get('q') or '').strip()
    rotinas = (RotinaGerencial.objects
               .select_related('user', 'user__sector', 'atualizado_por', 'modelo_origem')
               .annotate(total=Count('atividades'))
               .order_by('user__first_name', 'user__last_name'))
    for termo in q.split():
        rotinas = rotinas.filter(
            Q(user__first_name__icontains=termo) | Q(user__last_name__icontains=termo)
            | Q(user__email__icontains=termo) | Q(user__sector__name__icontains=termo)
            | Q(user__job_title__icontains=termo))

    resumo = RotinaGerencial.objects.aggregate(
        total=Count('id'), ativas=Count('id', filter=Q(ativa=True)),
        criam=Count('id', filter=Q(pode_criar=True)))
    modelos_ativos = (ModeloRotina.objects.filter(ativo=True)
                      .annotate(total=Count('atividades')).order_by('criado_em', 'id'))
    from core.telefone import problema

    rotinas = list(rotinas)
    for rotina in rotinas:
        # O interruptor do WhatsApp fica apagado para quem não tem telefone: ligado ou
        # não, não sairia nada — melhor a tela dizer isso do que prometer um aviso.
        rotina.tem_telefone = servicos.tem_telefone(rotina.user)
        rotina.problema_telefone = problema(rotina.user.phone)
    return render(request, 'rotina/gestao.html', _contexto(
        request, 'gestao',
        rotinas=rotinas, q=q, resumo=resumo, modelos=modelos_ativos,
        # Por que o WhatsApp não chega: canal, varredura, falhas de hoje e telefones ruins.
        whatsapp=whatsapp.diagnostico(),
        com_rotina=set(RotinaGerencial.objects.values_list('user_id', flat=True)),
        candidatos=(User.objects.filter(is_active=True).select_related('sector')
                    .order_by('first_name', 'last_name')),
        pessoas_para_editar=_pessoas_para_editar(),
    ))


@so_superadmin
@require_POST
def gestao_adicionar(request):
    ids = {_numero(valor, minimo=1) for valor in request.POST.getlist('usuarios')} - {None}
    pessoas = list(User.objects.filter(id__in=ids, is_active=True).order_by('first_name', 'last_name'))
    if not pessoas:
        messages.error(request, 'Escolha pelo menos uma pessoa.')
        return redirect('rotina:gestao')

    modelo = None
    modelo_id = _numero(request.POST.get('modelo'), minimo=1)
    if modelo_id:
        modelo = ModeloRotina.objects.filter(pk=modelo_id).first()
        if modelo is None:
            messages.error(request, 'O modelo escolhido não existe mais.')
            return redirect('rotina:gestao')
    pode_criar = request.POST.get('pode_criar') == 'on'
    avisar_whatsapp = request.POST.get('avisar_whatsapp') == 'on'
    substituir = request.POST.get('substituir') == 'on'

    novas = substituidas = mantidas = 0
    with transaction.atomic():
        for pessoa in pessoas:
            rotina, criada = RotinaGerencial.objects.get_or_create(
                user=pessoa,
                defaults={'pode_criar': pode_criar, 'avisar_whatsapp': avisar_whatsapp,
                          'criado_por': request.user, 'atualizado_por': request.user})
            if criada:
                novas += 1
                if modelo:
                    servicos.aplicar_modelo(rotina, modelo, request.user)
            elif modelo and substituir:
                servicos.aplicar_modelo(rotina, modelo, request.user)
                substituidas += 1
            else:
                mantidas += 1

    partes = []
    if novas:
        partes.append(_plural(novas, 'pessoa adicionada', 'pessoas adicionadas')
                      + (f' com o modelo "{modelo.nome}"' if modelo else ' com a rotina vazia'))
    if substituidas:
        partes.append(_plural(substituidas, 'rotina substituída', 'rotinas substituídas') + ' pelo modelo')
    if mantidas:
        partes.append(_plural(mantidas, 'pessoa já tinha rotina e ficou como estava',
                              'pessoas já tinham rotina e ficaram como estavam'))
    messages.success(request, '; '.join(partes) + '.')
    if len(pessoas) == 1:
        return redirect('rotina:gestao_pessoa', user_id=pessoas[0].id)
    return redirect('rotina:gestao')


@so_superadmin
def gestao_pessoa(request, user_id):
    pessoa = get_object_or_404(User.objects.select_related('sector'), pk=user_id)
    rotina = (RotinaGerencial.objects.select_related('user', 'modelo_origem', 'atualizado_por')
              .filter(user=pessoa).first())
    if rotina is None:
        if not pessoa.is_active:
            messages.info(request, f'{servicos.nome_de(pessoa)} está inativa no portal e não tem rotina gerencial.')
            return redirect('rotina:gestao')
        # Quem ainda não tem rotina: a tela oferece criar ali mesmo (com um modelo ou vazia) e já abre a semana.
        return render(request, 'rotina/pessoa_sem_rotina.html', _contexto(
            request, 'gestao', pessoa=pessoa, pessoas_para_editar=_pessoas_para_editar(),
            modelos=(ModeloRotina.objects.filter(ativo=True).annotate(total=Count('atividades'))
                     .order_by('criado_em', 'id'))))

    dados = servicos.payload_rotina(rotina, request.user)
    com_domingo = dados['semana']['com_domingo']
    outras = (RotinaGerencial.objects.exclude(pk=rotina.pk)
              .select_related('user', 'user__sector')
              .annotate(total=Count('atividades')).filter(total__gt=0)
              .order_by('user__first_name', 'user__last_name'))
    return render(request, 'rotina/editor.html', _contexto(
        request, 'gestao', com_domingo=com_domingo,
        modo='gestao', pessoa=pessoa, rotina=rotina, dados=dados, semana=dados['semana'],
        outras=outras, tem_telefone=dados['rotina']['tem_telefone'], pessoas_para_editar=_pessoas_para_editar(),
        modelos=ModeloRotina.objects.annotate(total=Count('atividades')).order_by('-ativo', 'nome'),
        config=_config(request, 'gestao', _urls_rotina(pessoa.id),
                       podeCriar=True, podeTravar=True, usuario=pessoa.id, domingo=com_domingo,
                       urlOpcoes=reverse('rotina:api_gestao_opcoes', args=[pessoa.id])),
    ))


@so_superadmin
@require_POST
def gestao_pessoa_acao(request, user_id):
    rotina = get_object_or_404(RotinaGerencial.objects.select_related('user'), user_id=user_id)
    nome = servicos.nome_de(rotina.user)
    acao = request.POST.get('acao')
    try:
        if acao == 'aplicar_modelo':
            modelo = ModeloRotina.objects.filter(pk=_numero(request.POST.get('modelo'), 1) or 0).first()
            if modelo is None:
                raise servicos.ErroValidacao('Escolha um modelo para aplicar.')
            total = servicos.aplicar_modelo(rotina, modelo, request.user)
            messages.success(request, f'Modelo "{modelo.nome}" aplicado: a rotina de {nome} agora tem '
                                      f'{_plural(total, "atividade", "atividades")}.')
        elif acao == 'copiar':
            origem = (RotinaGerencial.objects.select_related('user')
                      .filter(user_id=_numero(request.POST.get('origem'), 1) or 0).first())
            if origem is None:
                raise servicos.ErroValidacao('Escolha de quem copiar a rotina.')
            total = servicos.copiar_rotina(rotina, origem, request.user)
            messages.success(request, f'Rotina de {servicos.nome_de(origem.user)} copiada para {nome} '
                                      f'({_plural(total, "atividade", "atividades")}).')
        elif acao == 'limpar':
            total = servicos.limpar_rotina(rotina, request.user)
            messages.success(request, f'Rotina de {nome} limpa '
                                      f'({_plural(total, "atividade removida", "atividades removidas")}).')
        elif acao in ('ativar', 'desativar'):
            rotina.ativa = acao == 'ativar'
            rotina.atualizado_por = request.user
            rotina.save(update_fields=['ativa', 'atualizado_por', 'atualizado_em'])
            messages.success(request, f'Rotina de {nome} ativada.' if rotina.ativa else
                             f'Rotina de {nome} desativada: sai do menu da pessoa e os avisos param.')
        elif acao == 'remover':
            rotina.delete()
            messages.success(request, f'{nome} saiu da rotina gerencial.')
            return redirect('rotina:gestao')
        else:
            messages.error(request, 'Ação desconhecida.')
    except servicos.ErroValidacao as exc:
        messages.error(request, str(exc))
    return redirect('rotina:gestao_pessoa', user_id=user_id)


# ---------------------------------------------------------------------------
# Gestão: modelos
# ---------------------------------------------------------------------------
PREVIA_INICIO = 7 * 60     # a miniatura da semana vai das 07:00…
PREVIA_FIM = 20 * 60       # …às 20:00


def _previa_da_semana(atividades, com_domingo=False):
    """Blocos da miniatura da semana no cartão do modelo: topo e altura em %.

    Os números saem como texto já formatado: passando float, o template em
    pt-br escreveria "12,5%" e o CSS ignoraria.
    """
    dias = [[] for _ in servicos.dias_da_semana(com_domingo)]
    janela = PREVIA_FIM - PREVIA_INICIO
    for atividade in atividades:
        inicio = max(atividade.inicio.hour * 60 + atividade.inicio.minute, PREVIA_INICIO)
        fim = min(atividade.fim.hour * 60 + atividade.fim.minute, PREVIA_FIM)
        if fim <= inicio or not 0 <= atividade.dia_semana < len(dias):
            continue
        dias[atividade.dia_semana].append({
            'topo': f'{100 * (inicio - PREVIA_INICIO) / janela:.2f}',
            'altura': f'{100 * (fim - inicio) / janela:.2f}',
            'cor': atividade.cor['cor'],
        })
    return [{'curto': servicos.DIAS_CURTOS[numero], 'blocos': blocos} for numero, blocos in enumerate(dias)]


@so_superadmin
def modelos(request):
    lista = list(ModeloRotina.objects
                 .annotate(total=Count('atividades', distinct=True), pessoas=Count('rotinas', distinct=True))
                 .prefetch_related('atividades')
                 .order_by('-ativo', 'nome'))
    for modelo in lista:
        atividades = list(modelo.atividades.all())
        modelo.semana_com_domingo = servicos.semana_com_domingo(modelo, atividades)
        modelo.previa = _previa_da_semana(atividades, modelo.semana_com_domingo)
    return render(request, 'rotina/modelos.html', _contexto(request, 'modelos', modelos=lista))


@so_superadmin
@require_POST
def modelo_novo(request):
    nome = (request.POST.get('nome') or '').strip()[:120]
    if not nome:
        messages.error(request, 'Dê um nome ao modelo.')
        return redirect('rotina:modelos')
    descricao = (request.POST.get('descricao') or '').strip()
    com_domingo = request.POST.get('com_domingo') == 'on'
    base = ModeloRotina.objects.filter(pk=_numero(request.POST.get('base'), 1) or 0).first()
    with transaction.atomic():
        if base:
            modelo = servicos.duplicar_modelo(base, request.user)
            modelo.nome = nome
            modelo.descricao = descricao or base.descricao
            # A cópia herda o domingo da base; a caixa só acrescenta (tirar apagaria atividades).
            modelo.com_domingo = modelo.com_domingo or com_domingo
            modelo.save(update_fields=['nome', 'descricao', 'com_domingo', 'atualizado_em'])
        else:
            modelo = ModeloRotina.objects.create(nome=nome, descricao=descricao, com_domingo=com_domingo,
                                                 criado_por=request.user)
    messages.success(request, f'Modelo "{modelo.nome}" criado.')
    return redirect('rotina:modelo_editor', modelo_id=modelo.id)


@so_superadmin
def modelo_editor(request, modelo_id):
    modelo = get_object_or_404(ModeloRotina, pk=modelo_id)
    dados = servicos.payload_modelo(modelo)
    com_domingo = dados['semana']['com_domingo']
    return render(request, 'rotina/editor.html', _contexto(
        request, 'modelos', com_domingo=com_domingo,
        modo='modelo', modelo=modelo, dados=dados, semana=dados['semana'],
        pessoas_usando=modelo.rotinas.count(),
        config=_config(request, 'modelo', _urls_modelo(modelo), podeCriar=True, podeTravar=True,
                       domingo=com_domingo),
    ))


@so_superadmin
@require_POST
def modelo_acao(request, modelo_id):
    modelo = get_object_or_404(ModeloRotina, pk=modelo_id)
    acao = request.POST.get('acao')
    if acao == 'salvar':
        nome = (request.POST.get('nome') or '').strip()[:120]
        if not nome:
            messages.error(request, 'O modelo precisa de um nome.')
        else:
            modelo.nome = nome
            modelo.descricao = (request.POST.get('descricao') or '').strip()
            modelo.ativo = request.POST.get('ativo') == 'on'
            com_domingo = request.POST.get('com_domingo') == 'on'
            aviso_domingo = None
            if modelo.com_domingo and not com_domingo:
                try:
                    servicos.conferir_pode_tirar_domingo(modelo)
                except servicos.ErroValidacao as exc:
                    # O resto do formulário salva; só o domingo fica como estava.
                    aviso_domingo = str(exc)
                    com_domingo = True
            modelo.com_domingo = com_domingo
            modelo.save(update_fields=['nome', 'descricao', 'ativo', 'com_domingo', 'atualizado_em'])
            if aviso_domingo:
                messages.error(request, f'Modelo salvo, mas o domingo continua na semana: {aviso_domingo}')
            else:
                messages.success(request, 'Modelo salvo.')
    elif acao == 'duplicar':
        copia = servicos.duplicar_modelo(modelo, request.user)
        messages.success(request, f'Cópia criada: "{copia.nome}".')
        return redirect('rotina:modelo_editor', modelo_id=copia.id)
    elif acao == 'reaplicar':
        rotinas = list(modelo.rotinas.all())
        with transaction.atomic():
            for rotina in rotinas:
                servicos.aplicar_modelo(rotina, modelo, request.user)
        messages.success(request, f'Modelo reaplicado em {_plural(len(rotinas), "pessoa", "pessoas")}.')
    elif acao == 'excluir':
        nome = modelo.nome
        modelo.delete()
        messages.success(request, f'Modelo "{nome}" excluído. Quem já tinha recebido continua com a rotina.')
        return redirect('rotina:modelos')
    else:
        messages.error(request, 'Ação desconhecida.')
    return redirect('rotina:modelo_editor', modelo_id=modelo.id)
