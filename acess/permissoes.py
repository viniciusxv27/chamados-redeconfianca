"""Quem faz o quê no Acess: o SUPERADMIN tem tudo e libera, função por função, para cada pessoa."""
from functools import wraps

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect

# (chave, rótulo, o que libera, chaves que ela exige junto)
FUNCOES = [
    ('colaboradores.ver', 'Consultar colaboradores', 'Ver fichas, listas e dados pessoais; exportar a lista em Excel.', []),
    ('chamados.ver', 'Consultar chamados', 'Consultar chamados e o acompanhamento deles.', []),
    ('colaboradores.cadastrar', 'Cadastrar colaboradores', 'Criar fichas (inclusive a partir de usuário do portal).',
     ['colaboradores.ver']),
    ('colaboradores.editar', 'Editar dados pessoais', 'Alterar nome, CPF, nascimento, e-mail, telefone e cargo.',
     ['colaboradores.ver']),
    ('colaboradores.transferir', 'Transferir colaboradores', 'Alterar o local de trabalho ou a loja de vínculo.',
     ['colaboradores.ver']),
    ('colaboradores.excluir', 'Excluir (arquivar) colaboradores', 'Arquivar a ficha (sem chamado ativo).',
     ['colaboradores.ver']),
    ('sistemas.concluir', 'Concluir/alterar sistemas', 'Status dos sistemas, Academia V, checklist e voltar etapa.',
     ['colaboradores.ver']),
    ('gefac.concluir', 'Concluir GEFAC', 'Registrar a aprovação do GEFAC (Etapa 1).', ['colaboradores.ver']),
    ('login.preencher', 'Preencher login', 'Informar ou alterar a matrícula/login (Etapa 2).', ['colaboradores.ver']),
    ('chamados.criar', 'Criar chamados', 'Registrar chamado associado a um colaborador.',
     ['colaboradores.ver', 'chamados.ver']),
    ('chamados.tratar', 'Concluir e reagendar chamados', 'Responsável, prazos, reagendamento e encerramento.',
     ['chamados.ver']),
    ('historico.ver', 'Consultar histórico', 'Consultar as alterações e os filtros históricos.', []),
    ('desligamentos.gerenciar', 'Gerenciar desligamentos', 'Agendar, cancelar e confirmar desligamentos.',
     ['colaboradores.ver']),
    ('mensagens.gerenciar', 'Gerenciar mensagens WhatsApp', 'Editar os textos dos modelos de mensagem.', []),
]
CHAVES = {f[0] for f in FUNCOES}
EXIGE = {f[0]: f[3] for f in FUNCOES}


def e_superadmin(user):
    return bool(user and getattr(user, 'is_authenticated', False)
                and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def completar(chaves):
    """Acrescenta as consultas que as operações exigem (como o original faz ao salvar o cargo)."""
    saida = {c for c in chaves if c in CHAVES}
    for c in list(saida):
        saida.update(EXIGE.get(c, []))
    return sorted(saida)


def _acesso(user):
    if not (user and getattr(user, 'is_authenticated', False)):
        return None
    if not hasattr(user, '_acess_acesso'):
        from .models import AcessoAcess
        try:
            user._acess_acesso = AcessoAcess.objects.filter(user=user, ativo=True).first()
        except Exception:  # noqa: BLE001 — tabela não migrada noutro servidor
            user._acess_acesso = None
    return user._acess_acesso


def chaves_de(user):
    if e_superadmin(user):
        return set(CHAVES)
    a = _acesso(user)
    return set(completar(a.permissoes or [])) if a else set()


def pode(user, chave):
    return chave in chaves_de(user)


def pode_entrar(user):
    return e_superadmin(user) or bool(chaves_de(user))


def trata_chamados(user):
    if e_superadmin(user):
        return True
    a = _acesso(user)
    return bool(a and a.trata_chamados and pode(user, 'chamados.tratar'))


def exige(*chaves):
    def decorador(view):
        @wraps(view)
        def _view(request, *args, **kwargs):
            u = request.user
            ok = pode_entrar(u) if not chaves else any(pode(u, c) for c in chaves)
            if not ok:
                aviso = 'Esta função do Acess não está liberada para o seu usuário.'
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'ok': False, 'erro': aviso}, status=403)
                messages.error(request, aviso)
                return redirect('acess:dashboard' if pode_entrar(u) else 'home')
            return view(request, *args, **kwargs)
        return _view
    return decorador
