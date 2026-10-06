/* Explicit image targets share a full-resolution, keyboard-accessible preview. */
(() => {
  const dialog = document.createElement('dialog');
  dialog.id = 'image-viewer'; dialog.className = 'image-viewer';
  dialog.setAttribute('aria-label', '查看图片');
  const header = document.createElement('header'), title = document.createElement('h2');
  title.textContent = '查看图片';
  const close = document.createElement('button'); close.type = 'button';
  close.textContent = '关闭'; close.setAttribute('aria-label', '关闭图片');
  const picture = document.createElement('img'); picture.alt = '图片预览';
  const status = document.createElement('p'); status.setAttribute('role', 'status');
  header.append(title, close); dialog.append(header, picture, status); document.body.append(dialog);
  let origin = null;
  function open(image) {
    if (image.hidden || !image.getAttribute('src')) return;
    const url = image.dataset.imagePreview || image.getAttribute('src');
    try { if (!['https:', 'http:', 'blob:', 'data:'].includes(new URL(url, location.href).protocol)) return; } catch (_) { return; }
    origin = image; status.textContent = '正在加载原图…'; picture.hidden = false;
    picture.src = url;
    if (!dialog.open) dialog.showModal();
    close.focus();
  }
  picture.addEventListener('load', () => { status.textContent = ''; });
  picture.addEventListener('error', () => { picture.hidden = true; status.textContent = '图片暂时无法读取，请关闭后重试。'; });
  close.addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const box = dialog.getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close();
  });
  dialog.addEventListener('close', () => {
    picture.removeAttribute('src'); status.textContent = '';
    if (origin?.isConnected) origin.focus({preventScroll:true});
    origin = null;
  });
  document.addEventListener('click', event => {
    const image = event.target.closest('img[data-image-preview]');
    if (!image) return;
    event.preventDefault(); event.stopPropagation(); open(image);
  }, true);
  document.addEventListener('keydown', event => {
    if (!['Enter',' '].includes(event.key) || !event.target.matches('img[data-image-preview]')) return;
    event.preventDefault(); event.stopPropagation(); open(event.target);
  }, true);
})();
