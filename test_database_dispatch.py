from __future__ import annotations
import ast
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

source=(Path(__file__).resolve().parents[1] / 'app.py').read_text()
parsed=ast.parse(source)
functions=['_s2_list_dispatch_manifests','_s2_despacho_data']
selected=[n for n in parsed.body if isinstance(n,ast.FunctionDef) and n.name in functions]
assert len(selected)==2

URI='file:dispatchtest?mode=memory&cache=shared'
keeper=sqlite3.connect(URI,uri=True)
keeper.executescript('''
CREATE TABLE s2_manifests (id INTEGER PRIMARY KEY,status TEXT,created_at TEXT);
CREATE TABLE s2_sales (manifest_id INTEGER,sale_id TEXT,shipment_id TEXT,page_no INTEGER,row_no INTEGER,customer TEXT,pack_id TEXT,destino TEXT,status TEXT,mesa INTEGER);
CREATE TABLE s2_page_assign (manifest_id INTEGER,page_no INTEGER,mesa INTEGER);
CREATE TABLE s2_items (manifest_id INTEGER,sale_id TEXT,sku TEXT,qty INTEGER);
CREATE TABLE s2_labels (manifest_id INTEGER,shipment_id TEXT,raw TEXT);
INSERT INTO s2_manifests VALUES(1,'ACTIVE','2026-10-01'),(2,'DONE','2026-10-02');
INSERT INTO s2_sales VALUES(1,'20000000001','46000000001',1,1,'Fulano','','Santiago','PENDING',NULL);
INSERT INTO s2_sales VALUES(2,'20000000002','46000000002',2,1,'Ana','','Maipu','DONE',5);
INSERT INTO s2_page_assign VALUES(1,1,9);
INSERT INTO s2_items VALUES(1,'20000000001','5001',2),(2,'20000000002','9999',3);
INSERT INTO s2_labels VALUES(1,'46000000001','^XA^FO20,20^FDEnvio 1^FS^XZ');
INSERT INTO s2_labels VALUES(2,'46000000002','^XA^FO20,20^FDEnvio 2^FS^XZ');
''')
import despacho_kame
def get_conn(): return sqlite3.connect(URI,uri=True)
def normalize_sku(v): return str(v).strip()
def _s2_create_tables(): pass
ns={'get_conn':get_conn,'normalize_sku':normalize_sku,'_s2_create_tables':_s2_create_tables}
exec(compile(ast.Module(body=selected,type_ignores=[]),'<app.py>','exec'),ns)
assert len(ns['_s2_list_dispatch_manifests']())==2
actual=ns['_s2_despacho_data']([1,2],{'5001':'ALICATE CAJA KAME'})
assert len(actual['ventas'])==2
assert len(actual['etiquetas_zpl'])==2
assert actual['skus_sin_kame']==['9999']
assert actual['faltantes']==[]
assert actual['ventas'][0]['mesa']==9
assert actual['ventas'][1]['mesa']==5
keeper.execute("DELETE FROM s2_labels WHERE manifest_id=2"); keeper.commit()
actual=ns['_s2_despacho_data']([1,2],{'5001':'KAME'})
assert len(actual['faltantes'])==1
print('OK: lotes activos y cerrados, cruce Kame, mesa, etiquetas y error por faltantes')
keeper.close()
