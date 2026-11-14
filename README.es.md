# IRRADIUM

*Modelado y simulación de irradiación*

**An Integrated Framework for Irradiator Modeling, Simulation and Optimization**

Versión pública inicial **0.3.0-alpha.1**, Linux x86-64. El flujo implementado corresponde a un irradiador gamma de categoría IV con almacenamiento húmedo y transporte suspendido. Las categorías I, II y III son vistas previas y puntos de extensión; sus simuladores aún no están implementados.

[English](README.md) · [Guía de instalación y uso en español](docs/quickstart/IRRADIUM-guia-rapida-es.pdf) · [English quick start](docs/quickstart/IRRADIUM-quick-start-en.pdf)

## Instalación

Descarga el `.tar.gz` de **Releases**, extráelo en una carpeta con permisos de escritura y ejecuta:

```bash
tar -xzf IRRADIUM-0.3.0-alpha.1-linux.tar.gz
cd IRRADIUM
./install.sh
```

No requiere permisos de administrador. Necesita Internet, Linux compatible, Bash, curl, tar y navegador. Reserva al menos **20 GB libres**, además del espacio de los estudios. Python, Node, OpenMC, CAD y sus dependencias se instalan dentro de esta carpeta. No muevas los runtimes Conda ya instalados a otra ubicación.

Los datos nucleares **no se incluyen en el tar.gz ni en Git**. El instalador descarga por streaming la biblioteca oficial ENDF/B-VII.1 de OpenMC y conserva un perfil gamma, verificando cada archivo HDF5 mediante SHA-256. No guarda el archivo comprimido completo. OpenMC 0.15.3 necesita algunos datos de núclidos para inicializar los materiales incluso al transportar solo fotones; no se pueden eliminar todos los datos de neutrones. No se modifica ninguna biblioteca externa. Consulta [procedencia y avisos](DATA_NOTICES.md).

PHITS/EGS5 es opcional, se instala y licencia aparte. Su ruta se configura en `workspace/Configuracion/instalacion.json`. El programa utiliza sus propios datos nucleares locales.

Abre **IRRADIUM** desde el menú de aplicaciones o ejecuta `./Iniciar_IRRADIUM.sh`. La dirección local es <http://127.0.0.1:3001/>. La guía de primera ejecución explica las funciones y se vuelve a abrir con **Primeros pasos**. El selector **English / Español** guarda la preferencia dentro de `workspace`.

## Ejemplo didáctico

Se incluye una configuración completa inspirada en el irradiador comercial **SVST-Co-60/B**: 68 posiciones con coordenadas y giros explícitos, fuentes cilíndricas equivalentes, un STEP lleno de agua y otro de referencia en aire. Crea una copia independiente desde la guía inicial.

El [estudio publicado](https://www.irjet.net/archives/V2/i2/Irjet-v2i2196.pdf) describe los niveles, las pasadas, dimensiones de contenedores y un tiempo de permanencia de referencia. Las coordenadas, distribución individual de fuentes y giros que no publica se identifican como reconstrucciones didácticas en [la procedencia](app/examples/commercial_svst/provenance.json). No es un gemelo validado por el fabricante ni una reproducción de su inventario real.

Una instalación nueva prepara el ejemplo; una instalación existente conserva sus casos e inventario. Cada caso guarda su instantánea de geometría, materiales, fuentes, fecha y recorrido. Una prueba corta sirve para aprender; no garantiza precisión suficiente para decisiones operativas.

## Flujo de trabajo

1. Importa el STEP de la carga y asigna materiales, composición y densidad.
2. Comprueba las fuentes, fecha de irradiación, centros de posiciones y giros.
3. Crea un caso con una instantánea independiente.
4. En **Resultados**, elige recorrido o posiciones, ocupación, método, malla e historias. Puedes pausar y continuar.
5. Inspecciona mapas 3D, materiales, cortes, isodosis, DUR y errores relativos.
6. Integra un intervalo: `dosis = suma(tasa_i × tiempo_i)`. Para resolver tiempos, elige conscientemente dosis mínima, media o máxima como objetivo. Los giros se aplican a la superposición; las vistas individuales mantienen los ejes reales.

OpenMC rápido por flujo y conversión es recomendable para referencias en aire. OpenMC intensivo y PHITS calculan depósito de energía en materiales. La atenuación por cargas vecinas se configura explícitamente. El número de historias no garantiza un error y la incertidumbre estadística no incluye toda la incertidumbre del modelo.

## Organización y desinstalación

Código en `app/` y `scripts/`; dependencias en `runtime/`; datos descargados en `nuclear-data/`; guías bilingües en `docs/quickstart/`. La configuración general, idioma y apariencia están en `workspace/Configuracion/`. Cada categoría guarda sus configuraciones y casos en `workspace/irradiators/<categoría>/`.

Ejecuta `./uninstall.sh`. Pregunta si deseas conservar los estudios y configuraciones; **conservar** es la opción predeterminada. Para borrarlos requiere una confirmación adicional. Retira el programa, sus dependencias, biblioteca local y acceso del menú. No elimina PHITS externo ni otras aplicaciones, archivos ajenos, historial Git de desarrollo o copias creadas por el usuario. Para revisar sin borrar: `./uninstall.sh --dry-run`.

Si conservas los datos, reinstala en la misma carpeta para mantener referencias absolutas históricas. Los registros y pruebas están documentados en [la versión inglesa](README.md).

## Licencia y alcance

Código y documentación originales: [MIT](LICENSE), conservando el aviso original de SIM Gamma. Las dependencias y los datos mantienen sus licencias y atribuciones: [terceros](app/THIRD_PARTY_NOTICES.md), [datos](DATA_NOTICES.md).

Versión inicial de investigación y aprendizaje. No certifica blindajes, controles de seguridad ni la liberación operativa de productos irradiados. Se prevé incorporar las demás categorías y más conjuntos de validación.

El [estudio didáctico de 54 cuerpos](docs/quickstart/results/verification.json) completó 68 posiciones en 22 min 52 s, con 680 millones de historias y un error relativo máximo acumulado del 3,70%. La guía muestra cómo editar los giros alrededor de Z y comprobar su superposición.
