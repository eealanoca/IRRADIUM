import { buildStructuredSurface } from './doseSurface';
import type { DoseImage, Vector3 } from './doseViewFrame';

export type RouteDoseField = {
  name: string;
  image: DoseImage & { originalCells?: number };
  centerMm: Vector3;
  yaw: number;
};

export function buildRouteSurface(
  fields: RouteDoseField[],
  axis: number,
  cut: number,
  bottomCenter: boolean,
  visibleMaterials: Set<number> | undefined,
  colorMode: 'dose' | 'error' | 'material',
) {
  const parts = fields.map(({ image, centerMm, yaw }) => {
    const lengths = image.dimensions.map((count, i) => count * image.spacing[i]);
    const half = lengths.map((length) => length / 2);
    const angle = yaw * Math.PI / 180;
    const c = Math.cos(angle), s = Math.sin(angle);
    const horizontalCut = axis < 2 && cut < 100;
    const extent = axis === 0
      ? Math.abs(c) * half[0] + Math.abs(s) * half[1]
      : Math.abs(s) * half[0] + Math.abs(c) * half[1];
    const threshold = extent * (2 * cut / 100 - 1);
    const visibleXY = horizontalCut ? new Uint8Array(image.dimensions[0] * image.dimensions[1]) : undefined;
    if (visibleXY) {
      const [nx, ny] = image.dimensions;
      for (let j = 0; j < ny; j++) {
        const y = (j + .5) * image.spacing[1] - half[1];
        for (let i = 0; i < nx; i++) {
          const x = (i + .5) * image.spacing[0] - half[0];
          visibleXY[i + nx * j] = (axis === 0 ? c * x - s * y : s * x + c * y) <= threshold + 1e-8 ? 1 : 0;
        }
      }
    }
    const filter = visibleXY ? (i: number, j: number) => visibleXY[i + image.dimensions[0] * j] === 1 : undefined;
    const field = colorMode === 'error' && image.relativeErrors
      ? { ...image, values: image.relativeErrors } : image;
    const surface = buildStructuredSurface(field, 2, axis === 2 ? cut : 100, visibleMaterials,
      colorMode === 'material' ? 'material' : 'dose', filter);
    const zOffset = bottomCenter ? 0 : half[2];
    for (let i = 0; i < surface.points.length; i += 3) {
      const x = surface.points[i] - image.origin[0] - half[0];
      const y = surface.points[i + 1] - image.origin[1] - half[1];
      surface.points[i] = centerMm[0] / 10 + c * x - s * y;
      surface.points[i + 1] = centerMm[1] / 10 + s * x + c * y;
      surface.points[i + 2] += centerMm[2] / 10 - image.origin[2] - zOffset;
    }
    if (colorMode === 'error') {
      for (let i = 0; i < surface.values.length; i++) surface.values[i] *= 100;
    }
    return surface;
  });
  const points = new Float32Array(parts.reduce((total, part) => total + part.points.length, 0));
  const polys = new Uint32Array(parts.reduce((total, part) => total + part.polys.length, 0));
  const values = new Float32Array(points.length / 3);
  let pointOffset = 0;
  let polyOffset = 0;
  for (const part of parts) {
    points.set(part.points, pointOffset * 3);
    values.set(part.values, pointOffset);
    for (let i = 0; i < part.polys.length; i += 5) {
      polys[polyOffset + i] = 4;
      for (let j = 1; j <= 4; j++) polys[polyOffset + i + j] = part.polys[i + j] + pointOffset;
    }
    pointOffset += part.values.length;
    polyOffset += part.polys.length;
  }
  return { points, polys, values };
}
