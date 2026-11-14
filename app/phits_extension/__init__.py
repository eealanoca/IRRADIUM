"""Integración aislada de PHITS para IRRADIUM.

Todo lo específico del simulador (geometría tetraédrica, fuentes, entrada,
ejecución y posprocesamiento) vive en este paquete para que el backend pueda
reemplazarse sin acoplarlo al modelo OpenMC.
"""

from .cad_workflow import CadToPhitsWorkflow, prepare_phits_from_step
from .availability import PhitsAvailability, detect_phits_availability
from .model import PhitsModel, prepare_phits_model

__all__ = [
    "CadToPhitsWorkflow",
    "PhitsModel",
    "PhitsAvailability",
    "detect_phits_availability",
    "prepare_phits_from_step",
    "prepare_phits_model",
]
