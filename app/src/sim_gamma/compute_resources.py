"""Resolución portable de los recursos de cómputo disponibles para un proceso."""

import os


def available_cpu_threads() -> int:
    """Devuelve los CPU lógicos permitidos por Linux, un contenedor o un job HPC."""

    try:
        affinity = os.sched_getaffinity(0)
        if affinity:
            return len(affinity)
    except (AttributeError, OSError):
        pass
    return max(1, os.cpu_count() or 1)


def resolve_cpu_threads(requested: int) -> int:
    """Convierte -1 en todos los CPU permitidos y aplica el límite solicitado."""

    available = available_cpu_threads()
    requested = int(requested)
    if requested == -1:
        return available
    if requested < 1:
        raise ValueError("El límite de núcleos CPU debe ser -1 o un entero positivo")
    return min(requested, available)
