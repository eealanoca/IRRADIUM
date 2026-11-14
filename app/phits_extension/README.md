# PHITS extension

Backend PHITS de IRRADIUM. El método reutiliza
la instantánea del caso (STEP, materiales, fuentes y trayectoria) y
construye una geometría tetraédrica NASTRAN para PHITS.

## Modelo físico

- Transporte de fotones, electrones y positrones con EGS5 (`negs = 1`).
- Dosis absorbida directa con `[T-Deposit]`, `output = dose`, `unit = 0`.
- `totfact` contiene la tasa fotónica real, incluido decaimiento y rendimiento
  gamma. `factor = 3.6` convierte Gy/s en kGy/h.
- Las fuentes emiten isotrópicamente en todo el ángulo sólido, como corresponde
  a las fuentes físicas. La reducción de varianza usa `[Importance]` para
  fotones: importancia 1 en aire y 4 al entrar en la caja dinámica de carga.
  Esto divide trayectorias con ajuste de peso y conserva incluso contribuciones
  que se dispersan en aire antes de llegar a la carga.
- PHITS 3.341 se ejecuta mediante su lanzador oficial (`bin/phits.sh`). La
  prueba funcional verificó 500 subsecciones `<source>` por entrada; casos
  mayores se dividen sin descartar fuentes. Los VTK parciales y los VTK de
  error estadístico se combinan.
- En 3.341 `nfile` añade automáticamente `.bdf`, y los nodos NASTRAN se
  convierten a cm: el modelo usa `nfile=load` y `TSFAC=0.01` para conservar
  las dimensiones STEP. `itgchk=1` se reserva para la verificación previa.
- La evaluación usa mínimo, máximo y DUR extremos de todas las celdas de carga;
  no usa percentiles.
- En 3.341 el campo `material` del VTK etiqueta erróneamente como aire algunos
  bins periféricos que sí tienen dosis. La máscara y los ID de material se
  calculan desde los sólidos STEP para no recortar la carga.

## Archivos

- `model.py`: materiales, geometría tetraédrica, fuentes e inputs PHITS.
- `runner.py`: selección MPI/OpenMP y ejecución por el lanzador oficial. Las
  bibliotecas MPICH necesarias se cargan del entorno conda. MPI reserva un
  proceso coordinador y utiliza los núcleos CPU disponibles según el límite
  configurado. Si el sistema no admite MPI, se usa OpenMP. La geometría se
  comprueba una sola vez antes del transporte.
- `postprocess.py`: lectura/suma de VTK, máscara material y extremos crudos.
- `worker_cli.py`: estados, comprobación geométrica, transporte y artefactos web.

La detección prioriza `PHITSPATH`, el lanzador disponible en `PATH` y la
configuración activa del shell. Como ubicación convencional adicional se revisa
`/opt/phits`.
Puede cambiarse con `PHITSPATH`. El ejecutable debe ser compatible con la
GLIBC y sus bibliotecas compartidas deben estar presentes.

## Conversión geométrica automática

La conversión no necesita un LLM. Usa los sólidos CAD nativos con sus nombres,
materiales y colocaciones, incluidos los componentes con varios sólidos. Las
interfaces se fragmentan y mallan juntas para compartir nodos y triángulos;
mallar por separado dos superficies curvas coincidentes produce intersecciones.
Un solapamiento de volumen entre materiales distintos se rechaza identificando
ambos componentes. Los volúmenes coincidentes del mismo material se unifican.

El mallado geométrico es independiente del tamaño de los vóxeles de dosis.
Gmsh se inicia sin leer `.gmshrc` ni `.gmsh-options` del usuario, para que
los factores de tamaño y suavizado de otras aplicaciones no afecten el caso.
Gmsh usa HXT con hasta ocho hilos y Delaunay como alternativa si HXT falla.
Cada región se compara con su volumen CAD exacto. Si la diferencia supera
el 1 %, se refina automáticamente, con un máximo de tres intentos. Si no
converge, el transporte se bloquea con un diagnóstico. Esta diferencia de
volumen no es el error estadístico de la dosis ni una cota de su error físico.
El informe queda en `load.audit.json` y en `cad_to_phits_manifest.json`.
La escritura NASTRAN se hace por flujo, sin duplicar el archivo completo en RAM.
Para geometrías grandes se prioriza OpenMP: comparte la geometría entre hilos
en lugar de replicarla en cada proceso MPI. La selección considera el tamaño
del BDF y el presupuesto de memoria; el límite de recursos sigue activo.

Antes del transporte se ejecutan `itgchk=1` y una comprobación `gshow=2`.
El diagnóstico gráfico tiene un máximo de 24 divisiones por eje, independiente
de los vóxeles de dosis. La comprobación de intersecciones sigue usando todos
los tetraedros. Su resultado satisfactorio se guarda por huella del BDF,
materiales, celdas, superficies, malla y binarios PHITS. Las siguientes
posiciones pueden reutilizarlo cuando esos datos son iguales. Sólo se permite
normalizar la caja exterior de aire si contiene estrictamente ambas cajas
interiores. Un cambio del material, de la malla, de la geometría o del binario
invalida la caché. Los fallos nunca se guardan como comprobaciones válidas.
Cada posición registra `geometry-validation.json`,
`geometry_check_reused` y `geometry_check_wall_seconds`.
Las líneas de muestreo del diagnóstico gshow se desplazan 0,1 micrómetros
para evitar planos exactamente tangentes; la geometría y la malla de dosis
de producción conservan sus coordenadas. Cualquier error TETRA, incluso
con código de salida cero, impide aceptar el cálculo. Los CAD sin sólidos
válidos y los casos que exceden los recursos disponibles requieren corregir
la entrada o el presupuesto; no se alteran materiales para forzar su aceptación.

## Criterios de validación

Las pruebas públicas emplean únicamente fuentes, coordenadas y materiales
sintéticos. Deben comprobar la división de inventarios grandes sin pérdida de
fuentes, la conservación de dimensiones STEP, el depósito no nulo en cada
material, EGS5 activo, cero partículas perdidas y la combinación coherente de
los VTK parciales. Una comparación con OpenMC debe usar la misma geometría,
respuesta dosimétrica, resolución de malla y presupuesto estadístico. Los
extremos y el DUR no se consideran convergidos mientras el error relativo de
las celdas de interés exceda el criterio definido para el caso.
