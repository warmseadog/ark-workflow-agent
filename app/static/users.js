(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let users = [];
  let taskVersion = 0;
  let taskPage = 1;
  let activeFilter = {user_id:'', status:''};
  let taskTimer = null;
  let taskAccountId = null;
  let auditItems = [];
  let auditPage = 1, auditVersion = 0, auditPages = 1;
  const pageSize = 10;
  const reads = new Set();
  function stopTaskTimer() { clearTimeout(taskTimer); taskTimer = null; }
  function canRefreshTasks() {
    const account = window.currentAccount;
    return !document.hidden && window.adminEmbedVisible !== false && taskAccountId !== null && account?.id === taskAccountId && (account.is_admin || account.role === 'admin');
  }
  const statusLabels = {queued:'排队中', running:'生成中', succeeded:'已完成', failed:'失败', needs_attention:'需要处理', cancelled:'已取消'};
  function element(tag, value, className) {
    const node = document.createElement(tag);
    if (value != null) node.textContent = String(value);
    if (className) node.className = className;
    return node;
  }
  function message(id, text, success = false) {
    const node = $(id); node.textContent = text; node.hidden = !text;
    node.dataset.state = success ? 'success' : 'error';
  }
  function empty(body, columns, text) {
    const cell = element('td', text, 'account-empty'); cell.colSpan = columns;
    const row = element('tr'); row.append(cell); body.replaceChildren(row);
  }
  async function request(path, method = 'GET', payload) {
    const options = {method, cache:'no-store'};
    const controller = method === 'GET' ? new AbortController() : null;
    let timer;
    if (controller) { reads.add(controller); options.signal = controller.signal; timer = setTimeout(() => controller.abort(), 8000); }
    if (payload !== undefined) { options.headers = {'Content-Type':'application/json'}; options.body = JSON.stringify(payload); }
    try {
    const response = await fetch(path, options);
    try { return await window.accountUI.readJSON(response); }
    catch (error) { error.status = response.status; throw error; }
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('读取超时，请检查网络后重试。');
      throw error;
    } finally { clearTimeout(timer); if (controller) reads.delete(controller); }
  }
  function quota(value, name, username) {
    const input = document.createElement('input');
    input.type = 'number'; input.min = name === 'max_concurrent' ? '1' : '0'; input.max = '10000'; input.step = '1'; input.required = true;
    input.className = 'quota-input'; input.value = value ?? (name === 'max_concurrent' ? 1 : 10);
    input.setAttribute('aria-label', `${username}的${name === 'max_concurrent' ? '同时生成上限' : '排队上限'}`);
    return input;
  }
  function button(text) { const node = element('button', text, 'account-secondary'); node.type = 'button'; return node; }
  function cell(row, value) { const td = element('td'); td.append(value instanceof Node ? value : document.createTextNode(String(value ?? '—'))); row.append(td); return td; }
  function renderUsers() {
    const body = $('users-list'); body.replaceChildren();
    if (!users.length) empty(body, 7, '暂无用户。创建第一个工作账号。');
    for (const user of users) {
      const row = element('tr');
      cell(row, user.username);
      const role = document.createElement('select'); role.className = 'role-select';
      role.setAttribute('aria-label', `${user.username}的角色`);
      role.add(new Option('普通用户', 'user')); role.add(new Option('管理员', 'admin'));
      role.value = user.role === 'admin' || user.is_admin ? 'admin' : 'user';
      cell(row, role);
      const enabled = document.createElement('input'); enabled.type = 'checkbox'; enabled.checked = user.enabled !== false;
      enabled.setAttribute('aria-label', `启用账号 ${user.username}`);
      cell(row, enabled);
      const concurrent = quota(user.max_concurrent, 'max_concurrent', user.username);
      const queued = quota(user.max_queued, 'max_queued', user.username);
      cell(row, concurrent); cell(row, queued);
      const save = button('保存'); cell(row, save);
      save.addEventListener('click', async () => {
        if (!concurrent.reportValidity() || !queued.reportValidity()) return;
        save.disabled = true; message('users-status', '');
        try {
          await request(`/api/admin/users/${encodeURIComponent(user.id)}`, 'PATCH', {enabled:enabled.checked, role:role.value, max_concurrent:Number(concurrent.value), max_queued:Number(queued.value)});
          if (String(user.id) === String(window.currentAccount?.id) && role.value !== window.currentAccount.role) {
            window.accountUI.finishSession(); return;
          }
          message('users-status', `已保存 ${user.username} 的账号设置。`, true);
          void loadAudit();
        } catch (error) { message('users-status', error.message); }
        finally { save.disabled = false; }
      });
      const resetForm = element('form', null, 'row-actions');
      const password = document.createElement('input'); password.type = 'password'; password.autocomplete = 'new-password'; password.minLength = 6; password.maxLength = 1024; password.required = true; password.placeholder = '新密码（至少 6 位）';
      password.setAttribute('aria-label', `重置 ${user.username} 的密码`);
      const reset = button('重置'); reset.type = 'submit';
      resetForm.append(password, reset); cell(row, resetForm);
      resetForm.addEventListener('submit', async event => {
        event.preventDefault(); if (reset.disabled) return;
        reset.disabled = true; message('users-status', '');
        try {
          await request(`/api/admin/users/${encodeURIComponent(user.id)}/reset-password`, 'POST', {password:password.value});
          password.value = '';
          message('users-status', `已重置 ${user.username} 的密码，请使用新密码重新登录。`, true);
          void loadAudit();
        } catch (error) { password.value = ''; message('users-status', error.message); }
        finally { reset.disabled = false; }
      });
      body.append(row);
    }
    for (const id of ['task-user', 'audit-user']) {
      const select = $(id); const previous = select.value;
      select.replaceChildren(new Option('全部用户', ''));
      users.forEach(user => select.add(new Option(user.username, user.id)));
      if (users.some(user => String(user.id) === previous)) select.value = previous;
    }
    window.adminUsers = users;
    window.dispatchEvent(new CustomEvent('admin-users-loaded', {detail:{users}}));
    renderAudit();
  }
  async function loadUsers() {
    $('users-refresh').disabled = true;
    try { const data = await request('/api/admin/users'); users = data.items || []; renderUsers(); }
    catch (error) { message('users-status', error.message); }
    finally { $('users-refresh').disabled = false; }
  }
  function number(value) { return value != null && Number.isFinite(Number(value)) ? Number(value).toLocaleString('zh-CN', {maximumFractionDigits:1}) : '—'; }
  function bytes(value) {
    if (value == null || !Number.isFinite(Number(value))) return '—';
    let size = Math.max(0, Number(value)), index = 0;
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    while (size >= 1024 && index < units.length - 1) { size /= 1024; index++; }
    return `${number(size)} ${units[index]}`;
  }
  function date(value) {
    if (!value) return '—';
    const parsed = new Date(typeof value === 'number' && value < 1e12 ? value * 1000 : value);
    return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleString('zh-CN', {hour12:false});
  }
  function username(id) { return users.find(user => String(user.id) === String(id))?.username || '—'; }
  function elapsed(seconds) {
    if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return '—';
    const total = Math.floor(seconds), hours = Math.floor(total / 3600), minutes = Math.floor(total % 3600 / 60);
    return `${hours ? hours + '小时' : ''}${hours || minutes ? minutes + '分' : ''}${total % 60}秒`;
  }
  function taskTiming(timing) {
    const total = timing?.available ? elapsed(timing.total_seconds) : '—';
    const node = element('span', total === '—' ? total : `${timing.is_live ? '已耗时 ' : ''}${total}`);
    if (total !== '—') {
      const parts = [`排队 ${elapsed(timing.queue_seconds)}`, `执行 ${elapsed(timing.execution_seconds)}`];
      if (typeof timing.paused_seconds === 'number' && timing.paused_seconds > 0) parts.push(`暂停 ${elapsed(timing.paused_seconds)}`);
      if (timing.interrupted) parts.push('任务曾中断');
      node.title = parts.join(' · ');
      node.setAttribute('aria-label', `${node.textContent}；${node.title}`);
    }
    return node;
  }
  async function loadTasks(background = false) {
    stopTaskTimer();
    if (!canRefreshTasks()) return;
    const version = ++taskVersion;
    let needsRefresh = false;
    const submit = $('task-filter').querySelector('button');
    if (!background) {
      submit.disabled = true;
      $('tasks-previous').disabled = true; $('tasks-next').disabled = true;
      $('tasks-duration-note').hidden = true;
      empty($('admin-tasks-list'), 6, '正在读取任务…');
      document.querySelectorAll('[data-stat]').forEach(node => { node.textContent = '—'; });
    }
    message('tasks-status', '');
    try {
      const query = new URLSearchParams({...activeFilter, page:String(taskPage), page_size:String(pageSize)});
      const data = await request(`/api/admin/tasks?${query}`);
      if (version !== taskVersion) return;
      const body = $('admin-tasks-list'); body.replaceChildren();
      const items = data.items || [];
      needsRefresh = items.some(task => task.status === 'queued' || task.status === 'running');
      if (!items.length) empty(body, 6, '没有符合条件的任务。');
      for (const task of items) {
        const row = element('tr');
        cell(row, task.name || task.snapshot?.name || '未命名任务');
        cell(row, task.username || username(task.user_id || task.owner_id));
        const badge = element('span', statusLabels[task.status] || task.status || '—', 'account-status'); badge.dataset.state = task.status; cell(row, badge);
        cell(row, date(task.created_at));
        const duration = task.generated_seconds ?? task.duration ?? task.snapshot?.model?.duration;
        cell(row, Number(duration) > 0 ? `${number(duration)} 秒` : '—');
        cell(row, taskTiming(task.timing));
        body.append(row);
      }
      document.querySelectorAll('[data-stat]').forEach(node => {
        const key = node.dataset.stat; node.textContent = key === 'storage_bytes' ? bytes(data.stats?.[key]) : number(data.stats?.[key]);
      });
      const unknown = Number(data.stats?.duration_unknown || 0);
      $('tasks-duration-note').textContent = `${number(unknown)} 条已完成任务的实际时长暂未统计。`;
      $('tasks-duration-note').hidden = unknown === 0;
      const total = Number(data.total ?? items.length);
      const pages = Math.max(1, Math.ceil(total / Number(data.page_size || pageSize)));
      if (data.pages) taskPage = data.page;
      $('tasks-pagination').hidden = false;
      $('tasks-page').textContent = `第 ${taskPage} / ${pages} 页 · 共 ${total} 条`;
      $('tasks-previous').disabled = taskPage <= 1; $('tasks-next').disabled = taskPage >= pages;
    } catch (error) {
      needsRefresh = false;
      if (error.name === 'AccountSessionError' || error.status === 401 || error.status === 403) {
        taskAccountId = null; stopTaskTimer();
      }
      if (version !== taskVersion) return;
      empty($('admin-tasks-list'), 6, '任务未能加载，请重试。'); message('tasks-status', error.message);
      document.querySelectorAll('[data-stat]').forEach(node => { node.textContent = '—'; });
      $('tasks-duration-note').hidden = true;
      $('tasks-pagination').hidden = true;
    } finally {
      if (version === taskVersion) {
        submit.disabled = false;
        // Only the latest successful response schedules another request. Timing
        // stays server-authored, and terminal results do not keep polling.
        if (needsRefresh && canRefreshTasks()) taskTimer = setTimeout(() => { void loadTasks(true); }, 10000);
      }
    }
  }
  async function loadAudit() {
    const version = ++auditVersion;
    $('audit-refresh').disabled = true; message('audit-status', '');
    $('audit-previous').disabled = true; $('audit-next').disabled = true;
    try {
      const query = new URLSearchParams({page:String(auditPage), page_size:String(pageSize), scope:$('audit-scope').value, user_id:$('audit-user').value});
      const data = await request('/api/admin/audit?' + query);
      if (version !== auditVersion) return;
      auditItems = data.items || []; renderAudit();
      const total = Number(data.total ?? auditItems.length);
      auditPages = Math.max(1, Math.ceil(total / Number(data.page_size || pageSize)));
      auditPage = Number(data.page || auditPage);
      $('audit-page').textContent = `第 ${auditPage} / ${auditPages} 页 · 共 ${total} 条`;
      $('audit-previous').disabled = auditPage <= 1; $('audit-next').disabled = auditPage >= auditPages;
    } catch (error) {
      if (version !== auditVersion) return;
      auditItems = []; message('audit-status', error.message); empty($('audit-list'), 4, '审计记录未能加载，请重试。');
      $('audit-page').textContent = '加载失败，请刷新重试';
    } finally { if (version === auditVersion) $('audit-refresh').disabled = false; }
  }
  function auditAction(action) {
      const labels = {'user.init_admin':'初始化管理员', 'user.create':'创建账号', 'user.register':'用户自助注册', 'user.update':'修改账号设置', 'user.reset_password':'重置密码', 'user.change_password':'修改密码', 'auth.login':'登录', 'auth.login_failed':'登录失败', 'auth.logout':'退出登录', 'portrait.access':'调整真人权限', view_user_tasks:'查看用户任务', view_user_task:'查看任务详情', view_user_media:'读取用户媒体'};
    if (labels[action]) return labels[action];
    const [method, path = ''] = String(action || '').split(' ');
    if (path.includes('settings') || path === '/api/model-catalog' || path === '/api/portrait/config') return method === 'GET' ? '查看配置' : '修改配置';
    if (path.startsWith('/api/prompt-templates') || path.startsWith('/api/admin/prompt-templates')) return method === 'DELETE' ? '删除提示词模板' : '修改提示词模板';
    if (path.startsWith('/api/production/runs')) return method === 'DELETE' ? '删除任务' : path.endsWith('/cancel') ? '取消任务' : path.endsWith('/resume') ? '恢复任务' : path.endsWith('/retry') ? '重试任务' : method === 'POST' ? '提交任务' : '修改任务';
    if (path.includes('/portrait/photos')) return method === 'DELETE' ? '移除人物素材' : '更新人物素材';
    if (path.includes('/portrait/people')) return method === 'DELETE' ? '移除人物' : '更新人物';
    if (path.includes('/drafts')) return '保存草稿';
    if (path.includes('/assets')) return method === 'DELETE' ? '删除素材' : '上传素材';
    if (path.includes('/admin/users')) return '账号管理';
    return action || '其他操作';
  }
  function renderAudit() {
      const body = $('audit-list'); body.replaceChildren();
      if (!auditItems.length) empty(body, 4, '暂无操作记录。');
      for (const item of auditItems) {
        const row = element('tr'); cell(row, date(item.created_at));
        cell(row, item.actor_username || item.username || (item.actor_id ? username(item.actor_id) : item.details?.username || '未登录用户'));
        const targetName = username(item.target_user_id || String(item.target || '').split(':')[0]);
        cell(row, auditAction(item.action));
        cell(row, item.target_username || (targetName !== '—' ? targetName : item.target === 'all' ? '全部用户' : item.target_type || '—'));
        body.append(row);
      }
  }
  $('create-user-form').addEventListener('submit', async event => {
    event.preventDefault(); const form = event.currentTarget; const fields = $('create-user-fields');
    if (fields.disabled) return;
    fields.disabled = true; message('create-user-status', '');
    try {
      await request('/api/admin/users', 'POST', {username:form.elements.username.value.trim(), password:form.elements.password.value, role:form.elements.role.value, max_concurrent:Number(form.elements.max_concurrent.value), max_queued:Number(form.elements.max_queued.value)});
      form.reset(); message('create-user-status', '账号已创建，请将初始密码单独告知用户。', true);
      await Promise.all([loadUsers(), loadAudit()]);
    } catch (error) { form.elements.password.value = ''; message('create-user-status', error.message); }
    finally { fields.disabled = false; }
  });
  $('users-refresh').addEventListener('click', () => { message('users-status', ''); void loadUsers(); });
  $('audit-refresh').addEventListener('click', () => { auditPage = 1; void loadAudit(); });
  $('audit-scope').addEventListener('change', () => { auditPage = 1; void loadAudit(); });
  $('audit-user').addEventListener('change', () => { auditPage = 1; void loadAudit(); });
  $('audit-previous').addEventListener('click', () => { if (auditPage > 1) { auditPage--; void loadAudit(); } });
  $('audit-next').addEventListener('click', () => { if (auditPage < auditPages) { auditPage++; void loadAudit(); } });
  $('task-filter').addEventListener('submit', event => {
    event.preventDefault(); taskPage = 1;
    activeFilter = {user_id:$('task-user').value, status:$('task-status').value};
    void loadTasks();
  });
  for (const id of ['task-user', 'task-status']) {
    $(id).addEventListener('change', () => $('task-filter').requestSubmit());
  }
  $('tasks-previous').addEventListener('click', () => { if (taskPage > 1) { taskPage--; void loadTasks(); } });
  $('tasks-next').addEventListener('click', () => { taskPage++; void loadTasks(); });
  document.addEventListener('visibilitychange', () => {
    stopTaskTimer();
    if (canRefreshTasks()) void loadTasks(true);
  });
  window.addEventListener('admin-embed-visibility', () => {
    stopTaskTimer();
    if (canRefreshTasks()) void loadTasks(true);
  });
  window.addEventListener('pagehide', () => { taskAccountId = null; stopTaskTimer(); reads.forEach(controller => controller.abort()); reads.clear(); });
  window.accountReady.then(async account => {
    if (!account.auth_enabled || !(account.user?.is_admin || account.user?.role === 'admin')) {
      message('users-access-error', '此页面仅供已登录的管理员使用。'); return;
    }
    $('users-workspace').hidden = false;
    taskAccountId = account.user.id;
    await Promise.all([loadUsers(), loadTasks(), loadAudit()]);
  }).catch(error => { message('users-access-error', error.message); });
})();
