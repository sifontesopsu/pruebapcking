# WMS Aurora — TEST sin respaldo Google Sheets

## Qué cambia en esta edición

Esta es una **copia de pruebas**, basada en **WMS Aurora — Manifiesto Kame + Etiquetas Flex V1**.

- **Google Sheets/Apps Script: completamente deshabilitado para el respaldo de Picking.** No se envían eventos, no se lee la hoja, no se ejecuta rescate automático, no hay cola de sincronización y se ocultan sus controles. El endpoint operativo se ha retirado del archivo.
- **SQLite separado:** `aurora_ml_PRUEBAS_SIN_SHEETS.db` en lugar de `aurora_ml.db`. Las ventas y los lotes de esta edición no son los de la base operativa. Si se borran datos de la app de pruebas, no habrá recuperación automática desde Sheets.
- El **respaldo y restauración manual con archivos `.db`** por módulo siguen disponibles.
- El picking, la distribución por lote, Sorting, la generación de manifiesto Kame y la consolidación de etiquetas Flex se conservan.
- La conversión opcional ZPL→PDF con **Labelary** sigue siendo una conexión independiente (no relacionada con Google Sheets), sólo cuando se autorice explícitamente. Para evitar esa conexión, utiliza la **unión local de PDF de etiquetas**.

## Ejecución local (Windows)

1. Descomprime el ZIP en una carpeta nueva, separada de la versión de producción.
2. Abre PowerShell dentro de `v1-picking-main`.
3. Instala las dependencias, si hace falta: `python -m pip install -r requirements.txt`.
4. Ejecuta: `python -m streamlit run app.py`.
5. Verifica el mensaje lateral **MODO PRUEBAS · SIN GOOGLE SHEETS · BD LOCAL INDEPENDIENTE**.
6. Entra a Picking/Sorting, importa ventas y manifiestos de **prueba** y valida el nuevo módulo en Sorting → **Documentos de despacho**.

## Publicación de pruebas

Si usas Streamlit Cloud, despliega esta copia como **otra aplicación o rama de pruebas**, no sustituyas el proyecto en producción. SQLite conserva los datos sólo mientras permanezca el almacenamiento del entorno; no supone persistencia garantizada después de recrear/reiniciar el servicio.

**No ejecutes `robot_kame_stock.py` durante las pruebas**: es una herramienta separada que tiene operaciones con Kame y Git, y no se necesita para generar documentos de despacho desde los datos de prueba ya importados.

## Limitaciones y pruebas

- Los datos actuales del respaldo Sheets **no aparecerán** en esta copia de pruebas. Debes cargar un nuevo lote o restaurar **manualmente** un respaldo `.db` aislado, si procede.
- El apagado de Sheets no deshabilita Internet para otras funciones del WMS; exclusivamente bloquea su sincronización/rescate Sheets.
- El código se ha validado con `py_compile` y con los siguientes tests automatizados:

```bash
python tests/test_sin_sheets.py
python tests/test_despacho.py
python tests/test_database_dispatch.py
```

- No se ha ejecutado una prueba integral multi-PDA con Streamlit ni una conversión real contra Labelary con etiquetas del día.

Para conocer todos los detalles de los documentos nuevos, consultar `LEEME_DESPACHO_KAME.md`.
