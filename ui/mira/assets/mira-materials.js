// Presentation only: observe decoded dimensions, including restored and replaced assets.
// Never uploads, modifies files, or calls a generation service.
(() => {
  const root = document.querySelector('.creation-workspace');
  if (!root) return;
  const grid = root.querySelector('.asset-grid');
  let pending = false;
  function measure(media) {
    const width = media.naturalWidth || media.videoWidth;
    const height = media.naturalHeight || media.videoHeight;
    if (!width || !height) return;
    const frame = media.closest('.reference-thumb, .video-cover, .reference-video');
    if (!frame) return;
    const value = `${Math.round(420 * width / height)}px`;
    if (frame.style.getPropertyValue('--material-width') !== value) frame.style.setProperty('--material-width', value);
    if (!media.parentElement.classList.contains('material-visual')) {
      const visual = document.createElement('span');
      visual.className = 'material-visual';
      media.before(visual);
      visual.append(media);
    }
  }
  function scan() {
    pending = false;
    root.querySelectorAll('.asset-picker img, .asset-picker video').forEach(measure);
    if (!grid) return;
    const cards = [...grid.children].filter(el => el.matches('.asset-entry'));
    const columns = new Set(cards.map(card => Math.round(card.getBoundingClientRect().left)));
    const aligned = columns.size > 1;
    grid.classList.toggle('materials-aligned', aligned);
    grid.style.removeProperty('--material-stage-height');
    grid.style.removeProperty('--material-heading-height');
    cards.forEach(card => card.style.removeProperty('--material-picker-height'));
    if (!aligned) return;
    const headings = cards.map(card => card.querySelector('.asset-heading')).filter(Boolean);
    grid.style.setProperty('--material-heading-height', `${Math.max(0,...headings.map(el => el.getBoundingClientRect().height))}px`);
    // Only the first asset in each card shares a display baseline. Subsequent
    // references retain their own natural shape, without padding every image.
    const firstVisuals = cards.map(card => card.querySelector('.material-visual')).filter(Boolean);
    const height = Math.max(0, ...firstVisuals.map(el => el.firstElementChild.getBoundingClientRect().height));
    grid.style.setProperty('--material-stage-height', `${Math.ceil(height)}px`);
    const pickers = cards.map(card => card.querySelector('.asset-picker.has-media')).filter(Boolean);
    const pickerHeight = Math.max(0,...pickers.map(el => el.getBoundingClientRect().height));
    cards.forEach(card => card.style.setProperty('--material-picker-height',`${Math.ceil(pickerHeight)}px`));
  }
  function schedule() { if (!pending) { pending = true; requestAnimationFrame(scan); } }
  root.addEventListener('load', schedule, true);
  root.addEventListener('loadedmetadata', schedule, true);
  const observer = new MutationObserver(schedule);
  observer.observe(root, {subtree: true, childList: true, attributes: true, attributeFilter: ['src']});
  let previousWidth = 0;
  const resize = new ResizeObserver(entries => {
    const width = entries[0].contentRect.width;
    if (width !== previousWidth) { previousWidth = width; schedule(); }
  });
  if (grid) resize.observe(grid);
  window.addEventListener('resize', schedule);
  document.fonts?.ready.then(schedule);
  scan();
})();
