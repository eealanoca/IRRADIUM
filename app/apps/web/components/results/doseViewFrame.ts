import { normalizeRawVti } from './vtiBinary';

export type Vector3 = [number, number, number];

export type DoseImage = {
  dimensions: Vector3;
  origin: Vector3;
  spacing: Vector3;
  values: ArrayLike<number>;
  ghosts?: ArrayLike<number>;
  materialIds?: ArrayLike<number>;
  relativeErrors?: ArrayLike<number>;
};

export const HIDDEN_CELL = 32;

export async function readDoseImage(buffer: ArrayBuffer) {
  const { default: readerModule } = await import('@kitware/vtk.js/IO/XML/XMLImageDataReader');
  const reader = readerModule.newInstance();
  try {
    reader.parseAsArrayBuffer(normalizeRawVti(buffer));
    const image = reader.getOutputData(0);
    const cells = image.getCellData();
    const scalars = cells.getScalars();
    if (!scalars) throw new Error('El VTI no contiene escalares asociados a las celdas.');
    return {
      dimensions: image.getDimensions().map((value: number) => value - 1) as Vector3,
      origin: image.getOrigin() as Vector3,
      spacing: image.getSpacing() as Vector3,
      values: scalars.getData(),
      ghosts: cells.getArrayByName('vtkGhostType')?.getData(),
      materialIds: cells.getArrayByName('material_id')?.getData(),
      relativeErrors: cells.getArrayByName('relative_error')?.getData(),
      scalarName: scalars.getName() || 'absorbed_dose_rate_kgy_h',
    };
  } finally {
    reader.delete();
  }
}

export function isOblique(yaw: number) {
  return Math.abs(yaw / 90 - Math.round(yaw / 90)) > 1e-9;
}

/** Orient a display copy about the load center. Source arrays stay in the native tally frame. */
export function orientDoseImage<T extends DoseImage>(image: T, yaw: number): T {
  const [nx, ny, nz] = image.dimensions;
  const [sx, sy, sz] = image.spacing;
  const halfX = nx * sx / 2, halfY = ny * sy / 2;
  const quarterTurn = ((Math.round(yaw / 90) % 4) + 4) % 4;
  const oblique = isOblique(yaw);
  const angle = (yaw % 360) * Math.PI / 180;
  const c = oblique ? Math.cos(angle) : [1, 0, -1, 0][quarterTurn];
  const s = oblique ? Math.sin(angle) : [0, 1, 0, -1][quarterTurn];
  const worldHalfX = Math.abs(c) * halfX + Math.abs(s) * halfY;
  const worldHalfY = Math.abs(s) * halfX + Math.abs(c) * halfY;
  const origin: Vector3 = [-worldHalfX, -worldHalfY, -nz * sz / 2];
  if (!oblique && quarterTurn === 0) return { ...image, origin };

  // Cardinal orientations use exact index permutations. Oblique views sample
  // cell centers on a bounded world grid so the interactive cuts follow global axes.
  const swap = quarterTurn % 2 === 1;
  const [dx, dy] = oblique ? [nx, ny] : swap ? [ny, nx] : [nx, ny];
  const wx = oblique ? 2 * worldHalfX / dx : swap ? sy : sx;
  const wy = oblique ? 2 * worldHalfY / dy : swap ? sx : sy;
  const size = dx * dy * nz;
  const values = new Float32Array(size);
  const ghosts = new Uint8Array(size);
  const materialIds = image.materialIds && new Uint32Array(size);
  const relativeErrors = image.relativeErrors && new Float32Array(size);
  for (let j = 0; j < dy; j++) {
    for (let i = 0; i < dx; i++) {
      const x = origin[0] + (i + .5) * wx;
      const y = origin[1] + (j + .5) * wy;
      const u = Math.floor((c * x + s * y + halfX) / sx);
      const v = Math.floor((-s * x + c * y + halfY) / sy);
      const inside = u >= 0 && u < nx && v >= 0 && v < ny;
      for (let k = 0; k < nz; k++) {
        const target = i + dx * (j + dy * k);
        if (!inside) {
          ghosts[target] = HIDDEN_CELL;
          continue;
        }
        const source = u + nx * (v + ny * k);
        values[target] = image.values[source];
        ghosts[target] = image.ghosts?.[source] ?? 0;
        if (materialIds && image.materialIds) materialIds[target] = image.materialIds[source];
        if (relativeErrors && image.relativeErrors) relativeErrors[target] = image.relativeErrors[source];
      }
    }
  }
  return { ...image, origin, dimensions: [dx, dy, nz], spacing: [wx, wy, sz], values, ghosts, materialIds, relativeErrors };
}
