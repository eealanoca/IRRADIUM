/** Match every scientific viewport to the current application surface. */
export function viewerBackground(): [number, number, number] {
  const channels = getComputedStyle(document.documentElement).getPropertyValue('--viewer-rgb').trim().split(/\s+/).map(Number);
  return channels.length === 3 && channels.every(Number.isFinite)
    ? [channels[0] / 255, channels[1] / 255, channels[2] / 255]
    : [1, 1, 1];
}
