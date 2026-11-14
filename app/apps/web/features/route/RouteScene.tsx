'use client';

import { useMemo } from 'react';
import StepViewer, { type StepPreviewPart, type ViewerPrimitive } from '../../components/cad/StepViewer';
import type { GammaSource } from '../../lib/types';
import type { RoutePosition } from './RouteConfigurationView';

const noParts: StepPreviewPart[] = [];

export default function RouteScene({ positions, sources, size, selected, reference, yawOffset = 0 }: {
  positions: RoutePosition[];
  sources: GammaSource[];
  size: [number, number, number];
  selected?: string;
  reference: 'center' | 'bottom_center';
  yawOffset?: number;
}) {
  const primitives = useMemo<ViewerPrimitive[]>(() => [
    ...positions.map((item): ViewerPrimitive => {
      const [x, y, z] = item.center_mm;
      return { shape: 'box', center: [x, y, z + (reference === 'bottom_center' ? size[2] / 2 : 0)],
        size, yaw: item.yaw_degrees + yawOffset, selected: item.name === selected };
    }),
    ...sources.map((item): ViewerPrimitive => ({
      shape: 'cylinder', center: item.center_point_mm,
      size: [item.diameter_mm ?? 11, item.diameter_mm ?? 11, item.active_length_mm],
    })),
  ], [positions, reference, selected, size, sources, yawOffset]);
  return <StepViewer parts={noParts} primitives={primitives}/>;
}
