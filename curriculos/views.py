"""Banco de Talentos: importar currículo, procurar por vaga, marcar contratado."""
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from users.models import Sector

from .busca import procurar, separar_intencao
from .extrator import extrair
from .integracao import extrair_token, resultado_da_entrevista
from .models import ConfiguracaoCurriculos, Curriculo
from .permissions import e_superadmin, pode_usar

logger = logging.getLogger(__name__)

TAMANHO_MAXIMO = 15 * 1024 * 1024        # currículo é PDF de texto, não álbum


def _lugares_conhecidos():
    """Nomes que o portal já sabe que são lugar: os setores e as lojas.

    É o que faz "viana" ser entendido como lugar sem ninguém cadastrar cidade.
    """
    nomes = set()
    for nome in Sector.objects.values_list('name', flat=True):
        if not nome:
            continue
        nomes.add(nome)
        limpo = nome.replace('Loja ', '').strip()
        if limpo:
            nomes.add(limpo)
    return nomes


def _guarda(request):
    if not pode_usar(request.user):
        messages.error(request, 'O banco de talentos é restrito ao RH.')
        return redirect('dashboard')
    return None


@login_required
def banco(request):
    """A tela principal: a busca por vaga e o resultado."""
    barrado = _guarda(request)
    if barrado:
        return barrado

    consulta = (request.GET.get('q') or '').strip()
    lugares = _lugares_conhecidos()
    resultados = procurar(consulta, lugares_conhecidos=lugares)
    funcoes, locais = separar_intencao(consulta, lugares)

    total = Curriculo.objects.count()
    return render(request, 'curriculos/banco.html', {
        'consulta': consulta,
        'resultados': resultados,
        'funcoes': funcoes,
        'locais': locais,
        'total': total,
        'disponiveis': Curriculo.objects.filter(
            situacao__in=[Curriculo.Situacao.NOVO,
                          Curriculo.Situacao.ENTREVISTA]).count(),
        'contratados': Curriculo.objects.filter(
            situacao=Curriculo.Situacao.CONTRATADO).count(),
        'e_superadmin': e_superadmin(request.user),
        'situacoes': Curriculo.Situacao.choices,
    })


@login_required
@require_POST
def importar(request):
    """Recebe um ou vários PDFs e lê cada um."""
    barrado = _guarda(request)
    if barrado:
        return barrado

    arquivos = request.FILES.getlist('curriculos')
    if not arquivos:
        messages.error(request, 'Escolha ao menos um currículo em PDF.')
        return redirect('curriculos:banco')

    cidades = _lugares_conhecidos()
    criados = ilegiveis = recusados = 0
    for arquivo in arquivos:
        nome = (arquivo.name or '').lower()
        if not nome.endswith('.pdf'):
            recusados += 1
            continue
        if arquivo.size > TAMANHO_MAXIMO:
            recusados += 1
            continue

        try:
            arquivo.seek(0)
            dados = arquivo.read()
            arquivo.seek(0)
            lido = extrair(dados, cidades_conhecidas=cidades)
        except Exception as exc:                             # noqa: BLE001
            logger.warning('Currículo %s não pôde ser lido: %s', arquivo.name, exc)
            lido = {'texto': '', 'nome': '', 'endereco': '', 'cidade': '',
                    'bairro': '', 'experiencia': '', 'cargos': [],
                    'telefone': '', 'email': '', 'legivel': False}

        Curriculo.objects.create(
            arquivo=arquivo,
            nome_arquivo=(arquivo.name or '')[:255],
            nome=lido['nome'],
            endereco=lido['endereco'],
            cidade=lido['cidade'],
            bairro=lido['bairro'],
            telefone=lido['telefone'],
            email=lido['email'],
            experiencia=lido['experiencia'],
            cargos='\n'.join(lido['cargos']),
            texto=lido['texto'],
            enviado_por=request.user,
        )
        criados += 1
        if not lido['legivel']:
            ilegiveis += 1

    if criados:
        messages.success(request, f'{criados} currículo{"s" if criados > 1 else ""} '
                                  f'no banco de talentos.')
    if ilegiveis:
        # PDF escaneado é imagem: não há texto para ler. Dizer isso evita o RH
        # achar que o portal errou.
        messages.warning(
            request,
            f'{ilegiveis} não tinha texto para ler (PDF escaneado). '
            f'Estão no banco, mas precisam do nome e do endereço preenchidos na mão.')
    if recusados:
        messages.error(request, f'{recusados} arquivo(s) fora do formato: só PDF de até 15MB.')
    return redirect('curriculos:banco')


@login_required
def detalhe(request, curriculo_id):
    barrado = _guarda(request)
    if barrado:
        return barrado

    c = get_object_or_404(Curriculo.objects.select_related(
        'enviado_por', 'contratado_por', 'reuniao'), id=curriculo_id)
    return render(request, 'curriculos/detalhe.html', {
        'c': c,
        'entrevista': resultado_da_entrevista(c.entrevista_token),
        'situacoes': Curriculo.Situacao.choices,
        'e_superadmin': e_superadmin(request.user),
    })


@login_required
@require_POST
def atualizar(request, curriculo_id):
    """Corrige o que a leitura errou e muda a situação do candidato."""
    barrado = _guarda(request)
    if barrado:
        return barrado

    c = get_object_or_404(Curriculo, id=curriculo_id)

    for campo, limite in (('nome', 180), ('endereco', 300), ('cidade', 120),
                          ('bairro', 120), ('telefone', 40), ('email', 254)):
        if campo in request.POST:
            setattr(c, campo, (request.POST.get(campo) or '').strip()[:limite])
    for campo in ('experiencia', 'cargos', 'observacao'):
        if campo in request.POST:
            setattr(c, campo, (request.POST.get(campo) or '').strip())

    situacao = request.POST.get('situacao')
    if situacao in dict(Curriculo.Situacao.choices):
        virou_contratado = (situacao == Curriculo.Situacao.CONTRATADO
                            and c.situacao != Curriculo.Situacao.CONTRATADO)
        c.situacao = situacao
        if virou_contratado:
            c.contratado_em = timezone.localdate()
            c.contratado_por = request.user
        elif situacao != Curriculo.Situacao.CONTRATADO:
            c.contratado_em = None
            c.contratado_por = None

    if 'entrevista_token' in request.POST:
        c.entrevista_token = extrair_token(request.POST.get('entrevista_token'))
        if c.entrevista_token and not c.entrevista_em:
            c.entrevista_em = timezone.now()

    c.save()
    if c.situacao == Curriculo.Situacao.CONTRATADO:
        messages.success(request, f'{c.nome or "Candidato"} marcado como contratado — '
                                  f'sai das buscas por vaga.')
    else:
        messages.success(request, 'Currículo atualizado.')
    return redirect('curriculos:detalhe', curriculo_id=c.id)


@login_required
@require_POST
def excluir(request, curriculo_id):
    """Tira o currículo do banco. Só SUPERADMIN."""
    if not e_superadmin(request.user):
        messages.error(request, 'Apenas o SUPERADMIN exclui currículo do banco.')
        return redirect('curriculos:detalhe', curriculo_id=curriculo_id)

    c = get_object_or_404(Curriculo, id=curriculo_id)
    nome = c.nome or c.nome_arquivo
    c.delete()
    messages.success(request, f'Currículo de {nome} removido do banco.')
    return redirect('curriculos:banco')


@login_required
def configuracao(request):
    """Quem usa o banco e onde fica a IA do RH. Só SUPERADMIN."""
    if not e_superadmin(request.user):
        messages.error(request, 'Só o SUPERADMIN configura o banco de talentos.')
        return redirect('curriculos:banco')

    from communications.models import CommunicationGroup

    cfg = ConfiguracaoCurriculos.get()
    if request.method == 'POST':
        cfg.url_sistema_perfil = (request.POST.get('url_sistema_perfil') or '').strip()[:300]
        cfg.atualizado_por = request.user
        cfg.save()
        cfg.grupos.set(CommunicationGroup.objects.filter(
            id__in=[g for g in request.POST.getlist('grupos') if g.isdigit()]))
        messages.success(request, 'Configuração salva.')
        return redirect('curriculos:configuracao')

    return render(request, 'curriculos/configuracao.html', {
        'cfg': cfg,
        'grupos': CommunicationGroup.objects.order_by('name'),
        'marcados': set(cfg.grupos.values_list('id', flat=True)),
    })
