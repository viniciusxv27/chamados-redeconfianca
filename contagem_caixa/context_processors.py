"""Gate do item de menu da Contagem de Caixa."""
from .permissions import lotado_em_loja, pode_ver_caixa, pode_ver_sangrias


def caixa_menu(request):
    user = getattr(request, 'user', None)
    liberado = pode_ver_caixa(user)
    return {
        'caixa_liberado': liberado,
        # O grupo ADMINISTRATIVO do menu abria para quem está lotado em loja por
        # causa do caixa, e lá dentro também fica Reuniões. O PADRÃO fora do
        # grupo GERENTES perde o item do caixa, não o grupo inteiro.
        # ADM da loja (PADRÃO do grupo "ADM's LOJAS"): só o item Sangrias.
        'sangrias_liberado': not liberado and pode_ver_sangrias(user),
        'caixa_abre_grupo_administrativo': liberado or lotado_em_loja(user),
    }
