// Embedded pages keep their own forms and scripts inside the persistent admin shell.
if (window.self !== window.top && new URLSearchParams(location.search).get('embedded') === '1') {
  document.documentElement.classList.add('admin-embedded');
}
