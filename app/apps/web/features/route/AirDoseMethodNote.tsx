import { Wind } from 'lucide-react';

export function isAirReference(tags?: string[]) {
  return !!tags?.length && tags.every((tag) => tag === 'airload' || tag === 'air');
}

export default function AirDoseMethodNote() {
  return <aside className="air-dose-guidance" role="note" aria-label="Método recomendado para aire">
    <Wind size={19} aria-hidden="true"/>
    <div><strong>Para aire · OpenMC rápido recomendado</strong>
      <p>Flujo y conversión permite obtener mapas en aire con menor ruido estadístico a igual número de historias. La conversión debe corresponder al medio dosimétrico elegido.</p>
    </div>
  </aside>;
}
