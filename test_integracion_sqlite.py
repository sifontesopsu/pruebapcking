"""Prueba real de funciones de Picking + documentos, sin arrancar Streamlit ni acceder a la red."""
import sys
import types
import tempfile
from pathlib import Path
import importlib.util
import pandas as pd
from io import BytesIO

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Streamlit no es necesario para probar los modelos de datos.
st = types.ModuleType('streamlit')
st.session_state = {}
st.cache_data=lambda *args, **kwargs: (lambda fn:fn)
comp = types.ModuleType('streamlit.components')
comp_v1 = types.ModuleType('streamlit.components.v1')
st.components=comp
comp.v1=comp_v1
sys.modules.update({'streamlit':st,'streamlit.components':comp,'streamlit.components.v1':comp_v1})

spec=importlib.util.spec_from_file_location('aurora_test_app',ROOT/'app.py')
app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
from manifiestos_etiquetas import prepare_upload,load_selected,list_batches

with tempfile.TemporaryDirectory() as tmp:
    app.DB_NAME=str(Path(tmp)/'solo_test.db')
    app.CORTES_FILE=str(ROOT/'CORTES.xlsx')
    app.init_db()
    conn=app.get_conn()
    assert len(conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'despacho_%'").fetchall())==4
    conn.close()
    rows=pd.DataFrame([
        dict(ml_order_id='20000010101',buyer='Ana',sku_ml='901',title_ml='Nombre ML 1',qty=3,pagina_ml=2),
        dict(ml_order_id='20000010102',buyer='Luis',sku_ml='902',title_ml='Nombre ML 2',qty=1,pagina_ml=2),
    ])
    zpl=b'^XA^FO10,10^FDVenta: 20000010101^FS^XZ^XA^FO10,10^FDVenta: 20000010102^FS^XZ'
    payload=prepare_upload(rows,{'901':'PINTURA TECNICA','902':'LIJA KAME'},[('envios.zpl',zpl)],'control.pdf','PDF')
    result=app.save_orders_and_build_ots(rows,{'901':'PINTURA TECNICA','902':'LIJA KAME'},2,source_label='control.pdf',dispatch_payload=payload)
    assert result['created'],result
    conn=app.get_conn()
    assert len(list_batches(conn))==1
    assert conn.execute('SELECT COUNT(*) FROM picking_ots').fetchone()[0]==2
    assert conn.execute('SELECT COUNT(*) FROM order_items').fetchone()[0]==2
    conn.close()

    # Añadir otro lote, manteniendo el anterior; número de PDA variable.
    rows2=pd.DataFrame([dict(ml_order_id='20000020202',buyer='Juan',sku_ml='901',title_ml='ML',qty=2,pagina_ml=1)])
    zpl2=b'^XA^FO10,10^FDVenta: 20000020202^FS^XZ'
    payload2=prepare_upload(rows2,{'901':'PINTURA TECNICA'},[('2.zpl',zpl2)],'control2.pdf','PDF')
    result2=app.append_orders_and_build_ots(rows2,{'901':'PINTURA TECNICA'},1,source_label='control2.pdf',dispatch_payload=payload2)
    assert result2['created'] and result2['new_orders']==1
    conn=app.get_conn()
    assert len(list_batches(conn))==2
    assert conn.execute('SELECT COUNT(*) FROM picking_ots').fetchone()[0]==3
    assert len(load_selected(conn,[result['batch_key'],result2['batch_key']])['sales'])==3

    # Reiniciar Picking no borra el histórico de documentos.
    for tab in ['picking_tasks','picking_ots','ot_orders','orders','order_items','pickers']:
        conn.execute(f'DELETE FROM {tab}')
    conn.commit()
    assert len(list_batches(conn))==2
    conn.close()
    archive = app._export_tables_to_db_bytes(app.PICKING_TABLES)
    assert archive[:16] == b'SQLite format 3\x00'
    assert len(archive) > 1024
    ok,err = app._restore_tables_from_db_bytes(archive, app.PICKING_TABLES)
    assert ok,err
    conn = app.get_conn()
    assert len(list_batches(conn))==2
    conn.close()
print('OK: DB real, 2 tandas, reinicio, respaldo y restauración local de documentos')
