'use client';

import { doseFieldStatistics } from './doseStatistics';

import { viewerBackground } from '../../lib/viewerAppearance';

import { useEffect, useMemo, useRef, useState } from 'react';
import { Box, Crosshair, Layers3, Rotate3D, SlidersHorizontal } from 'lucide-react';

import { HIDDEN_CELL, orientDoseImage, readDoseImage, type DoseImage } from './doseViewFrame';

type DoseField = {
  dimensions: [number, number, number];
  origin: [number, number, number];
  spacing: [number, number, number];
  values: Float32Array;
  referenceDose: number;
  meanDose: number;
  minimumDose: number;
  maximumDose: number;
  quantiles: { p05: number; p10: number; p25: number; p50: number; p75: number; p90: number; p95: number };
  stride: number;
};

type SceneController = {
  updateSlice: (axis: number, percent: number) => void;
  updateVolumeTransparency: (transparency: number) => void;
  updateVisibleItems: (items: string[]) => void;
};

type IsoLevel = {
  id: string;
  label: string;
  dose: number;
  detail: string;
  color: [number, number, number];
  opacity: number;
};

type IsoItem = {
  id: string;
  label: string;
  detail: string;
  color: [number, number, number];
  opacity: number;
  lowerDose: number;
  upperDose?: number;
  boundaryIds: string[];
};

type LevelMode = 'uniformity' | 'adaptive' | 'p99' | 'absolute';
type RepresentationMode = 'surfaces' | 'bands';

const MAX_VISUALIZATION_VOXELS = 360_000;
const LEVEL_STYLES: Array<{ color: [number, number, number]; opacity: number }> = [
  { color: [0.16, 0.44, 0.96], opacity: 0.11 },
  { color: [0.12, 0.75, 0.91], opacity: 0.14 },
  { color: [0.19, 0.78, 0.48], opacity: 0.18 },
  { color: [0.98, 0.72, 0.16], opacity: 0.23 },
  { color: [0.94, 0.24, 0.24], opacity: 0.31 },
];

const P99_LEVELS = [20, 40, 60, 80, 95];
const ADAPTIVE_PERCENTILES = [10, 25, 50, 75, 90] as const;

function levelStyle(index: number, count: number) {
  if (count <= 1) return LEVEL_STYLES[2];
  const styleIndex = Math.round(index * (LEVEL_STYLES.length - 1) / (count - 1));
  return LEVEL_STYLES[styleIndex];
}

function niceStep(value: number) {
  const power = 10 ** Math.floor(Math.log10(Math.max(value, 0.1)));
  const normalized = value / power;
  const factor = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 3 ? 3 : normalized <= 5 ? 5 : 10;
  return factor * power;
}

function defaultAbsoluteDoses(field: DoseField) {
  const span = field.maximumDose - field.minimumDose;
  return [0.1, 0.3, 0.5, 0.7, 0.9].map((fraction) => field.minimumDose + span * fraction);
}

function parseAbsoluteDoses(text: string | null, field: DoseField) {
  if (text === null) return defaultAbsoluteDoses(field);
  const values = text.split(/[;,\s]+/).map(Number).filter((value) => Number.isFinite(value) && value > 0);
  return [...new Set(values)].sort((left, right) => left - right).slice(0, 8);
}

function buildLevels(field: DoseField, mode: LevelMode, absoluteText: string | null, unit: string): IsoLevel[] {
  let definitions: Array<{ id: string; label: string; dose: number; detail: string }>;
  if (mode === 'uniformity') {
    const robustDeviation = Math.max(
      Math.abs(field.quantiles.p05 / field.meanDose * 100 - 100),
      Math.abs(field.quantiles.p95 / field.meanDose * 100 - 100),
    );
    const step = niceStep(Math.max(robustDeviation / 2, 1));
    const percentages = [100 - 2 * step, 100 - step, 100, 100 + step, 100 + 2 * step];
    definitions = percentages.map((percent) => ({
      id: `mean-${percent.toFixed(3)}`,
      label: `${Number(percent.toFixed(2))}%`,
      dose: field.meanDose * percent / 100,
      detail: 'de la media',
    }));
  } else if (mode === 'adaptive') {
    const quantileValues = [field.quantiles.p10, field.quantiles.p25, field.quantiles.p50, field.quantiles.p75, field.quantiles.p90];
    definitions = ADAPTIVE_PERCENTILES.map((percentile, index) => ({
      id: `p-${percentile}`,
      label: `P${percentile}`,
      dose: quantileValues[index],
      detail: `percentil volumétrico ${percentile}`,
    }));
  } else if (mode === 'p99') {
    definitions = P99_LEVELS.map((percent) => ({
      id: `p99-${percent}`,
      label: `${percent}%`,
      dose: field.referenceDose * percent / 100,
      detail: 'de P99',
    }));
  } else {
    definitions = parseAbsoluteDoses(absoluteText, field).map((dose, index) => ({
      id: `absolute-${index}-${dose.toPrecision(8)}`,
      label: `${dose.toExponential(2)}`,
      dose,
      detail: `${unit} absoluto`,
    }));
  }
  return definitions.filter((definition) => Number.isFinite(definition.dose) && definition.dose > 0)
    .map((definition, index, available) => ({ ...definition, ...levelStyle(index, available.length) }));
}

function buildItems(levels: IsoLevel[], representation: RepresentationMode, unit: string): IsoItem[] {
  if (representation === 'surfaces') {
    return levels.map((level) => ({
      id: `surface-${level.id}`,
      label: level.label,
      detail: `${level.dose.toExponential(2)} ${unit}`,
      color: level.color,
      opacity: level.opacity,
      lowerDose: level.dose,
      boundaryIds: [level.id],
    }));
  }
  return levels.slice(0, -1).map((level, index) => {
    const upper = levels[index + 1];
    const style = levelStyle(index, Math.max(levels.length - 1, 1));
    return {
      id: `band-${level.id}-${upper.id}`,
      label: `${level.label}–${upper.label}`,
      detail: `${level.dose.toExponential(2)}–${upper.dose.toExponential(2)} ${unit}`,
      color: style.color,
      opacity: Math.min(0.34, style.opacity + 0.07),
      lowerDose: level.dose,
      upperDose: upper.dose,
      boundaryIds: [level.id, upper.id],
    };
  });
}


function colorCss(color: [number, number, number]) {
  return `rgb(${color.map((value) => Math.round(value * 255)).join(',')})`;
}

function blurAxis(
  source: Float32Array,
  dimensions: [number, number, number],
  axis: number,
) {
  const [nx, ny, nz] = dimensions;
  const target = new Float32Array(source.length);
  const id = (i: number, j: number, k: number) => i + nx * (j + ny * k);
  for (let k = 0; k < nz; k += 1) {
    for (let j = 0; j < ny; j += 1) {
      for (let i = 0; i < nx; i += 1) {
        const before = [i, j, k];
        const after = [i, j, k];
        before[axis] = Math.max(0, before[axis] - 1);
        after[axis] = Math.min(dimensions[axis] - 1, after[axis] + 1);
        target[id(i, j, k)] = (
          source[id(before[0], before[1], before[2])]
          + 2 * source[id(i, j, k)]
          + source[id(after[0], after[1], after[2])]
        ) / 4;
      }
    }
  }
  return target;
}

function smooth(values: Float32Array, dimensions: [number, number, number], passes: number) {
  let current = values;
  for (let pass = 0; pass < passes; pass += 1) {
    current = blurAxis(current, dimensions, 0);
    current = blurAxis(current, dimensions, 1);
    current = blurAxis(current, dimensions, 2);
  }
  return current;
}

function buildDoseField(dataset: DoseImage, smoothingPasses: number): DoseField {
  const [nx, ny, nz] = dataset.dimensions;
  const sourceVoxelCount = nx * ny * nz;
  const stride = Math.max(1, Math.ceil(Math.cbrt(sourceVoxelCount / MAX_VISUALIZATION_VOXELS)));
  const sampledDimensions = [
    Math.ceil(nx / stride),
    Math.ceil(ny / stride),
    Math.ceil(nz / stride),
  ] as [number, number, number];
  const [dx, dy, dz] = sampledDimensions;
  const sampledSpacing = dataset.spacing.map((value) => value * stride) as [number, number, number];
  const sampledOrigin = dataset.origin.map((value, axis) => value + sampledSpacing[axis] / 2) as [number, number, number];
  const sampled = new Float32Array(dx * dy * dz);
  const originalId = (i: number, j: number, k: number) => i + nx * (j + ny * k);
  const sampledId = (i: number, j: number, k: number) => i + dx * (j + dy * k);

  for (let sk = 0; sk < dz; sk += 1) {
    for (let sj = 0; sj < dy; sj += 1) {
      for (let si = 0; si < dx; si += 1) {
        let total = 0;
        let count = 0;
        const kLimit = Math.min(nz, (sk + 1) * stride);
        const jLimit = Math.min(ny, (sj + 1) * stride);
        const iLimit = Math.min(nx, (si + 1) * stride);
        for (let k = sk * stride; k < kLimit; k += 1) {
          for (let j = sj * stride; j < jLimit; j += 1) {
            for (let i = si * stride; i < iLimit; i += 1) {
              const sourceId = originalId(i, j, k);
              if (dataset.ghosts && (dataset.ghosts[sourceId] & HIDDEN_CELL)) continue;
              const value = dataset.values[sourceId];
              if (!Number.isFinite(value) || value < 0) continue;
              total += value;
              count += 1;
            }
          }
        }
        sampled[sampledId(si, sj, sk)] = count ? total / count : 0;
      }
    }
  }

  // El kernel usa extensión por borde (índices limitados), no una corona de
  // ceros. Así el suavizado no introduce una caída de dosis inexistente.
  const values = smooth(sampled, sampledDimensions, smoothingPasses);
  const statistics = doseFieldStatistics(dataset);

  return {
    dimensions: sampledDimensions,
    origin: sampledOrigin,
    spacing: sampledSpacing,
    values,
    ...statistics,
    stride,
  };
}

function buildSliceSurface(field: DoseField, axis: number, slice: number) {
  const dimensions = field.dimensions;
  const [nx, ny] = axis === 2
    ? [dimensions[0], dimensions[1]]
    : axis === 1
      ? [dimensions[0], dimensions[2]]
      : [dimensions[1], dimensions[2]];
  const points = new Float32Array(nx * ny * 3);
  const values = new Float32Array(nx * ny);
  const polys = new Uint32Array((nx - 1) * (ny - 1) * 5);
  const sourceId = (i: number, j: number, k: number) => i + dimensions[0] * (j + dimensions[1] * k);
  let pointOffset = 0;
  for (let v = 0; v < ny; v += 1) {
    for (let u = 0; u < nx; u += 1) {
      const ijk = axis === 2 ? [u, v, slice] : axis === 1 ? [u, slice, v] : [slice, u, v];
      for (let component = 0; component < 3; component += 1) {
        points[pointOffset * 3 + component] = field.origin[component] + ijk[component] * field.spacing[component];
      }
      values[pointOffset] = field.values[sourceId(ijk[0], ijk[1], ijk[2])];
      pointOffset += 1;
    }
  }
  let polygonOffset = 0;
  for (let v = 0; v < ny - 1; v += 1) {
    for (let u = 0; u < nx - 1; u += 1) {
      const first = u + nx * v;
      polys.set([4, first, first + 1, first + nx + 1, first + nx], polygonOffset);
      polygonOffset += 5;
    }
  }
  return { points, values, polys };
}

function sliceIndex(field: DoseField, axis: number, percent: number) {
  const pointCount = Math.max(1, field.dimensions[axis]);
  return Math.round((pointCount - 1) * percent / 100);
}

function slicePosition(field: DoseField, axis: number, percent: number) {
  return field.origin[axis] + sliceIndex(field, axis, percent) * field.spacing[axis];
}

export default function Isodose3DViewer({ url, format, unit = 'kGy/h', positionYaw }: { url: string; format: 'vtk' | 'vti'; unit?: string; positionYaw?: number }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const labelsRef = useRef<HTMLDivElement>(null);
  const axisLabelsRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<SceneController | null>(null);
  const axisRef = useRef(2);
  const sliceRef = useRef(50);
  const transparencyRef = useRef(45);
  const visibleItemsRef = useRef(new Set<string>());
  const [dataset, setDataset] = useState<DoseImage | null>(null);
  const [axis, setAxis] = useState(2);
  const [slicePercent, setSlicePercent] = useState(50);
  const [volumeTransparency, setVolumeTransparency] = useState(45);
  const [smoothingPasses, setSmoothingPasses] = useState(2);
  const [levelMode, setLevelMode] = useState<LevelMode>('uniformity');
  const [representationMode, setRepresentationMode] = useState<RepresentationMode>('surfaces');
  const [absoluteText, setAbsoluteText] = useState<string | null>(null);
  const [hiddenItems, setHiddenItems] = useState<string[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    axisRef.current = axis;
    sliceRef.current = slicePercent;
    transparencyRef.current = volumeTransparency;
  }, [axis, slicePercent, volumeTransparency]);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    if (format !== 'vti') {
      return () => { cancelled = true; controller.abort(); };
    }
    fetch(url, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error('No fue posible descargar la malla VTI.');
        const image = await readDoseImage(await response.arrayBuffer());
        return positionYaw === undefined ? image : orientDoseImage(image, positionYaw);
      })
      .then((nextDataset) => {
        if (cancelled) return;
        setDataset(nextDataset);
        setError('');
      })
      .catch((caught: Error) => { if (!cancelled) setError(caught.message); });
    return () => { cancelled = true; controller.abort(); };
  }, [format, positionYaw, url]);

  const fieldBuild = useMemo(() => {
    if (!dataset) return { field: null, error: '' };
    try {
      return { field: buildDoseField(dataset, smoothingPasses), error: '' };
    } catch (caught) {
      return {
        field: null,
        error: caught instanceof Error ? caught.message : 'No fue posible construir las isodosis.',
      };
    }
  }, [dataset, smoothingPasses]);
  const field = fieldBuild.field;
  const levels = useMemo(
    () => field ? buildLevels(field, levelMode, absoluteText, unit) : [],
    [absoluteText, field, levelMode, unit],
  );
  const items = useMemo(
    () => buildItems(levels, representationMode, unit),
    [levels, representationMode, unit],
  );
  const availableItems = useMemo(
    () => items.filter((item) => {
      const lowerInside = item.lowerDose >= (field?.minimumDose ?? 0) && item.lowerDose <= (field?.maximumDose ?? 0);
      const upperInside = item.upperDose === undefined
        || (item.upperDose >= (field?.minimumDose ?? 0) && item.upperDose <= (field?.maximumDose ?? 0));
      return representationMode === 'bands'
        ? item.upperDose !== undefined && item.upperDose > (field?.minimumDose ?? 0) && item.lowerDose < (field?.maximumDose ?? 0)
        : lowerInside && upperInside;
    }),
    [field, items, representationMode],
  );
  const visibleItemIds = useMemo(
    () => availableItems.filter((item) => !hiddenItems.includes(item.id)).map((item) => item.id),
    [availableItems, hiddenItems],
  );
  useEffect(() => {
    if (!field || !containerRef.current) return;
    const container = containerRef.current;
    let disposed = false;
    let dispose = () => undefined;
    const render = async () => {
      await import('@kitware/vtk.js/Rendering/Profiles/Geometry');
      const [
        windowModule,
        imageDataModule,
        polyDataModule,
        dataArrayModule,
        mapperModule,
        actorModule,
        colorModule,
        scalarBarModule,
        cubesModule,
        squaresModule,
        outlineModule,
        styleModule,
        rotateModule,
        panModule,
        zoomModule,
        axesModule,
        orientationModule,
      ] = await Promise.all([
        import('@kitware/vtk.js/Rendering/Misc/GenericRenderWindow'),
        import('@kitware/vtk.js/Common/DataModel/ImageData'),
        import('@kitware/vtk.js/Common/DataModel/PolyData'),
        import('@kitware/vtk.js/Common/Core/DataArray'),
        import('@kitware/vtk.js/Rendering/Core/Mapper'),
        import('@kitware/vtk.js/Rendering/Core/Actor'),
        import('@kitware/vtk.js/Rendering/Core/ColorTransferFunction'),
        import('@kitware/vtk.js/Rendering/Core/ScalarBarActor'),
        import('@kitware/vtk.js/Filters/General/ImageMarchingCubes'),
        import('@kitware/vtk.js/Filters/General/ImageMarchingSquares'),
        import('@kitware/vtk.js/Filters/General/ImageDataOutlineFilter'),
        import('@kitware/vtk.js/Interaction/Style/InteractorStyleManipulator'),
        import('@kitware/vtk.js/Interaction/Manipulators/MouseCameraTrackballRotateManipulator'),
        import('@kitware/vtk.js/Interaction/Manipulators/MouseCameraTrackballPanManipulator'),
        import('@kitware/vtk.js/Interaction/Manipulators/MouseCameraTrackballZoomManipulator'),
        import('@kitware/vtk.js/Rendering/Core/AxesActor'),
        import('@kitware/vtk.js/Interaction/Widgets/OrientationMarkerWidget'),
      ]);
      if (disposed) return;

      const imageData = imageDataModule.default.newInstance();
      imageData.setDimensions(...field.dimensions);
      imageData.setOrigin(field.origin);
      imageData.setSpacing(field.spacing);
      const doseArray = dataArrayModule.default.newInstance({
        name: 'smoothed_absorbed_dose_rate_kgy_h',
        values: field.values,
        numberOfComponents: 1,
      });
      imageData.getPointData().setScalars(doseArray);

      const view = windowModule.default.newInstance({ background: viewerBackground() });
      view.setContainer(container);
      const renderer = view.getRenderer();
      const renderWindow = view.getRenderWindow();
      const interactorStyle = styleModule.default.newInstance();
      interactorStyle.addMouseManipulator(rotateModule.default.newInstance({ button: 1, useFocalPointAsCenterOfRotation: true }));
      interactorStyle.addMouseManipulator(panModule.default.newInstance({ button: 2 }));
      interactorStyle.addMouseManipulator(zoomModule.default.newInstance({ button: 3, dragEnabled: true, scrollEnabled: true }));
      view.getInteractor().setInteractorStyle(interactorStyle);

      const colors = colorModule.default.newInstance();
      colors.addRGBPoint(field.minimumDose, ...LEVEL_STYLES[0].color);
      levels.forEach((level) => colors.addRGBPoint(level.dose, ...level.color));
      colors.addRGBPoint(field.maximumDose, ...LEVEL_STYLES[LEVEL_STYLES.length - 1].color);
      const availableItemIds = new Set(availableItems.map((item) => item.id));
      const initiallyVisibleItems = new Set(visibleItemsRef.current);
      const visibleBoundaryIds = (visibleIds: Set<string>) => new Set(
        items.filter((item) => visibleIds.has(item.id)).flatMap((item) => item.boundaryIds),
      );

      const surfaceObjects: Array<{ delete: () => void }> = [];
      const surfaceActors: Array<{ actor: ReturnType<typeof actorModule.default.newInstance>; item: IsoItem }> = [];
      const surfaceOpacity = (item: IsoItem, transparency: number) => {
        const visibleFraction = Math.max(0, Math.min(1, (100 - transparency) / 55));
        return Math.min(1, item.opacity * visibleFraction);
      };
      items.forEach((item) => {
        let contourInput = imageData;
        if (item.upperDose !== undefined) {
          const bandValues = new Float32Array(field.values.length);
          for (let index = 0; index < field.values.length; index += 1) {
            const value = field.values[index];
            bandValues[index] = value >= item.lowerDose && value < item.upperDose ? 1 : 0;
          }
          const bandImage = imageDataModule.default.newInstance();
          bandImage.setDimensions(...field.dimensions);
          bandImage.setOrigin(field.origin);
          bandImage.setSpacing(field.spacing);
          const bandArray = dataArrayModule.default.newInstance({
            name: `dose_band_${item.id}`,
            values: bandValues,
            numberOfComponents: 1,
          });
          bandImage.getPointData().setScalars(bandArray);
          contourInput = bandImage;
          surfaceObjects.push(bandArray, bandImage);
        }
        const contour = cubesModule.default.newInstance({
          contourValue: item.upperDose === undefined ? item.lowerDose : 0.5,
          computeNormals: true,
          mergePoints: true,
        });
        contour.setInputData(contourInput);
        const mapper = mapperModule.default.newInstance();
        mapper.setInputConnection(contour.getOutputPort());
        mapper.setScalarVisibility(false);
        const actor = actorModule.default.newInstance();
        actor.setMapper(mapper);
        actor.getProperty().setColor(...item.color);
        actor.getProperty().setOpacity(surfaceOpacity(item, transparencyRef.current));
        actor.getProperty().setInterpolationToPhong();
        actor.getProperty().setAmbient(0.24);
        actor.getProperty().setDiffuse(0.66);
        actor.getProperty().setSpecular(0.28);
        actor.getProperty().setSpecularPower(22);
        actor.setVisibility(initiallyVisibleItems.has(item.id) && availableItemIds.has(item.id));
        renderer.addActor(actor);
        surfaceActors.push({ actor, item });
        surfaceObjects.push(actor, mapper, contour);
      });

      const slicePolyData = polyDataModule.default.newInstance();
      const sliceScalars = dataArrayModule.default.newInstance({
        name: 'smoothed_absorbed_dose_rate_kgy_h',
        values: new Float32Array(),
        numberOfComponents: 1,
      });
      slicePolyData.getPointData().setScalars(sliceScalars);
      const sliceMapper = mapperModule.default.newInstance();
      sliceMapper.setInputData(slicePolyData);
      sliceMapper.setLookupTable(colors);
      sliceMapper.setScalarRange(field.minimumDose, field.maximumDose);
      sliceMapper.setUseLookupTableScalarRange(true);
      const sliceActor = actorModule.default.newInstance();
      sliceActor.setMapper(sliceMapper);
      sliceActor.getProperty().setOpacity(0.76);
      sliceActor.getProperty().setAmbient(0.82);
      sliceActor.getProperty().setDiffuse(0.18);
      renderer.addActor(sliceActor);

      const initialBoundaryIds = visibleBoundaryIds(initiallyVisibleItems);
      const contourObjects = levels.map((level, levelIndex) => {
        const contour = squaresModule.default.newInstance({
          contourValues: [level.dose],
          slicingMode: axisRef.current,
          slice: sliceIndex(field, axisRef.current, sliceRef.current),
          mergePoints: true,
        });
        contour.setInputData(imageData);
        const mapper = mapperModule.default.newInstance();
        mapper.setInputConnection(contour.getOutputPort());
        mapper.setScalarVisibility(false);
        const actor = actorModule.default.newInstance();
        actor.setMapper(mapper);
        actor.getProperty().setColor(...level.color);
        actor.getProperty().setLineWidth(levelIndex >= levels.length - 2 ? 3.2 : 2.4);
        actor.setVisibility(initialBoundaryIds.has(level.id));
        renderer.addActor(actor);
        return { level, contour, mapper, actor };
      });

      const outline = outlineModule.default.newInstance();
      outline.setInputData(imageData);
      const outlineMapper = mapperModule.default.newInstance();
      outlineMapper.setInputConnection(outline.getOutputPort());
      const outlineActor = actorModule.default.newInstance();
      outlineActor.setMapper(outlineMapper);
      outlineActor.getProperty().setColor(0.42, 0.50, 0.66);
      outlineActor.getProperty().setOpacity(0.55);
      outlineActor.getProperty().setLineWidth(1.2);
      renderer.addActor(outlineActor);

      const legendTextColor = document.documentElement.dataset.theme === 'dark' ? '#eef3ff' : '#17233c';
      const scalarBar = scalarBarModule.default.newInstance({
        axisLabel: unit === 'kGy' ? 'Dosis absorbida [kGy]' : 'Tasa de dosis absorbida [kGy/h]',
        automated: true,
        drawNanAnnotation: false,
        drawBelowRangeSwatch: false,
        drawAboveRangeSwatch: false,
      });
      scalarBar.setScalarsToColors(colors);
      scalarBar.setOrientationToVertical();
      scalarBar.setAxisTextStyle({ fontColor: legendTextColor, fontFamily: 'Arial', fontSize: 14 });
      scalarBar.setTickTextStyle({ fontColor: legendTextColor, fontFamily: 'Arial', fontSize: 12 });
      renderer.addActor(scalarBar);

      const axesActor = axesModule.default.newInstance({
        config: { recenter: false, tipResolution: 60, tipRadius: 0.12, tipLength: 0.24, shaftResolution: 60, shaftRadius: 0.035, invert: false },
        xConfig: { color: [220, 64, 86], invert: false },
        yConfig: { color: [37, 160, 112], invert: false },
        zConfig: { color: [49, 95, 202], invert: false },
      } as never);
      const orientationWidget = orientationModule.default.newInstance({ actor: axesActor, interactor: view.getInteractor(), parentRenderer: renderer });
      orientationWidget.setViewportCorner(orientationModule.default.Corners.BOTTOM_LEFT);
      orientationWidget.setViewportSize(0.17);
      orientationWidget.setMinPixelSize(90);
      orientationWidget.setMaxPixelSize(160);
      orientationWidget.setEnabled(true);

      const labelAnchors = new Map<string, [number, number, number]>();
      const axisLabelPoints = [
        { axis: 'x', point: [1.13, 0, 0] as const },
        { axis: 'y', point: [0, 1.13, 0] as const },
        { axis: 'z', point: [0, 0, 1.13] as const },
      ];
      const updateLabels = () => {
        if (disposed) return;
        labelsRef.current?.querySelectorAll<HTMLElement>('[data-level]').forEach((label) => {
          label.style.display = 'none';
        });
        const [width, height] = view.getInteractor().getView().getSize();
        const aspect = width / Math.max(height, 1);
        const projectedLabels: Array<{ label: HTMLElement; left: number; top: number }> = [];
        labelAnchors.forEach((point, levelId) => {
          const label = labelsRef.current?.querySelector<HTMLElement>(`[data-level="${levelId}"]`);
          if (!label) return;
          const [x, y] = renderer.worldToNormalizedDisplay(point[0], point[1], point[2], aspect);
          const visible = x >= 0 && x <= 1 && y >= 0 && y <= 1;
          label.style.display = visible ? 'block' : 'none';
          if (visible) projectedLabels.push({ label, left: x * width, top: (1 - y) * height });
        });
        projectedLabels.sort((left, right) => left.top - right.top);
        projectedLabels.forEach((item, index) => {
          const previous = projectedLabels[index - 1];
          if (previous && item.top - previous.top < 28) item.top = previous.top + 28;
        });
        const overflow = projectedLabels.length ? projectedLabels[projectedLabels.length - 1].top - (height - 54) : 0;
        if (overflow > 0) projectedLabels.forEach((item) => { item.top -= overflow; });
        projectedLabels.forEach((item) => {
          item.label.style.left = `${Math.min(item.left, width - 145)}px`;
          item.label.style.top = `${Math.max(42, item.top)}px`;
        });
        orientationWidget.updateMarkerOrientation();
        const markerRenderer = orientationWidget.getRenderer();
        const [xMin, yMin, xMax, yMax] = orientationWidget.computeViewport();
        const markerAspect = Math.max((xMax - xMin) * width, 1) / Math.max((yMax - yMin) * height, 1);
        axisLabelPoints.forEach(({ axis: axisName, point }) => {
          const label = axisLabelsRef.current?.querySelector<HTMLElement>(`[data-axis="${axisName}"]`);
          if (!label) return;
          const [x, y] = markerRenderer.worldToNormalizedDisplay(point[0], point[1], point[2], markerAspect);
          label.style.left = `${x * width}px`;
          label.style.top = `${(1 - y) * height}px`;
        });
      };

      const updateSlice = (nextAxis: number, nextPercent: number) => {
        const nextSlice = sliceIndex(field, nextAxis, nextPercent);
        const surface = buildSliceSurface(field, nextAxis, nextSlice);
        slicePolyData.getPoints().setData(surface.points, 3);
        slicePolyData.getPolys().setData(surface.polys);
        sliceScalars.setData(surface.values, 1);
        slicePolyData.modified();
        labelAnchors.clear();
        const boundaryIds = visibleBoundaryIds(visibleItemsRef.current);
        contourObjects.forEach(({ level, contour, actor }, levelIndex) => {
          contour.setSlicingMode(nextAxis);
          contour.setSlice(nextSlice);
          contour.update();
          const isVisible = boundaryIds.has(level.id);
          actor.setVisibility(isVisible);
          const output = contour.getOutputData();
          const points = output.getPoints().getData() as ArrayLike<number>;
          if (points.length >= 3 && isVisible) {
            const pointCount = Math.floor(points.length / 3);
            const pointId = Math.min(pointCount - 1, Math.floor(pointCount * (0.14 + levelIndex * 0.16)));
            labelAnchors.set(level.id, [points[pointId * 3], points[pointId * 3 + 1], points[pointId * 3 + 2]]);
          }
        });
        renderer.resetCameraClippingRange();
        renderWindow.render();
        updateLabels();
      };
      const updateVolumeTransparency = (transparency: number) => {
        surfaceActors.forEach(({ actor, item }) => actor.getProperty().setOpacity(surfaceOpacity(item, transparency)));
        renderWindow.render();
      };
      const updateVisibleItems = (nextItems: string[]) => {
        visibleItemsRef.current = new Set(nextItems);
        const boundaryIds = visibleBoundaryIds(visibleItemsRef.current);
        surfaceActors.forEach(({ actor, item }) => actor.setVisibility(visibleItemsRef.current.has(item.id) && availableItemIds.has(item.id)));
        contourObjects.forEach(({ actor, level }) => actor.setVisibility(boundaryIds.has(level.id)));
        updateSlice(axisRef.current, sliceRef.current);
      };
      sceneRef.current = { updateSlice, updateVolumeTransparency, updateVisibleItems };
      updateVisibleItems([...initiallyVisibleItems]);

      renderer.resetCamera();
      const camera = renderer.getActiveCamera();
      const focalPoint = camera.getFocalPoint();
      const distance = camera.getDistance();
      const top = positionYaw !== undefined;
      camera.setPosition(focalPoint[0] + (top ? 0 : distance * .78), focalPoint[1] - (top ? 0 : distance * .78), focalPoint[2] + distance * (top ? 1 : .58));
      camera.setViewUp(0, top ? 1 : 0, top ? 0 : 1);
      camera.orthogonalizeViewUp();
      renderer.resetCamera();
      interactorStyle.setCenterOfRotation(camera.getFocalPoint());
      renderer.resetCameraClippingRange();
      view.resize();
      renderWindow.render();
      updateLabels();

      const observer = new ResizeObserver(() => { view.resize(); updateLabels(); });
      const cameraSubscription = camera.onModified(updateLabels);
      const themeObserver = new MutationObserver(() => {
        renderer.setBackground(...viewerBackground());
        const textColor = document.documentElement.dataset.theme === 'dark' ? '#eef3ff' : '#17233c';
        scalarBar.setAxisTextStyle({ fontColor: textColor });
        scalarBar.setTickTextStyle({ fontColor: textColor });
        renderWindow.render();
      });
      observer.observe(container);
      themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

      dispose = () => {
        observer.disconnect();
        cameraSubscription.unsubscribe();
        themeObserver.disconnect();
        orientationWidget.setEnabled(false);
        orientationWidget.delete();
        axesActor.delete();
        sceneRef.current = null;
        view.getInteractor().setContainer(null);
        view.delete();
        scalarBar.delete();
        colors.delete();
        contourObjects.forEach(({ actor, mapper, contour }) => { actor.delete(); mapper.delete(); contour.delete(); });
        surfaceObjects.forEach((object) => object.delete());
        outlineActor.delete();
        outlineMapper.delete();
        outline.delete();
        sliceActor.delete();
        sliceMapper.delete();
        sliceScalars.delete();
        slicePolyData.delete();
        doseArray.delete();
        imageData.delete();
      };
    };
    void render().catch((caught: Error) => { if (!disposed) setError(caught.message); });
    return () => { disposed = true; dispose(); };
  }, [availableItems, field, items, levels, positionYaw, unit]);

  useEffect(() => {
    sceneRef.current?.updateSlice(axis, slicePercent);
  }, [axis, slicePercent]);

  useEffect(() => {
    sceneRef.current?.updateVolumeTransparency(volumeTransparency);
  }, [volumeTransparency]);

  useEffect(() => {
    visibleItemsRef.current = new Set(visibleItemIds);
    sceneRef.current?.updateVisibleItems(visibleItemIds);
  }, [visibleItemIds]);

  const formatError = format !== 'vti' ? 'La vista isodósica 3D requiere la malla estructurada VTI de Monte Carlo.' : '';
  if (error || fieldBuild.error || formatError) return <div className="vtk-error"><Box size={24}/><p>{error || fieldBuild.error || formatError}</p></div>;
  if (!field) return <div className="vtk-loading">Preparando isodosis tridimensionales…</div>;

  const currentPosition = slicePosition(field, axis, slicePercent);
  const availableIds = new Set(availableItems.map((item) => item.id));
  const defaultAbsoluteText = defaultAbsoluteDoses(field).map((dose) => dose.toPrecision(5)).join(', ');
  const reference = levelMode === 'uniformity'
    ? { label: 'Dosis de referencia · media', value: `${field.meanDose.toExponential(3)} ${unit}` }
    : levelMode === 'adaptive'
      ? { label: 'Intervalo adaptativo · P10–P90', value: `${field.quantiles.p10.toExponential(2)}–${field.quantiles.p90.toExponential(2)} ${unit}` }
      : levelMode === 'p99'
        ? { label: 'Dosis de referencia · P99', value: `${field.referenceDose.toExponential(3)} ${unit}` }
        : { label: 'Intervalo real de la carga', value: `${field.minimumDose.toExponential(2)}–${field.maximumDose.toExponential(2)} ${unit}` };
  return (
    <div className="isodose-shell">
      <div className="isodose-heading">
        <div><span className="section-label">Posprocesamiento Monte Carlo</span><h4>Distribución dosimétrica · Isodosis 3D</h4><p>Superficies volumétricas y curvas isodósicas sobre un plano interactivo.</p></div>
        <div className="isodose-reference"><Crosshair size={16}/><span>{reference.label}</span><strong>{reference.value}</strong></div>
      </div>
      <div className="vtk-toolbar isodose-toolbar">
        <div><SlidersHorizontal size={15}/><strong>Análisis dosimétrico</strong></div>
        <label><span>Modo de niveles</span><select aria-label="Modo de niveles" value={levelMode} onChange={(event) => { setLevelMode(event.target.value as LevelMode); setHiddenItems([]); }}><option value="uniformity">Uniformidad · media</option><option value="adaptive">Adaptativo · percentiles</option><option value="p99">Relativo a P99</option><option value="absolute">Absoluto · {unit}</option></select></label>
        <label><span>Representación</span><select aria-label="Representación de isodosis" value={representationMode} onChange={(event) => { setRepresentationMode(event.target.value as RepresentationMode); setHiddenItems([]); }}><option value="surfaces">Superficies</option><option value="bands">Bandas volumétricas</option></select></label>
        <label><span>Plano</span><select value={axis} onChange={(event) => setAxis(Number(event.target.value))}><option value={0}>YZ · eje X</option><option value={1}>XZ · eje Y</option><option value={2}>XY · eje Z</option></select></label>
        <label><span>Suavizado</span><select value={smoothingPasses} onChange={(event) => setSmoothingPasses(Number(event.target.value))}><option value={1}>Suave</option><option value={2}>Medio</option><option value={3}>Alto</option></select></label>
        {levelMode === 'absolute' && <label className="isodose-absolute"><span>Niveles absolutos [{unit}]</span><input aria-label={`Niveles absolutos en ${unit}`} value={absoluteText ?? defaultAbsoluteText} onChange={(event) => { setAbsoluteText(event.target.value); setHiddenItems([]); }}/></label>}
        <label className="vtk-range isodose-opacity"><span>Transparencia del volumen: {volumeTransparency}%</span><input aria-label="Transparencia del volumen" type="range" min="0" max="100" value={volumeTransparency} onInput={(event) => setVolumeTransparency(Number(event.currentTarget.value))}/></label>
        <label className="vtk-range"><span>Posición: {currentPosition.toFixed(2)} cm</span><input type="range" min="0" max="100" value={slicePercent} onChange={(event) => setSlicePercent(Number(event.target.value))}/></label>
      </div>
      <div className="isodose-view-shell">
        <div className="vtk-view" ref={containerRef}/>
        <aside className="isodose-levels">
          <header><Layers3 size={15}/><div><span>{representationMode === 'bands' ? 'Bandas de dosis' : 'Niveles de isodosis'}</span><small>{levelMode === 'uniformity' ? 'relativos a la dosis media' : levelMode === 'adaptive' ? 'percentiles del volumen' : levelMode === 'p99' ? 'relativos a P99' : 'valores absolutos'}</small></div></header>
          {items.map((item) => {
            const available = availableIds.has(item.id);
            const checked = available && !hiddenItems.includes(item.id);
            return <label key={item.id} className={available ? '' : 'unavailable'}><input type="checkbox" disabled={!available} checked={checked} onChange={() => setHiddenItems((current) => checked ? [...current, item.id] : current.filter((id) => id !== item.id))}/><i style={{ background: colorCss(item.color) }}/><strong>{item.label}</strong><span>{available ? item.detail : 'sin cruce'}</span></label>;
          })}
        </aside>
        <div className="isodose-curve-labels" ref={labelsRef} aria-hidden="true">
          {levels.map((level) => <span key={level.id} data-level={level.id} style={{ borderColor: colorCss(level.color), color: colorCss(level.color) }}>{level.label} · {level.dose.toExponential(2)}</span>)}
        </div>
        <div className="viewer-axis-labels" ref={axisLabelsRef} aria-hidden="true"><span data-axis="x">X</span><span data-axis="y">Y</span><span data-axis="z">Z</span></div>
        <div className="viewer-hint"><Rotate3D size={14}/>Izquierdo: Rotar · Rueda Presionada: Desplazar · Rueda: Zoom · Derecho: Zoom Continuo</div>
      </div>
      <div className="isodose-caption"><div><Box size={14}/><span>Plano {axis === 0 ? 'YZ' : axis === 1 ? 'XZ' : 'XY'} con curvas etiquetadas</span></div><small>{representationMode === 'bands' ? 'Cada banda representa el intervalo entre dos límites consecutivos.' : 'Cada superficie representa una dosis constante.'} El suavizado no modifica el VTI.</small>{field.stride > 1 && <b>Muestreo visual ×{field.stride}</b>}</div>
    </div>
  );
}
