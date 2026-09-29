(() => {
  'use strict';
  const form = document.getElementById('password-form');
  const fields = document.getElementById('password-fields');
  const error = document.getElementById('password-error');
  const button = form.querySelector('[type="submit"]');
  window.accountReady.then(account => {
    if (!account.auth_enabled) { window.location.replace('/'); return; }
    fields.disabled = false;
  }).catch(reason => { error.textContent = reason.message; error.hidden = false; });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (fields.disabled) return;
    error.hidden = true;
    const current_password = form.elements.current_password.value;
    const new_password = form.elements.new_password.value;
    if (new_password !== form.elements.confirm_password.value || new_password === current_password) {
      error.textContent = new_password === current_password ? '新密码不能与当前密码相同。' : '两次输入的新密码不一致。';
      error.hidden = false; return;
    }
    fields.disabled = true; button.textContent = '正在保存…';
    try {
      const data = await window.accountUI.readJSON(await fetch('/api/auth/password', {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({current_password, new_password}),
      }));
      form.reset();
      if (data.needs_login) window.accountUI.finishSession();
      else window.location.replace('/');
    } catch (reason) {
      error.textContent = reason.message; error.hidden = false;
      fields.disabled = false; button.textContent = '保存新密码';
    }
  });
})();
