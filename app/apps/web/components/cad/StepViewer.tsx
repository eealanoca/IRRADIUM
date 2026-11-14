'use client';

import { viewerBackground } from '../../lib/viewerAppearance';
import { ASSET_RELOAD_MESSAGE, isAssetLoadError } from '../../lib/assetLoadError';

import { useEffect, useRef, useState } from 'react';
import { Box, Rotate3D } from 'lucide-react';

export type StepPreviewPart = {
  name: string;
  material: string;
  url: string;
  transparent: boolean;
};
export type ViewerPrimitive = {
  center: [number, number, number];
  size: [number, number, number];
  shape: 'box' | 'cylinder';
  yaw?: number;
  selected?: boolean;
};

function hslToRgb(hue: number, saturation: number, lightness: number): [number, number, number] {
  const chroma = (1 - Math.abs(2 * lightness - 1)) * saturation;
  const sector = hue / 60;
  const intermediate = chroma * (1 - Math.abs((sector % 2) - 1));
  const [red, green, blue] = sector < 1 ? [chroma, intermediate, 0]
    : sector < 2 ? [intermediate, chroma, 0]
      : sector < 3 ? [0, chroma, intermediate]
        : sector < 4 ? [0, intermediate, chroma]
          : sector < 5 ? [intermediate, 0, chroma]
            : [chroma, 0, intermediate];
  const match = lightness - chroma / 2;
  return [red + match, green + match, blue + match];
}

function componentColor(index: number) {
  const hue = 207 + (index * 17.37) % 68;
  const lightness = 0.52 + (index % 4) * 0.045;
  return hslToRgb(hue, 0.72, lightness);
}


export default function StepViewer({ parts, primitives }: { parts: StepPreviewPart[]; primitives?: ViewerPrimitive[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const axisLabelsRef = useRef<HTMLDivElement>(null);
  const [message, setMessage] = useState('Generando vista del modelo STEP…');

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let disposed = false;
    let dispose = () => undefined;

    const render = async () => {
      try {
        await import('@kitware/vtk.js/Rendering/Profiles/Geometry');
        const [
          windowModule,
          readerModule,
          mapperModule,
          actorModule,
          styleModule,
          rotateModule,
          panModule,
          zoomModule,
          axesModule,
          orientationModule,
        ] = await Promise.all([
          import('@kitware/vtk.js/Rendering/Misc/GenericRenderWindow'),
          import('@kitware/vtk.js/IO/Geometry/STLReader'),
          import('@kitware/vtk.js/Rendering/Core/Mapper'),
          import('@kitware/vtk.js/Rendering/Core/Actor'),
          import('@kitware/vtk.js/Interaction/Style/InteractorStyleManipulator'),
          import('@kitware/vtk.js/Interaction/Manipulators/MouseCameraTrackballRotateManipulator'),
          import('@kitware/vtk.js/Interaction/Manipulators/MouseCameraTrackballPanManipulator'),
          import('@kitware/vtk.js/Interaction/Manipulators/MouseCameraTrackballZoomManipulator'),
          import('@kitware/vtk.js/Rendering/Core/AxesActor'),
          import('@kitware/vtk.js/Interaction/Widgets/OrientationMarkerWidget'),
        ]);
        if (disposed) return;
        const view = windowModule.default.newInstance({ background: viewerBackground() });
        view.setContainer(container);
        view.resize();
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
          axisLabelPoints.forEach(({ axis, point }) => {
            const label = axisLabelsRef.current?.querySelector<HTMLElement>(`[data-axis="${axis}"]`);
            if (!label) return;
            const [normalizedX, normalizedY] = markerRenderer.worldToNormalizedDisplay(
              point[0],
              point[1],
              point[2],
              aspect,
            );
            label.style.left = `${normalizedX * width}px`;
            label.style.top = `${(1 - normalizedY) * height}px`;
          });
        };
        const resources: Array<{ reader: { delete: () => void }; mapper: ReturnType<typeof mapperModule.default.newInstance>; actor: ReturnType<typeof actorModule.default.newInstance> }> = [];
        const observer = new ResizeObserver(() => {
          view.resize();
          updateAxisLabels();
        });
        const cameraSubscription = renderer.getActiveCamera().onModified(updateAxisLabels);
        const themeObserver = new MutationObserver(() => {
          renderer.setBackground(...viewerBackground());
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
          view.delete();
          resources.forEach(({ reader, mapper, actor }) => {
            reader.delete();
            mapper.delete();
            actor.delete();
          });
        };
        if (!parts.length && !primitives?.length) {
          setMessage('Selecciona una geometría para mostrarla.');
          return;
        }
        if (primitives?.length) {
          const [cube, cylinder] = await Promise.all([
            import('@kitware/vtk.js/Filters/Sources/CubeSource'),
            import('@kitware/vtk.js/Filters/Sources/CylinderSource'),
          ]);
          if (disposed) return;
          for (const primitive of primitives) {
            const [x, y, z] = primitive.size;
            const source = primitive.shape === 'box'
              ? cube.default.newInstance({ xLength: x, yLength: y, zLength: z })
              : cylinder.default.newInstance({ radius: x / 2, height: z, resolution: 16 });
            const mapper = mapperModule.default.newInstance();
            mapper.setInputConnection(source.getOutputPort());
            const actor = actorModule.default.newInstance();
            actor.setMapper(mapper);
            actor.setPosition(...primitive.center);
            actor.setOrientation(primitive.shape === 'cylinder' ? 90 : 0, 0, primitive.yaw ?? 0);
            const property = actor.getProperty();
            const color: [number, number, number] = primitive.shape === 'cylinder' ? [.24, .79, .45] : primitive.selected ? [.99, .56, .17] : [.29, .57, .84];
            property.setColor(...color);
            property.setOpacity(primitive.shape === 'cylinder' || primitive.selected ? 1 : .19);
            property.setEdgeVisibility(primitive.shape === 'box');
            property.setEdgeColor(...color);
            resources.push({ reader: source, mapper, actor });
            renderer.addActor(actor);
          }
        }
        await Promise.all(parts.map(async (part, index) => {
          const response = await fetch(part.url);
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const reader = readerModule.default.newInstance();
          const mapper = mapperModule.default.newInstance();
          const actor = actorModule.default.newInstance();
          reader.parseAsArrayBuffer(await response.arrayBuffer());
          mapper.setInputConnection(reader.getOutputPort());
          mapper.setScalarVisibility(false);
          actor.setMapper(mapper);
          const color = componentColor(index);
          actor.getProperty().setColor(...color);
          actor.getProperty().setOpacity(part.name.toLowerCase() === 'air_001' ? 0.16 : 0.38);
          actor.getProperty().setAmbient(0.38);
          actor.getProperty().setDiffuse(0.48);
          actor.getProperty().setSpecular(0.42);
          actor.getProperty().setSpecularPower(24);
          actor.getProperty().setEdgeVisibility(true);
          actor.getProperty().setEdgeColor(color[0] * .55, color[1] * .55, color[2] * .55);
          resources.push({ reader, mapper, actor });
          renderer.addActor(actor);
        }));
        renderer.resetCamera();
        const camera = renderer.getActiveCamera();
        const focalPoint = camera.getFocalPoint();
        const distance = camera.getDistance();
        camera.setPosition(
          focalPoint[0] + distance * .78,
          focalPoint[1] - distance * .78,
          focalPoint[2] + distance * .56,
        );
        camera.setViewUp(0, 0, 1);
        camera.orthogonalizeViewUp();
        renderer.resetCamera();
        interactorStyle.setCenterOfRotation(camera.getFocalPoint());
        renderer.resetCameraClippingRange();
        updateAxisLabels();
        renderWindow.render();
        setMessage('Izquierdo: Rotar · Rueda Presionada: Desplazar · Rueda: Zoom · Derecho: Zoom Continuo');
      } catch (error) {
        if (!disposed) setMessage(isAssetLoadError(error) ? ASSET_RELOAD_MESSAGE
          : 'No fue posible generar la vista. Revisa el archivo STEP o la API local.');
      }
    };
    void render();
    return () => {
      disposed = true;
      dispose();
    };
  }, [parts, primitives]);

  return (
    <div className="step-viewer-shell">
      <div className="step-viewer" ref={containerRef} />
      <div className="viewer-axis-labels" ref={axisLabelsRef} aria-hidden="true">
        <span data-axis="x">X</span>
        <span data-axis="y">Y</span>
        <span data-axis="z">Z</span>
      </div>
      <div className="viewer-model-badge"><Box size={15} /> {primitives ? 'Posiciones y fuentes' : `${parts.length} componentes · transparentes`}</div>
      <div className="viewer-hint"><Rotate3D size={14} />{message}</div>
    </div>
  );
}
