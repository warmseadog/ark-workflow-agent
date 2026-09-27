(() => {
  const dialog = document.getElementById('portrait-dialog');
  if (!dialog) return;
  const byId = id => document.getElementById('portrait-' + id);
  const officialConsole = 'https://console.volcengine.com/ark/region:ark+cn-beijing/experience/vision?modelId=doubao-seedance-2-0-260128';
  let config = null, working = false, qrUrl = null, qrController = null, session = 0, sessionController = null;
  let pollTimer = null, activeVerification = null;
  let pendingRequestId = null;
  try { pendingRequestId = sessionStorage.getItem('portrait-request-id'); } catch (_) {}
  function rememberRequest(value) {
    pendingRequestId = value;
    try { if (value) sessionStorage.setItem('portrait-request-id', value); else sessionStorage.removeItem('portrait-request-id'); } catch (_) {}
  }
  function message(id, text, error = false) {
    const el = byId(id); el.textContent = text; el.dataset.error = String(error);
  }
  async function request(path, method = 'GET', body) {
    const requestedSession = session;
    const response = await fetch('/api/portrait/' + path, {method, signal: sessionController?.signal,
      headers: body === undefined ? {} : {'Content-Type': 'application/json'},
      body: body === undefined ? undefined : JSON.stringify(body)});
    const data = await response.json().catch(() => ({}));
    if (requestedSession !== session) throw new DOMException('Dialog closed', 'AbortError');
    if (!response.ok) { const error = new Error(typeof data.detail === 'string' ? data.detail : '服务暂时不可用，请检查设置后重试。'); error.status = response.status; throw error; }
    return data;
  }
  function setWorking(value) {
    working = value;
    dialog.querySelectorAll('button:not(#portrait-close),input').forEach(el => el.disabled = value);
    if (!value) updateCredentialInputs();
  }
  async function run(status, action) {
    if (working) return;
    const requestedSession = session;
    setWorking(true);
    try { await action(); }
    catch (error) { if (requestedSession === session && error.name !== 'AbortError') message(status, error.message || '网络异常，请重试。', true); }
    finally { if (requestedSession === session) setWorking(false); }
  }
  function clearQR() {
    clearTimeout(pollTimer); pollTimer = null; activeVerification = null;
    qrController?.abort(); qrController = null;
    if (qrUrl) URL.revokeObjectURL(qrUrl);
    qrUrl = null; byId('qr-image').removeAttribute('src'); byId('qr').hidden = true;
  }
  function updateCredentialInputs() {
    const reuse = byId('reuse-storage').checked;
    byId('access-key').disabled = working || reuse;
    byId('secret-key').disabled = working || reuse;
  }
  function applyConfig(data) {
    config = data;
    const automatic = data.mode === 'automatic';
    byId('auto').hidden = !automatic; byId('manual').hidden = automatic;
    byId('session-note').hidden = !automatic;
    byId('mode').textContent = automatic ? '本人扫码认证，工作台自动核实官方结果。认证通过后，图片仍需通过官方素材校验。' : '请在火山官方平台完成本人认证和授权，再返回这里刷新已授权人物。';
    byId('project').value = data.project_name || 'default';
    byId('reuse-storage').checked = Boolean(data.use_storage_credentials);
    byId('access-key').value = ''; byId('secret-key').value = '';
    byId('console').href = officialConsole;
    byId('key-state').textContent = data.use_storage_credentials
      ? (data.has_credentials ? '已选择使用 TOS 凭据。' : '已选择 TOS 凭据，但配置不完整，请在后台补齐。')
      : `Access Key：${data.has_access_key ? '已保存' : '未配置'} · Secret Key：${data.has_secret_key ? '已保存' : '未配置'}`;
    updateCredentialInputs();
  }
  async function loadAssets() {
    byId('assets').replaceChildren();
    if (!config?.has_credentials) {
      message('assets-status', '请先展开“连接设置”，保存有真人素材访问权限的 AK/SK，再刷新。', true); return;
    }
    message('assets-status', '正在查询已有授权照片…');
    const data = await request('assets');
    const items = (data.items || []).filter(item => item.status === 'Active' && item.asset_type === 'Image');
    for (const item of items) {
      const row = document.createElement('div'); row.className = 'portrait-asset';
      const info = document.createElement('div');
      const title = document.createElement('strong'); title.textContent = item.name || item.id;
      const description = document.createElement('small'); description.textContent = `官方可用 · ${item.id}`;
      info.append(title, description);
      const button = document.createElement('button'); button.type = 'button'; button.className = 'secondary';
      button.dataset.portraitImport = item.id; button.textContent = '设为主人物';
      button.addEventListener('click', () => run('assets-status', async () => {
        message('assets-status', '正在导入官方人物图片…');
        const asset = await request('import', 'POST', {remote_asset_id: item.id});
        if (!window.productionPortraits) throw new Error('编辑器尚未就绪，请关闭弹窗并刷新页面。');
        const importingSession = session;
        await window.productionPortraits.importAsset(asset, {signal: sessionController.signal});
        if (dialog.open && importingSession === session) dialog.close();
      }));
      row.append(info, button); byId('assets').append(row);
    }
    message('assets-status', items.length ? `找到 ${items.length} 张可用授权图片。选择后将替换主参考图。` : '还没有可用照片。已认证人物会显示在页面人物选择中，可关闭弹窗直接上传照片。');
  }
  async function startAutomatic(force = false) {
    const previous = activeVerification;
    if (force && previous) await request('sessions/' + previous, 'DELETE');
    if (force) rememberRequest(null);
    clearQR();
    if (!config?.has_credentials) {
      byId('settings').open = true;
      throw new Error('请先配置有真人认证权限的 AK/SK，再重新获取二维码。');
    }
    const openedSession = session;
    message('invite-status', '正在向官方申请认证二维码…');
    if (!pendingRequestId) rememberRequest(crypto.randomUUID().replaceAll('-', ''));
    let created;
    try { created = await request('sessions', 'POST', {request_id: pendingRequestId}); }
    catch (error) { if (error.status) rememberRequest(null); throw error; }
    if (['failed','expired','cancelled','verified'].includes(created.status)) {
      rememberRequest(null);
      if (!force) return startAutomatic(true);
      throw new Error(created.message || '认证会话未就绪，请重新获取。');
    }
    if (created.status !== 'pending') throw new Error(created.message || '认证会话未就绪，请重试。');
    activeVerification = created.id;
    const response = await fetch(created.qr_url, {signal: sessionController.signal, cache: 'no-store'});
    if (!response.ok) throw new Error('二维码读取失败，请重新获取。');
    const blob = await response.blob();
    if (openedSession !== session || !dialog.open) return;
    if (blob.type.split(';')[0] !== 'image/svg+xml') throw new Error('二维码格式异常，请重新获取。');
    qrUrl = URL.createObjectURL(blob); byId('qr-image').src = qrUrl; byId('qr').hidden = false;
    activeVerification = created.id;
    message('qr-status', '等待本人扫码认证');
    message('invite-status', created.message);
    const poll = async () => {
      if (openedSession !== session || activeVerification !== created.id || !dialog.open) return;
      try {
        const result = await request('sessions/' + created.id);
        if (activeVerification !== created.id) return;
        message('invite-status', result.message, ['failed','expired'].includes(result.status));
        if (result.status === 'verified') {
          rememberRequest(null); clearQR(); message('invite-status', result.message);
          window.dispatchEvent(new CustomEvent('portrait-verified', {detail:result}));
          message('assets-status', '人物已添加。关闭弹窗后直接上传照片，系统会自动校验。'); return;
        }
        if (['failed','expired','cancelled'].includes(result.status)) { rememberRequest(null); clearQR(); return; }
      } catch (error) {
        if (openedSession !== session || error.name === 'AbortError') return;
        message('invite-status', error.message || '认证状态查询暂时失败，正在重试…', true);
      }
      if (openedSession === session && activeVerification === created.id) pollTimer = setTimeout(poll, 5000);
    };
    pollTimer = setTimeout(poll, 5000);
  }
  byId('auto-retry').addEventListener('click', () => run('invite-status', () => startAutomatic(true)));
  async function open(mode) {
    if (dialog.open) return;
    session++; const openedSession = session;
    sessionController = new AbortController();
    byId('assets').replaceChildren();
    byId('settings').open = false;
    dialog.showModal();
    await run(mode === 'assets' ? 'assets-status' : 'invite-status', async () => {
      message('assets-status', '正在读取连接配置…');
      const data = await request('config');
      if (!dialog.open || session !== openedSession) return;
      applyConfig(data);
      if (mode === 'assets') await loadAssets();
      else if (data.mode === 'automatic') { message('assets-status', '认证完成后将自动刷新可用人物素材。'); await startAutomatic(); }
      else message('assets-status', data.has_credentials ? '完成官方认证后，点击刷新已授权人物。' : '查看已授权人物前，请先展开“连接设置”配置 AK/SK。');
    });
  }
  document.querySelectorAll('[data-portrait-open]').forEach(button => button.addEventListener('click', () => open(button.dataset.portraitOpen)));
  byId('close').addEventListener('click', () => { closeSession(); dialog.close(); });
  function closeSession() {
    if (!sessionController) return;
    session++; sessionController?.abort(); sessionController = null;
    clearQR(); setWorking(false); byId('invitation').value = '';
    byId('access-key').value = ''; byId('secret-key').value = '';
    message('invite-status', '');
  }
  dialog.addEventListener('cancel', closeSession);
  dialog.addEventListener('close', closeSession);
  byId('invitation').addEventListener('input', clearQR);
  byId('reuse-storage').addEventListener('change', updateCredentialInputs);
  byId('qr-create').addEventListener('click', () => run('invite-status', async () => {
    clearQR();
    const raw = byId('invitation').value.trim();
    let url;
    try { url = new URL(raw); } catch (_) { throw new Error('请粘贴控制台提供的完整官方 HTTPS 邀约链接。'); }
    if (url.protocol !== 'https:' || url.username || url.password) throw new Error('请使用官方 HTTPS 邀约链接。');
    const openedSession = session;
    qrController = new AbortController();
    message('invite-status', '正在生成二维码…');
    const response = await fetch('/api/portrait/qr', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({url: raw}), signal: qrController.signal});
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(typeof data.detail === 'string' ? data.detail : '二维码生成失败，请检查官方邀约链接。');
    }
    const blob = await response.blob();
    if (!dialog.open || openedSession !== session) return;
    if (!['image/svg+xml', 'image/png'].includes(blob.type.split(';')[0])) throw new Error('二维码响应格式异常，请重试。');
    qrUrl = URL.createObjectURL(blob); byId('qr-image').src = qrUrl; byId('qr').hidden = false;
    message('invite-status', '请本人扫码前往官方页面；完成后返回刷新已授权人物。');
  }));
  byId('refresh').addEventListener('click', () => run('assets-status', loadAssets));
  async function save(clear = false) {
    const data = await request('config', 'PUT', {
      project_name: byId('project').value.trim() || 'default',
      use_storage_credentials: clear ? false : byId('reuse-storage').checked,
      access_key: clear || byId('reuse-storage').checked ? '' : byId('access-key').value.trim(),
      secret_key: clear || byId('reuse-storage').checked ? '' : byId('secret-key').value.trim(),
      clear_credentials: clear,
    });
    clearQR(); rememberRequest(null); applyConfig(data); byId('assets').replaceChildren();
    message('settings-status', clear ? '真人凭据已清除。现有 TOS 配置不受影响。' : '连接设置已保存。');
    message('assets-status', '连接设置已更新，请重新刷新已授权人物。');
  }
  byId('save').addEventListener('click', () => run('settings-status', () => save()));
  byId('clear').addEventListener('click', () => run('settings-status', () => save(true)));
  byId('test').addEventListener('click', () => run('settings-status', async () => {
    if (byId('access-key').value || byId('secret-key').value || byId('project').value.trim() !== config?.project_name || byId('reuse-storage').checked !== Boolean(config?.use_storage_credentials)) {
      throw new Error('请先保存当前连接设置，再测试连接。');
    }
    message('settings-status', '正在执行只读查询…');
    const data = await request('test', 'POST', {});
    message('settings-status', data.message || (data.ok ? '连接成功。' : '连接失败，请检查 AK/SK 和项目权限。'), !data.ok);
  }));
})();
