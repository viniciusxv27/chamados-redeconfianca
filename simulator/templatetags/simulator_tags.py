from django import template

register = template.Library()


@register.filter
def get_dict_item(dict_obj, key):
    """Get value from dictionary: dict|get_dict_item:"key" """
    if not isinstance(dict_obj, dict):
        return None
    return dict_obj.get(key)


@register.filter
def get_list_item(seq, index):
    """Get item by index from list/tuple: seq|get_list_item:idx"""
    try:
        return seq[int(index)]
    except (TypeError, IndexError, ValueError):
        return None


def _to_decimal(value):
    try:
        if value is None or value == '':
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_brazilian(value, decimals):
    formatted = f"{value:,.{decimals}f}"
    # 1,234.56 -> 1.234,56
    return formatted.replace(',', '_').replace('.', ',').replace('_', '.')


@register.filter
def brl(value, decimals=2):
    """Formata como R$ no padrão brasileiro: brl 1234.5 -> 'R$ 1.234,50'."""
    val = _to_decimal(value)
    if val is None:
        return '—'
    try:
        decimals = int(decimals)
    except (TypeError, ValueError):
        decimals = 2
    return f"R$ {_format_brazilian(val, decimals)}"


@register.filter
def number_br(value, decimals=2):
    """Formata número no padrão brasileiro (sem R$)."""
    val = _to_decimal(value)
    if val is None:
        return '—'
    try:
        decimals = int(decimals)
    except (TypeError, ValueError):
        decimals = 2
    return _format_brazilian(val, decimals)


@register.filter
def pct_br(value, decimals=1):
    """Formata fração como percentual brasileiro: 0.123 -> '12,3%'."""
    val = _to_decimal(value)
    if val is None:
        return '—'
    try:
        decimals = int(decimals)
    except (TypeError, ValueError):
        decimals = 1
    return f"{_format_brazilian(val * 100, decimals)}%"


@register.filter
def raw_value(value):
    """Devolve o valor numérico bruto (string vazia se None)."""
    if value is None or value == '':
        return ''
    try:
        # Mantém precisão sem notação científica
        v = float(value)
        if v.is_integer():
            return str(int(v))
        return ('%.10f' % v).rstrip('0').rstrip('.')
    except (TypeError, ValueError):
        return value


@register.filter
def first_word(value):
    if not value:
        return value
    return str(value).strip().split(' ')[0]


@register.filter
def dict_items(dict_obj):
    """Retorna os pares (chave, valor) de um dicionário para uso em loops."""
    if not isinstance(dict_obj, dict):
        return []
    return list(dict_obj.items())
