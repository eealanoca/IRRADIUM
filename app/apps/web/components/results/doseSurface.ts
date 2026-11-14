import { HIDDEN_CELL, type DoseImage, type Vector3 } from './doseViewFrame';

// Only the browser preview is sampled. The downloaded field and all metrics
// retain the complete calculation mesh.
export function boundedPreview<T extends DoseImage>(data: T, limit = 400_000): T & { originalCells?: number } {
  if (data.values.length <= limit) return data;
  let stride = Math.ceil(Math.cbrt(data.values.length / limit));
  const getDimensions = () => data.dimensions.map((size) => Math.ceil(size / stride)) as Vector3;
  let dimensions = getDimensions();
  let count = dimensions[0] * dimensions[1] * dimensions[2];
  while (count > limit) {
    stride *= 2;
    dimensions = getDimensions();
    count = dimensions[0] * dimensions[1] * dimensions[2];
  }
  const values = new Float32Array(count);
  const ghosts = data.ghosts ? new Uint8Array(count) : undefined;
  const materialIds = data.materialIds ? new Int32Array(count) : undefined;
  const relativeErrors = data.relativeErrors ? new Float32Array(count) : undefined;
  const [nx, ny] = data.dimensions;
  for (let k = 0; k < dimensions[2]; k += 1) for (let j = 0; j < dimensions[1]; j += 1) for (let i = 0; i < dimensions[0]; i += 1) {
    const source = Math.min(data.dimensions[0] - 1, Math.floor((i + .5) * data.dimensions[0] / dimensions[0]))
      + nx * (Math.min(data.dimensions[1] - 1, Math.floor((j + .5) * data.dimensions[1] / dimensions[1]))
      + ny * Math.min(data.dimensions[2] - 1, Math.floor((k + .5) * data.dimensions[2] / dimensions[2])));
    const target = i + dimensions[0] * (j + dimensions[1] * k);
    values[target] = data.values[source];
    if (ghosts && data.ghosts) ghosts[target] = data.ghosts[source];
    if (materialIds && data.materialIds) materialIds[target] = data.materialIds[source];
    if (relativeErrors && data.relativeErrors) relativeErrors[target] = data.relativeErrors[source];
  }
  return { ...data, dimensions, values, ghosts, materialIds, relativeErrors, originalCells: data.values.length,
    spacing: data.spacing.map((step, axis) => step * data.dimensions[axis] / dimensions[axis]) as [number, number, number] };
}

export function structuredValueExtent(dataset: DoseImage, visibleMaterials?: Set<number>, fallback: readonly [number, number] = [0, 1]) {
  let minimum = Number.POSITIVE_INFINITY;
  let maximum = Number.NEGATIVE_INFINITY;
  for (let index = 0; index < dataset.values.length; index += 1) {
    if (dataset.ghosts && (dataset.ghosts[index] & HIDDEN_CELL)) continue;
    if (dataset.materialIds && visibleMaterials && !visibleMaterials.has(dataset.materialIds[index])) continue;
    const value = dataset.values[index];
    if (!Number.isFinite(value)) continue;
    if (value < minimum) minimum = value;
    if (value > maximum) maximum = value;
  }
  if (!Number.isFinite(minimum) || !Number.isFinite(maximum)) return fallback;
  return [minimum, maximum] as const;
}

export function buildStructuredSurface(
  dataset: DoseImage,
  axis: number,
  cut: number,
  visibleMaterials?: Set<number>,
  colorMode: 'dose' | 'material' = 'dose',
  includeCell?: (i: number, j: number, k: number) => boolean,
) {
  const [nx, ny, nz] = dataset.dimensions;
  const limits = [nx, ny, nz];
  limits[axis] = Math.max(1, Math.ceil(limits[axis] * cut / 100));
  const [ox, oy, oz] = dataset.origin;
  const [sx, sy, sz] = dataset.spacing;
  const points: number[] = [];
  const polys: number[] = [];
  const values: number[] = [];

  const cellId = (i: number, j: number, k: number) => i + nx * (j + ny * k);
  const visible = (i: number, j: number, k: number) => {
    if (i < 0 || j < 0 || k < 0 || i >= limits[0] || j >= limits[1] || k >= limits[2]) return false;
    if (includeCell && !includeCell(i, j, k)) return false;
    const id = cellId(i, j, k);
    if (dataset.ghosts && (dataset.ghosts[id] & HIDDEN_CELL)) return false;
    if (!Number.isFinite(dataset.values[id])) return false;
    if (dataset.materialIds && visibleMaterials && !visibleMaterials.has(dataset.materialIds[id])) return false;
    return true;
  };
  const addFace = (corners: number[][], value: number) => {
    const start = points.length / 3;
    corners.forEach((corner) => points.push(corner[0], corner[1], corner[2]));
    values.push(value, value, value, value);
    polys.push(4, start, start + 1, start + 2, start + 3);
  };

  for (let k = 0; k < limits[2]; k += 1) {
    const z0 = oz + k * sz;
    const z1 = z0 + sz;
    for (let j = 0; j < limits[1]; j += 1) {
      const y0 = oy + j * sy;
      const y1 = y0 + sy;
      for (let i = 0; i < limits[0]; i += 1) {
        if (!visible(i, j, k)) continue;
        const id = cellId(i, j, k);
        const value = colorMode === 'material' && dataset.materialIds
          ? dataset.materialIds[id]
          : dataset.values[id];
        const x0 = ox + i * sx;
        const x1 = x0 + sx;
        if (!visible(i - 1, j, k)) addFace([[x0, y0, z0], [x0, y0, z1], [x0, y1, z1], [x0, y1, z0]], value);
        if (!visible(i + 1, j, k)) addFace([[x1, y0, z0], [x1, y1, z0], [x1, y1, z1], [x1, y0, z1]], value);
        if (!visible(i, j - 1, k)) addFace([[x0, y0, z0], [x1, y0, z0], [x1, y0, z1], [x0, y0, z1]], value);
        if (!visible(i, j + 1, k)) addFace([[x0, y1, z0], [x0, y1, z1], [x1, y1, z1], [x1, y1, z0]], value);
        if (!visible(i, j, k - 1)) addFace([[x0, y0, z0], [x0, y1, z0], [x1, y1, z0], [x1, y0, z0]], value);
        if (!visible(i, j, k + 1)) addFace([[x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], value);
      }
    }
  }
  return {
    points: new Float32Array(points),
    polys: new Uint32Array(polys),
    values: new Float32Array(values),
  };
}

