/* Public curated films and the existing account login form. No preview handlers. */
(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const film = byId('film');
  const poster = byId('poster');
  const dialog = byId('login-dialog');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const narrow = matchMedia('(max-width:700px)');
  const slides = [
    {file:'city', name:'街头，自有态度。', position:'50% 20%'},
    {file:'dusk', name:'暮色，自成氛围。', position:'50% 40%'},
  ];
  let index = 0;
  let resumeAfterLogin = false;
  let backdropDown = false;
  function play() {
    film.play().catch(() => { byId('play').textContent = '播放'; });
  }
  function position() {
    film.style.objectPosition = poster.style.objectPosition = narrow.matches ? '50% 35%' : slides[index].position;
  }
  function render() {
    const slide = slides[index];
    film.pause();
    film.poster = `/static/mira/cover/still-${slide.file}-original.png`;
    film.src = `/static/mira/cover/film-${slide.file}-original.mp4`;
    film.muted = true;
    film.hidden = false;
    poster.hidden = true;
    document.body.classList.add('has-media');
    byId('play').hidden = false;
    byId('play').textContent = '播放';
    byId('kind').textContent = 'MIRA / FASHION FILM';
    byId('title').textContent = slide.name;
    byId('preview-note').textContent = '灵感，从这里开始。';
    byId('current').textContent = String(index + 1).padStart(2, '0');
    byId('total').textContent = String(slides.length).padStart(2, '0');
    byId('previous').disabled = byId('next').disabled = false;
    byId('announcement').textContent = `第 ${index + 1} 个，共 ${slides.length} 个。`;
    position();
    if (!reduced.matches && !document.hidden && !dialog.open) play();
  }
  function navigate(delta) {
    index = (index + delta + slides.length) % slides.length;
    render();
  }
  byId('next').addEventListener('click', () => navigate(1));
  byId('previous').addEventListener('click', () => navigate(-1));
  byId('play').addEventListener('click', () => film.paused ? play() : film.pause());
  film.addEventListener('play', () => { byId('play').textContent = '暂停'; });
  film.addEventListener('pause', () => { byId('play').textContent = '播放'; });
  film.addEventListener('error', () => {
    poster.src = film.poster;
    poster.alt = slides[index].name;
    poster.hidden = false;
    film.hidden = true;
    byId('play').hidden = true;
    byId('preview-note').textContent = '当前展示影片定帧。';
  });
  function openLogin() {
    resumeAfterLogin = !film.paused;
    film.pause();
    if (!dialog.open) dialog.showModal();
    byId('login-username').focus();
  }
  byId('open-login').addEventListener('click', openLogin);
  byId('close-login').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => {
    byId('login-password').value = '';
    byId('open-login').focus();
    if (resumeAfterLogin && !reduced.matches && !document.hidden) play();
  });
  function outside(event) {
    const box = dialog.getBoundingClientRect();
    return event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom;
  }
  dialog.addEventListener('pointerdown', event => { backdropDown = event.target === dialog && outside(event); });
  dialog.addEventListener('click', event => {
    if (backdropDown && event.target === dialog && outside(event)) dialog.close();
    backdropDown = false;
  });
  document.addEventListener('keydown', event => {
    if (dialog.open || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey ||
        ['INPUT','BUTTON','A','TEXTAREA'].includes(document.activeElement.tagName)) return;
    if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
      event.preventDefault();
      navigate(event.key === 'ArrowRight' ? 1 : -1);
    }
  });
  document.addEventListener('visibilitychange', () => { if (document.hidden) film.pause(); });
  window.addEventListener('pagehide', () => { film.pause(); byId('login-password').value = ''; });
  reduced.addEventListener('change', () => { if (reduced.matches) film.pause(); });
  narrow.addEventListener('change', position);
  window.accountReady.catch(openLogin);
  render();
})();
