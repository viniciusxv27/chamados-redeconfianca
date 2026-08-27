"""Histórico de alterações no cadastro de um colaborador.

A ideia é simples: tira-se uma fotografia dos campos antes de salvar, outra
depois, e o que diferir vira uma linha de log. Assim nenhum campo precisa ser
lembrado na hora de gravar — quem adiciona um campo novo à tela só o inclui em
``CAMPOS_AUDITADOS`` e o histórico passa a cobri-lo.
"""
from django.utils import timezone

# Campo -> rótulo mostrado no histórico. A ordem aqui é a ordem de leitura.
CAMPOS_AUDITADOS = [
    ('first_name', 'Nome'),
    ('last_name', 'Sobrenome'),
    ('email', 'E-mail'),
    ('username', 'Usuário'),
    ('phone', 'Telefone'),
    ('cpf', 'CPF'),
    ('pis', 'PIS'),
    ('birth_date', 'Data de nascimento'),
    ('hierarchy', 'Hierarquia'),
    ('job_title', 'Cargo'),
    ('sector', 'Setor principal'),
    ('sectors', 'Setores'),
    ('pdv', 'PDV'),
    ('login_code', 'Código de login'),
    ('admission_date', 'Data de admissão'),
    ('demission_date', 'Data de demissão'),
    ('has_experience_window', 'Janela de experiência'),
    ('contract_type', 'Tipo de contrato'),
    ('branch_cnpj', 'CNPJ da filial'),
    ('salary', 'Salário'),
    ('is_active', 'Login ativo'),
    ('status', 'Situação'),
    ('inactivation_reason', 'Motivo da inativação'),
    ('leave_reason', 'Motivo do afastamento'),
    ('leave_return_date', 'Retorno do afastamento'),
    ('vacation_start_date', 'Início das férias'),
    ('vacation_end_date', 'Fim das férias'),
    ('disc_profile', 'Perfil DISC'),
    ('uniform_size_shirt', 'Camisa'),
    ('uniform_size_pants', 'Calça'),
    ('neighborhood', 'Bairro'),
    ('city', 'Cidade'),
    ('theme', 'Tema do portal'),
]

# Campos que não se leem como texto simples.
_M2M = {'sectors'}


def _texto(user, campo):
    """Valor do campo como a pessoa o lê na tela.

    Usa o rótulo das escolhas quando existe: no histórico interessa "Gerente",
    não "GERENTE_LOJA". Vazio vira "—" para a linha não ficar ambígua entre
    "apagado" e "nunca preenchido".
    """
    if campo in _M2M:
        nomes = sorted(s.name for s in getattr(user, campo).all())
        return ', '.join(nomes) if nomes else '—'

    getter = getattr(user, f'get_{campo}_display', None)
    valor = getter() if callable(getter) else getattr(user, campo, None)

    if valor is None or valor == '':
        return '—'
    if isinstance(valor, bool):
        return 'Sim' if valor else 'Não'
    if hasattr(valor, 'name') and not isinstance(valor, str):   # FK tipo Sector
        return valor.name
    if hasattr(valor, 'strftime'):
        return timezone.localtime(valor).strftime('%d/%m/%Y %H:%M') \
            if hasattr(valor, 'hour') else valor.strftime('%d/%m/%Y')
    return str(valor)


def fotografar(user):
    """Estado atual dos campos auditados, para comparar depois de salvar."""
    return {campo: _texto(user, campo) for campo, _rotulo in CAMPOS_AUDITADOS}


def registrar(user, antes, autor, ip=None):
    """Grava uma linha para cada campo que mudou. Devolve quantas gravou.

    Nunca levanta exceção: o histórico é registro, não parte da operação. Se ele
    falhar, a edição do cadastro já aconteceu e não pode ser desfeita por causa
    de um log.
    """
    from .models import UserChangeLog

    try:
        depois = fotografar(user)
        rotulos = dict(CAMPOS_AUDITADOS)

        linhas = [
            UserChangeLog(
                target=user, changed_by=autor, field=campo,
                field_label=rotulos.get(campo, campo),
                old_value=antes.get(campo, '—'), new_value=depois.get(campo, '—'),
                ip=ip,
            )
            for campo in rotulos
            if antes.get(campo) != depois.get(campo)
        ]
        if linhas:
            UserChangeLog.objects.bulk_create(linhas)
        return len(linhas)
    except Exception:
        return 0
