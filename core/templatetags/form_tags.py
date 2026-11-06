from django import template

register = template.Library()


@register.filter
def get_item(mapping, key):
    """Look up ``mapping[key]`` in a template; empty string when missing.

    Works with plain dicts and with ``request.POST``.
    """
    try:
        return mapping.get(key, "")
    except AttributeError:
        return ""