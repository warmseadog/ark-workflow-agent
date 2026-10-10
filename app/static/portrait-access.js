(() => {
  'use strict';
  const panel = document.getElementById('portrait-access');
  if (!panel) return;
  const list = document.getElementById('portrait-access-list');
  const status = document.getElementById('portrait-access-status');
  const refresh = document.getElementById('portrait-access-refresh');
  let loaded = false, loading = false;
  function node(tag, text) { const value = document.createElement(tag); if (text) value.textContent = text; return value; }
  function report(text) { status.textContent = text; status.hidden = !text; }
  function userChoices(card, selected) {
    const choices = card.querySelector('.portrait-access-users');
    choices.replaceChildren();
    if (!window.adminUsers) { choices.append(node('p', '正在读取用户列表，请稍候；读取失败时请点击上方“刷新用户”。')); return; }
    for (const user of window.adminUsers.filter(user => !['admin','super_admin'].includes(user.role) && !user.is_admin)) {
      const label = node('label'), input = node('input'); input.type = 'checkbox'; input.value = user.id;
      input.checked = selected.includes(user.id);
      label.append(input, document.createTextNode(user.username + (user.enabled === false ? '（已停用）' : '')));
      choices.append(label);
    }
    if (!choices.children.length) choices.append(node('p', '尚无普通用户；指定空名单时仅管理员可用。'));
  }
  function render(items) {
    list.replaceChildren();
    if (!items.length) { list.append(node('p', '暂无可分配的真人。添加真人后默认向所有用户共享。')); return; }
    for (const item of items) {
      const card = node('article'); card.dataset.personAccess = item.id; card.className = 'portrait-access-card';
      card.append(node('h3', item.name || '未命名真人'));
      const label = node('label', '使用范围 '), select = node('select');
      select.add(new Option('所有用户（默认共享）', 'all')); select.add(new Option('指定用户', 'selected'));
      select.value = item.mode === 'selected' ? 'selected' : 'all'; label.append(select); card.append(label);
      const choices = node('div'); choices.className = 'portrait-access-users'; choices.hidden = select.value === 'all'; card.append(choices);
      card._selectedUsers = item.user_ids || [];
      userChoices(card, card._selectedUsers);
      const hint = node('p', '选择所有用户即恢复默认共享；权限调整会在后续读取和使用时生效。'); hint.className = 'account-muted'; card.append(hint);
      const save = node('button', '保存权限'); save.type = 'button'; save.className = 'account-secondary';
      const message = node('p'); message.className = 'account-message'; message.setAttribute('role','status');
      card.append(save, message);
      card.addEventListener('change', () => { card.dataset.dirty = 'true'; });
      select.addEventListener('change', () => { choices.hidden = select.value === 'all'; message.textContent = ''; });
      save.addEventListener('click', async () => {
        if (select.value === 'selected' && !window.adminUsers) { message.textContent = '请先成功读取用户列表，再保存指定名单。'; return; }
        const mode = select.value;
        const user_ids = mode === 'all' ? [] : [...choices.querySelectorAll('input:checked')].map(input => input.value);
        if (loading) return;
        card.setAttribute('aria-busy','true'); refresh.disabled = true;
        save.disabled = true; select.disabled = true; choices.querySelectorAll('input').forEach(input => input.disabled = true);
        message.textContent = '正在保存…';
        try {
          await window.accountUI.readJSON(await fetch('/api/admin/portrait-access/' + encodeURIComponent(item.id), {
            method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({mode,user_ids}),
          }));
          card._selectedUsers = user_ids;
          delete card.dataset.dirty;
          message.textContent = mode === 'all' ? '已保存：所有用户可用，包括新建用户。' : user_ids.length ? `已保存：${user_ids.length} 位指定用户及管理员可用。` : '已保存：仅管理员可用。';
          message.dataset.state = 'success';
        } catch (error) { message.textContent = error.message; message.dataset.state = 'error'; }
        finally {
          card.removeAttribute('aria-busy'); save.disabled = false; select.disabled = false;
          choices.querySelectorAll('input').forEach(input => input.disabled = false);
          refresh.disabled = Boolean(list.querySelector('[aria-busy="true"]'));
          if (card._usersPending) { delete card._usersPending; updateChoices(card); }
        }
      });
      list.append(card);
    }
  }
  async function load(force = false) {
    if (loading || (loaded && !force)) return;
    if (list.querySelector('[aria-busy="true"]')) return;
    if (force && list.querySelector('[data-dirty="true"]') && !confirm('有未保存的权限修改，确认刷新并放弃这些修改？')) return;
    loading = true; refresh.disabled = true; report('正在读取人物权限…');
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 8000);
    try {
      const account = await window.accountReady;
      if (!account.user?.is_admin && !['admin','super_admin'].includes(account.user?.role)) throw new Error('仅管理员可以分配真人库权限。');
      const data = await window.accountUI.readJSON(await fetch('/api/admin/portrait-access', {cache:'no-store', signal:controller.signal}));
      render(data.items || []); loaded = true; report('');
    } catch (error) { report(error.name === 'AbortError' ? '读取超时，请点击“刷新人物权限”重试。' : error.message); }
    finally { clearTimeout(timer); loading = false; refresh.disabled = false; }
  }
  panel.addEventListener('toggle', () => { if (panel.open) void load(); });
  refresh.addEventListener('click', () => void load(true));
  function updateChoices(card) {
    if (card.getAttribute('aria-busy') === 'true') { card._usersPending = true; return; }
    const inputs = card.querySelectorAll('input');
    const selected = inputs.length ? [...inputs].filter(input => input.checked).map(input => input.value) : card._selectedUsers;
    userChoices(card, selected);
  }
  window.addEventListener('admin-users-loaded', () => {
    list.querySelectorAll('[data-person-access]').forEach(updateChoices);
  });
})();
