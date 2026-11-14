import type { IrradiatorCategory } from './types';

export const SERVER_URL = process.env.NEXT_PUBLIC_IRRADIUM_API_URL ?? process.env.NEXT_PUBLIC_SIM_GAMMA_API_URL ?? 'http://127.0.0.1:8001';
export let API_URL = `${SERVER_URL}/irradiators/IV`;

export function selectIrradiator(category: IrradiatorCategory) {
  API_URL = `${SERVER_URL}/irradiators/${category}`;
}

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { cache: 'no-store' });
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<T>;
}

export async function apiJson<T>(path: string, method: 'POST' | 'PUT' | 'DELETE', body?: unknown): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<T>;
}
