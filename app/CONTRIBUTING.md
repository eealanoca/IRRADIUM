# Contribuir a SIM Gamma

## Principios

Los cambios deben preservar reproducibilidad, unidades explícitas, separación entre motores y trazabilidad de los casos. Una mejora visual no debe modificar silenciosamente la física ni la normalización.

## Flujo

1. Cree una rama con un alcance único.
2. Añada pruebas para toda corrección o contrato nuevo.
3. No incluya resultados grandes, datos nucleares, geometrías confidenciales ni PHITS.
4. Ejecute las pruebas de API, PHITS, humo determinista, lint y build.
5. Documente cambios de esquema, unidades o comportamiento físico.

## Motores nuevos

Implemente el contrato de simulador en una carpeta independiente. El backend debe recibir una instantánea del caso y producir estado, metadatos, volumen VTK, cortes y resumen común. No introduzca dependencias específicas del motor en la interfaz.

## Cambios físicos

Una solicitud que cambie tallies, normalización, materiales, reducción de varianza o estadística debe incluir caso de referencia, parámetros efectivos y comparación numérica. No ajuste resultados mediante percentiles o factores empíricos sin justificar su dominio de validez.
