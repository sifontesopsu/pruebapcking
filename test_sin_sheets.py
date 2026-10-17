"""Prueba sin Internet del bloqueo de Google Sheets en esta edición."""
from __future__ import annotations
import ast
from pathlib import Path

source = (Path(__file__).resolve().parents[1] / 'app.py').read_text(encoding='utf-8')
tree = ast.parse(source)
assignments = {}
for node in tree.body:
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id in {'DB_NAME', 'PICKING_SHEETS_BACKUP_ENABLED','DEFAULT_PICKING_SHEETS_WEBHOOK_URL'}:
                assignments[t.id] = ast.literal_eval(node.value)
assert assignments['PICKING_SHEETS_BACKUP_ENABLED'] is False
assert assignments['DEFAULT_PICKING_SHEETS_WEBHOOK_URL'] == ''
assert assignments['DB_NAME'] != 'aurora_ml.db'
assert 'script.google.com/macros/s/' not in source

names = {
    'get_picking_backup_webhook_url', 'enqueue_picking_backup_events_batch',
    '_send_picking_webhook_event', 'flush_picking_backup_queue',
    'trigger_picking_backup_sync_async', '_read_picking_sheets_json',
    'auto_restore_picking_if_database_was_lost', 'restore_picking_batch_from_sheets',
    'render_picking_backup_sidebar', 'render_picking_recovery_admin_ui',
}
funcs = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
assert len(funcs) == len(names)

# No dependemos de Streamlit ni de red en esta prueba. Si alguna función intentase
# acceder a otros recursos, daría un NameError antes de devolver su resultado.
ns={'PICKING_SHEETS_BACKUP_ENABLED': False}
exec(compile(ast.Module(body=funcs, type_ignores=[]),'<offline-app.py>','exec'),ns)
assert ns['get_picking_backup_webhook_url']() == ''
assert ns['enqueue_picking_backup_events_batch']([('venta', {'dato':'ejemplo'})]) == []
assert ns['_send_picking_webhook_event']('https://example.com', {'dato':'prueba'})[0] is False
assert ns['flush_picking_backup_queue'](include_failed=True) == 0
assert ns['trigger_picking_backup_sync_async']() is False
assert ns['_read_picking_sheets_json']({'action':'lotes'})[0] is False
assert ns['auto_restore_picking_if_database_was_lost']()[0] is False
assert ns['restore_picking_batch_from_sheets']('PK-100')[0] is False
assert ns['render_picking_backup_sidebar']() is None
assert ns['render_picking_recovery_admin_ui']() is None

# Las conexiones activas en main() quedan debajo del interruptor desactivado.
main = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name=='main')
assert any(isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == 'PICKING_SHEETS_BACKUP_ENABLED' for node in ast.walk(main))
print('OK: Sheets bloqueado en 10 puntos, sin HTTP ni cola de respaldo; BD de pruebas aislada.')
