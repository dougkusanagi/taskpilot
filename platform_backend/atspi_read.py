"""Leitura isolada AT-SPI; executada pelo Python do sistema, sem input."""
import json
import os


def read():
    if not os.environ.get('DBUS_SESSION_BUS_ADDRESS'):
        raise RuntimeError('barramento de sessão explícito ausente')
    import gi
    gi.require_version('Atspi', '2.0')
    from gi.repository import Atspi

    desktop = Atspi.get_desktop(0)
    windows = []
    def children(node):
        return (node.get_child_at_index(i) for i in range(node.get_child_count()))

    for app in children(desktop):
        for window in children(app):
            if window.get_state_set().contains(Atspi.StateType.ACTIVE):
                windows.append(window)
    if len(windows) != 1:
        raise RuntimeError('janela ativa ausente ou ambígua')
    items = []
    focused = ''
    visited = 0
    truncated = False

    def walk(node, depth=0):
        nonlocal focused, visited, truncated
        visited += 1
        if depth > 80 or visited > 5000:
            truncated = True
            return
        role = {'page tab': 'TabItem', 'text': 'Edit', 'entry': 'Edit',
                'push button': 'Button', 'button': 'Button'}.get(
                    node.get_role_name(), node.get_role_name())
        name = node.get_name() or ''
        value = ''
        try:
            text = node.get_text_iface()
            value = text.get_text(0, min(text.get_character_count(), 8192))
        except Exception:
            pass
        if node.get_state_set().contains(Atspi.StateType.FOCUSED):
            focused = value
        if name or value or role in ('Edit', 'TabItem'):
            items.append(dict(id=len(items), name=name, type=role, value=value,
                              bounds=None, context='AT-SPI'))
        for child in children(node):
            walk(child, depth + 1)

    walk(windows[0])
    return dict(items=items, title=windows[0].get_name(), focused=focused,
                truncated=truncated)


if __name__ == '__main__':
    try:
        print(json.dumps(read(), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps(dict(error=str(exc))))
