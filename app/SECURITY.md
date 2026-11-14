# Política de seguridad

## Reporte

No publique vulnerabilidades, credenciales ni archivos operativos sensibles en un issue público. Informe de forma privada al mantenedor designado del repositorio e incluya versión, impacto, pasos mínimos y evidencia no sensible.

## Alcance

Son especialmente relevantes la ejecución de procesos, carga de CAD, lectura y escritura de rutas, exposición de la API, cancelación de procesos de cálculo y validación del JSON entregado al sistema de control.

## Operación

La configuración predeterminada escucha únicamente en `127.0.0.1`. Un despliegue remoto requiere proxy inverso, TLS, autenticación, autorización, firewall y registros de auditoría. Los archivos de terceros deben tratarse como entradas no confiables.

## Datos sensibles

No adjunte a reportes públicos geometrías propietarias, inventarios reales de fuentes, configuraciones de seguridad, resultados de producción ni licencias de PHITS.

Las publicaciones oficiales contienen exclusivamente un inventario de fuentes y secciones de trabajo sintéticos. Los datos operativos deben configurarse y conservarse en el espacio de trabajo local del operador; nunca deben copiarse al repositorio.
