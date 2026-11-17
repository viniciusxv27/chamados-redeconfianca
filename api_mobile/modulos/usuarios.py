"""Módulo Usuários no app — a lista de colaboradores de /users/manage/users/ (só leitura).

Regras (todas do portal, nenhuma nova):

- Entrada: ``user.can_manage_users()`` (users/models.py:907) — a mesma checagem da
  view web ``manage_users_view`` (users/views.py:259) e do gate do menu; já soma a
  liberação individual ``usuarios.gerenciar`` (users/module_access.py GATES).
- Recorte: a view web lista ``User.objects.all()`` (users/views.py:264) e o perfil
  abre qualquer id (``view_user_profile_view``, users/views.py:2823-2830) para quem
  passa no gate. Então quem entra vê todos; id que não existe → 404.
- Busca e filtros: os mesmos da tela (templates/admin/users.html:129-150 e
  :757-786): texto em nome, login, e-mail e setor; setor principal; hierarquia;
  situação (ativo/inativo/afastado/férias pelo campo ``status``).
- Saldo C$: só aparece para quem a tela mostra a coluna (templates/admin/users.html:247
  — hierarquia fora de PADRÃO/ADMINISTRATIVO/SUPERVISOR).

De fora nesta etapa (o app abre o portal para isso): editar cadastro, ativar/
desativar (na web é pela edição completa, com sinal de grupo por cargo no save),
trocar senha, foto, liberar módulo, apagar, C$, pré-cadastro e documentos.
Nunca sai senha, hash, código de login (login_code), token de pré-cadastro, CPF
nem dado bancário.
"""
import re

from django.contrib.auth import get_user_model
from django.db.models import Q, Value
from django.db.models.functions import Concat
from django.http import Http404
from django.urls import path
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from users.models import Sector

from .. import dados
from ..autenticacao import TokenMobileAuthentication
from ..views import _pagina
from . import permissao


def pode_entrar(user):
    # users/models.py:907 — a mesma regra de users/views.py:259 (lista) e :2823 (perfil).
    return bool(user.can_manage_users())


MODULO = {'rotulo': 'Usuários', 'descricao': 'Colaboradores: busca, contato e cadastro',
          'icone': 'people-outline', 'cor': '#2C3E50', 'pode_entrar': pode_entrar}

User = get_user_model()

STATUS_VALIDOS = {c for c, _ in User.STATUS_CHOICES}
HIERARQUIAS = [{'valor': v, 'rotulo': r} for v, r in User.HIERARCHY_CHOICES]


def _protecao():
    """Decorators de todo endpoint do módulo."""
    def aplicar(funcao):
        funcao = permission_classes([permissao('usuarios')])(funcao)
        funcao = authentication_classes([TokenMobileAuthentication])(funcao)
        return api_view(['GET'])(funcao)
    return aplicar


def _ve_saldo(quem):
    # templates/admin/users.html:247 — a coluna Saldo C$ só tem valor fora destas três.
    return quem.hierarchy not in ('PADRAO', 'ADMINISTRATIVO', 'SUPERVISOR')


def _whatsapp(telefone):
    """Número do WhatsApp (55 + DDD + número) ou None se o telefone não parece celular/fixo do Brasil."""
    digitos = re.sub(r'\D', '', telefone or '')
    if len(digitos) in (10, 11):
        return '55' + digitos
    if len(digitos) in (12, 13) and digitos.startswith('55'):
        return digitos
    return None


def _resumo(request, u, ver_saldo):
    setor = u.sector
    item = {
        'id': u.pk,
        'nome': u.full_name or u.email,
        'login': u.username,
        'email': u.email,
        'telefone': u.phone or None,
        'foto': dados.url_absoluta(request, getattr(u, 'profile_picture', None)),
        'cargo': u.job_title or None,
        'setor': setor.name if setor else None,
        'hierarquia': u.hierarchy,
        'hierarquia_nome': u.get_hierarchy_display(),
        'status': u.status,
        'status_nome': u.get_status_display(),
        'acesso_ativo': u.is_active,
    }
    if ver_saldo:
        item['saldo_cs'] = str(u.balance_cs)
    return item


@_protecao()
def lista(request):
    """GET ?busca=&setor=<id>&hierarquia=&status=&pagina= — a tabela de /users/manage/users/."""
    qs = User.objects.all().select_related('sector')   # users/views.py:264 — todos
    p = request.query_params

    busca = (p.get('busca') or '').strip()
    if busca:
        # Mesmo alcance do data-search da tela: nome completo, login, e-mail e setor principal.
        qs = qs.annotate(_nome=Concat('first_name', Value(' '), 'last_name')).filter(
            Q(_nome__icontains=busca) | Q(username__icontains=busca) | Q(email__icontains=busca)
            | Q(sector__name__icontains=busca))
    setor = p.get('setor')
    if setor and str(setor).isdigit():
        qs = qs.filter(sector_id=int(setor))
    hierarquia = (p.get('hierarquia') or '').upper()
    if hierarquia in {h['valor'] for h in HIERARQUIAS}:
        qs = qs.filter(hierarchy=hierarquia)
    status = (p.get('status') or '').upper()
    if status in STATUS_VALIDOS:
        qs = qs.filter(status=status)

    itens, meta = _pagina(request, qs.order_by('first_name', 'last_name', 'pk'))
    ver_saldo = _ve_saldo(request.user)
    return Response({'resultados': [_resumo(request, u, ver_saldo) for u in itens], **meta})


@_protecao()
def filtros(request):
    """Opções dos filtros e os números do topo da tela web (users/views.py:266-268)."""
    todos = User.objects.all()
    return Response({
        'setores': [{'id': s.pk, 'nome': s.name} for s in Sector.objects.all().order_by('name')],
        'hierarquias': HIERARQUIAS,
        'situacoes': [{'valor': v, 'rotulo': r} for v, r in User.STATUS_CHOICES],
        'totais': {
            'usuarios': todos.count(),
            'ativos': todos.filter(is_active=True).count(),
            'superadmins': todos.filter(hierarchy='SUPERADMIN').count(),
        },
    })


@_protecao()
def detalhe(request, pk):
    """O perfil (users/views.py:2821) sem os dados pessoais/documentos: identificação, lotação e contato."""
    u = User.objects.select_related('sector').prefetch_related('sectors').filter(pk=pk).first()
    if u is None:
        raise Http404
    item = _resumo(request, u, _ve_saldo(request.user))
    principal = u.primary_sector          # o "Setor principal" do perfil web
    setores = sorted({s.name for s in u.sectors.all()} | ({principal.name} if principal else set()))
    grupos = list(u.communication_groups.filter(is_active=True).order_by('name').values_list('name', flat=True))
    item.update({
        'primeiro_nome': u.first_name or item['nome'].split(' ')[0],
        'setor_principal': principal.name if principal else None,
        'setores': setores,
        'grupos': grupos,
        'admissao': dados.iso(u.admission_date),
        'whatsapp': _whatsapp(u.phone),
        'afastamento_retorno': dados.iso(u.leave_return_date) if u.status == 'AFASTADO' else None,
        'ferias_inicio': dados.iso(u.vacation_start_date) if u.status == 'FERIAS' else None,
        'ferias_fim': dados.iso(u.vacation_end_date) if u.status == 'FERIAS' else None,
        'superadmin': bool(u.is_superuser or u.hierarchy == 'SUPERADMIN'),
        'url_web': f'/users/manage/users/{u.pk}/profile/',
    })
    return Response(item)


urlpatterns = [
    path('', lista, name='usuarios_lista'),
    path('filtros/', filtros, name='usuarios_filtros'),
    path('<int:pk>/', detalhe, name='usuarios_detalhe'),
]
