import type { IrradiatorType } from '../../lib/types';

export type Point = [number, number, number];
export type Face = { points: Point[]; fill: string; stroke: string; edges?: boolean[] };
type Material = readonly [front: string, side: string, top: string];
type Footprint = [left: number, right: number, near: number, far: number];

const concrete: Material = ['#687983', '#50616b', '#9ba9ae'];
const cutConcrete: Material = ['#72858e', '#586c76', '#b5c4c7'];
const foundation: Material = ['#455762', '#364953', '#647883'];
const floor: Material = ['#5a7079', '#4d626c', '#778d96'];
const steel: Material = ['#405864', '#2f4654', '#849fa9'];
const lightSteel: Material = ['#7b969f', '#55717e', '#b2c6cb'];
const darkSteel: Material = ['#263f4b', '#1d333e', '#527482'];
const crate: Material = ['#937956', '#73583d', '#bea585'];
const amber: Material = ['#b98a49', '#927044', '#d8b478'];
const blue: Material = ['#4e788d', '#395e71', '#7ea6b5'];
const liner: Material = ['#315e70', '#284b5b', '#51899a'];
const outline = 'rgba(195,220,226,.21)';

function prism(faces: Face[], ring: [number, number][], bottom: number, top: number, colors: Material, stroke = outline) {
  const base: Point[] = ring.map(([x, z]) => [x, bottom, z]);
  const cap: Point[] = ring.map(([x, z]) => [x, top, z]);
  faces.push({ points: cap, fill: colors[2], stroke }, { points: [...base].reverse(), fill: colors[0], stroke });
  for (let i = 0; i < ring.length; i++) {
    const j = (i + 1) % ring.length;
    faces.push({ points: [base[i], base[j], cap[j], cap[i]], fill: colors[i % 2], stroke });
  }
}

function block(faces: Face[], [left, right, near, far]: Footprint, bottom: number, top: number, colors: Material, stroke = outline) {
  prism(faces, [[left, near], [right, near], [right, far], [left, far]], bottom, top, colors, stroke);
}

function box(faces: Face[], [x, y, z]: Point, [w, h, d]: Point, colors: Material, stroke = outline) {
  block(faces, [x - w / 2, x + w / 2, z - d / 2, z + d / 2], y - h / 2, y + h / 2, colors, stroke);
}

function cylinder(faces: Face[], x: number, z: number, bottom: number, top: number, radius: number, colors: Material) {
  const segments = 24;
  const ring: [number, number][] = Array.from({ length: segments }, (_, i) => {
    const angle = i / segments * Math.PI * 2;
    return [x + Math.cos(angle) * radius, z + Math.sin(angle) * radius];
  });
  const first = faces.length;
  prism(faces, ring, bottom, top, colors, 'transparent');
  // Gradual shading keeps the curved shell readable without outlining each facet.
  for (let i = 0; i < segments; i++) {
    const light = Math.cos((i + .5) / segments * Math.PI * 2 + 2.1);
    const color = colors[0].slice(1).match(/../g)!.map((channel) =>
      Math.min(255, Math.round(parseInt(channel, 16) * (.84 + .18 * light))));
    faces[first + 2 + i].fill = `rgb(${color.join(',')})`;
  }
}

function sourceRack(faces: Face[], x: number, y: number, z: number, width: number, height: number) {
  const bar = .075;
  for (const end of [-1, 1]) {
    box(faces, [x + end * (width - bar) / 2, y, z], [bar, height, .19], steel);
    box(faces, [x, y + end * (height - bar) / 2, z], [width - 2 * bar, bar, .19], lightSteel);
  }
  for (let i = 1; i <= 9; i++) {
    const slot = x + (i / 10 - .5) * (width - 2 * bar);
    box(faces, [slot, y, z], [.055, height - 2 * bar, .085], amber, 'transparent');
  }
  box(faces, [x, y + height / 2 + .055, z], [.31, .11, .19], lightSteel);
}

function cable(faces: Face[], x: number, z: number, bottom: number, top: number) {
  box(faces, [x, (bottom + top) / 2, z], [.027, top - bottom, .027], lightSteel, 'transparent');
}

function load(faces: Face[], x: number, y: number, z: number) {
  box(faces, [x, y + .035, z], [.63, .07, .56], darkSteel);
  box(faces, [x, y + .36, z], [.56, .58, .5], crate);
  for (const side of [-1, 1]) box(faces, [x + side * .19, y + .36, z], [.029, .589, .508], lightSteel, 'transparent');
}

type RailPoint = [x: number, z: number];

function railCurve(route: RailPoint[]) {
  const curve = [route[0]];
  const radius = .42;
  for (let i = 1; i < route.length - 1; i++) {
    const [x, z] = route[i];
    const [fromX, fromZ] = route[i - 1];
    const [toX, toZ] = route[i + 1];
    const approach: RailPoint = [Math.sign(x - fromX), Math.sign(z - fromZ)];
    const departure: RailPoint = [Math.sign(toX - x), Math.sign(toZ - z)];
    const turnX = x - radius * approach[0] + radius * departure[0];
    const turnZ = z - radius * approach[1] + radius * departure[1];
    for (let step = 0; step <= 6; step++) {
      const angle = step / 6 * Math.PI / 2;
      curve.push([
        turnX + radius * (Math.sin(angle) * approach[0] - Math.cos(angle) * departure[0]),
        turnZ + radius * (Math.sin(angle) * approach[1] - Math.cos(angle) * departure[1]),
      ]);
    }
  }
  curve.push(route[route.length - 1]);
  return curve;
}

function railSection(faces: Face[], path: RailPoint[], width: number, bottom: number, top: number, colors: Material) {
  const edges = path.map(([x, z], i) => {
    const [fromX, fromZ] = path[Math.max(i - 1, 0)];
    const [toX, toZ] = path[Math.min(i + 1, path.length - 1)];
    const length = Math.hypot(toX - fromX, toZ - fromZ);
    const dx = -(toZ - fromZ) * width / (2 * length);
    const dz = (toX - fromX) * width / (2 * length);
    return { left: [x + dx, z + dz] as RailPoint, right: [x - dx, z - dz] as RailPoint };
  });

  for (let i = 1; i < edges.length; i++) {
    const previous = edges[i - 1], current = edges[i];
    prism(faces, [previous.right, current.right, current.left, previous.left], bottom, top, colors, 'transparent');
  }
}

function hangingCarrier(faces: Face[], x: number, z: number, ground: number, track: number, alongZ = false) {
  const hangerTop = ground + 1.83;
  const lowerShelf = ground + .52;
  const upperShelf = ground + 1.17;
  const part = (offsetX: number, y: number, offsetZ: number, width: number, height: number, depth: number, material: Material) => {
    const center: Point = alongZ ? [x + offsetZ, y, z + offsetX] : [x + offsetX, y, z + offsetZ];
    const size: Point = alongZ ? [depth, height, width] : [width, height, depth];
    box(faces, center, size, material);
  };

  part(0, track - .026, 0, .26, .10, .19, darkSteel);
  part(0, (hangerTop + track) / 2, 0, .068, track - hangerTop, .068, lightSteel);
  part(0, hangerTop, 0, .63, .068, .12, lightSteel);
  for (const dx of [-.278, .278]) {
    for (const dz of [-.218, .218]) {
      part(dx, (hangerTop + lowerShelf) / 2, dz, .039, hangerTop - lowerShelf, .039, steel);
    }
    part(dx, hangerTop - .014, 0, .045, .046, .46, steel);
  }
  for (const shelf of [lowerShelf, upperShelf]) {
    part(0, shelf, 0, .60, .069, .49, lightSteel);
    part(0, shelf + .25, 0, .47, .43, .38, crate);
  }
}

function suspendedTransport(faces: Face[], ground: number, ceiling: number) {
  const track = ground + 2.23;
  const route: RailPoint[] = [
    [3.92, -3.01], [3.92, 1.24], [2.35, 1.24], [2.35, -1.31],
    [-2.85, -1.31], [-2.85, 1.16], [.85, 1.16],
  ];
  const path = railCurve(route);
  railSection(faces, path, .16, track, track + .034, lightSteel);
  railSection(faces, path, .062, track + .034, track + .137, steel);
  railSection(faces, path, .17, track + .137, track + .178, lightSteel);

  // Roof steel spans the room, with hangers to both passes along the pool.
  for (const x of [-2.23, .63]) {
    box(faces, [x, ceiling - .056, .35], [.13, .13, 3.33], steel);
    for (const z of [-1.31, 1.16]) {
      const top = ceiling - .12;
      box(faces, [x, (top + track + .17) / 2, z], [.067, top - track - .17, .067], steel);
    }
  }

  // Brackets in the roof cut tie the exposed maze spans back to the retained wall.
  const bracket = track + .25;
  for (const x of [2.92, 3.36]) {
    block(faces, [x - .063, x + .063, 1.24, 2.045], bracket - .05, bracket + .05, steel);
    box(faces, [x, track + .207, 1.24], [.064, .085, .071], steel);
  }
  box(faces, [3.92, track + .209, -2.39], [.069, .063, .071], steel);

  hangingCarrier(faces, -1.69, -1.31, ground, track);
  hangingCarrier(faces, .37, -1.31, ground, track);
  hangingCarrier(faces, -.48, 1.16, ground, track);
  hangingCarrier(faces, 3.92, -2.34, ground, track, true);
}

function tank(faces: Face[], [left, right, near, far]: Footprint, bottom: number, rim: number) {
  const wall = .29;
  const water = rim - .26;
  block(faces, [left - wall, right + wall, near - wall, far + wall], bottom - .23, bottom, foundation);
  block(faces, [left - wall, left, near - wall, far + wall], bottom, rim, cutConcrete);
  block(faces, [right, right + wall, near - wall, far + wall], bottom, rim, cutConcrete);
  block(faces, [left, right, far, far + wall], bottom, rim, concrete);
  // The near wall is cut below the waterline to reveal the well in section.
  block(faces, [left, right, near - wall, near], bottom, bottom + .22, cutConcrete);
  block(faces, [left, right, near, far], bottom, bottom + .04, liner);
  faces.push({
    points: [[left, water, near], [right, water, near], [right, water, far], [left, water, far]],
    fill: 'rgba(57,151,181,.48)', stroke: 'rgba(160,218,227,.64)',
  }, {
    points: [[left, bottom + .22, near], [right, bottom + .22, near], [right, water, near], [left, water, near]],
    fill: 'rgba(35,110,139,.31)', stroke: 'rgba(126,190,207,.47)',
  });
}

function bunkerDeck(faces: Face[], ground: number, wet: boolean) {
  if (!wet) {
    block(faces, [-4.38, 5.2, -3.05, 2.85], ground - .32, ground, foundation);
    return;
  }
  const pit: Footprint = [-2.05, .22, -.79, .79];
  tank(faces, pit, .23, ground - .02);
  const [left, right, near, far] = pit;
  block(faces, [-4.38, left, -3.05, 2.85], ground - .32, ground, floor);
  block(faces, [right, 5.2, -3.05, 2.85], ground - .32, ground, floor);
  block(faces, [left, right, -3.05, near - .27], ground - .32, ground, floor);
  block(faces, [left, right, far, 2.85], ground - .32, ground, floor);
  // The below-grade plinth surrounds the well; the front is sectioned in line with it.
  block(faces, [-4.38, 5.2, 1.12, 2.85], .23, ground - .32, foundation);
  block(faces, [-4.38, left - .29, -3.05, 1.12], .23, ground - .32, foundation);
  block(faces, [right + .29, 5.2, -3.05, 1.12], .23, ground - .32, foundation);
}

function bunkerShell(faces: Face[], ground: number, wet: boolean) {
  const ceiling = ground + 2.85;
  const roof = ceiling + .6;
  // Retained walls and the slab meet flush at the cut; the roof opens over the cell.
  block(faces, [-4.2, 5.03, 2.02, 2.65], ground, ceiling, concrete);
  block(faces, [-4.2, -3.57, -2.65, 2.02], ground, ceiling, concrete);
  block(faces, [-4.2, 5.03, 1.36, 2.65], ceiling, roof, cutConcrete);
  block(faces, [-4.2, -3.34, -2.65, 1.36], ceiling, roof, cutConcrete);
  block(faces, [-3.57, 1.25, -2.65, -2.12], ground, ground + .34, cutConcrete);

  // The right wing shares the back wall and carries two overlapping sight barriers.
  // Its reduced cut height leaves the dogleg and exterior access visible.
  block(faces, [4.43, 5.03, -2.65, 2.02], ground, ground + 1.22, cutConcrete);
  block(faces, [1.25, 1.85, -.67, 2.02], ground, ground + 1.62, cutConcrete);
  block(faces, [1.25, 1.85, -2.65, -1.94], ground, ground + 1.62, cutConcrete);
  block(faces, [1.85, 3.41, -2.65, -2.1], ground, ground + 1.22, cutConcrete);
  block(faces, [2.86, 3.41, -2.1, .57], ground, ground + 1.22, cutConcrete);
  const doorwayTop = ground + (wet ? 2.47 : 1.38);
  block(faces, [3.41, 3.53, -2.65, -2.12], ground, doorwayTop, concrete);
  block(faces, [4.31, 4.43, -2.65, -2.12], ground, doorwayTop, concrete);
  block(faces, [3.41, 4.43, -2.65, -2.12], doorwayTop, ground + (wet ? 2.75 : 1.66), cutConcrete);
  block(faces, [3.53, 4.31, -2.68, -2.64], ground, ground + .07, steel);
  return ceiling;
}

function panoramic(faces: Face[], wet: boolean) {
  const ground = wet ? 1.78 : .36;
  const sourceX = -.93;
  bunkerDeck(faces, ground, wet);
  const ceiling = bunkerShell(faces, ground, wet);
  const rackY = ground + 1.13;
  sourceRack(faces, sourceX, rackY, 0, wet ? 1.67 : .94, 1.19);
  // The lift hangs from the chamber ceiling. A cut through the roof reveals the guide path.
  box(faces, [sourceX, ceiling - .05, .67], [1.2, .17, 1.55], steel);
  box(faces, [sourceX, ceiling + .13, .09], [.55, .19, .51], lightSteel);
  cable(faces, sourceX, 0, rackY + .705, ceiling + .08);
  for (const dx of [-.69, .69]) cable(faces, sourceX + dx, .18, ground + .1, ceiling - .12);

  if (wet) {
    suspendedTransport(faces, ground, ceiling);
  } else {
    block(faces, [-1.65, -.22, -.49, .49], ground, ground + .27, concrete);
    box(faces, [sourceX, ground + .282, 0], [1.05, .04, .31], darkSteel);
    for (const [x, z] of [[-1.63, -1.29], [.46, -.32]]) {
      cylinder(faces, x, z, ground, ground + .18, .49, steel);
      load(faces, x, ground + .18, z);
    }
  }
}

function selfShielded(faces: Face[]) {
  const x = -.51;
  block(faces, [-1.89, 1.58, -1.35, 1.18], 0, .15, foundation);
  cylinder(faces, x, .11, .15, .37, 1.03, darkSteel);
  cylinder(faces, x, .11, .37, 2.47, .94, lightSteel);
  cylinder(faces, x, .11, 2.47, 2.66, .98, blue);
  cylinder(faces, x, .11, 2.66, 2.83, .65, lightSteel);
  // The sample drawer sits in a projecting collar on the round shield body.
  block(faces, [-1.12, .1, -1.03, -.66], 1.03, 1.95, darkSteel);
  block(faces, [-1.04, .02, -1.12, -1.03], 1.11, 1.87, blue);
  box(faces, [x, 1.45, -1.166], [.49, .074, .093], lightSteel);
  box(faces, [x, 1.68, -1.127], [.094, .094, .016], amber, 'transparent');
  block(faces, [.55, 1.35, -.73, .38], .15, 1.91, steel);
  block(faces, [.64, 1.26, -.759, -.73], 1.37, 1.77, darkSteel);
  block(faces, [.73, 1.17, -.771, -.759], 1.49, 1.69, blue);
}

function underwater(faces: Face[]) {
  const deck = 2.62;
  tank(faces, [-1.55, 1.33, -1.08, .94], .28, deck);
  block(faces, [-2.2, -1.55, -1.42, 1.51], deck - .22, deck, floor);
  block(faces, [1.33, 2.31, -1.42, 1.51], deck - .22, deck, floor);
  block(faces, [-1.55, 1.33, .94, 1.51], deck - .22, deck, floor);
  sourceRack(faces, -.22, .92, .5, 1.78, .92);
  const basketX = .03, basketZ = -.34;
  const basketBottom = 1.16, basketTop = 1.8;
  box(faces, [basketX, basketBottom + .045, basketZ], [.81, .09, .61], steel);
  box(faces, [basketX, basketBottom + .26, basketZ], [.61, .36, .42], crate);
  for (const dx of [-.37, .37]) for (const dz of [-.27, .27]) {
    box(faces, [basketX + dx, (basketBottom + basketTop) / 2, basketZ + dz], [.045, basketTop - basketBottom, .045], lightSteel);
  }
  box(faces, [basketX, basketTop, basketZ], [.79, .065, .11], lightSteel);
  const lift = deck + 1.48;
  for (const x of [-1.89, 1.84]) box(faces, [x, (deck + lift) / 2, basketZ], [.135, lift - deck, .17], steel);
  box(faces, [-.025, lift, basketZ], [3.91, .19, .25], lightSteel);
  box(faces, [basketX, lift - .12, basketZ], [.41, .18, .36], steel);
  cable(faces, basketX, basketZ, basketTop + .03, lift - .21);
  for (const x of [-2.03, -1, 0, 1, 2.15]) box(faces, [x, deck + .26, 1.36], [.051, .52, .051], lightSteel);
  box(faces, [.06, deck + .52, 1.36], [4.24, .052, .065], lightSteel);
  block(faces, [1.64, 2.15, .41, .82], deck, deck + .61, steel);
}

export function irradiatorModel(type?: IrradiatorType): Face[] {
  const faces: Face[] = [];
  if (type?.geometry === 'self-contained') {
    if (type.source_storage === 'wet') underwater(faces);
    else selfShielded(faces);
  } else panoramic(faces, type?.source_storage !== 'dry');
  return faces;
}

type Plane = { normal: Point; offset: number };
type Polygon = Face & { plane: Plane };
type DepthNode = { plane: Plane; polygons: Polygon[]; positive?: DepthNode; negative?: DepthNode };
const depthTrees = new WeakMap<Face[], DepthNode>();
const epsilon = .000001;

function dot([ax, ay, az]: Point, [bx, by, bz]: Point) {
  return ax * bx + ay * by + az * bz;
}

function facePlane([a, b, c]: Point[]): Plane {
  const [ux, uy, uz] = b.map((coordinate, i) => coordinate - a[i]);
  const [vx, vy, vz] = c.map((coordinate, i) => coordinate - a[i]);
  const normal: Point = [uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx];
  const length = Math.hypot(...normal);
  const unit = normal.map((coordinate) => coordinate / length) as Point;
  return { normal: unit, offset: dot(unit, a) };
}

function signedDistance(plane: Plane, point: Point) {
  return dot(plane.normal, point) - plane.offset;
}

function polygonSide(plane: Plane, polygon: Polygon) {
  let side = 0;
  for (const point of polygon.points) {
    const distance = signedDistance(plane, point);
    if (distance > epsilon) side |= 1;
    if (distance < -epsilon) side |= 2;
    if (side === 3) break;
  }
  return side;
}

function clipPolygon(polygon: Polygon, plane: Plane, sign: number): Polygon {
  const points: Point[] = [];
  const incomingEdges: boolean[] = [];
  const { points: source, edges } = polygon;
  let previous = source[source.length - 1];
  let previousDistance = sign * signedDistance(plane, previous);
  for (let i = 0; i < source.length; i++) {
    const current = source[i];
    const currentDistance = sign * signedDistance(plane, current);
    const edgeVisible = edges?.[(i + source.length - 1) % source.length] ?? true;
    const entering = previousDistance < -epsilon && currentDistance > epsilon;
    const leaving = previousDistance > epsilon && currentDistance < -epsilon;
    if (entering || leaving) {
      const t = previousDistance / (previousDistance - currentDistance);
      points.push(previous.map((coordinate, axis) => coordinate + t * (current[axis] - coordinate)) as Point);
      incomingEdges.push(leaving && edgeVisible);
    }
    if (currentDistance >= -epsilon) {
      points.push(current);
      incomingEdges.push((previousDistance >= -epsilon || entering) && edgeVisible);
    }
    previous = current;
    previousDistance = currentDistance;
  }
  const outgoingEdges = points.map((_, i) => incomingEdges[(i + 1) % points.length]);
  return { ...polygon, points, edges: outgoingEdges };
}

function partition(polygons: Polygon[]): DepthNode | undefined {
  if (!polygons.length) return;
  let plane = polygons[0].plane;
  let bestScore = Infinity;
  // Sample candidate planes to avoid fragmenting large slabs on every small fixture.
  const step = Math.max(1, Math.floor(polygons.length / 22));
  for (let i = 0; i < polygons.length; i += step) {
    const candidate = polygons[i].plane;
    const counts = [0, 0, 0, 0];
    for (const polygon of polygons) counts[polygonSide(candidate, polygon)]++;
    const score = counts[3] * 6 + Math.abs(counts[1] - counts[2]);
    if (score < bestScore) {
      bestScore = score;
      plane = candidate;
    }
  }
  const node: DepthNode = { plane, polygons: [] };
  const positive: Polygon[] = [], negative: Polygon[] = [];
  for (const polygon of polygons) {
    const side = polygonSide(plane, polygon);
    if (side === 0) node.polygons.push(polygon);
    if (side === 1) positive.push(polygon);
    if (side === 2) negative.push(polygon);
    if (side === 3) {
      positive.push(clipPolygon(polygon, plane, 1));
      negative.push(clipPolygon(polygon, plane, -1));
    }
  }
  node.positive = partition(positive);
  node.negative = partition(negative);
  return node;
}

function visiblePolygons(node: DepthNode | undefined, eye: Point, result: Polygon[]) {
  if (!node) return;
  const fromPositive = signedDistance(node.plane, eye) > 0;
  visiblePolygons(fromPositive ? node.negative : node.positive, eye, result);
  for (const polygon of node.polygons) {
    if (signedDistance(polygon.plane, eye) < -epsilon) result.push(polygon);
  }
  visiblePolygons(fromPositive ? node.positive : node.negative, eye, result);
}

export function projectIrradiator(model: Face[], width: number, height: number, yaw: number, pitch: number, category?: string) {
  const cosYaw = Math.cos(yaw), sinYaw = Math.sin(yaw);
  const cosPitch = Math.cos(pitch), sinPitch = Math.sin(pitch);
  const centerHeight = category === 'I' ? 1.4 : category === 'II' ? 1.65 : 2.45;
  const cameraDistance = 23;
  const camera = ([x, y, z]: Point) => {
    const rx = x * cosYaw - z * sinYaw;
    const rz = x * sinYaw + z * cosYaw;
    const vertical = y - centerHeight;
    const depth = rz * cosPitch - vertical * sinPitch;
    const perspective = cameraDistance / (cameraDistance + depth);
    return { x: rx * perspective, y: -(vertical * cosPitch + rz * sinPitch) * perspective };
  };
  let left = Infinity, right = -Infinity, top = Infinity, bottom = -Infinity;
  for (const { points } of model) for (const point of points) {
    const { x, y } = camera(point);
    left = Math.min(left, x);
    right = Math.max(right, x);
    top = Math.min(top, y);
    bottom = Math.max(bottom, y);
  }
  const scale = Math.min(width * .86 / (right - left), height * .84 / (bottom - top));
  const centerX = width * .5 - (left + right) / 2 * scale;
  const centerY = height * .49 - (top + bottom) / 2 * scale;
  const project = (point: Point) => {
    const { x, y } = camera(point);
    return { x: centerX + x * scale, y: centerY + y * scale };
  };

  // A static partition splits intersecting faces once. Ordering by face centers alone
  // lets large walls, floors and water draw over nearer parts when the camera turns.
  let tree = depthTrees.get(model);
  if (!tree) {
    tree = partition(model.map((face) => ({ ...face, plane: facePlane(face.points) })));
    if (tree) depthTrees.set(model, tree);
  }
  const eye: Point = [-cameraDistance * sinYaw * cosPitch,
    centerHeight + cameraDistance * sinPitch, -cameraDistance * cosYaw * cosPitch];
  const ordered: Polygon[] = [];
  visiblePolygons(tree, eye, ordered);
  const visibleFaces = ordered.map(({ points, fill, stroke, edges }) => ({ polygon: points.map(project), fill, stroke, edges }));
  return { scale, project, visibleFaces };
}
