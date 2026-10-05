(() => {
  'use strict';
  const form = document.getElementById('login-form');
  const button = document.getElementById('login-submit');
  const error = document.getElementById('login-error');
  const registerForm = document.getElementById('register-form');
  const registerButton = document.getElementById('register-submit');
  const registerError = document.getElementById('register-error');
  const text = key => window.miraAuthText(key);
  function buttonText(target, key) { target.dataset.i18n = key; target.textContent = text(key); }
  window.accountReady.then(account => {
    if (!account.auth_enabled || account.user) window.location.replace('/');
  }).catch(reason => {
    const target = form.hidden ? registerError : error;
    target.textContent = reason.message; target.hidden = false;
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (button.disabled) return;
    button.disabled = true; buttonText(button, 'loginBusy'); error.hidden = true;
    try {
      const data = await window.accountUI.readJSON(await fetch('/api/auth/login', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({username:form.elements.username.value.trim(), password:form.elements.password.value}),
      }));
      form.elements.password.value = '';
      window.accountUI.announceLogin(data.user);
      window.location.replace('/');
    } catch (reason) {
      error.textContent = reason.message; error.hidden = false;
      form.elements.password.value = ''; form.elements.password.focus();
      button.disabled = false; buttonText(button, 'loginSubmit');
    }
  });
  registerForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (registerButton.disabled) return;
    registerError.hidden = true;
    const password = registerForm.elements.password.value;
    const confirm_password = registerForm.elements.confirm_password.value;
    if (password !== confirm_password) {
      registerError.textContent = text('passwordMismatch'); registerError.hidden = false;
      registerForm.elements.confirm_password.focus(); return;
    }
    registerButton.disabled = true; buttonText(registerButton, 'registerBusy');
    try {
      const data = await window.accountUI.readJSON(await fetch('/api/auth/register', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({username:registerForm.elements.username.value.trim(), password, confirm_password}),
      }));
      registerForm.reset();
      window.accountUI.announceLogin(data.user);
      window.location.replace('/');
    } catch (reason) {
      registerError.textContent = reason.message; registerError.hidden = false;
      registerForm.elements.password.value = ''; registerForm.elements.confirm_password.value = '';
      registerForm.elements.password.focus();
      registerButton.disabled = false; buttonText(registerButton, 'registerSubmit');
    }
  });
})();
