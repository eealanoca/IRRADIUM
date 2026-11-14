export function isAssetLoadError(error: unknown): boolean {
  const message = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
  return /failed to fetch dynamically imported module|error loading dynamically imported module|importing a module script failed|unable to preload css|chunkloaderror|loading chunk .+ failed/i.test(message);
}

export const ASSET_RELOAD_MESSAGE = 'La interfaz se ha actualizado y falta cargar un componente. Guarda los cambios pendientes y recarga la página. La simulación seguirá ejecutándose.';
