"""Vínculo entre os usuários do portal e os funcionários do Tangerino.

Estratégia, nesta ordem:

1. **CPF** — chave real, imune a apelido, acento e nome de casada. Cobre a
   quase totalidade da base (na primeira medição: 164 de 181).
2. **Nome normalizado** — só para quem não tem CPF dos dois lados. Sem acento,
   sem pontuação, espaços colapsados, tudo em caixa alta. Um nome que aparece
   duas vezes no Tangerino é considerado **ambíguo e nunca casado sozinho**:
   vincular a pessoa errada num sistema de ponto é pior do que não vincular.

O que sobrar vai para a tela de vínculo manual.
"""
import re
import unicodedata

from django.contrib.auth import get_user_model
from django.utils import timezone

from .client import listar_funcionarios

User = get_user_model()


def so_digitos(valor):
    return re.sub(r'\D', '', valor or '')


def chave_nome(valor):
    """Nome comparável: sem acento, sem pontuação, espaços colapsados, caixa alta."""
    sem_acento = unicodedata.normalize('NFKD', valor or '').encode('ascii', 'ignore').decode()
    limpo = re.sub(r'[^A-Za-z ]', ' ', sem_acento)
    return re.sub(r'\s+', ' ', limpo).strip().upper()


def _indices(funcionarios):
    por_cpf, por_nome = {}, {}
    for f in funcionarios:
        cpf = so_digitos(f.get('cpf'))
        if cpf:
            por_cpf[cpf] = f
        por_nome.setdefault(chave_nome(f.get('name')), []).append(f)
    return por_cpf, por_nome


def sincronizar_vinculos(revincular=False, aplicar=True):
    """Casa usuários do portal com funcionários do Tangerino.

    ``revincular=False`` (padrão) não mexe em quem já tem ID — vínculo ajustado
    à mão não é sobrescrito por uma rodada automática.
    ``aplicar=False`` só simula, para a tela mostrar o que aconteceria.
    """
    funcionarios = listar_funcionarios(usar_cache=False)
    por_cpf, por_nome = _indices(funcionarios)
    tomados = set(User.objects.exclude(tangerino_employee_id__isnull=True)
                  .values_list('tangerino_employee_id', flat=True))

    resultado = {
        'casados_cpf': 0, 'casados_nome': 0, 'ja_vinculados': 0,
        'sem_correspondencia': 0, 'ambiguos': 0,
        'total_tangerino': len(funcionarios), 'pendentes': [],
    }

    for usuario in User.objects.filter(is_active=True).order_by('first_name', 'last_name'):
        if usuario.tangerino_employee_id and not revincular:
            resultado['ja_vinculados'] += 1
            continue

        achado, via = None, None
        cpf = so_digitos(usuario.cpf)
        if cpf and cpf in por_cpf:
            achado, via = por_cpf[cpf], 'cpf'
        else:
            candidatos = por_nome.get(chave_nome(usuario.full_name), [])
            if len(candidatos) == 1:
                achado, via = candidatos[0], 'nome'
            elif len(candidatos) > 1:
                resultado['ambiguos'] += 1

        # Um mesmo funcionário não pode ser vinculado a dois usuários.
        if achado and achado['id'] in tomados and usuario.tangerino_employee_id != achado['id']:
            achado = None

        if not achado:
            resultado['sem_correspondencia'] += 1
            resultado['pendentes'].append({
                'user_id': usuario.id, 'nome': usuario.full_name,
                'email': usuario.email, 'cpf': cpf,
            })
            continue

        if aplicar:
            usuario.tangerino_employee_id = achado['id']
            usuario.tangerino_synced_at = timezone.now()
            usuario.save(update_fields=['tangerino_employee_id', 'tangerino_synced_at'])
        tomados.add(achado['id'])
        resultado['casados_cpf' if via == 'cpf' else 'casados_nome'] += 1

    return resultado


def funcionarios_disponiveis():
    """Funcionários do Tangerino ainda não vinculados a ninguém, para o select
    da tela de vínculo manual."""
    tomados = set(User.objects.exclude(tangerino_employee_id__isnull=True)
                  .values_list('tangerino_employee_id', flat=True))
    livres = [f for f in listar_funcionarios() if f['id'] not in tomados]
    return sorted(livres, key=lambda f: (f.get('name') or '').upper())
