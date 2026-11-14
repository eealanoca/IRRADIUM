"""Time and dose calculations for a sequence of stationary load positions."""
import math

WORKFLOW = "suspended_litters"
SOURCE_DIAMETER_MM = 11.0


def source_endpoints(source):
    lower = tuple(float(value) for value in source["lower_point"])
    upper = tuple(float(value) for value in source["upper_point"])
    if len(lower) != 3 or len(upper) != 3 or not all(math.isfinite(value) for value in (*lower, *upper)):
        raise ValueError("Los extremos de la fuente deben ser finitos")
    if lower[:2] != upper[:2] or lower[2] == upper[2]:
        name = source.get("identifier", "")
        raise ValueError(f"La fuente {name} debe tener los mismos X e Y en ambos extremos y valores Z distintos")
    return sorted((lower, upper), key=lambda point: point[2])


def integrate_dose(rates, mode, value=None, times=None, *, target_rate_kgy_h=None):
    """Use mean rates in kGy/h and residence times in minutes."""
    if not rates or any(not math.isfinite(rate) or rate < 0 for rate in rates):
        raise ValueError("El intervalo debe tener tasas válidas para todas sus posiciones")
    count = len(rates)
    if mode == "by_position":
        if times is None or len(times) != count:
            raise ValueError("Indica un tiempo para cada posición del intervalo")
    else:
        if value is None or not math.isfinite(value) or value <= 0:
            raise ValueError("Indica un valor mayor que cero")
        if mode == "target_dose":
            reference_rate = math.fsum(rates) / count if target_rate_kgy_h is None else target_rate_kgy_h
            if not math.isfinite(reference_rate) or reference_rate <= 0:
                raise ValueError("La dosis objetivo no se alcanza con una tasa de referencia nula o inválida")
            value = value / reference_rate * 60
        times = [value if mode == "uniform" else value / count] * count

    if any(not math.isfinite(minutes) or minutes < 0 for minutes in times):
        raise ValueError("Los tiempos por posición deben ser finitos y no negativos")
    doses = [rate * (minutes / 60) for rate, minutes in zip(rates, times, strict=True)]
    if any(not math.isfinite(dose) for dose in doses):
        raise ValueError("La dosis calculada supera el rango representable")
    try:
        total_minutes = math.fsum(times)
        total_dose = math.fsum(doses)
    except OverflowError as error:
        raise ValueError("El total calculado supera el rango representable") from error
    return {
        "total_minutes": total_minutes,
        "dose_kgy": total_dose,
        "times_minutes": times,
        "doses_kgy": doses,
        "rate_range_kgy_h": [min(rates), max(rates)],
        "effective_rate_kgy_h": total_dose / total_minutes * 60 if total_minutes else None,
    }
