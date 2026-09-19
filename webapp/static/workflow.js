(() => {
  const canvas = document.getElementById('workflow-canvas');
  if (!canvas) return;
  const width = 1792, height = 1387;
  const sectionSelect = document.getElementById('workflow-section');
  const regions = [...canvas.querySelectorAll('[data-section]')];
  let view = { x: 0, y: 0, width, height };
  function render() {
    view.x = Math.max(0, Math.min(width - view.width, view.x));
    view.y = Math.max(0, Math.min(height - view.height, view.y));
    canvas.setAttribute('viewBox', `${view.x} ${view.y} ${view.width} ${view.height}`);
    canvas.classList.toggle('zoomed', view.width < width);
    document.getElementById('zoom-level').textContent = `${Math.round(width / view.width * 100)}%`;
    document.getElementById('zoom-out').disabled = view.width >= width;
    document.getElementById('zoom-in').disabled = view.width <= width / 5;
  }
  function zoom(scale, cx = view.x + view.width / 2, cy = view.y + view.height / 2) {
    const nextWidth = Math.min(width, Math.max(width / 5, width / scale));
    const nextHeight = nextWidth * height / width;
    view = { x: cx - nextWidth / 2, y: cy - nextHeight / 2, width: nextWidth, height: nextHeight };
    render();
  }
  function focusSection(key) {
    const region = regions.find(item => item.dataset.section === key);
    sectionSelect.value = region ? key : '';
    regions.forEach(item => item.classList.toggle('selected', item === region));
    if (!region) { zoom(1); return; }
    const x = Number(region.getAttribute('x')), y = Number(region.getAttribute('y'));
    const w = Number(region.getAttribute('width')), h = Number(region.getAttribute('height'));
    zoom(Math.min(width / (w + 100), height / (h + 100)), x + w / 2, y + h / 2);
  }
  document.getElementById('zoom-in').addEventListener('click', () => zoom(width / view.width * 1.3));
  document.getElementById('zoom-out').addEventListener('click', () => zoom(width / view.width / 1.3));
  document.getElementById('zoom-fit').addEventListener('click', () => focusSection(''));
  sectionSelect.addEventListener('change', () => focusSection(sectionSelect.value));
  let drag = null, moved = false;
  canvas.addEventListener('pointerdown', event => {
    moved = false;
    if (event.button !== 0 || view.width >= width) return;
    drag = { id: event.pointerId, x: event.clientX, y: event.clientY, view: { ...view } };
  });
  canvas.addEventListener('pointermove', event => {
    if (!drag || event.pointerId !== drag.id) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (!moved && Math.hypot(dx, dy) < 5) return;
    moved = true;
    canvas.setPointerCapture(event.pointerId);
    canvas.classList.add('dragging');
    const bounds = canvas.getBoundingClientRect();
    view.x = drag.view.x - dx * drag.view.width / bounds.width;
    view.y = drag.view.y - dy * drag.view.height / bounds.height;
    render();
  });
  function endDrag() { drag = null; canvas.classList.remove('dragging'); }
  canvas.addEventListener('pointerup', endDrag);
  canvas.addEventListener('pointercancel', endDrag);
  canvas.addEventListener('lostpointercapture', endDrag);
  regions.forEach(region => {
    region.addEventListener('click', () => { if (!moved) focusSection(region.dataset.section); });
    region.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault(); focusSection(region.dataset.section);
      }
    });
  });
  const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const toggle = document.getElementById('flow-toggle');
  let paused = motion.matches;
  function updateMotion() {
    canvas.closest('.workflow-panel').classList.toggle('paused', paused);
    toggle.textContent = motion.matches ? 'Animation off (reduced motion)' : paused ? 'Resume animation' : 'Pause animation';
    toggle.disabled = motion.matches;
    toggle.setAttribute('aria-pressed', String(paused));
  }
  toggle.addEventListener('click', () => { paused = !paused; updateMotion(); });
  motion.addEventListener('change', () => { paused = motion.matches; updateMotion(); });
  updateMotion(); render();
})();
