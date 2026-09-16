"""Telas do Vini Renova."""
import csv
import logging
import re
import unicodedata
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST
from PIL import Image

from core.middleware import log_action

from . import checklist, conteudo
from .context_processors import limpar_cache_do_menu
from .models import PrecoAparelho, Renova
from .permissoes import (configuracao, e_gerente, e_superadmin, pode_aprovar, pode_excluir, pode_fazer,
                         pode_informar_venda, pode_receber, pode_ver, pode_ver_gestao, pode_ver_modulo,
                         renovas_visiveis, setor_recebedor)
from .padrao import regras_para_tela
from .fotos import gravar_fotos, ler_fotos
from .servicos import (DecisaoInvalida, abrir_chamado, avisar_gerentes, decidir, excluir_avaliacao,
                       gerentes_da_loja, registrar_recebimento, resumo_da_avaliacao)
from .validacao import NUMERO_VENDA_MAX, ler_checklist, ler_numero_venda, ler_valor

logger = logging.getLogger(__name__)
User = get_user_model()

POR_PAGINA = 20
IMAGENS_PADRAO = {
    'imagem_tabela': 'renova/tabela-de-avaliacao.jpg',
    'imagem_checklist': 'renova/checklist-de-avaliacao.jpg',
    'imagem_passo_a_passo': 'renova/passo-a-passo-da-avaliacao.jpg',
}
ROTULOS_DAS_IMAGENS = [
    ('imagem_tabela', 'Tabela de avaliação'),
    ('imagem_checklist', 'Checklist'),
    ('imagem_passo_a_passo', 'Passo a passo'),
]
TIPOS_DE_IMAGEM = ('image/jpeg', 'image/png', 'image/webp')
IMAGEM_MAX_BYTES = 8 * 1024 * 1024
# Formato que o Pillow reconhece -> extensão com que o arquivo é gravado
FORMATOS_DE_IMAGEM = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}


# ─── Apoio ───────────────────────────────────────────────────────────────────

def _estatico(caminho):
    """Caminho de um estático sem derrubar a tela se o manifesto ainda não o tiver."""
    try:
        return static(caminho)
    except Exception:                                           # noqa: BLE001 — collectstatic pendente
        logger.warning('Estático fora do manifesto: %s', caminho)
        return f'{settings.STATIC_URL}{caminho}'


def _imagem(cfg, campo):
    arquivo = getattr(cfg, campo)
    if arquivo:
        try:
            return arquivo.url
        except Exception as exc:                                # noqa: BLE001
            logger.warning('Imagem %s do Renova indisponível: %s', campo, exc)
    return _estatico(IMAGENS_PADRAO[campo])


def _normal(texto):
    base = unicodedata.normalize('NFKD', str(texto or ''))
    return ' '.join(''.join(c for c in base if not unicodedata.combining(c)).lower().split())


def _aguardando():
    return (Renova.objects.filter(aprovacao=Renova.APROVADA, recebimento=Renova.PENDENTE)
            .exclude(parecer=checklist.NAO_APROVADO).count())


def _contexto(request, cfg, aba, **extra):
    user = request.user
    receber = pode_receber(user, cfg)
    gerente = e_gerente(user)
    ctx = {
        'aba': aba,
        'cfg': cfg,
        'rn_pode_fazer': pode_fazer(user, cfg),
        'rn_pode_receber': receber,
        'rn_gerente': gerente,
        'rn_pode_ver_gestao': pode_ver_gestao(user, cfg),
        'rn_superadmin': e_superadmin(user),
        'rn_pode_excluir': pode_excluir(user),
        'renova_aguardando': _aguardando() if receber else 0,
        'rn_a_aprovar': (Renova.objects.filter(aprovacao=Renova.AGUARDANDO_GERENTE, loja_id=user.sector_id).count()
                         if gerente and user.sector_id else 0),
    }
    ctx.update(extra)
    return ctx


def _sem_acesso(request):
    messages.error(request, 'O Vini Renova não está liberado para você.')
    return redirect('dashboard')


def _setores():
    """Setores para a loja de origem: as lojas primeiro, os demais depois."""
    from users.models import Sector

    todos = list(Sector.objects.order_by('name'))
    lojas = [s for s in todos if 'loja' in s.name.lower()]
    return lojas, [s for s in todos if s not in lojas]


def _precos_para_tela(cfg):
    return [{'id': p.pk, 'marca': p.marca, 'modelo': p.modelo, 'armazenamento': p.armazenamento,
             'valores': {letra: float(valor) for letra, _, valor in p.valores(cfg)}}
            for p in PrecoAparelho.objects.filter(ativo=True, marca='APPLE').order_by('ordem', 'id')]


def _modelos_da_tabela():
    """Os modelos da tabela de avaliação ativa, na ordem dela — a lista do formulário."""
    modelos = []
    for modelo in (PrecoAparelho.objects.filter(ativo=True, marca='APPLE').order_by('ordem', 'id')
                   .values_list('modelo', flat=True)):
        if modelo not in modelos:
            modelos.append(modelo)
    return modelos


def _voltar_seguro(valor, padrao):
    return valor if isinstance(valor, str) and valor.startswith('/renova/') else padrao


# ─── Avaliações ──────────────────────────────────────────────────────────────

@login_required
def inicio(request):
    cfg = configuracao()
    user = request.user
    if not pode_ver_modulo(user, cfg):
        return _sem_acesso(request)

    base = renovas_visiveis(user, cfg)
    filtros = {
        'q': (request.GET.get('q') or '').strip()[:60],
        'recebimento': request.GET.get('recebimento') or '',
        'aprovacao': request.GET.get('aprovacao') or '',
        'loja': request.GET.get('loja') or '',
        'mes': request.GET.get('mes') or '',
    }
    qs = base
    if filtros['q']:
        termo = filtros['q']
        condicao = (Q(imei1__icontains=termo) | Q(imei2__icontains=termo) | Q(modelo__icontains=termo)
                    | Q(vendedor_nome__icontains=termo) | Q(cor__icontains=termo) | Q(numero_venda__icontains=termo))
        codigo = re.fullmatch(r'(?i)rn-?0*(\d+)', termo.replace(' ', ''))
        if codigo:
            condicao |= Q(pk=int(codigo.group(1)))
        qs = qs.filter(condicao)
    if filtros['recebimento'] == 'AGUARDANDO':
        qs = qs.filter(aprovacao=Renova.APROVADA, recebimento=Renova.PENDENTE).exclude(parecer=checklist.NAO_APROVADO)
    elif filtros['recebimento'] in dict(Renova.RECEBIMENTOS):
        qs = qs.filter(recebimento=filtros['recebimento'])
    if filtros['aprovacao'] in dict(Renova.APROVACOES):
        qs = qs.filter(aprovacao=filtros['aprovacao'])
    if filtros['loja'].isdigit():
        qs = qs.filter(loja_id=int(filtros['loja']))
    ano_mes = re.fullmatch(r'(\d{4})-(\d{2})', filtros['mes'])
    if ano_mes:
        qs = qs.filter(criado_em__year=int(ano_mes.group(1)), criado_em__month=int(ano_mes.group(2)))

    hoje = timezone.localdate()
    kpis = base.aggregate(
        total=Count('id'),
        no_mes=Count('id', filter=Q(criado_em__year=hoje.year, criado_em__month=hoje.month)),
        a_aprovar=Count('id', filter=Q(aprovacao=Renova.AGUARDANDO_GERENTE)),
        aguardando=Count('id', filter=Q(aprovacao=Renova.APROVADA, recebimento=Renova.PENDENTE)
                         & ~Q(parecer=checklist.NAO_APROVADO)),
        chegaram=Count('id', filter=Q(recebimento=Renova.CHEGOU)),
        nao_chegaram=Count('id', filter=Q(recebimento=Renova.NAO_CHEGOU)),
    )
    pagina = Paginator(qs.order_by('-criado_em'), POR_PAGINA).get_page(request.GET.get('pagina'))
    lojas_filtro = []
    if pode_receber(user, cfg) or pode_ver_gestao(user, cfg):
        from users.models import Sector

        lojas_filtro = Sector.objects.filter(renovas__isnull=False).distinct().order_by('name')
    return render(request, 'renova/inicio.html', _contexto(
        request, cfg, 'inicio', pagina=pagina, kpis=kpis, filtros=filtros, lojas_filtro=lojas_filtro,
        querystring=urlencode({k: v for k, v in filtros.items() if v}), aprovacoes=Renova.APROVACOES,
        pode_aprovar_ids={r.pk for r in pagina if r.aguardando_aprovacao and pode_aprovar(user, r)},
        voltar=request.get_full_path()))


# Etapas da tela de nova avaliação (a ordem em que o vendedor preenche) e onde cada erro aparece.
ETAPAS = [
    (1, 'Aparelho', 'fa-solid fa-mobile-screen-button'),
    (2, 'Funcionalidades', 'fa-solid fa-power-off'),
    (3, 'Estética', 'fa-solid fa-wand-magic-sparkles'),
    (4, 'Valor', 'fa-solid fa-calculator'),
    (5, 'Fotos', 'fa-solid fa-camera'),
    (6, 'Responsável', 'fa-solid fa-signature'),
    (7, 'Concluir', 'fa-solid fa-list-check'),
]
ETAPA_DO_ERRO = {
    'modelo': 1, 'armazenamento': 1, 'imei1': 1, 'imei2': 1, 'data_avaliacao': 1, 'loja': 1, 'saude_bateria': 1,
    'funcionalidades': 2, 'estetica': 3, 'observacoes': 4, 'cliente_segue': 4, 'fotos': 5,
    'vendedor_nome': 6, 'assinatura': 6, 'numero_venda': 6, 'itens_obrigatorios': 7, 'categoria': 7,
}


def _secoes(post):
    """Os itens do checklist com o que já veio marcado (para reabrir a tela preenchida)."""
    obrigatorios = [(chave, titulo, descricao, icone, post.get(f'obrig_{chave}') == 'on')
                    for chave, titulo, descricao, icone in checklist.ITENS_OBRIGATORIOS]
    funcionalidades = [(chave, titulo, descricao, icone, post.get(f'func_{chave}', ''))
                       for chave, titulo, descricao, icone in checklist.FUNCIONALIDADES]
    estetica = [(chave, titulo, descricao, icone, post.get(f'est_{chave}', ''))
                for chave, titulo, descricao, icone in checklist.ESTETICA]
    return obrigatorios, funcionalidades, estetica


@login_required
def nova(request):
    cfg = configuracao()
    user = request.user
    if not pode_fazer(user, cfg):
        if pode_ver_modulo(user, cfg):
            messages.error(request, 'Só quem o SUPERADMIN habilitou pode fazer a avaliação.')
            return redirect('renova:inicio')
        return _sem_acesso(request)

    lojas, outros_setores = _setores()
    sem_categoria = cfg.categoria_id is None or not cfg.categoria.is_active
    erros = {}
    if request.method == 'POST':
        dados, erros = ler_checklist(
            request.POST, lojas={str(s.pk): s for s in lojas + outros_setores},
            precos={str(p.pk): p for p in PrecoAparelho.objects.filter(ativo=True)}, cfg=cfg)
        fotos, erros_fotos = ler_fotos(request.FILES)
        erros.update(erros_fotos)
        if sem_categoria:
            erros['categoria'] = ('A categoria do chamado do Renova não está configurada. '
                                  'Peça ao SUPERADMIN para configurar antes de concluir.')
        if not erros:
            try:
                with transaction.atomic():
                    renova = Renova.objects.create(criado_por=user, **dados)
                    gravar_fotos(renova, fotos)
            except Exception:                                   # noqa: BLE001 — armazenamento fora do ar
                logger.exception('Renova não foi salva (fotos no armazenamento ou gravação no banco)')
                erros['fotos'] = ('A avaliação não foi salva: as fotos não subiram (o armazenamento não respondeu). '
                                  'Nada foi gravado — tente enviar de novo em instantes.')
        if not erros:
            gerentes = avisar_gerentes(renova, user)
            limpar_cache_do_menu([g.pk for g in gerentes])
            if gerentes:
                nomes = ', '.join(g.full_name or g.get_username() for g in gerentes)
                messages.success(request, f'Avaliação {renova.codigo} enviada para o gerente da loja aprovar ({nomes}). '
                                          'O chamado e a etiqueta saem depois da aprovação.')
            else:
                messages.warning(request, f'Avaliação {renova.codigo} salva, mas não há gerente do grupo GERENTES na loja '
                                          f'{renova.loja.name if renova.loja else ""} para aprovar. Avise o SUPERADMIN.')
            return redirect('renova:detalhe', pk=renova.pk)
        valores = request.POST
        if request.FILES:
            # O navegador não devolve arquivos escolhidos: com erro, as fotos precisam ser escolhidas de novo.
            erros.setdefault('fotos_de_novo', 'Por segurança do navegador, escolha as fotos de novo antes de enviar.')
    else:
        hoje = timezone.localdate().isoformat()
        valores = {'data_avaliacao': hoje, 'data_responsavel': hoje, 'vendedor_nome': user.full_name,
                   'loja': str(user.sector_id or '')}

    obrigatorios, funcionalidades, estetica = _secoes(request.POST if request.method == 'POST' else {})
    return render(request, 'renova/nova.html', _contexto(
        request, cfg, 'nova', valores=valores, erros=erros, lojas=lojas, outros_setores=outros_setores,
        padroes=checklist.PADROES, regras_padrao=regras_para_tela(), modelos=_modelos_da_tabela(),
        itens_obrigatorios=obrigatorios, funcionalidades=funcionalidades, estetica=estetica,
        opcoes_func=checklist.OPCOES_FUNCIONALIDADE, opcoes_est=checklist.OPCOES_ESTETICA,
        precos_json=_precos_para_tela(cfg),
        imagem_checklist=_imagem(cfg, 'imagem_checklist'), sem_categoria=sem_categoria,
        etapas=ETAPAS, etapa_inicial=min((ETAPA_DO_ERRO.get(c, 7) for c in erros if c != 'fotos_de_novo'), default=1),
        fotos=checklist.FOTOS, fotos_avaria_max=checklist.FOTOS_AVARIA_MAX))


def _renova_visivel(request, cfg, pk):
    """A avaliação, se existe e a pessoa pode vê-la; senão None (quem chama responde com _nao_abre)."""
    renova = (Renova.objects.select_related('loja', 'criado_por', 'chamado', 'chamado__category', 'chamado__sector',
                                            'recebido_por', 'preco_tabela')
              .filter(pk=pk).first())
    return renova if renova is not None and pode_ver(request.user, renova, cfg) else None


def _nao_abre(request, cfg, pk):
    """Avaliação que não abre para a pessoa.

    A excluída pelo SUPERADMIN continua linkada nos avisos do sino e na descrição
    do chamado: quem clica lê que ela foi excluída, em vez de cair numa página de erro.
    """
    if Renova.objects.filter(pk=pk).exists():
        messages.error(request, 'Essa avaliação não está entre as que você pode ver.')
    else:
        messages.error(request, f'A avaliação {Renova(pk=pk).codigo} não está no portal — pode ter sido excluída '
                                'pelo SUPERADMIN.')
    return redirect('renova:inicio' if pode_ver_modulo(request.user, cfg) else 'dashboard')


def _pode_editar_venda(user, renova):
    """Reprovada não tem venda: o cliente ficou com o aparelho."""
    return not renova.reprovada and pode_informar_venda(user, renova)


@login_required
def detalhe(request, pk):
    cfg = configuracao()
    renova = _renova_visivel(request, cfg, pk)
    if renova is None:
        return _nao_abre(request, cfg, pk)
    user = request.user
    return render(request, 'renova/detalhe.html', _contexto(
        request, cfg, 'inicio', renova=renova,
        pode_decidir=renova.aguardando_aprovacao and pode_aprovar(user, renova),
        gerentes=gerentes_da_loja(renova) if renova.aguardando_aprovacao else [],
        pode_marcar=renova.aprovada and pode_receber(user, cfg),
        pode_reabrir_chamado=(renova.aprovada and renova.chamado_id is None
                              and (renova.criado_por_id == user.pk or e_superadmin(user) or pode_aprovar(user, renova))),
        pode_editar_venda=_pode_editar_venda(user, renova), numero_venda_max=NUMERO_VENDA_MAX,
        voltar=request.get_full_path(), setor=setor_recebedor(cfg)))


@login_required
def etiqueta(request, pk):
    cfg = configuracao()
    renova = _renova_visivel(request, cfg, pk)
    if renova is None:
        return _nao_abre(request, cfg, pk)
    if not renova.aprovada:
        messages.info(request, f'A etiqueta de {renova.codigo} sai depois que o gerente da loja aprova a troca.')
        return redirect('renova:detalhe', pk=renova.pk)
    return render(request, 'renova/etiqueta.html', _contexto(
        request, cfg, 'inicio', renova=renova, novo=request.GET.get('novo') == '1',
        logo_url=_estatico('images/logo.png'),
        pode_editar_venda=_pode_editar_venda(request.user, renova), numero_venda_max=NUMERO_VENDA_MAX,
        voltar=reverse('renova:etiqueta', args=[renova.pk])))


@login_required
@require_POST
def informar_venda(request, pk):
    """Informa, corrige ou apaga o nº da venda — a venda costuma fechar depois da avaliação."""
    cfg = configuracao()
    renova = get_object_or_404(Renova.objects.select_related('loja'), pk=pk)
    destino = _voltar_seguro(request.POST.get('voltar'), reverse('renova:detalhe', args=[renova.pk]))
    if not pode_informar_venda(request.user, renova):
        messages.error(request, 'Só quem fez a avaliação, o gerente da loja ou o SUPERADMIN informa o nº da venda.')
        return redirect(destino if pode_ver(request.user, renova, cfg) else 'renova:inicio')
    if renova.reprovada:
        messages.error(request, f'{renova.codigo} foi reprovada: o cliente ficou com o aparelho, então não há venda.')
        return redirect(destino)
    try:
        numero = ler_numero_venda(request.POST.get('numero_venda'))
    except ValueError:
        messages.error(request, f'O nº da venda vai até {NUMERO_VENDA_MAX} caracteres — ficou como estava.')
        return redirect(destino)
    if numero == renova.numero_venda:
        messages.info(request, f'{renova.codigo}: o nº da venda já estava assim.')
        return redirect(destino)
    renova.numero_venda = numero
    renova.save(update_fields=['numero_venda', 'atualizado_em'])
    if numero:
        quando_sai = 'ele já sai na etiqueta' if renova.aprovada else 'ele sai na etiqueta quando o gerente aprovar'
        messages.success(request, f'{renova.codigo}: nº da venda {numero} salvo — {quando_sai}.')
    else:
        messages.success(request, f'{renova.codigo}: nº da venda apagado.')
    return redirect(destino)


@login_required
@require_http_methods(['GET', 'POST'])
def excluir(request, pk):
    """Exclui uma avaliação feita (só o SUPERADMIN).

    GET mostra o que some junto e o que fica — nunca exclui. POST exclui, com o
    código da avaliação digitado (como na exclusão de usuário): o botão fica na
    lista, ao lado de outras avaliações, e não tem volta.
    """
    cfg = configuracao()
    user = request.user
    if not pode_excluir(user):
        messages.error(request, 'Só o SUPERADMIN exclui uma avaliação.')
        return redirect('renova:inicio' if pode_ver_modulo(user, cfg) else 'dashboard')
    renova = (Renova.objects.select_related('loja', 'criado_por', 'aprovacao_por', 'recebido_por', 'chamado')
              .filter(pk=pk).first())
    if renova is None:
        return _nao_abre(request, cfg, pk)

    tela_dela = reverse('renova:detalhe', args=[renova.pk])
    voltar = _voltar_seguro(request.POST.get('voltar') or request.GET.get('voltar'), tela_dela)
    if request.method == 'GET':
        return render(request, 'renova/excluir.html', _contexto(request, cfg, 'inicio', renova=renova, voltar=voltar))

    codigo = renova.codigo
    if re.sub(r'\s+', '', request.POST.get('confirmacao') or '').upper() != codigo:
        messages.error(request, f'Nada foi excluído: para confirmar, digite o código {codigo} exatamente como aparece.')
        return redirect(f"{reverse('renova:excluir', args=[renova.pk])}?{urlencode({'voltar': voltar})}")

    resumo = resumo_da_avaliacao(renova)
    gerentes = gerentes_da_loja(renova) if renova.aguardando_aprovacao else []
    chamado_id = excluir_avaliacao(renova, user)
    log_action(user, 'ADMIN_ACTION', f'Excluiu o Vini Renova {codigo} ({resumo}).', request)
    limpar_cache_do_menu([user.pk] + [g.pk for g in gerentes])
    if chamado_id:
        messages.success(request, f'{codigo} excluída. O chamado #{chamado_id} continua, com a exclusão anotada '
                                  'no histórico dele.')
    else:
        messages.success(request, f'{codigo} excluída.')
    # Voltar para a tela dela (ou a etiqueta) cairia no aviso de "não existe mais".
    return redirect(voltar if not voltar.startswith(tela_dela) else 'renova:inicio')


@login_required
@require_POST
def recebimento(request, pk):
    cfg = configuracao()
    renova = get_object_or_404(Renova, pk=pk)
    destino = _voltar_seguro(request.POST.get('voltar'), reverse('renova:detalhe', args=[renova.pk]))
    setor = setor_recebedor(cfg)
    if not pode_receber(request.user, cfg):
        messages.error(request, f'Só quem é do setor {setor.name if setor else "que recebe"} marca a chegada.')
        return redirect(destino)
    situacao = request.POST.get('situacao')
    if situacao not in dict(Renova.RECEBIMENTOS):
        messages.error(request, 'Diga se o aparelho chegou ou não.')
        return redirect(destino)
    if not renova.recebe_aparelho and situacao != Renova.PENDENTE:
        if renova.aguardando_aprovacao:
            messages.error(request, f'{renova.codigo} ainda espera a aprovação do gerente: nada foi enviado.')
        else:
            messages.error(request, f'{renova.codigo} foi reprovada: o cliente ficou com o aparelho, então não há recebimento.')
        return redirect(destino)
    registrar_recebimento(renova, request.user, situacao, request.POST.get('observacao', ''))
    limpar_cache_do_menu([request.user.pk])
    messages.success(request, f'{renova.codigo}: marcado como "{renova.get_recebimento_display()}".')
    return redirect(destino)


@login_required
@require_POST
def abrir_chamado_de_novo(request, pk):
    cfg = configuracao()
    renova = get_object_or_404(Renova, pk=pk)
    if not renova.aprovada:
        messages.error(request, f'O chamado de {renova.codigo} abre na aprovação do gerente.')
    elif not (renova.criado_por_id == request.user.pk or e_superadmin(request.user) or pode_aprovar(request.user, renova)):
        messages.error(request, 'Só quem fez a avaliação, o gerente da loja ou o SUPERADMIN abre o chamado dela.')
    elif renova.chamado_id:
        messages.info(request, f'O chamado #{renova.chamado_id} já está aberto.')
    else:
        ticket = None
        try:
            ticket = abrir_chamado(renova, request.user)
        except Exception:                                       # noqa: BLE001
            logger.exception('Chamado do Renova %s não abriu (nova tentativa)', renova.pk)
        if ticket:
            messages.success(request, f'Chamado #{ticket.pk} aberto.')
        elif cfg.categoria_id is None:
            messages.error(request, 'A categoria do chamado não está configurada.')
        else:
            messages.error(request, 'O chamado não abriu. Tente de novo em instantes.')
    return redirect('renova:detalhe', pk=renova.pk)


@login_required
@require_POST
def aprovacao(request, pk):
    cfg = configuracao()
    renova = get_object_or_404(Renova.objects.select_related('loja', 'criado_por'), pk=pk)
    if not pode_aprovar(request.user, renova):
        messages.error(request, 'Só o gerente da loja (grupo GERENTES) ou o SUPERADMIN aprova esta troca.')
        return redirect('renova:detalhe' if pode_ver(request.user, renova, cfg) else 'renova:inicio',
                        **({'pk': pk} if pode_ver(request.user, renova, cfg) else {}))
    decisao = request.POST.get('decisao')
    if decisao not in ('aprovar', 'reprovar'):
        messages.error(request, 'Escolha aprovar ou reprovar.')
        return redirect('renova:detalhe', pk=pk)
    try:
        renova, chamado = decidir(renova, request.user, decisao == 'aprovar', request.POST.get('observacao', ''))
    except DecisaoInvalida as exc:
        messages.error(request, str(exc))
        return redirect('renova:detalhe', pk=pk)
    limpar_cache_do_menu([request.user.pk] + [g.pk for g in gerentes_da_loja(renova)])
    if renova.aprovada and chamado:
        messages.success(request, f'{renova.codigo} aprovada: chamado #{chamado.pk} aberto e etiqueta liberada.')
    elif renova.aprovada:
        messages.warning(request, f'{renova.codigo} aprovada, mas o chamado não abriu. Abra de novo por esta tela.')
    else:
        messages.success(request, f'{renova.codigo} reprovada: o vendedor foi avisado e o cliente fica com o aparelho.')
    return redirect('renova:detalhe', pk=pk)


# ─── Quadro de gestão (financeiro) ───────────────────────────────────────────

FILTROS_SITUACAO = {
    'APROVACAO': Q(aprovacao=Renova.AGUARDANDO_GERENTE),
    'A_CAMINHO': Q(aprovacao=Renova.APROVADA, recebimento=Renova.PENDENTE) & ~Q(parecer=checklist.NAO_APROVADO),
    'CHEGOU': Q(aprovacao=Renova.APROVADA, recebimento=Renova.CHEGOU) & ~Q(parecer=checklist.NAO_APROVADO),
    'NAO_CHEGOU': Q(aprovacao=Renova.APROVADA, recebimento=Renova.NAO_CHEGOU) & ~Q(parecer=checklist.NAO_APROVADO),
    'REPROVADA': Q(aprovacao=Renova.REPROVADA) | Q(parecer=checklist.NAO_APROVADO),
}


@login_required
def gestao(request):
    cfg = configuracao()
    user = request.user
    if not pode_ver_gestao(user, cfg):
        messages.error(request, 'O quadro de gestão do Renova é do financeiro — peça acesso ao SUPERADMIN.')
        return redirect('renova:inicio' if pode_ver_modulo(user, cfg) else 'dashboard')

    filtros = {
        'q': (request.GET.get('q') or '').strip()[:60],
        'situacao': request.GET.get('situacao') or '',
        'loja': request.GET.get('loja') or '',
        'de': request.GET.get('de') or '',
        'ate': request.GET.get('ate') or '',
    }
    base = Renova.objects.select_related('loja', 'criado_por', 'aprovacao_por', 'recebido_por', 'chamado')
    if filtros['loja'].isdigit():
        base = base.filter(loja_id=int(filtros['loja']))
    for campo, lookup in (('de', 'criado_em__date__gte'), ('ate', 'criado_em__date__lte')):
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', filtros[campo]):
            base = base.filter(**{lookup: filtros[campo]})
    if filtros['q']:
        termo = filtros['q']
        condicao = (Q(imei1__icontains=termo) | Q(modelo__icontains=termo) | Q(vendedor_nome__icontains=termo)
                    | Q(numero_venda__icontains=termo))
        codigo = re.fullmatch(r'(?i)rn-?0*(\d+)', termo.replace(' ', ''))
        if codigo:
            condicao |= Q(pk=int(codigo.group(1)))
        base = base.filter(condicao)

    # Resumo do período e da loja filtrados — independe da situação escolhida.
    resumo = []
    for codigo, rotulo in Renova.SITUACOES:
        dados = base.filter(FILTROS_SITUACAO[codigo]).aggregate(n=Count('id'), valor=Sum('valor_estimado'))
        resumo.append({'codigo': codigo, 'rotulo': rotulo, 'quantidade': dados['n'], 'valor': dados['valor'] or 0})

    qs = base.filter(FILTROS_SITUACAO[filtros['situacao']]) if filtros['situacao'] in FILTROS_SITUACAO else base
    qs = qs.order_by('-criado_em')

    if request.GET.get('formato') == 'csv':
        return _gestao_csv(qs)

    from users.models import Sector

    pagina = Paginator(qs, 50).get_page(request.GET.get('pagina'))
    return render(request, 'renova/gestao.html', _contexto(
        request, cfg, 'gestao', pagina=pagina, resumo=resumo, filtros=filtros, situacoes=Renova.SITUACOES,
        lojas_filtro=Sector.objects.filter(renovas__isnull=False).distinct().order_by('name'),
        querystring=urlencode({k: v for k, v in filtros.items() if v}),
        base_qs=urlencode({k: v for k, v in filtros.items() if v and k != 'situacao'})))


def _gestao_csv(qs):
    resposta = HttpResponse(content_type='text/csv; charset=utf-8')
    resposta['Content-Disposition'] = f'attachment; filename="renova-gestao-{timezone.localdate():%Y-%m-%d}.csv"'
    resposta.write('\ufeff')                     # BOM: o Excel abre os acentos certos
    escrita = csv.writer(resposta, delimiter=';')
    escrita.writerow(['Código', 'Avaliado em', 'Loja', 'Vendedor', 'Aparelho', 'IMEI 1', 'Padrão', 'Valor (R$)',
                      'Situação', 'Aprovação por', 'Aprovação em', 'Chamado', 'Nº da venda', 'Recebimento por',
                      'Recebimento em', 'Obs. do recebimento'])

    def quando(valor):
        return timezone.localtime(valor).strftime('%d/%m/%Y %H:%M') if valor else ''

    for r in qs.iterator():
        escrita.writerow([
            r.codigo, quando(r.criado_em), r.loja.name if r.loja else '', r.vendedor_nome, r.aparelho, r.imei1,
            r.padrao, f'{r.valor_estimado:.2f}'.replace('.', ',') if r.valor_estimado is not None else '',
            r.situacao[1], (r.aprovacao_por.full_name or r.aprovacao_por.get_username()) if r.aprovacao_por else '',
            quando(r.aprovacao_em), f'#{r.chamado_id}' if r.chamado_id else '', r.numero_venda,
            (r.recebido_por.full_name or r.recebido_por.get_username()) if r.recebido_por else '',
            quando(r.recebido_em), r.recebimento_obs,
        ])
    return resposta


# ─── Materiais ───────────────────────────────────────────────────────────────

@login_required
def tabela(request):
    cfg = configuracao()
    if not pode_ver_modulo(request.user, cfg):
        return _sem_acesso(request)
    descontos = cfg.descontos()
    padroes = [dict(p, desconto=descontos.get(p['letra'], 0)) for p in conteudo.PADROES_AVALIACAO]
    precos, atualizado_em = [], None
    for p in PrecoAparelho.objects.filter(ativo=True).order_by('marca', 'ordem', 'id'):
        precos.append({'modelo': p.modelo, 'armazenamento': p.armazenamento, 'valores': p.valores(cfg),
                       'busca': _normal(f'{p.modelo} {p.armazenamento}')})
        atualizado_em = max(atualizado_em, p.atualizado_em) if atualizado_em else p.atualizado_em
    return render(request, 'renova/tabela.html', _contexto(
        request, cfg, 'tabela', padroes=padroes, precos=precos, atualizado_em=atualizado_em,
        imagem_url=_imagem(cfg, 'imagem_tabela')))


@login_required
def passo_a_passo(request):
    cfg = configuracao()
    if not pode_ver_modulo(request.user, cfg):
        return _sem_acesso(request)
    return render(request, 'renova/passo_a_passo.html', _contexto(
        request, cfg, 'passo', passos=conteudo.PASSO_A_PASSO, imagem_url=_imagem(cfg, 'imagem_passo_a_passo')))


# ─── Configuração (SUPERADMIN) ───────────────────────────────────────────────

@login_required
def configurar(request):
    cfg = configuracao()
    user = request.user
    if not e_superadmin(user):
        messages.error(request, 'Só o SUPERADMIN configura o Vini Renova.')
        return redirect('renova:inicio' if pode_ver_modulo(user, cfg) else 'dashboard')

    if request.method == 'POST':
        secao = request.POST.get('secao')
        if secao == 'acesso':
            _salvar_acesso(request, cfg)
        elif secao == 'materiais':
            _salvar_materiais(request, cfg)
        elif secao == 'tabela':
            _salvar_tabela(request)
        return redirect(f"{reverse('renova:configuracao')}#{secao or 'acesso'}")

    from tickets.models import Category

    setor = setor_recebedor(cfg)
    return render(request, 'renova/configuracao.html', _contexto(
        request, cfg, 'configuracao',
        pessoas=User.objects.filter(is_active=True).select_related('sector').order_by('first_name', 'last_name'),
        habilitados=set(cfg.habilitados.values_list('pk', flat=True)),
        financeiro=set(cfg.financeiro.values_list('pk', flat=True)),
        categorias=Category.objects.filter(is_active=True).select_related('sector').order_by('sector__name', 'name'),
        setor=setor,
        membros_setor=(User.objects.filter(Q(sector=setor) | Q(sectors=setor), is_active=True).distinct().count()
                       if setor else 0),
        descontos=[('desconto_b', 'B', cfg.desconto_b), ('desconto_c', 'C', cfg.desconto_c),
                   ('desconto_d', 'D', cfg.desconto_d)],
        imagens=[(campo, rotulo, _imagem(cfg, campo), bool(getattr(cfg, campo)))
                 for campo, rotulo in ROTULOS_DAS_IMAGENS],
        precos=PrecoAparelho.objects.order_by('marca', 'ordem', 'id')))


def _salvar_acesso(request, cfg):
    from tickets.models import Category

    antes = set(cfg.habilitados.values_list('pk', flat=True))
    ids = {int(x) for x in request.POST.getlist('habilitados') if str(x).isdigit()}
    habilitados = list(User.objects.filter(pk__in=ids, is_active=True))
    cfg.habilitados.set(habilitados)
    antes_fin = set(cfg.financeiro.values_list('pk', flat=True))
    ids_fin = {int(x) for x in request.POST.getlist('financeiro') if str(x).isdigit()}
    financeiro = list(User.objects.filter(pk__in=ids_fin, is_active=True))
    cfg.financeiro.set(financeiro)

    categoria_id = request.POST.get('categoria') or ''
    cfg.categoria = (Category.objects.filter(pk=int(categoria_id), is_active=True).first()
                     if categoria_id.isdigit() else None)
    for campo, letra in (('desconto_b', 'B'), ('desconto_c', 'C'), ('desconto_d', 'D')):
        texto = (request.POST.get(campo) or '').strip()
        if texto.isdigit() and int(texto) <= 100:
            setattr(cfg, campo, int(texto))
        else:
            messages.error(request, f'O desconto do padrão {letra} vai de 0 a 100% — ficou como estava.')
    cfg.atualizado_por = request.user
    cfg.save()
    limpar_cache_do_menu((antes ^ {u.pk for u in habilitados}) | (antes_fin ^ {u.pk for u in financeiro}))
    messages.success(request, f'Configuração salva: {len(habilitados)} pessoa(s) podem fazer Renova e '
                              f'{len(financeiro)} acompanham o quadro de gestão.')
    if cfg.categoria is None:
        messages.warning(request, 'Sem categoria de chamado, ninguém consegue concluir uma avaliação.')


def _imagem_conferida(arquivo):
    """Extensão do arquivo se o Pillow abrir como JPG, PNG ou WEBP; senão None.

    O tipo que o navegador manda é só uma declaração: sem abrir o arquivo, um HTML
    enviado como "image/png" iria para o armazenamento com a extensão dele.
    """
    try:
        arquivo.seek(0)
        with Image.open(arquivo) as imagem:
            formato = imagem.format
            imagem.verify()
    except Exception:                                            # noqa: BLE001 — qualquer falha: não é imagem
        return None
    finally:
        arquivo.seek(0)
    return FORMATOS_DE_IMAGEM.get(formato)


def _salvar_materiais(request, cfg):
    mudou = False
    for campo, rotulo in ROTULOS_DAS_IMAGENS:
        atual = getattr(cfg, campo)
        if request.POST.get(f'remover_{campo}') == 'on' and atual:
            try:
                atual.delete(save=False)
            except Exception as exc:                            # noqa: BLE001 — o registro volta ao padrão mesmo assim
                logger.warning('Imagem antiga do Renova não foi apagada: %s', exc)
            setattr(cfg, campo, '')
            mudou = True
        arquivo = request.FILES.get(campo)
        if not arquivo:
            continue
        if (arquivo.content_type or '') not in TIPOS_DE_IMAGEM:
            messages.error(request, f'{rotulo}: envie uma imagem JPG, PNG ou WEBP.')
            continue
        if arquivo.size > IMAGEM_MAX_BYTES:
            messages.error(request, f'{rotulo}: a imagem passa de 8 MB.')
            continue
        extensao = _imagem_conferida(arquivo)
        if extensao is None:
            messages.error(request, f'{rotulo}: o arquivo não abriu como imagem JPG, PNG ou WEBP.')
            continue
        arquivo.name = f'{campo.removeprefix("imagem_")}.{extensao}'
        setattr(cfg, campo, arquivo)
        mudou = True
    if mudou:
        cfg.atualizado_por = request.user
        cfg.save()
        messages.success(request, 'Materiais atualizados.')
    else:
        messages.info(request, 'Nada mudou nos materiais.')


def _armazenamento(texto):
    valor = re.sub(r'\s+', '', str(texto or '')).upper()[:10]
    return f'{valor}GB' if valor.isdigit() else valor


def _salvar_tabela(request):
    post = request.POST
    alterados = criados = excluidos = 0
    for pid in post.getlist('preco_id'):
        preco = PrecoAparelho.objects.filter(pk=int(pid)).first() if str(pid).isdigit() else None
        if preco is None:
            continue
        if post.get(f'excluir_{pid}') == 'on':
            preco.delete()
            excluidos += 1
            continue
        modelo = ' '.join(str(post.get(f'modelo_{pid}') or '').split())[:80]
        armazenamento = _armazenamento(post.get(f'armazenamento_{pid}'))
        try:
            valor = ler_valor(post.get(f'valor_{pid}'))
        except ValueError:
            valor = None
        if not modelo or not armazenamento or valor is None:
            messages.error(request, f'{preco}: preencha modelo, armazenamento e valor — ficou como estava.')
            continue
        ordem = int(post.get(f'ordem_{pid}')) if str(post.get(f'ordem_{pid}') or '').isdigit() else preco.ordem
        ativo = post.get(f'ativo_{pid}') == 'on'
        novo = (modelo, armazenamento, valor, ordem, ativo)
        if novo == (preco.modelo, preco.armazenamento, preco.valor_excelente, preco.ordem, preco.ativo):
            continue
        preco.modelo, preco.armazenamento, preco.valor_excelente, preco.ordem, preco.ativo = novo
        try:
            with transaction.atomic():
                preco.save()
            alterados += 1
        except IntegrityError:
            messages.error(request, f'{modelo} {armazenamento} já existe na tabela.')

    ordem = (PrecoAparelho.objects.order_by('-ordem').values_list('ordem', flat=True).first() or 0)
    for marca, modelo, armazenamento, valor_texto in zip(post.getlist('novo_marca'), post.getlist('novo_modelo'),
                                                         post.getlist('novo_armazenamento'),
                                                         post.getlist('novo_valor')):
        modelo = ' '.join(str(modelo or '').split())[:80]
        armazenamento = _armazenamento(armazenamento)
        if not (modelo or armazenamento or str(valor_texto or '').strip()):
            continue
        try:
            valor = ler_valor(valor_texto)
        except ValueError:
            valor = None
        if not modelo or not armazenamento or valor is None:
            messages.error(request, 'Linha nova incompleta: preencha modelo, armazenamento e valor.')
            continue
        ordem += 10
        try:
            with transaction.atomic():
                PrecoAparelho.objects.create(marca=marca if marca in dict(checklist.MARCAS) else 'APPLE',
                                             modelo=modelo, armazenamento=armazenamento,
                                             valor_excelente=valor, ordem=ordem)
            criados += 1
        except IntegrityError:
            messages.error(request, f'{modelo} {armazenamento} já existe na tabela.')
    messages.success(request, f'Tabela salva: {criados} nova(s), {alterados} alterada(s), {excluidos} excluída(s).')
