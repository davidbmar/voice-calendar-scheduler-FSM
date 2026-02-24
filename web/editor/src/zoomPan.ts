/**
 * zoomPan.ts — CSS transform-based zoom/pan controller.
 * Attaches mouse-wheel zoom (toward cursor) and click-drag pan
 * to a viewport/content pair. Content is transformed via
 * `transform: translate(tx, ty) scale(s)` with `transform-origin: 0 0`.
 */

export interface ZoomPanController {
  fitToView(): void;
  panToNode(nodeId: string): void;
  zoomToNode(nodeId: string): void;
  getTransform(): { tx: number; ty: number; scale: number };
  setTransform(tx: number, ty: number, scale: number): void;
  destroy(): void;
}

export function attachZoomPan(
  viewport: HTMLElement,
  content: HTMLElement,
  onChange?: () => void,
): ZoomPanController {
  let scale = 1;
  let tx = 0;
  let ty = 0;

  const MIN_SCALE = 0.2;
  const MAX_SCALE = 3.0;

  content.style.transformOrigin = '0 0';

  function apply(): void {
    content.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
    onChange?.();
  }

  // ── Wheel zoom toward cursor ──────────────────────────────────────

  function onWheel(e: WheelEvent): void {
    e.preventDefault();
    const rect = viewport.getBoundingClientRect();
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;

    // cursor position in content-space before zoom
    const contentX = (cx - tx) / scale;
    const contentY = (cy - ty) / scale;

    const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale * factor));

    // adjust translation so the content point under cursor stays put
    tx = cx - contentX * scale;
    ty = cy - contentY * scale;
    apply();
  }

  // ── Click-drag pan ────────────────────────────────────────────────

  let dragging = false;
  let startX = 0;
  let startY = 0;
  let startTx = 0;
  let startTy = 0;

  function onPointerDown(e: PointerEvent): void {
    // skip if clicking an interactive element
    const target = e.target as HTMLElement;
    if (target.closest('.wf-node') || target.closest('.wf-arrow')) return;

    dragging = true;
    startX = e.clientX;
    startY = e.clientY;
    startTx = tx;
    startTy = ty;
    viewport.setPointerCapture(e.pointerId);
  }

  function onPointerMove(e: PointerEvent): void {
    if (!dragging) return;
    tx = startTx + (e.clientX - startX);
    ty = startTy + (e.clientY - startY);
    apply();
  }

  function onPointerUp(): void {
    dragging = false;
  }

  viewport.addEventListener('wheel', onWheel, { passive: false });
  viewport.addEventListener('pointerdown', onPointerDown);
  viewport.addEventListener('pointermove', onPointerMove);
  viewport.addEventListener('pointerup', onPointerUp);

  // ── fitToView: scale content to fit viewport with margin ──────────

  function fitToView(): void {
    // Reset transform to measure natural layout
    content.style.transform = 'none';

    // Compute the true visual bounding box by unioning all descendant rects.
    // This captures content that overflows leftward (which scrollWidth misses)
    // when align-items: center causes wider children to extend past the left edge.
    const origin = content.getBoundingClientRect();
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;

    // Measure leaf graph elements only. Skip .wf-branch (has width:100% which
    // inflates bounds) and the absolutely-positioned link-connector SVG.
    content.querySelectorAll('.wf-node, .wf-arrow, .link-target, .add-state-btn').forEach(child => {
      const r = child.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) return;
      minX = Math.min(minX, r.left);
      minY = Math.min(minY, r.top);
      maxX = Math.max(maxX, r.right);
      maxY = Math.max(maxY, r.bottom);
    });

    if (!isFinite(minX)) return;

    // Convert to content-local coordinates
    const localMinX = minX - origin.left;
    const localMinY = minY - origin.top;
    const localMaxX = maxX - origin.left;
    const localMaxY = maxY - origin.top;
    const cw = localMaxX - localMinX;
    const ch = localMaxY - localMinY;

    const vw = viewport.clientWidth;
    const vh = viewport.clientHeight;

    if (cw === 0 || ch === 0) return;

    const margin = 0.9; // 10% padding
    scale = Math.min(vw / cw, vh / ch) * margin;
    scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale));

    // Center the visual bounding box in the viewport
    const cx = (localMinX + localMaxX) / 2;
    const cy = (localMinY + localMaxY) / 2;
    tx = vw / 2 - cx * scale;
    ty = vh / 2 - cy * scale;
    apply();
  }

  function panToNode(nodeId: string): void {
    const node = content.querySelector(`[data-node="${nodeId}"]`) as HTMLElement | null;
    if (!node) return;

    const nodeRect = node.getBoundingClientRect();
    const contentRect = content.getBoundingClientRect();

    // Node center in content-local coordinates (before transform)
    const nodeCx = (nodeRect.left + nodeRect.width / 2 - contentRect.left) / scale;
    const nodeCy = (nodeRect.top + nodeRect.height / 2 - contentRect.top) / scale;

    const vw = viewport.clientWidth;
    const vh = viewport.clientHeight;

    // Smoothly animate to center the node
    const targetTx = vw / 2 - nodeCx * scale;
    const targetTy = vh / 2 - nodeCy * scale;

    const startTxAnim = tx;
    const startTyAnim = ty;
    const duration = 300;
    const start = performance.now();

    function animate(now: number): void {
      const progress = Math.min((now - start) / duration, 1);
      const ease = 1 - Math.pow(1 - progress, 3); // ease-out cubic
      tx = startTxAnim + (targetTx - startTxAnim) * ease;
      ty = startTyAnim + (targetTy - startTyAnim) * ease;
      apply();
      if (progress < 1) requestAnimationFrame(animate);
    }
    requestAnimationFrame(animate);
  }

  /** Smoothly zoom in and pan to center a node — used for live debug walkthrough. */
  function zoomToNode(nodeId: string): void {
    const node = content.querySelector(`[data-node="${nodeId}"]`) as HTMLElement | null;
    if (!node) return;

    const nodeRect = node.getBoundingClientRect();
    const contentRect = content.getBoundingClientRect();

    // Node center in content-local coordinates
    const nodeCx = (nodeRect.left + nodeRect.width / 2 - contentRect.left) / scale;
    const nodeCy = (nodeRect.top + nodeRect.height / 2 - contentRect.top) / scale;

    // Node width in content-local coords — target scale makes node ~35% of viewport
    const nodeW = nodeRect.width / scale;
    const vw = viewport.clientWidth;
    const vh = viewport.clientHeight;
    const targetScale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, (vw * 0.35) / Math.max(nodeW, 1)));

    // Center the node at the target scale
    const targetTx = vw / 2 - nodeCx * targetScale;
    const targetTy = vh / 2 - nodeCy * targetScale;

    const startTxAnim = tx;
    const startTyAnim = ty;
    const startScale = scale;
    const duration = 700;
    const start = performance.now();

    function animate(now: number): void {
      const progress = Math.min((now - start) / duration, 1);
      // ease-in-out cubic for a gentle, deliberate feel
      const ease = progress < 0.5
        ? 4 * progress * progress * progress
        : 1 - Math.pow(-2 * progress + 2, 3) / 2;

      scale = startScale + (targetScale - startScale) * ease;
      tx = startTxAnim + (targetTx - startTxAnim) * ease;
      ty = startTyAnim + (targetTy - startTyAnim) * ease;
      apply();
      if (progress < 1) requestAnimationFrame(animate);
    }
    requestAnimationFrame(animate);
  }

  function destroy(): void {
    viewport.removeEventListener('wheel', onWheel);
    viewport.removeEventListener('pointerdown', onPointerDown);
    viewport.removeEventListener('pointermove', onPointerMove);
    viewport.removeEventListener('pointerup', onPointerUp);
  }

  function getTransform(): { tx: number; ty: number; scale: number } {
    return { tx, ty, scale };
  }

  function setTransform(newTx: number, newTy: number, newScale: number): void {
    tx = newTx;
    ty = newTy;
    scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, newScale));
    apply();
  }

  return { fitToView, panToNode, zoomToNode, getTransform, setTransform, destroy };
}
