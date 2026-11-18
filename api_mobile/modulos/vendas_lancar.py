"""Lançar venda pelo app (/api/mobile/v1/m/vendas/lancar/…) — o MESMO caminho da tela /vendas/nova/.

- Quem lança: ``vendas.lancar`` do Vendas (vendas/views.py:415, ``@exige('vendas.lancar')``), além da
  porta do módulo. Sem a função: 403 em tudo daqui.
- PDV e vendedor: ``vendas.views._pdv_e_vendedor`` (vendas/views.py:438) — o setor principal do
  cadastro e a própria pessoa; só quem tem ``vendas.lancar_outros`` escolhe outra loja/vendedor.
  As opções da tela vêm de ``vendas.views._lojas_do_vendedor``/``_venda_form_context``
  (vendas/views.py:452/467). A web troca em silêncio um PDV pedido que a pessoa não pode usar
  pelo dela; o app recusa (400) para ninguém lançar achando que foi noutro PDV.
- Gravar: ``vendas.slv.registrar_venda`` com o mesmo JSON da tela (vendas/slv.py:647) — cliente,
  pré-análise/plano anterior, IMEI/serial, Renova, grupo/condição do preço, Vivo+, pagamentos com
  NSU e desconto no Pix, os 9 tipos de serviço. Os erros voltam 400, separados por etapa.
- As consultas da tela (cliente + pré-análise, busca de produto com preço sugerido, Renova, planos
  fora do portfólio) chamam as próprias views JSON da web com o pedido da pessoa.

Nada sai para fora: a consulta de CNPJ na Receita (``/vendas/api/cnpj/``, API pública externa) não
está aqui — no app o nome da empresa é digitado.
"""
import json

from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from vendas.permissions import pode

from .vendas import AUTH, PERM, _pedido_web

# Os campos que a tela da web mostra para cada tipo de serviço (templates/vendas/venda_form.html:912-937).
# Só desenham o formulário: quem decide é slv.registrar_venda (vendas/slv.py:707-800).
TODAS = ['POS', 'CONTROLE', 'PRE', 'EMPRESAS', 'FIBRA']
CAMPOS_SERVICO = {
    'ALTA': {'plano': TODAS, 'portabilidade': True},
    'REATIVACAO': {'plano': ['POS', 'CONTROLE'], 'sem_plano': True, 'plano_fora': True},
    'TROCA_PLANO': {'plano': TODAS, 'plano_anterior': ['CONTROLE', 'POS', 'PRE'], 'sem_plano': True, 'plano_fora': True},
    'MIGRACAO': {'plano': TODAS, 'plano_anterior': ['CONTROLE', 'POS', 'PRE'], 'sem_plano': True, 'plano_fora': True},
    'TROCA_SIMCARD': {'plano': TODAS, 'serial': True},
    'SEGURO': {'adicional': True},
    'SVA': {'adicional': True},
    'TROCA_TITULARIDADE': {'plano': TODAS, 'titular': True},
    'TROCA_NUMERO': {'numero_novo': True},
}


def _sem_lancar(user):
    if pode(user, 'vendas.lancar'):
        return None
    return Response({'erro': 'Lançar venda não está liberado para o seu usuário.'}, status=status.HTTP_403_FORBIDDEN)


def _da_web(resposta):
    """JsonResponse de uma view da web → Response do DRF (mesmo corpo e status)."""
    return Response(json.loads(resposta.content or b'{}'), status=resposta.status_code)


def _pessoa(u):
    return {'id': u.pk, 'nome': u.get_full_name() or u.get_username(),
            'detalhe': u.sector.name if getattr(u, 'sector_id', None) else None}


# ── Formulário ────────────────────────────────────────────────────────────────

@api_view(['GET', 'POST'])
@authentication_classes(AUTH)
@permission_classes(PERM)
def lancar(request):
    barrado = _sem_lancar(request.user)
    if barrado:
        return barrado
    if request.method == 'POST':
        return _gravar(request)
    return _formulario(request)


def _formulario(request):
    from vendas import slv
    from vendas.models import FORMAS_PAGAMENTO, VendaServico
    from vendas.views import _venda_form_context

    user = request.user
    ctx = _venda_form_context(_pedido_web(request))
    outros = pode(user, 'vendas.lancar_outros')
    loja = ctx['loja']
    s = ctx['slv']
    return Response({
        'pode_outros': outros,
        'sem_pdv': loja is None and not outros,          # a web mostra o aviso e não abre o formulário
        'loja': {'id': loja.pk, 'nome': loja.name} if loja else None,
        'eu': _pessoa(user),
        'lojas': [{'id': l.pk, 'nome': l.name} for l in ctx['lojas']] if outros else [],
        'vendedores': [_pessoa(u) for u in ctx['vendedores'].select_related('sector')] if outros else [],
        'planos': [{**p, 'valor': float(p['valor'])} for p in s['planos']],
        'adicionais': [{**a, 'valor': float(a['valor'])} for a in s['adicionais']],
        'segmentacoes': [{'valor': k, 'rotulo': r} for k, r in s['segmentacoes']],
        'tipos_servico': [{'valor': k, 'rotulo': r, 'campos': CAMPOS_SERVICO.get(k, {})} for k, r in s['tipos_servico']],
        'operadoras': [{'valor': k, 'rotulo': r} for k, r in VendaServico.OPERADORAS],
        'formas': [{'valor': k, 'rotulo': r, 'nsu': k in slv.FORMAS_COM_NSU, 'parcelas': k == 'CARTAO'}
                   for k, r in FORMAS_PAGAMENTO],
        'vivo_mais': float(s['vivo_mais']),
        'cond_base': slv.COND_BASE,
        'cond_pix': slv.COND_PIX,
        'rotulos_condicao': slv.ROTULOS_CONDICAO,
    })


# ── Gravar ────────────────────────────────────────────────────────────────────

def _rotulos_de_servico():
    from vendas.models import VendaServico
    return tuple(f'{r}:' for _k, r in VendaServico.TIPOS)


def _etapa(erro):
    """Em que etapa da tela o erro do slv.registrar_venda se corrige."""
    m = erro.lower()
    if m.startswith(('cpf/cnpj do cliente', 'informe o nome do cliente', 'seu usuário não tem pdv')):
        return 'cliente'
    if ('pagamento' in m or 'desconto no pix' in m or m.startswith(('cartão', 'débito', 'os pagamentos somam'))):
        return 'pagamento'
    if (erro.startswith(_rotulos_de_servico()) or m.startswith(('serviço ', 'informe a linha', 'linha ', 'reativação:'))):
        return 'servicos'
    return 'produtos'


def _invalido(erros):
    etapas = {}
    for e in erros:
        etapas.setdefault(_etapa(e), []).append(e)
    return Response({'erro': '\n'.join(erros), 'erros': erros, 'etapas': etapas}, status=status.HTTP_400_BAD_REQUEST)


def _gravar(request):
    """O corpo de vendas.views.venda_create (vendas/views.py:416-436), com o 400 no lugar do ok:false."""
    from vendas import slv
    from vendas.views import _pdv_e_vendedor

    dados = request.data
    if not isinstance(dados, dict):
        return _invalido(['Envio inválido.'])
    loja, vendedor = _pdv_e_vendedor(request, dados)
    erros = []
    pedida = str(dados.get('loja_id') or '')
    if pedida and pedida != str(loja.pk if loja else ''):
        erros.append(f'Seu usuário lança só no PDV {loja.name if loja else "do cadastro"}.')
    pedido = str(dados.get('vendedor_id') or '')
    if pedido and pedido != str(vendedor.pk):
        erros.append('Seu usuário lança só em nome de quem ele pode escolher.')
    if erros:
        return Response({'erro': '\n'.join(erros), 'erros': erros, 'etapas': {'cliente': erros}},
                        status=status.HTTP_400_BAD_REQUEST)
    try:
        venda = slv.registrar_venda(dados, request.user, loja, vendedor)
    except slv.VendaInvalida as exc:
        return _invalido(exc.erros)
    return Response({'ok': True, 'id': venda.pk, 'url_web': f'/vendas/{venda.pk}/'}, status=status.HTTP_201_CREATED)


# ── Consultas da tela (as views JSON da web) ──────────────────────────────────

def _consulta(view_nome, *chaves):
    @api_view(['GET'])
    @authentication_classes(AUTH)
    @permission_classes(PERM)
    def consulta(request):
        barrado = _sem_lancar(request.user)
        if barrado:
            return barrado
        from vendas import views
        params = {c: request.query_params.get(c, '') for c in chaves}
        return _da_web(getattr(views, view_nome)(_pedido_web(request, **params)))
    consulta.__name__ = f'lancar_{view_nome}'
    return consulta


# /vendas/api/cliente/ (vendas/views.py:967): CPF/CNPJ válido, cadastro e pré-análise.
cliente = _consulta('api_cliente', 'cpf')
# /vendas/precos/buscar/ (vendas/views.py:892): busca, preço sugerido pelo plano, grupos e condições, SKUs.
produtos = _consulta('precos_buscar', 'q', 'ids', 'segmentacao', 'grupamento', 'condicao')
# /vendas/api/renova/ (vendas/views.py:1025): o código do Renova Vini existe no portal?
renova = _consulta('api_renova', 'codigo')
# /vendas/api/planos-fora/ (vendas/views.py:1201): plano antigo fora do portfólio.
planos_fora = _consulta('api_planos_fora', 'q')
