(() => {
  'use strict';
  const form = document.getElementById('login-form');
  const button = document.getElementById('login-submit');
  const error = document.getElementById('login-error');
  const destination = user => user?.must_change_password ? '/account/password' : '/';
  window.accountReady.then(account => {
    if (!account.auth_enabled || account.user) window.location.replace(destination(account.user));
  }).catch(reason => { error.textContent = reason.message; error.hidden = false; });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (button.disabled) return;
    button.disabled = true; button.textContent = '正在登录…'; error.hidden = true;
    try {
      const data = await window.accountUI.readJSON(await fetch('/api/auth/login', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({username:form.elements.username.value.trim(), password:form.elements.password.value}),
      }));
      form.elements.password.value = '';
      window.accountUI.announceLogin(data.user);
      window.location.replace(destination(data.user));
    } catch (reason) {
      error.textContent = reason.message; error.hidden = false;
      form.elements.password.value = ''; form.elements.password.focus();
      button.disabled = false; button.textContent = '登录工作台';
    }
  });
})();
