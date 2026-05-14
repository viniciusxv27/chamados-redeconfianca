from django import template

register = template.Library()


@register.filter
def get_row_range(cell_range_str):
    """Extract row count from cell range like 'B6:E8' -> returns [6, 7, 8]"""
    if not cell_range_str or ':' not in cell_range_str:
        return []
    _, end = cell_range_str.split(':')
    # Extract row number from end cell (e.g., 'E8' -> '8')
    row_end = int(''.join(filter(str.isdigit, end)))
    row_start = int(''.join(filter(str.isdigit, cell_range_str.split(':')[0])))
    return range(row_start, row_end + 1)


@register.filter
def get_col_range(cell_range_str):
    """Extract column count from cell range like 'B6:E8' -> returns ['B', 'C', 'D', 'E']"""
    if not cell_range_str or ':' not in cell_range_str:
        return []
    start, end = cell_range_str.split(':')
    # Extract column letters
    col_start = ''.join(filter(str.isalpha, start))
    col_end = ''.join(filter(str.isalpha, end))
    # Convert to numbers for range
    start_num = sum((ord(c) - ord('A') + 1) * (26 ** i) for i, c in enumerate(reversed(col_start)))
    end_num = sum((ord(c) - ord('A') + 1) * (26 ** i) for i, c in enumerate(reversed(col_end)))
    return [chr(ord('A') + i) for i in range(start_num - 1, end_num)]


@register.filter
def get_range_value(ranges_dict, args):
    """Get value from ranges dict: ranges|get_range_value:"commission:1:0" """
    if not ranges_dict or not args:
        return ''
    parts = args.split(':')
    if len(parts) != 3:
        return ''
    key, row, col = parts
    if key in ranges_dict and isinstance(ranges_dict[key], list):
        row_idx = int(row)
        col_idx = int(col)
        if row_idx < len(ranges_dict[key]) and col_idx < len(ranges_dict[key][row_idx]):
            val = ranges_dict[key][row_idx][col_idx]
            return val if val is not None else ''
    return ''


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
def pct_input(value, decimals=4):
    """Renderiza um número como percentual editável (input). 0.05 -> '5'.

    Útil para inputs de fatores que armazenamos como fração (0–1).
    """
    val = _to_decimal(value)
    if val is None:
        return ''
    try:
        decimals = int(decimals)
    except (TypeError, ValueError):
        decimals = 4
    return f"{val * 100:.{decimals}f}".rstrip('0').rstrip('.') or '0'


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
def get_meta_value(meta_dict, key):
    if not isinstance(meta_dict, dict):
        return ''
    return meta_dict.get(key, '')


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
