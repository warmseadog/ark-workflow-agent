(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const tos = byId('tos-form'), tik = byId('tikhub-form'), prompts = byId('prompt-form');
  const redaction = byId('redaction-form');
  const mediakit = byId('mediakit-form');
  let redactionProfiles = null, displayedProfile = null;
  const service = byId('redaction-service-form');
  let overview = null, templates = [], promptBaseline = '', templateId = '';
  const sectionLoads = new Map();
  function note(id, text, error = false) {
    const node = byId(id); node.textContent = text; node.dataset.error = String(error); node.hidden = !text;
  }
  async function request(url, method = 'GET', body) {
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), method === 'GET' ? 8000 : 90000);
    try {
      const multipart = body instanceof FormData;
      const response = await fetch(url, {method, cache:'no-store', signal:controller.signal,
        headers:body && !multipart ? {'Content-Type':'application/json'} : {}, body:body ? (multipart ? body : JSON.stringify(body)) : undefined});
      const data = await response.json();
      if (!response.ok) {
        const error = new Error(typeof data.detail === 'string' ? data.detail : '配置请求失败，请检查填写内容。');
        error.status = response.status; throw error;
      }
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('请求超时，请检查网络或服务状态后重试。');
      if (error instanceof SyntaxError || error instanceof TypeError) throw new Error('无法读取服务响应，请刷新页面重试。');
      throw error;
    } finally { clearTimeout(timeout); }
  }
  function selectSection() {
    let section = location.hash.slice(1) || 'overview';
    if (!byId('section-' + section)) section = 'overview';
    document.querySelectorAll('.section').forEach(node => node.hidden = node.id !== 'section-' + section);
    document.querySelectorAll('[data-section]').forEach(node => {
      node.classList.toggle('active', node.dataset.section === section);
      if (node.dataset.section === section) node.setAttribute('aria-current', 'page'); else node.removeAttribute('aria-current');
    });
    if (section === 'model') byId('model-settings').open = true;
    const embedded = section === 'users' || section === 'my-people';
    byId('admin-section-name').textContent = byId('section-' + section).querySelector('h2')?.textContent || (section === 'users' ? '用户与任务' : '我的人物');
    document.querySelector('.content').classList.toggle('has-embedded-section', embedded);
    document.querySelectorAll('[data-admin-frame]').forEach(frame => {
      const visible = frame.closest('.section').id === 'section-' + section;
      const syncVisibility = () => {
        try {
          frame.contentWindow.adminEmbedVisible = visible;
          frame.contentWindow.dispatchEvent(new Event('admin-embed-visibility'));
        } catch (_) { /* Login redirects can leave the embedded document. */ }
      };
      if (visible && !frame.hasAttribute('src')) {
        frame.addEventListener('load', () => {
          try {
            if (frame.contentWindow.location.pathname === '/login') {
              window.location.assign(frame.contentWindow.location.href); return;
            }
            frame.contentWindow.adminEmbedVisible = !frame.closest('.section').hidden;
            frame.contentWindow.dispatchEvent(new Event('admin-embed-visibility'));
          } catch (_) { /* Keep the shell usable if a frame cannot be inspected. */ }
        });
        frame.src = frame.dataset.src;
      } else if (frame.hasAttribute('src')) syncVisibility();
    });
    void ensureSection(section);
    window.dispatchEvent(new CustomEvent('admin-section-visible', {detail:{section}}));
  }
  window.addEventListener('hashchange', selectSection);
  function info(id, rows) {
    const node = byId(id); node.replaceChildren();
    for (const [label, value] of rows) {
      const row = document.createElement('div'), dt = document.createElement('dt'), dd = document.createElement('dd');
      dt.textContent = label; dd.textContent = String(value ?? '—'); row.append(dt, dd); node.append(row);
    }
  }
  function fillRedaction(config, form = redaction) {
    const defaults = {hair_mosaic_size:20,detection_width:540,tracking_width:960,hair_update_hz:6,primary_confidence:.15,score_threshold:.65,hold_frames:12,encoder:'auto'};
    for (const [name, value] of Object.entries({...config, ...(form === redaction ? (config.local_options || defaults) : {})})) {
      const input = form.elements.namedItem(name);
      if (!input) continue;
      if (input.type === 'checkbox') input.checked = value;
      else input.value = value ?? '';
      if (input.type === 'number') input.dataset.validValue = input.value;
    }
    if (form === redaction) syncLocalControls();
    validateRedaction(form);
  }
  function syncLocalControls() {
    const local = displayedProfile === 'local', mode = local ? redaction.elements.mask_mode.value : 'face';
    if (local && mode !== 'face') redaction.elements.blur_style.value = 'mosaic';
    redaction.querySelectorAll('[data-local-only]').forEach(node => {
      node.hidden = !local;
      node.querySelectorAll('input,select').forEach(input => input.disabled = !local);
    });
    redaction.querySelectorAll('[data-mask-for]').forEach(node => {
      const visible = node.dataset.maskFor.split(' ').includes(mode)
        && (!node.hasAttribute('data-mosaic-only') || redaction.elements.blur_style.value === 'mosaic')
        && (!node.hasAttribute('data-blur-only') || redaction.elements.blur_style.value === 'blur')
        && (!node.hasAttribute('data-tracking-only') || redaction.elements.robust_tracking.checked);
      node.hidden = !visible;
      node.querySelectorAll('input,select').forEach(input => input.disabled = !visible);
    });
    const descriptions = {
      face:'Deface / CenterFace 逐帧检测所有人脸。马赛克和黑色遮挡为矩形，模糊可选椭圆或矩形。',
      face_hair_primary:'MediaPipe 选择并跟踪主角，覆盖脸部与关联头发；稳定跟踪用于侧脸或短时检测丢失。',
      hair_primary:'跟踪主角并对关联头发做语义分割，只覆盖头发，不覆盖脸部。',
      face_hair_all:'YuNet 检测所有人脸，结合多人位置跟踪和头发分割；丢失保留帧数控制短时补偿。'
    };
    byId('local-mask-explanation').textContent = local ? descriptions[mode] : '通用外部 API 使用独立的人脸参数；本地专用高级选项不发送到外部服务。';
  }
  function readRedaction(form) {
    const values = {};
    const localOptions = {};
    form.querySelectorAll('input,select').forEach(input => {
      const target = input.hasAttribute('data-local-option') ? localOptions : values;
      target[input.name] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.dataset.validValue ?? input.value) : input.name === 'detection_size' ? (input.value ? Number(input.value) : null) : input.value;
    });
    if (form === redaction) {
      values.local_options = displayedProfile === 'local' ? localOptions : null;
      if (displayedProfile !== 'local') values.mask_mode = 'face';
    }
    return values;
  }
  function validateRedaction(form) {
    const labels = {mask_scale:'遮罩倍数', mosaic_size:'马赛克颗粒', threshold:'检测阈值',
      face_box_expand:'人脸框扩展比例', face_confidence:'人脸识别阈值',hair_mosaic_size:'头发马赛克颗粒',detection_width:'检测画面宽度',tracking_width:'跟踪画面宽度',hair_update_hz:'头发刷新频率',primary_confidence:'主角检测阈值',score_threshold:'多人检测阈值',hold_frames:'丢失保留帧数'};
    let valid = true;
    for (const [name, label] of Object.entries(labels)) {
      const input = form.elements.namedItem(name);
      if (!input) continue;
      const value = input.valueAsNumber, min = Number(input.min), max = Number(input.max);
      const positive = min === 0 && name !== 'hold_frames', integer = input.step === '1';
      const range = positive ? `大于 0，最大 ${max}` : `${min}～${max}${integer ? ' 的整数' : ''}`;
      let message = '';
      if (!input.disabled && (!Number.isFinite(value) || (positive ? value <= 0 : value < min) || value > max || (integer && !Number.isInteger(value)))) {
        message = `${label}请输入${range}。`;
      } else if (name === 'face_box_expand' && 1 + value === 1) {
        message = `${label}请输入可实际执行的数值（大于 0，最大 ${max}），例如 0.2。`;
      }
      input.setCustomValidity(message);
      if (!message && !input.disabled) input.dataset.validValue = input.value;
      const id = `${form.id}-${name}-error`;
      let error = byId(id);
      if (!error) {
        error = document.createElement('span'); error.id = id;
        error.className = 'parameter-error'; error.setAttribute('role', 'alert');
        input.insertAdjacentElement('afterend', error);
        input.setAttribute('aria-describedby', [input.getAttribute('aria-describedby'), id].filter(Boolean).join(' '));
      }
      error.textContent = message; error.hidden = !message;
      if (message) { input.setAttribute('aria-invalid', 'true'); valid = false; }
      else input.removeAttribute('aria-invalid');
    }
    return valid;
  }
  for (const form of [redaction, mediakit]) {
    form.addEventListener('input', () => validateRedaction(form));
    form.addEventListener('change', () => { if (form === redaction) syncLocalControls(); validateRedaction(form); });
    form.addEventListener('invalid', event => {
      validateRedaction(form);
      const details = event.target.closest('details');
      if (details) details.open = true;
    }, true);
  }
  function selectedProfile() {
    if (service.elements.mode.value === 'local') return 'local';
    try { return new URL(service.elements.endpoint.value).hostname === 'mediakit.cn-beijing.volces.com' ? 'mediakit' : 'http'; }
    catch (_) { return 'mediakit'; }
  }
  function showRedactionProfile() {
    if (!redactionProfiles) return;
    const next = selectedProfile();
    if (displayedProfile !== next) {
      if (displayedProfile) redactionProfiles[displayedProfile] = readRedaction(displayedProfile === 'mediakit' ? mediakit : redaction);
      displayedProfile = next;
      fillRedaction(redactionProfiles[next], next === 'mediakit' ? mediakit : redaction);
    }
    byId('local-redaction-card').hidden = next === 'mediakit';
    byId('mediakit-redaction-card').hidden = next !== 'mediakit';
    byId('redaction-profile-title').textContent = next === 'mediakit' ? 'MediaKit 参数' : next === 'http' ? '通用外部 API 参数' : '本地打码参数';
    syncLocalControls();
  }
  function serviceMode() {
    const external = service.elements.mode.value === 'http';
    byId('redaction-api-fields').hidden = !external;
    service.elements.endpoint.required = external;
    service.elements.endpoint.disabled = !external;
    showRedactionProfile();
  }
  function fillService(config) {
    for (const name of ['mode','endpoint','timeout_seconds']) service.elements[name].value = config[name];
    service.elements.api_key.value = '';
    service.elements.clear_api_key.checked = false;
    byId('redaction-key-status').textContent = config.has_api_key ? '已保存' : '未配置';
    serviceMode();
  }
  service.elements.mode.addEventListener('change', serviceMode);
  service.elements.endpoint.addEventListener('input', showRedactionProfile);
  service.addEventListener('submit', event => {
    event.preventDefault();
    const payload = Object.fromEntries(new FormData(service));
    payload.timeout_seconds = Number(payload.timeout_seconds);
    payload.clear_api_key = service.elements.clear_api_key.checked;
    operate(service, 'redaction-service-status', '正在保存…', async () => {
      const {config} = await request('/api/redaction-service', 'PUT', payload);
      fillService(config);
      note('redaction-service-status', config.mode === 'local' ? '已使用本地打码。' : '已设置外部 API 优先，失败后自动使用本地打码。');
    });
  });
  for (const form of [redaction, mediakit]) form.addEventListener('submit', event => {
    event.preventDefault();
    if (!validateRedaction(form)) { form.reportValidity(); return; }
    if (!form.reportValidity()) return;
    const profile = displayedProfile, values = readRedaction(form);
    const status = form === mediakit ? 'mediakit-status' : 'redaction-status';
    operate(form, status, '正在保存…', async () => {
      const data = await request('/api/redaction-settings', 'PUT', {profile, values});
      redactionProfiles[profile] = data.config;
      if (displayedProfile === profile) fillRedaction(data.config, form);
      note(status, '打码设置已保存；启用该处理方式后，新建草稿使用此配置。已有草稿和任务保持原参数。');
      await refreshOverview();
    });
  });
  const testForm = byId('redaction-test-form'), testFields = testForm.querySelector('fieldset');
  let originalURL = '', testId = '', pollTimer;
  function testSummary(profile, values) {
    const name = {local:'本地打码', mediakit:'MediaKit', http:'通用外部 API'}[profile];
    const details = profile === 'mediakit'
      ? `${values.mask_mode} · 强度 ${values.mask_strength} · 扩展 ${values.face_box_expand} · 阈值 ${values.face_confidence}`
      : `${{face:'仅人脸',face_hair_primary:'主角人脸＋头发',hair_primary:'仅主角头发',face_hair_all:'所有人人脸＋头发'}[values.mask_mode] || ''} · ${values.blur_style} · 遮罩倍数 ${values.mask_scale} · 马赛克颗粒 ${values.mosaic_size}${values.local_options && values.mask_mode !== 'face' ? ` · 头发颗粒 ${values.local_options.hair_mosaic_size} · 刷新 ${values.local_options.hair_update_hz} 次/秒` : ` · 阈值 ${values.threshold}`}`;
    note('redaction-test-summary', `本次测试参数：${name} · ${details}`);
  }
  function clearTestOutput() {
    const output = byId('redaction-test-output');
    output.pause(); output.removeAttribute('src'); output.load(); output.hidden = true;
    byId('redaction-test-placeholder').hidden = false;
  }
  byId('redaction-test-video').addEventListener('change', () => {
    if (originalURL) URL.revokeObjectURL(originalURL);
    const file = byId('redaction-test-video').files[0];
    originalURL = file ? URL.createObjectURL(file) : '';
    const original = byId('redaction-test-original');
    if (originalURL) original.src = originalURL; else original.removeAttribute('src');
    byId('redaction-test-videos').hidden = !file;
    clearTestOutput(); note('redaction-test-summary', ''); note('redaction-test-status', '');
  });
  async function pollTest() {
    clearTimeout(pollTimer);
    byId('redaction-test-retry').hidden = true;
    try {
      const result = await request('/api/admin/redaction-tests/' + encodeURIComponent(testId));
      testSummary(result.profile, result.values);
      note('redaction-test-status', result.message, result.status === 'failed');
      if (result.status === 'succeeded') {
        const output = byId('redaction-test-output');
        output.src = result.output_url; output.hidden = false;
        byId('redaction-test-placeholder').hidden = true;
        byId('redaction-test-videos').hidden = false;
      }
      if (['succeeded', 'failed'].includes(result.status)) {
        testFields.disabled = false;
      } else pollTimer = setTimeout(pollTest, 2000);
    } catch (error) {
      note('redaction-test-status', error.message, true);
      if (error.status === 404) testFields.disabled = false;
      else byId('redaction-test-retry').hidden = false;
    }
  }
  byId('redaction-test-retry').addEventListener('click', pollTest);
  testForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (testFields.disabled) return;
    const profile = displayedProfile, form = profile === 'mediakit' ? mediakit : redaction;
    if (!profile) return;
    if (!validateRedaction(form)) { form.reportValidity(); return; }
    if (!form.reportValidity()) return;
    if (profile !== 'local' && (service.elements.api_key.value.trim() || service.elements.clear_api_key.checked)) {
      note('redaction-test-status', '请先保存接口密钥，再测试当前打码参数。', true); return;
    }
    const file = byId('redaction-test-video').files[0];
    if (!file) return;
    if (file.size > 200 * 1024 * 1024) {
      note('redaction-test-status', '测试视频不能超过 200 MB，请选用较短片段。', true); return;
    }
    const values = readRedaction(form), body = new FormData();
    body.append('video', file); body.append('profile', profile); body.append('values', JSON.stringify(values));
    body.append('connection', JSON.stringify({endpoint:service.elements.endpoint.value.trim(), timeout_seconds:Number(service.elements.timeout_seconds.value)}));
    testFields.disabled = true; clearTestOutput();
    byId('redaction-test-retry').hidden = true;
    testSummary(profile, values); note('redaction-test-status', '正在上传视频并创建测试…');
    try {
      const result = await request('/api/admin/redaction-tests', 'POST', body);
      testId = result.id;
      await pollTest();
    } catch (error) {
      note('redaction-test-status', error.message, true); testFields.disabled = false;
    }
  });
  async function refreshOverview() {
    try {
      overview = await request('/api/admin/overview');
      const {model,storage,tikhub,system,redaction} = overview;
      byId('overview-model').textContent = model.mode === 'mock' ? '本地演示模式' : (model.has_api_key ? '模型已配置' : '待填写 API Key');
      byId('overview-model-detail').textContent = model.model;
      byId('overview-tikhub').textContent = tikhub.has_api_key ? '密钥已保存' : '等待配置';
      byId('overview-storage').textContent = storage.ready ? '已启用 TOS' : storage.enabled ? '配置待补全' : '尚未启用';
      byId('overview-storage-detail').textContent = storage.bucket || '上传打码视频，生成临时访问链接';
      note('overview-attention', model.generation_message || (model.mode === 'mock' ? '当前为演示模式，可在视频模型中切换真实生成。' : ''));
      info('system-info', [['访问范围',system.access],['存储目录',system.storage_dir],['上传大小上限',system.max_upload_mb + ' MB'],['模型轮询间隔',system.seedance_poll_seconds + ' 秒'],['通用下载 Cookie',system.cookie_configured ? '已配置' : '未配置'],['配置生效','服务配置保存后用于新任务']]);
      note('page-error','');
      return true;
    } catch (error) { note('page-error',error.message,true); return false; }
  }
  byId('refresh-overview').addEventListener('click',refreshOverview);
  window.addEventListener('model-settings-saved',refreshOverview);
  function fillTos(config) {
    for (const name of ['region','endpoint','bucket','prefix','expires_seconds']) {
      const input = tos.elements.namedItem(name);
      if (name === 'expires_seconds' && ![...input.options].some(o => o.value === String(config[name]))) {
        const option = document.createElement('option'); option.value = config[name]; option.textContent = config[name] + ' 秒（已保存）'; input.append(option);
      }
      input.value = config[name];
    }
    tos.elements.enabled.checked = config.enabled;
    tos.elements.clear_credentials.checked = false;
    for (const [name, id] of [['access_key','tos-ak-status'],['secret_key','tos-sk-status']]) {
      const input = tos.elements.namedItem(name); input.value = '';
      input.placeholder = config['has_' + name] ? '已保存，留空保留；输入可替换' : '请输入 ' + (name === 'access_key' ? 'Access Key' : 'Secret Key');
      byId(id).textContent = config['has_' + name] ? '已保存' : '未配置';
    }
  }
  function tosPayload() {
    const data = Object.fromEntries(new FormData(tos));
    data.enabled = tos.elements.enabled.checked;
    data.clear_credentials = tos.elements.clear_credentials.checked;
    data.expires_seconds = Number(data.expires_seconds);
    return data;
  }
  tos.elements.region.addEventListener('change', () => {
    tos.elements.endpoint.value = 'https://tos-' + tos.elements.region.value.trim() + '.volces.com';
    note('tos-status','地域已修改，请核对 Endpoint 并填写对应的 AK/SK，保存后生效。');
  });
  async function operate(form, statusId, message, action) {
    const fields = form.querySelector('fieldset');
    if (fields.disabled) return;
    fields.disabled = true; note(statusId,message);
    try { await action(); } catch(error) { note(statusId,error.message,true); }
    finally { fields.disabled = false; }
  }
  tos.addEventListener('submit', event => {
    event.preventDefault();
    const payload = tosPayload();
    if (payload.clear_credentials && !confirm('确认清除 TOS 的 AK 和 SK？新的 TOS 上传任务将不可用。')) return;
    operate(tos,'tos-status','正在保存…',async () => {
      const data = await request('/api/storage-settings','PUT',payload); fillTos(data.config);
      note('tos-status',data.config.ready ? 'TOS 配置已保存并启用，将用于新的方舟生成任务。' : '配置已保存。' + data.config.message);
      await refreshOverview();
    });
  });
  byId('test-tos').addEventListener('click', () => {
    if (!tos.reportValidity()) return; const payload = tosPayload();
    operate(tos,'tos-status','正在验证 Bucket 访问权限…',async () => {
      const data = await request('/api/storage-settings/test','POST',payload); note('tos-status',data.message,!data.ok);
    });
  });
  function tikPayload() { return {api_key:tik.elements.api_key.value.trim(),clear_api_key:tik.elements.clear_api_key.checked}; }
  function fillTik(data) {
    byId('tikhub-key-status').textContent = data.has_api_key ? '已保存' : '未配置';
    tik.elements.api_key.placeholder = data.has_api_key ? '已保存，留空保留；输入新密钥可替换' : '填写 TikHub API Key';
    tik.elements.api_key.value = ''; tik.elements.clear_api_key.checked = false;
  }
  tik.addEventListener('submit',event => {
    event.preventDefault(); const payload = tikPayload();
    if (payload.clear_api_key && !confirm('确认清除 TikHub 密钥？')) return;
    operate(tik,'tikhub-status','正在保存…',async () => {
      fillTik(await request('/api/link-settings','PUT',payload));
      note('tikhub-status', payload.clear_api_key ? '密钥已清除。' : 'TikHub 配置已保存，制作页将直接使用此密钥。'); await refreshOverview();
    });
  });
  byId('test-tikhub').addEventListener('click', () => {
    const payload = tikPayload();
    operate(tik,'tikhub-status','正在验证 TikHub 账户…',async () => {
      const data = await request('/api/link-settings/test','POST',payload); note('tikhub-status',data.message,!data.ok);
    });
  });
  const promptValue = () => JSON.stringify([prompts.elements.namedItem('name').value,prompts.elements.content.value]);
  function fillPrompt(id) {
    const selected = templates.find(item => item.id === id);
    templateId = selected?.id || ''; byId('admin-template-select').value = templateId;
    prompts.elements.namedItem('name').value = selected?.name || ''; prompts.elements.content.value = selected?.content || '';
    const ruleNames = {'legacy-v1':'早期素材联动', 'exclusive-v2':'独立参考优先', 'yoyo-v3':'独立参考优先，其余配饰跟随穿搭图'};
    byId('admin-prompt-rule').textContent = (ruleNames[selected?.rule_version] || ruleNames['legacy-v1'])
      + (selected?.is_default ? '；当前默认模板，直接保存会影响之后新建的草稿。' : '；已有草稿保留自身内容。');
    byId('delete-template').disabled = !selected; promptBaseline = promptValue();
  }
  async function loadTemplates(selected = '') {
    templates = (await request('/api/admin/prompt-templates')).items;
    const select = byId('admin-template-select'); select.replaceChildren();
    const blank = document.createElement('option'); blank.value=''; blank.textContent='新建模板'; select.append(blank);
    templates.forEach(item => { const option = document.createElement('option'); option.value=item.id; option.textContent=item.name+(item.is_default ? '（当前默认）' : ''); select.append(option); });
    fillPrompt(selected);
  }
  byId('admin-template-select').addEventListener('change',event => {
    if (promptValue() !== promptBaseline && !confirm('有未保存的模板修改，确认切换？')) { event.target.value=templateId; return; }
    fillPrompt(event.target.value); note('prompt-status','');
  });
  byId('new-template').addEventListener('click',() => {
    if (promptValue() !== promptBaseline && !confirm('有未保存的模板修改，确认新建？')) return;
    fillPrompt(''); prompts.elements.namedItem('name').focus();
  });
  prompts.addEventListener('submit',event => {
    event.preventDefault(); const payload={name:prompts.elements.namedItem('name').value,content:prompts.elements.content.value};
    operate(prompts,'prompt-status','正在保存模板…',async () => {
      if (templateId) {
        const latest = (await request('/api/admin/prompt-templates')).items.find(x => x.id === templateId);
        if (!latest) throw new Error('模板已删除，请重新选择；编辑内容仍保留。');
        const original = JSON.parse(promptBaseline);
        for (const [key,index] of [['name',0],['content',1]]) {
          if (payload[key] === original[index]) payload[key] = latest[key];
          else if (latest[key] !== original[index] && payload[key] !== latest[key]) throw new Error('此模板已在其他编辑入口修改，请核对后重新选择；编辑内容仍保留。');
        }
      }
      const data = await request('/api/admin/prompt-templates' + (templateId ? '/' + encodeURIComponent(templateId) : ''),templateId ? 'PUT' : 'POST',payload);
      await loadTemplates(data.id); await window.PromptMechanism.load(); note('prompt-status','共享模板已直接保存，制作页刷新后可选用；若为默认模板，之后新建的草稿会使用新正文。');
    });
  });
  byId('delete-template').addEventListener('click',() => {
    if (!templateId || !confirm('确认删除此提示词模板？')) return;
    operate(prompts,'prompt-status','正在删除…',async () => { await request('/api/admin/prompt-templates/' + encodeURIComponent(templateId),'DELETE'); await loadTemplates(); await window.PromptMechanism.load(); note('prompt-status','模板已删除。'); });
  });
  const sectionRequests = {
    variation: ['variation-status', async () => Promise.all([window.VariationSettings.load(), window.InspirationSettings.load()])],
    continuation: ['continuation-status', async () => window.ContinuationSettings.load()],
    overview: ['page-error', refreshOverview],
    system: ['page-error', async () => overview ? true : refreshOverview()],
    redaction: ['redaction-status', async () => {
      const serviceData = await request('/api/redaction-service'); fillService(serviceData.config);
      service.querySelector('fieldset').disabled = false;
      const data = await request('/api/redaction-settings');
      redactionProfiles = data.profiles; displayedProfile = null; showRedactionProfile();
      redaction.querySelector('fieldset').disabled = false;
      mediakit.querySelector('fieldset').disabled = false;
      testFields.disabled = false;
    }],
    storage: ['tos-status', async () => { fillTos((await request('/api/storage-settings')).config); tos.querySelector('fieldset').disabled = false; }],
    tikhub: ['tikhub-status', async () => { fillTik(await request('/api/link-settings')); tik.querySelector('fieldset').disabled = false; }],
    prompts: ['prompt-status', async () => { await loadTemplates(); await window.PromptMechanism.load(); await window.PromptWorkbench.load(); prompts.querySelector('fieldset').disabled = false; }],
  };
  async function ensureSection(section) {
    if (!sectionRequests[section] || sectionLoads.has(section)) return;
    const [statusId, load] = sectionRequests[section];
    sectionLoads.set(section, true);
    note(statusId, '正在读取…');
    try {
      if (await load() === false) throw new Error(byId(statusId).textContent || '配置暂时无法读取。');
      note(statusId, '');
    } catch (error) {
      sectionLoads.delete(section); note(statusId, error.message, true);
      const retry = document.createElement('button'); retry.type = 'button'; retry.textContent = '重试加载';
      retry.addEventListener('click', () => void ensureSection(section));
      byId(statusId).append(document.createTextNode(' '), retry);
    }
  }
  selectSection();
})();
