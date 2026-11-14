'use client';
import { uiLocale } from '../../lib/i18n/number';

import { viewerBackground } from '../../lib/viewerAppearance';

import { useEffect, useMemo, useRef, useState } from 'react';
import { Box, Download, Rotate3D, SlidersHorizontal } from 'lucide-react';
import { orientDoseImage, readDoseImage, type DoseImage } from './doseViewFrame';
import { boundedPreview, buildStructuredSurface, structuredValueExtent } from './doseSurface';
import { buildRouteSurface, type RouteDoseField } from './routeDoseSurface';
import type { GammaSource } from '../../lib/types';

type DoseCloud = {
  kind: 'legacy';
  points: number[][];
  values: number[];
  scalarName: string;
  tetrahedra: number[][];
};

type StructuredDose = DoseImage & { kind: 'structured'; scalarName: string; originalCells?: number };

type DoseDataset = DoseCloud | StructuredDose;
type RouteDoseScene = {
  fields: RouteDoseField[];
  sources: GammaSource[];
  bottomCenter: boolean;
};
const sourceColor: [number, number, number] = [.19, .87, .60];
export type VtkMaterial = { id: number; name: string };
const EMPTY_MATERIALS: VtkMaterial[] = [];

const MATERIAL_COLORS: Array<[number, number, number]> = [
  [0.20, 0.48, 0.95],
  [0.12, 0.72, 0.64],
  [0.95, 0.58, 0.18],
  [0.72, 0.34, 0.91],
  [0.91, 0.30, 0.45],
  [0.42, 0.69, 0.20],
  [0.16, 0.68, 0.87],
  [0.86, 0.72, 0.16],
];

const materialColor = (id: number) => MATERIAL_COLORS[(Math.max(id, 1) - 1) % MATERIAL_COLORS.length];

function extent(values: ArrayLike<number>) {
  let minimum = Number.POSITIVE_INFINITY;
  let maximum = Number.NEGATIVE_INFINITY;
  for (let index = 0; index < values.length; index += 1) {
    const value = values[index];
    if (value < minimum) minimum = value;
    if (value > maximum) maximum = value;
  }
  return [minimum, maximum] as const;
}

function parseLegacyVtk(text: string): DoseCloud {
  const pointBlock = text.match(/POINTS\s+(\d+)\s+\w+\s+([\s\S]*?)\s+(?:CELLS|VERTICES|POLYGONS|POINT_DATA)\b/i);
  if (!pointBlock) throw new Error('El VTK no contiene un bloque POINTS ASCII compatible.');
  const count = Number(pointBlock[1]);
  const coordinates = pointBlock[2].trim().split(/\s+/).slice(0, count * 3).map(Number);
  const scalarBlock = text.match(/SCALARS\s+(\S+)\s+\w+(?:\s+\d+)?\s+LOOKUP_TABLE\s+\S+\s+([\s\S]+)/i);
  if (!scalarBlock) throw new Error('El VTK no contiene escalares en los puntos.');
  const values = scalarBlock[2].trim().split(/\s+/).slice(0, count).map(Number);
  if (coordinates.length !== count * 3 || values.length !== count) throw new Error('El VTK está incompleto.');
  const cellBlock = text.match(/CELLS\s+(\d+)\s+\d+\s+([\s\S]*?)\s+CELL_TYPES\b/i);
  const tetrahedra: number[][] = [];
  if (cellBlock) {
    const cellCount = Number(cellBlock[1]);
    const cellValues = cellBlock[2].trim().split(/\s+/).map(Number);
    let offset = 0;
    for (let index = 0; index < cellCount && offset < cellValues.length; index += 1) {
      const cellSize = cellValues[offset];
      const pointIds = cellValues.slice(offset + 1, offset + 1 + cellSize);
      if (cellSize === 4) tetrahedra.push(pointIds);
      offset += cellSize + 1;
    }
  }
  return {
    kind: 'legacy',
    points: Array.from({ length: count }, (_, index) => coordinates.slice(index * 3, index * 3 + 3)),
    values,
    scalarName: scalarBlock[1],
    tetrahedra,
  };
}


function extractSurfaceFaces(tetrahedra: number[][], visiblePoints: boolean[]) {
  const faces = new Map<string, { vertices: number[]; count: number }>();
  tetrahedra.forEach(([a, b, c, d]) => {
    if (![a, b, c, d].every((pointId) => visiblePoints[pointId])) return;
    ([
      [a, b, c],
      [a, d, b],
      [a, c, d],
      [b, d, c],
    ] as number[][]).forEach((vertices) => {
      const key = [...vertices].sort((left, right) => left - right).join(':');
      const existing = faces.get(key);
      if (existing) existing.count += 1;
      else faces.set(key, { vertices, count: 1 });
    });
  });
  return [...faces.values()].filter((face) => face.count === 1).map((face) => face.vertices);
}


export default function VtkDoseViewer({
  url,
  label,
  format,
  quantityLabel = 'Tasa de dosis absorbida [kGy/h]',
  materials: inputMaterials = EMPTY_MATERIALS,
  positionYaw,
  route,
}: {
  url?: string;
  label: string;
  format?: 'vtk' | 'vti';
  quantityLabel?: string;
  materials?: VtkMaterial[];
  positionYaw?: number;
  route?: RouteDoseScene;
}) {
  // Polling may replace the labels array without changing the scene. Preserve
  // its identity so an in-flight capture/camera is not destroyed every poll.
  const materialKey = JSON.stringify(inputMaterials);
  const materials = useMemo<VtkMaterial[]>(() => JSON.parse(materialKey), [materialKey]);
  const containerRef = useRef<HTMLDivElement>(null);
  const [sceneReady, setSceneReady] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [captureError, setCaptureError] = useState('');
  const axisLabelsRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<{
    updateCut: (nextAxis: number, nextCut: number, preview?: boolean) => void;
    setOpacity: (opacity: number) => void;
    camera: (axis: number | null) => void;
    snapshot: () => Promise<void>;
  } | null>(null);
  const axisRef = useRef(2);
  const cutRef = useRef(100);
  const routeCameraRef = useRef<{ position: [number, number, number]; focal: [number, number, number];
    up: [number, number, number]; scale: number } | undefined>(undefined);
  const [loadedDataset, setDataset] = useState<{ key: string; data: DoseDataset }>();
  const datasetKey = JSON.stringify([url, format, positionYaw]);
  const sourceDataset = loadedDataset?.key === datasetKey ? loadedDataset.data : null;
  const [axis, setAxis] = useState(2);
  const [cut, setCut] = useState(100);
  const [draggingCut, setDraggingCut] = useState(false);
  const [level, setLevel] = useState<'all' | 'I' | 'S'>('all');
  const [doseScale, setDoseScale] = useState<'log' | 'linear'>('log');
  const [colorMode, setColorMode] = useState<'dose' | 'material' | 'error'>('dose');
  const [opacity, setOpacity] = useState(1);
  const opacityRef = useRef(1);
  const [visibleMaterialIds, setVisibleMaterialIds] = useState<number[]>(materials.map((material) => material.id));
  const [error, setError] = useState('');
  const fields = useMemo(() => route?.fields.filter((field) =>
    (level === 'all' || (level === 'I' ? /^I\d+$/i : /^S\d+$/i).test(field.name))
    && (colorMode !== 'error' || field.image.relativeErrors)), [colorMode, level, route]);
  const dataset = useMemo<DoseDataset | null>(() => {
    if (colorMode !== 'error' || sourceDataset?.kind !== 'structured' || !sourceDataset.relativeErrors) return sourceDataset;
    return { ...sourceDataset, values: Float32Array.from(sourceDataset.relativeErrors, (value) => value * 100), scalarName: 'relative_error_percent' };
  }, [sourceDataset, colorMode]);

  useEffect(() => {
    axisRef.current = axis;
    cutRef.current = cut;
  }, [axis, cut]);

  useEffect(() => {
    if (!url) return;
    let cancelled = false;
    const controller = new AbortController();
    fetch(url, { signal: controller.signal, cache: 'no-store' })
      .then(async (response) => {
        if (!response.ok) throw new Error('No fue posible descargar el VTK.');
        if (format === 'vtk') return parseLegacyVtk(await response.text());
        const parsed = await readDoseImage(await response.arrayBuffer());
        const preview = boundedPreview<StructuredDose>({ ...parsed, kind: 'structured' });
        return positionYaw === undefined ? preview : orientDoseImage(preview, positionYaw);
      })
      .then((nextDataset) => {
        if (cancelled) return;
        setDataset({ key: datasetKey, data: nextDataset });
        setError('');
      })
      .catch((caught: Error) => { if (!cancelled) setError(caught.message); });
    return () => { cancelled = true; controller.abort(); };
  }, [datasetKey, format, positionYaw, url]);

  const bounds = useMemo(() => {
    if (!dataset) return null;
    if (dataset?.kind === 'legacy') return extent(dataset.points.map((point) => point[axis]));
    return [
      dataset.origin[axis],
      dataset.origin[axis] + dataset.spacing[axis] * dataset.dimensions[axis],
    ] as const;
  }, [axis, dataset]);

  useEffect(() => {
    setSceneReady(false);
    if ((!dataset && !route) || !containerRef.current) return;
    const container = containerRef.current;
    let disposed = false;
    let dispose = () => undefined;
    const render = async () => {
      await import('@kitware/vtk.js/Rendering/Profiles/Geometry');
      const windowModule = await import('@kitware/vtk.js/Rendering/Misc/GenericRenderWindow');
      const polyDataModule = await import('@kitware/vtk.js/Common/DataModel/PolyData');
      const dataArrayModule = await import('@kitware/vtk.js/Common/Core/DataArray');
      const mapperModule = await import('@kitware/vtk.js/Rendering/Core/Mapper');
      const actorModule = await import('@kitware/vtk.js/Rendering/Core/Actor');
      const colorModule = await import('@kitware/vtk.js/Rendering/Core/ColorTransferFunction');
      const scalarBarModule = await import('@kitware/vtk.js/Rendering/Core/ScalarBarActor');
      const styleModule = await import('@kitware/vtk.js/Interaction/Style/InteractorStyleManipulator');
      const rotateModule = await import('@kitware/vtk.js/Interaction/Manipulators/MouseCameraTrackballRotateManipulator');
      const panModule = await import('@kitware/vtk.js/Interaction/Manipulators/MouseCameraTrackballPanManipulator');
      const zoomModule = await import('@kitware/vtk.js/Interaction/Manipulators/MouseCameraTrackballZoomManipulator');
      const axesModule = await import('@kitware/vtk.js/Rendering/Core/AxesActor');
      const orientationModule = await import('@kitware/vtk.js/Interaction/Widgets/OrientationMarkerWidget');
      const cylinderModule = route && await import('@kitware/vtk.js/Filters/Sources/CylinderSource');
      if (disposed) return;

      const initialCoordinates = dataset?.kind === 'legacy'
        ? new Float32Array(dataset.points.flatMap((point) => point))
        : new Float32Array();
      const initialScalars = dataset?.kind === 'legacy'
        ? new Float32Array(dataset.values)
        : new Float32Array();
      const visibleMaterials = new Set(visibleMaterialIds);
      const ranges = route?.fields.flatMap(({ image }) => {
        if (colorMode === 'error' && !image.relativeErrors) return [];
        const values = colorMode === 'error' && image.relativeErrors ? image.relativeErrors : image.values;
        const range = structuredValueExtent({ ...image, values }, undefined, [Infinity, -Infinity]);
        if (!Number.isFinite(range[0])) return [];
        return [colorMode === 'error' ? [range[0] * 100, range[1] * 100] : range];
      });
      const [rawMinimum, rawMaximum] = colorMode === 'material' && materials.length
        ? [Math.min(...materials.map((material) => material.id)), Math.max(...materials.map((material) => material.id))]
        : ranges?.length
          ? [Math.min(...ranges.map(([low]) => low)), Math.max(...ranges.map(([, high]) => high))]
          : dataset?.kind === 'legacy'
            ? extent(dataset.values)
            : dataset?.kind === 'structured' ? structuredValueExtent(dataset, visibleMaterials) : [0, 1];
      const maxValue = rawMaximum === rawMinimum
        ? rawMinimum + Math.max(Math.abs(rawMinimum) * 1e-6, 1e-12)
        : rawMaximum;
      const log = Boolean(route) && colorMode === 'dose' && doseScale === 'log' && rawMaximum > 0;
      const floor = rawMinimum > 0 ? rawMinimum : rawMaximum * 1e-6;
      const minimum = log ? Math.log10(floor) : rawMinimum;
      const maximum = log ? Math.log10(maxValue) : maxValue;

      const view = windowModule.default.newInstance({ background: viewerBackground() });
      view.setContainer(container);
      const renderer = view.getRenderer();
      const renderWindow = view.getRenderWindow();
      const interactorStyle = styleModule.default.newInstance();
      interactorStyle.addMouseManipulator(rotateModule.default.newInstance({
        button: 1,
        useFocalPointAsCenterOfRotation: true,
      }));
      interactorStyle.addMouseManipulator(panModule.default.newInstance({ button: 2 }));
      interactorStyle.addMouseManipulator(zoomModule.default.newInstance({
        button: 3,
        dragEnabled: true,
        scrollEnabled: true,
      }));
      view.getInteractor().setInteractorStyle(interactorStyle);
      const axesActor = axesModule.default.newInstance({
        config: {
          recenter: false,
          tipResolution: 60,
          tipRadius: 0.12,
          tipLength: 0.24,
          shaftResolution: 60,
          shaftRadius: 0.035,
          invert: false,
        },
        xConfig: { color: [220, 64, 86], invert: false },
        yConfig: { color: [37, 160, 112], invert: false },
        zConfig: { color: [49, 95, 202], invert: false },
      } as never);
      const orientationWidget = orientationModule.default.newInstance({
        actor: axesActor,
        interactor: view.getInteractor(),
        parentRenderer: renderer,
      });
      orientationWidget.setViewportCorner(orientationModule.default.Corners.BOTTOM_LEFT);
      orientationWidget.setViewportSize(0.17);
      orientationWidget.setMinPixelSize(90);
      orientationWidget.setMaxPixelSize(160);
      orientationWidget.setEnabled(true);
      const axisLabelPoints = [
        { axis: 'x', point: [1.13, 0, 0] as const },
        { axis: 'y', point: [0, 1.13, 0] as const },
        { axis: 'z', point: [0, 0, 1.13] as const },
      ];
      const updateAxisLabels = () => {
        if (disposed) return;
        orientationWidget.updateMarkerOrientation();
        const markerRenderer = orientationWidget.getRenderer();
        const [width, height] = view.getInteractor().getView().getSize();
        const [xMin, yMin, xMax, yMax] = orientationWidget.computeViewport();
        const viewportWidth = Math.max((xMax - xMin) * width, 1);
        const viewportHeight = Math.max((yMax - yMin) * height, 1);
        const aspect = viewportWidth / viewportHeight;
        axisLabelPoints.forEach(({ axis: axisName, point }) => {
          const axisLabel = axisLabelsRef.current?.querySelector<HTMLElement>(`[data-axis="${axisName}"]`);
          if (!axisLabel) return;
          const [normalizedX, normalizedY] = markerRenderer.worldToNormalizedDisplay(
            point[0],
            point[1],
            point[2],
            aspect,
          );
          axisLabel.style.left = `${normalizedX * width}px`;
          axisLabel.style.top = `${(1 - normalizedY) * height}px`;
        });
      };
      const polyData = polyDataModule.default.newInstance();
      polyData.getPoints().setData(initialCoordinates, 3);
      const scalarArray = dataArrayModule.default.newInstance({
        name: colorMode === 'material' ? 'material_id' : dataset?.scalarName ?? 'route_rate',
        values: initialScalars,
        numberOfComponents: 1,
      });
      polyData.getPointData().setScalars(scalarArray);
      const colors = colorModule.default.newInstance();
      if (colorMode === 'material') {
        materials.forEach((material) => colors.addRGBPoint(material.id, ...materialColor(material.id)));
      } else {
        colors.addRGBPoint(minimum, 0.267, 0.005, 0.329);
        colors.addRGBPoint(minimum + (maximum - minimum) * .35, 0.19, 0.407, 0.556);
        colors.addRGBPoint(minimum + (maximum - minimum) * .7, 0.208, 0.719, 0.473);
        colors.addRGBPoint(maximum, 0.993, 0.906, 0.144);
      }
      const mapper = mapperModule.default.newInstance();
      mapper.setInputData(polyData);
      mapper.setLookupTable(colors);
      mapper.setScalarRange(minimum, maximum);
      mapper.setUseLookupTableScalarRange(true);
      mapper.setScalarVisibility(true);
      const actor = actorModule.default.newInstance();
      actor.setMapper(mapper);
      actor.getProperty().setRepresentationToSurface();
      actor.getProperty().setInterpolationToPhong();
      actor.getProperty().setAmbient(0.28);
      actor.getProperty().setDiffuse(0.72);
      actor.getProperty().setSpecular(0.18);
      actor.getProperty().setSpecularPower(18);
      actor.getProperty().setOpacity(opacityRef.current);
      renderer.addActor(actor);
      const sourceObjects = cylinderModule && route ? route.sources.flatMap((source) => {
        if (source.active_length_mm <= 0) return [];
        const direction = source.upper_point_mm.map((point, i) =>
          (point - source.lower_point_mm[i]) / source.active_length_mm) as [number, number, number];
        const cylinder = cylinderModule.default.newInstance({
          center: source.center_point_mm.map((point) => point / 10) as [number, number, number],
          radius: (source.diameter_mm ?? 11) / 20, height: source.active_length_mm / 10,
          direction, resolution: 12,
        });
        const sourceMapper = mapperModule.default.newInstance();
        sourceMapper.setInputConnection(cylinder.getOutputPort());
        const sourceActor = actorModule.default.newInstance();
        sourceActor.setMapper(sourceMapper);
        sourceActor.getProperty().setColor(...sourceColor);
        renderer.addActor(sourceActor);
        return [sourceActor, sourceMapper, cylinder];
      }) : [];
      const legendTextColor = document.documentElement.dataset.theme === 'dark' ? '#eef3ff' : '#17233c';
      const scalarBar = scalarBarModule.default.newInstance({
        axisLabel: colorMode === 'error' ? 'Error relativo [%]' : log ? 'Tasa de dosis [kGy/h] · escala log' : quantityLabel,
        automated: true,
        drawNanAnnotation: false,
        drawBelowRangeSwatch: false,
        drawAboveRangeSwatch: false,
      });
      if (log) scalarBar.setGenerateTicks((helper) => {
        const ticks = Array.from({ length: 5 }, (_, i) => minimum + (maximum - minimum) * i / 4);
        helper.setTicks(ticks);
        helper.setTickStrings(ticks.map((value, i) =>
          `${i === 0 && rawMinimum <= 0 ? '≤ ' : ''}${(10 ** value).toLocaleString(uiLocale(), { maximumSignificantDigits: 2 })}`));
      });
      scalarBar.setScalarsToColors(colors);
      scalarBar.setOrientationToVertical();
      scalarBar.setAxisTextStyle({ fontColor: legendTextColor, fontFamily: 'Arial', fontSize: 14 });
      scalarBar.setTickTextStyle({ fontColor: legendTextColor, fontFamily: 'Arial', fontSize: 12 });
      if (colorMode !== 'material') renderer.addActor(scalarBar);
      let dragFields: RouteDoseField[] | undefined;
      let lastRouteCut: string | undefined;
      const updateCut = (nextAxis: number, nextCut: number, preview = false) => {
        if (route && preview && !dragFields) {
          // Use a smaller display mesh while dragging and restore the usual mesh on release.
          dragFields = (fields ?? []).map((field) => ({ ...field, image: boundedPreview(field.image, 8_000) }));
        }
        const routeFields = (preview ? dragFields : fields) ?? [];
        if (route) {
          const layers = nextAxis === 2 ? routeFields.map(({ image }) => Math.ceil(image.dimensions[2] * nextCut / 100)).join(',') : nextCut;
          const key = `${preview}/${nextAxis}/${layers}`;
          if (lastRouteCut === key) return;
          lastRouteCut = key;
        }
        if (route || dataset?.kind === 'structured') {
          const surface = route
            ? buildRouteSurface(routeFields, nextAxis, nextCut, route.bottomCenter, materials.length ? visibleMaterials : undefined, colorMode)
            : dataset?.kind === 'structured'
              ? buildStructuredSurface(dataset, nextAxis, nextCut, visibleMaterials, colorMode === 'material' ? 'material' : 'dose')
              : null;
          if (!surface) return;
          polyData.getPoints().setData(surface.points, 3);
          polyData.getVerts().setData(new Uint32Array());
          polyData.getPolys().setData(surface.polys);
          if (log) {
            for (let i = 0; i < surface.values.length; i++) surface.values[i] = Math.log10(Math.max(floor, surface.values[i]));
          }
          scalarArray.setData(surface.values, 1);
          actor.getProperty().setRepresentationToSurface();
        } else if (dataset?.kind === 'legacy') {
          const axisBounds = extent(dataset.points.map((point) => point[nextAxis]));
          const threshold = axisBounds[0] + ((axisBounds[1] - axisBounds[0]) * nextCut) / 100;
          const visiblePoints = dataset.points.map((point) => point[nextAxis] <= threshold);
          const surfaceFaces = extractSurfaceFaces(dataset.tetrahedra, visiblePoints);
          if (surfaceFaces.length) {
            polyData.getVerts().setData(new Uint32Array());
            polyData.getPolys().setData(new Uint32Array(surfaceFaces.flatMap((face) => [3, ...face])));
            actor.getProperty().setRepresentationToSurface();
          } else {
            const visibleIds = visiblePoints.flatMap((visible, pointId) => visible ? [pointId] : []);
            polyData.getPolys().setData(new Uint32Array());
            polyData.getVerts().setData(new Uint32Array(visibleIds.flatMap((pointId) => [1, pointId])));
            actor.getProperty().setRepresentationToPoints();
            actor.getProperty().setPointSize(5);
          }
        }
        polyData.modified();
        renderer.resetCameraClippingRange();
        renderWindow.render();
      };
      updateCut(axisRef.current, cutRef.current);
      renderer.resetCamera();
      const camera = renderer.getActiveCamera();
      const focalPoint = camera.getFocalPoint();
      const distance = camera.getDistance();
      const top = positionYaw !== undefined;
      camera.setPosition(
        focalPoint[0] + (top ? 0 : distance * .78),
        focalPoint[1] - (top ? 0 : distance * .78),
        focalPoint[2] + distance * (top ? 1 : .56),
      );
      camera.setViewUp(0, top ? 1 : 0, top ? 0 : 1);
      camera.orthogonalizeViewUp();
      renderer.resetCamera();
      if (route) camera.zoom(1.15);
      interactorStyle.setCenterOfRotation(camera.getFocalPoint());
      renderer.resetCameraClippingRange();
      const initialPosition = [...camera.getPosition()];
      if (route && routeCameraRef.current) {
        const saved = routeCameraRef.current;
        camera.setPosition(...saved.position);
        camera.setFocalPoint(...saved.focal);
        camera.setViewUp(...saved.up);
        camera.setParallelScale(saved.scale);
        interactorStyle.setCenterOfRotation(saved.focal);
        renderer.resetCameraClippingRange();
      }
      sceneRef.current = {
        updateCut,
        setOpacity: (next) => { actor.getProperty().setOpacity(next); renderWindow.render(); },
        camera: (selectedAxis) => {
          if (selectedAxis == null) camera.setPosition(...initialPosition as [number, number, number]);
          else {
            const position = [...camera.getFocalPoint()];
            position[selectedAxis] += distance;
            camera.setPosition(...position as [number, number, number]);
          }
          const topView = selectedAxis === 2 || (selectedAxis === null && top);
          camera.setViewUp(0, topView ? 1 : 0, topView ? 0 : 1);
          renderer.resetCamera();
          if (route) camera.zoom(1.15);
          renderer.resetCameraClippingRange();
          renderWindow.render();
        },
        snapshot: async () => {
          const glWindow = view.getApiSpecificRenderWindow();
          const capture = glWindow.captureNextImage();
          if (!capture) throw new Error('El visor todavía no está disponible.');
          // Render this view explicitly, including overlays, before awaiting
          // ImageReady. Do not depend on another interaction/animation frame.
          glWindow.traverseAllPasses();
          let timer: ReturnType<typeof setTimeout> | undefined;
          try {
            const dataUrl = await Promise.race([capture, new Promise<string>((_, reject) => {
              timer = setTimeout(() => reject(new Error('No se pudo capturar la imagen. Vuelve a intentarlo cuando el visor esté listo.')), 5000);
            })]);
            const blob = await (await fetch(dataUrl)).blob();
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = route ? 'IRRADIUM-recorrido-3D.png' : 'IRRADIUM-resultados.png';
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
          } finally { clearTimeout(timer); }
        },
      };
      view.resize();
      updateAxisLabels();
      renderWindow.render();
      setSceneReady(true);
      const observer = new ResizeObserver(() => {
        view.resize();
        updateAxisLabels();
      });
      const cameraSubscription = camera.onModified(updateAxisLabels);
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
        if (route) routeCameraRef.current = {
          position: [...camera.getPosition()] as [number, number, number],
          focal: [...camera.getFocalPoint()] as [number, number, number],
          up: [...camera.getViewUp()] as [number, number, number],
          scale: camera.getParallelScale(),
        };
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
        sourceObjects.forEach((item) => item.delete());
        actor.delete();
        mapper.delete();
        colors.delete();
        scalarArray.delete();
        polyData.delete();
      };
    };
    void render().catch((cause) => { if (!disposed) setError(String(cause)); });
    return () => { disposed = true; dispose(); };
  }, [colorMode, dataset, doseScale, fields, materials, positionYaw, quantityLabel, route, visibleMaterialIds]);

  useEffect(() => {
    const frame = requestAnimationFrame(() => sceneRef.current?.updateCut(axis, cut, draggingCut));
    return () => cancelAnimationFrame(frame);
  }, [axis, cut, draggingCut]);

  useEffect(() => {
    if (!draggingCut) return;
    const stop = () => setDraggingCut(false);
    window.addEventListener('pointerup', stop);
    window.addEventListener('pointercancel', stop);
    window.addEventListener('blur', stop);
    return () => {
      window.removeEventListener('pointerup', stop);
      window.removeEventListener('pointercancel', stop);
      window.removeEventListener('blur', stop);
    };
  }, [draggingCut]);

  useEffect(() => {
    opacityRef.current = opacity;
    sceneRef.current?.setOpacity(opacity);
  }, [opacity]);

  if (error) return <div className="vtk-error"><Box size={24}/><p>{error}</p>{url && <a className="button secondary" href={url}><Download size={15}/> Descargar VTK</a>}</div>;
  if ((!dataset || !bounds) && !route) return <div className="vtk-loading">Preparando volumen VTK…</div>;

  const visibleLimit = bounds ? bounds[0] + ((bounds[1] - bounds[0]) * cut) / 100 : 0;
  const hasMaterialData = materials.length > 0 && (route
    ? route.fields.some(({ image }) => Boolean(image.materialIds))
    : dataset?.kind === 'structured' && Boolean(dataset.materialIds));
  const hasErrorData = route ? route.fields.some(({ image }) => Boolean(image.relativeErrors))
    : sourceDataset?.kind === 'structured' && Boolean(sourceDataset.relativeErrors);
  const lower = route?.fields.some(({ name }) => /^I\d+$/i.test(name));
  const upper = route?.fields.some(({ name }) => /^S\d+$/i.test(name));
  const reduced = route?.fields.some(({ image }) => image.originalCells !== undefined);
  const visibleMaterials = materials.filter((material) => visibleMaterialIds.includes(material.id));
  return (
    <div className={`vtk-dose-shell${route ? ' vtk-route-shell' : ''}`}>
      <div className={`vtk-toolbar ${route ? 'vtk-route-toolbar' : 'vtk-analysis-toolbar'}`}>
        <div className="vtk-controls-row">
        <div><SlidersHorizontal size={15}/><strong>{route ? 'Tasas del recorrido' : 'Representación: Superficie'}</strong></div>
        {lower && upper && <label><span>Nivel</span><select aria-label="Nivel del recorrido" value={level} onChange={(event) => setLevel(event.target.value as typeof level)}>
          <option value="all">Ambos niveles</option><option value="I">Inferior</option><option value="S">Superior</option>
        </select></label>}
        <label><span>Eje</span><select aria-label="Eje del corte" value={axis} onChange={(event) => setAxis(Number(event.target.value))}><option value={0}>X</option><option value={1}>Y</option><option value={2}>Z</option></select></label>
        <label><span>Color por</span><select aria-label="Color por" value={colorMode} onChange={(event) => setColorMode(event.target.value as typeof colorMode)}><option value="dose">{route ? 'Tasa de dosis' : 'Dosis'}</option>{hasMaterialData && <option value="material">Material</option>}{hasErrorData && <option value="error">Error relativo [%]</option>}</select></label>
        {route && colorMode === 'dose' && <label><span>Escala</span><select aria-label="Escala de tasa de dosis" value={doseScale} onChange={(event) => setDoseScale(event.target.value as typeof doseScale)}>
          <option value="log">Logarítmica</option><option value="linear">Lineal</option>
        </select></label>}
        </div>
        <div className="vtk-adjustments-row">
        <label className="vtk-range"><span>{route ? `Corte por carga · ${cut} %` : `Plano máximo: ${visibleLimit.toFixed(2)} cm`}</span><input aria-label="Profundidad del corte" type="range" min="2" max="100" value={cut}
          onPointerDown={() => { if (route) setDraggingCut(true); }}
          onPointerUp={() => setDraggingCut(false)} onPointerCancel={() => setDraggingCut(false)}
          onChange={(event) => setCut(Number(event.target.value))}/></label>
        <label className="vtk-range vtk-opacity"><span>Opacidad · {Math.round(opacity * 100)} %</span><input aria-label="Opacidad del volumen" type="range" min=".1" max="1" step=".05" value={opacity} onChange={(event) => setOpacity(Number(event.target.value))}/></label>
        <div className="vtk-camera-buttons">{['X', 'Y', 'Z'].map((name, index) => <button type="button" className="button ghost" key={name} aria-label={`Vista ${name}`} onClick={() => sceneRef.current?.camera(index)}>{name}</button>)}<button type="button" className="button ghost" onClick={() => sceneRef.current?.camera(null)}>Restablecer vista</button></div>
        <button type="button" className="button ghost" aria-label="Capturar PNG" title="Capturar PNG" disabled={!sceneReady || capturing} onClick={async () => {
          if (!sceneRef.current) return;
          setCapturing(true); setCaptureError('');
          try { await sceneRef.current.snapshot(); } catch (caught) { setCaptureError(String(caught)); }
          finally { setCapturing(false); }
        }}>{capturing ? 'Capturando…' : route ? <><Download size={14}/>PNG</> : 'Capturar PNG'}</button>
        {url && <a className="button ghost" href={url}><Download size={14}/> {format?.toUpperCase()}{positionYaw !== undefined && ' local'}</a>}
        </div>
      </div>
      {captureError && <p className="inline-notice" role="alert">{captureError}</p>}
      {dataset?.kind === 'structured' && dataset.originalCells && <p className="inline-notice">Vista reducida a {dataset.values.length.toLocaleString(uiLocale())} celdas para cuidar la memoria gráfica. El archivo y las métricas conservan los {dataset.originalCells.toLocaleString(uiLocale())} vóxeles calculados.</p>}
      {colorMode === 'error' && <p className="inline-notice">Error relativo por vóxel en porcentaje. Los vóxeles sin estimación finita se omiten de esta vista; su cobertura se indica en las métricas.</p>}
      <div className="vtk-view-shell">
        <div className="vtk-view" ref={containerRef} aria-label={route ? 'Distribuciones de tasa en el irradiador' : undefined}/>
        {hasMaterialData && <aside className="vtk-material-filter"><header><div><span>Materiales visibles</span><strong>{visibleMaterials.length} de {materials.length}</strong></div><div><button type="button" onClick={() => setVisibleMaterialIds(materials.map((material) => material.id))}>Todos</button><button type="button" onClick={() => setVisibleMaterialIds([])}>Ninguno</button></div></header><div>{materials.map((material) => { const selected = visibleMaterialIds.includes(material.id); const color = materialColor(material.id); return <label key={material.id}><input type="checkbox" checked={selected} onChange={() => setVisibleMaterialIds((current) => selected ? current.filter((id) => id !== material.id) : [...current, material.id])}/><i style={{ backgroundColor: `rgb(${color.map((value) => Math.round(value * 255)).join(',')})` }}/><span>{material.name}</span></label>; })}</div></aside>}
        {hasMaterialData && colorMode === 'material' && <div className="vtk-material-legend">{visibleMaterials.length ? visibleMaterials.map((material) => { const color = materialColor(material.id); return <div key={material.id}><i style={{ backgroundColor: `rgb(${color.map((value) => Math.round(value * 255)).join(',')})` }}/><span>{material.name}</span></div>; }) : <small>Sin materiales visibles</small>}</div>}
        <div className="viewer-axis-labels" ref={axisLabelsRef} aria-hidden="true"><span data-axis="x">X</span><span data-axis="y">Y</span><span data-axis="z">Z</span></div>
        <div className="viewer-hint" title="Izquierdo: rotar · Rueda presionada: desplazar · Rueda: zoom · Derecho: zoom continuo"><Rotate3D size={14}/>{route ? 'Arrastra para girar · Rueda para acercar' : 'Izquierdo: Rotar · Rueda Presionada: Desplazar · Rueda: Zoom · Derecho: Zoom Continuo'}</div>
      </div>
      <div className="vtk-caption"><Box size={14}/><span>{label}</span>
        <b>{route ? `${fields?.length ?? 0} posiciones visibles` : dataset?.kind === 'structured'
          ? `${dataset.values.length.toLocaleString(uiLocale())} celdas` : `${dataset?.points.length.toLocaleString(uiLocale())} puntos`}</b>
        <small>{route ? 'Escala común para todas las posiciones' : 'Corte interactivo por eje'}</small>
      </div>
      {reduced && <p className="inline-notice">Vista previa optimizada. Los mapas individuales y sus archivos conservan la resolución original.</p>}
    </div>
  );
}
