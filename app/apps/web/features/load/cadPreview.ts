import { API_URL } from '../../lib/api';
import type { StepPreviewPart } from '../../components/cad/StepViewer';

export const NOMINAL_LOAD_DIMENSIONS_MM: [number, number, number] = [500, 400, 1000];

export type PreviewResponse = {
  digest?: string;
  dimensions_mm?: [number, number, number] | null;
  material_tags?: string[];
  parts?: Array<Omit<StepPreviewPart, 'material'> & { material?: string }>;
};

export function normalizePreview(response: PreviewResponse) {
  const parts = (response.parts ?? []).map((part) => ({
    ...part,
    material: part.material || part.name.trim().split('_', 1)[0].toLowerCase(),
    url: part.url.startsWith('http') ? part.url : `${API_URL}${part.url}`,
  }));
  const tags = response.material_tags ?? [...new Set(parts.map((part) => part.material).filter(Boolean))].sort();
  return { parts, tags, dimensions: response.dimensions_mm };
}

export async function uploadCadPreview(file: File, signal?: AbortSignal): Promise<PreviewResponse> {
  const form = new FormData();
  form.append('step', file);
  const response = await fetch(`${API_URL}/api/cad/preview`, { method: 'POST', body: form, signal });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}
