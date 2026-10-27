import type { PolygonPoint } from "./model";

// SAM's outlines follow the mask to half a pixel, far more points than a label needs.
// Visvalingam's algorithm thins a line by dropping, again and again, the point whose
// triangle with its two neighbours is smallest. Ranking every point by when it drops lets
// the reviewer pick any point count at once: a count keeps the points that drop last.
export type RankedOutline = {
  points: PolygonPoint[];
  // The step at which each point drops; the three that never drop come last.
  dropStep: number[];
  // The area each step removes, never smaller than an earlier step's.
  dropArea: number[];
};

const MIN_POINTS = 3;
// A point is detail worth keeping by default when its triangle covers at least this
// share of the outline's bounding box. Being a share, it keeps a circle at about 25
// points however large the circle is.
const DETAIL_SHARE = 0.001;

export function rankOutline(points: PolygonPoint[]): RankedOutline {
  const count = points.length;
  const previous = points.map((_, index) => (index - 1 + count) % count);
  const next = points.map((_, index) => (index + 1) % count);
  const dropped = points.map(() => false);
  const area = points.map((_, index) => cornerArea(points, previous[index]!, index, next[index]!));
  const dropStep = new Array<number>(count);
  const dropArea: number[] = [];
  let largest = 0;
  for (let step = 0; step < count - MIN_POINTS; step += 1) {
    const victim = smallestCorner(area, dropped);
    largest = Math.max(largest, area[victim]!);
    dropStep[victim] = step;
    dropArea.push(largest);
    dropped[victim] = true;
    const before = previous[victim]!;
    const after = next[victim]!;
    next[before] = after;
    previous[after] = before;
    area[before] = cornerArea(points, previous[before]!, before, after);
    area[after] = cornerArea(points, before, after, next[after]!);
  }
  let step = count - MIN_POINTS;
  dropped.forEach((isDropped, index) => {
    if (!isDropped) {
      dropStep[index] = step;
      step += 1;
    }
  });
  return { points, dropStep, dropArea };
}

// The outline thinned to a number of points, in their original order.
export function outlineWithPoints(outline: RankedOutline, count: number): PolygonPoint[] {
  const total = outline.points.length;
  const firstKept = total - Math.min(Math.max(Math.round(count), MIN_POINTS), total);
  return outline.points.filter((_, index) => outline.dropStep[index]! >= firstKept);
}

// The point count that keeps every point carrying visible shape.
export function defaultPointCount(outline: RankedOutline): number {
  const xs = outline.points.map(([x]) => x);
  const ys = outline.points.map(([, y]) => y);
  const boundsArea = (Math.max(...xs) - Math.min(...xs)) * (Math.max(...ys) - Math.min(...ys));
  const threshold = DETAIL_SHARE * boundsArea;
  const firstKept = outline.dropArea.findIndex((area) => area >= threshold);
  const droppedCount = firstKept < 0 ? outline.dropArea.length : firstKept;
  return outline.points.length - droppedCount;
}

function smallestCorner(area: number[], dropped: boolean[]): number {
  let smallest = -1;
  area.forEach((value, index) => {
    if (!dropped[index] && (smallest < 0 || value < area[smallest]!)) {
      smallest = index;
    }
  });
  return smallest;
}

function cornerArea(points: PolygonPoint[], before: number, at: number, after: number): number {
  const [ax, ay] = points[before]!;
  const [bx, by] = points[at]!;
  const [cx, cy] = points[after]!;
  return Math.abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay)) / 2;
}
