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
