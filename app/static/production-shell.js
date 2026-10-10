/* Small shared feedback helper for the independent production page. */
(() => {
  let timer;
  window.toast = message => {
    const node = document.getElementById('toast');
    node.textContent = message;
    node.classList.add('show');
    clearTimeout(timer);
    timer = setTimeout(() => node.classList.remove('show'), 3000);
  };
})();
