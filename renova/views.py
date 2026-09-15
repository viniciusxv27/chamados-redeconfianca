"""Telas do Vini Renova."""
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
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from PIL import Image

from . import checklist, conteudo
from .context_processors import limpar_cache_do_menu
from .models import PrecoAparelho, Renova
from .permissoes import (configuracao, e_superadmin, pode_fazer, pode_receber, pode_ver, pode_ver_modulo,
                         renovas_visiveis, setor_recebedor)
from .servicos import abrir_chamado, registrar_recebimento
from .validacao import ler_checklist, ler_valor

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
    return Renova.objects.filter(recebimento=Renova.PENDENTE).exclude(parecer=checklist.NAO_APROVADO).count()


def _contexto(request, cfg, aba, **extra):
    user = request.user
    receber = pode_receber(user, cfg)
    ctx = {
        'aba': aba,
        'cfg': cfg,
        'rn_pode_fazer': pode_fazer(user, cfg),
        'rn_pode_receber': receber,
        'rn_superadmin': e_superadmin(user),
        'renova_aguardando': _aguardando() if receber else 0,
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
            for p in PrecoAparelho.objects.filter(ativo=True).order_by('marca', 'ordem', 'id')]


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
        'parecer': request.GET.get('parecer') or '',
        'loja': request.GET.get('loja') or '',
        'mes': request.GET.get('mes') or '',
    }
    qs = base
    if filtros['q']:
        termo = filtros['q']
        condicao = (Q(imei1__icontains=termo) | Q(imei2__icontains=termo) | Q(modelo__icontains=termo)
                    | Q(numero_serie__icontains=termo) | Q(vendedor_nome__icontains=termo)
                    | Q(cor__icontains=termo))
        codigo = re.fullmatch(r'(?i)rn-?0*(\d+)', termo.replace(' ', ''))
        if codigo:
            condicao |= Q(pk=int(codigo.group(1)))
        qs = qs.filter(condicao)
    if filtros['recebimento'] == 'AGUARDANDO':
        qs = qs.filter(recebimento=Renova.PENDENTE).exclude(parecer=checklist.NAO_APROVADO)
    elif filtros['recebimento'] in dict(Renova.RECEBIMENTOS):
        qs = qs.filter(recebimento=filtros['recebimento'])
    if filtros['parecer'] in dict(checklist.PARECERES):
        qs = qs.filter(parecer=filtros['parecer'])
    if filtros['loja'].isdigit():
        qs = qs.filter(loja_id=int(filtros['loja']))
    ano_mes = re.fullmatch(r'(\d{4})-(\d{2})', filtros['mes'])
    if ano_mes:
        qs = qs.filter(criado_em__year=int(ano_mes.group(1)), criado_em__month=int(ano_mes.group(2)))

    hoje = timezone.localdate()
    kpis = base.aggregate(
        total=Count('id'),
        no_mes=Count('id', filter=Q(criado_em__year=hoje.year, criado_em__month=hoje.month)),
        aguardando=Count('id', filter=Q(recebimento=Renova.PENDENTE) & ~Q(parecer=checklist.NAO_APROVADO)),
        chegaram=Count('id', filter=Q(recebimento=Renova.CHEGOU)),
        nao_chegaram=Count('id', filter=Q(recebimento=Renova.NAO_CHEGOU)),
    )
    pagina = Paginator(qs.order_by('-criado_em'), POR_PAGINA).get_page(request.GET.get('pagina'))
    lojas_filtro = []
    if pode_receber(user, cfg):
        from users.models import Sector

        lojas_filtro = Sector.objects.filter(renovas__isnull=False).distinct().order_by('name')
    return render(request, 'renova/inicio.html', _contexto(
        request, cfg, 'inicio', pagina=pagina, kpis=kpis, filtros=filtros, lojas_filtro=lojas_filtro,
        querystring=urlencode({k: v for k, v in filtros.items() if v}), pareceres=checklist.PARECERES,
        voltar=request.get_full_path()))


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
            precos={str(p.pk): p for p in PrecoAparelho.objects.filter(ativo=True)})
        if sem_categoria:
            erros['categoria'] = ('A categoria do chamado do Renova não está configurada. '
                                  'Peça ao SUPERADMIN para configurar antes de concluir.')
        if not erros:
            with transaction.atomic():
                renova = Renova.objects.create(criado_por=user, **dados)
            ticket = None
            try:
                ticket = abrir_chamado(renova, user)
            except Exception:                                   # noqa: BLE001 — a avaliação já está salva
                logger.exception('Chamado do Renova %s não abriu', renova.pk)
            if ticket:
                messages.success(request, f'Avaliação {renova.codigo} concluída e chamado #{ticket.pk} aberto.')
            else:
                messages.warning(request, f'Avaliação {renova.codigo} salva, mas o chamado não abriu. '
                                          'Abra de novo pela tela da avaliação.')
            return redirect(f"{reverse('renova:etiqueta', args=[renova.pk])}?novo=1")
        valores = request.POST
    else:
        hoje = timezone.localdate().isoformat()
        valores = {'data_avaliacao': hoje, 'data_responsavel': hoje, 'vendedor_nome': user.full_name,
                   'loja': str(user.sector_id or '')}

    obrigatorios, funcionalidades, estetica = _secoes(request.POST if request.method == 'POST' else {})
    return render(request, 'renova/nova.html', _contexto(
        request, cfg, 'nova', valores=valores, erros=erros, lojas=lojas, outros_setores=outros_setores,
        marcas=checklist.MARCAS, armazenamentos=checklist.ARMAZENAMENTOS, padroes=checklist.PADROES,
        itens_obrigatorios=obrigatorios, funcionalidades=funcionalidades, estetica=estetica,
        opcoes_func=checklist.OPCOES_FUNCIONALIDADE, opcoes_est=checklist.OPCOES_ESTETICA,
        pareceres=checklist.PARECERES, precos_json=_precos_para_tela(cfg),
        imagem_checklist=_imagem(cfg, 'imagem_checklist'), sem_categoria=sem_categoria))


def _renova_visivel(request, cfg, pk):
    renova = get_object_or_404(
        Renova.objects.select_related('loja', 'criado_por', 'chamado', 'chamado__category', 'chamado__sector',
                                      'recebido_por', 'preco_tabela'), pk=pk)
    return renova if pode_ver(request.user, renova, cfg) else None


@login_required
def detalhe(request, pk):
    cfg = configuracao()
    renova = _renova_visivel(request, cfg, pk)
    if renova is None:
        messages.error(request, 'Essa avaliação não está entre as que você pode ver.')
        return redirect('renova:inicio' if pode_ver_modulo(request.user, cfg) else 'dashboard')
    user = request.user
    return render(request, 'renova/detalhe.html', _contexto(
        request, cfg, 'inicio', renova=renova, pode_marcar=pode_receber(user, cfg),
        pode_reabrir_chamado=renova.chamado_id is None and (renova.criado_por_id == user.pk or e_superadmin(user)),
        setor=setor_recebedor(cfg)))


@login_required
def etiqueta(request, pk):
    cfg = configuracao()
    renova = _renova_visivel(request, cfg, pk)
    if renova is None:
        messages.error(request, 'Essa avaliação não está entre as que você pode ver.')
        return redirect('renova:inicio' if pode_ver_modulo(request.user, cfg) else 'dashboard')
    return render(request, 'renova/etiqueta.html', _contexto(
        request, cfg, 'inicio', renova=renova, novo=request.GET.get('novo') == '1',
        logo_url=_estatico('images/logo.png')))


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
        messages.error(request, f'{renova.codigo} não foi aprovado: o cliente ficou com o aparelho, então não há recebimento.')
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
    if not (renova.criado_por_id == request.user.pk or e_superadmin(request.user)):
        messages.error(request, 'Só quem fez a avaliação (ou o SUPERADMIN) abre o chamado dela.')
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
    limpar_cache_do_menu(antes ^ {u.pk for u in habilitados})
    messages.success(request, f'Configuração salva: {len(habilitados)} pessoa(s) podem fazer Renova.')
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
