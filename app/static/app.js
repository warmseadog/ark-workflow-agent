const form = document.querySelector('#job-form');
const generateForm = document.querySelector('#generate-form');
const submit = document.querySelector('#submit');
const generateSubmit = document.querySelector('#generate-submit');
const sourceStep = document.querySelector('#source-step');
const previewStep = document.querySelector('#preview-step');
const generateStep = document.querySelector('#generate-step');
const defacedVideo = document.querySelector('#defaced-video');
const nextStep = document.querySelector('#next-step');
const resetStep = document.querySelector('#reset-step');
const result = document.querySelector('#result');
const message = document.querySelector('#message');
const percent = document.querySelector('#percent');
const bar = document.querySelector('#bar');
const logs = document.querySelector('#logs');
const jobId = document.querySelector('#job-id');
const download = document.querySelector('#download');
const settingsToggle = document.querySelector('#settings-toggle');
const settingsDrawer = document.querySelector('#settings-drawer');
const settingsClose = document.querySelector('#settings-close');
const blurStyle = document.querySelector('#blur-style');
const maskMode = document.querySelector('#mask-mode');
const hairModeNote = document.querySelector('#hair-mode-note');
const shapeField = document.querySelector('#shape-field');
const mosaicSizeField = document.querySelector('#mosaic-size-field');
const replaceImageField = document.querySelector('#replace-image-field');
const maskScale = document.querySelector('#mask-scale');
const maskScaleValue = document.querySelector('#mask-scale-value');
const threshold = document.querySelector('#threshold');
const thresholdValue = document.querySelector('#threshold-value');
const blurShape = document.querySelector('[name="blur_shape"]');
const mosaicSize = document.querySelector('[name="mosaic_size"]');
const detectionSize = document.querySelector('[name="detection_size"]');
const keepAudio = document.querySelector('[name="keep_audio"]');
const robustTracking = document.querySelector('[name="robust_tracking"]');
const SETTINGS_STORAGE_KEY = 'face-mosaic-settings-v2';

let activeJobId = null;
let generationStepActive = false;

const persistedSettings = [
  ['blur_style', blurStyle],
  ['blur_shape', blurShape],
  ['mask_mode', maskMode],
  ['mask_scale', maskScale],
  ['mosaic_size', mosaicSize],
  ['threshold', threshold],
  ['detection_size', detectionSize],
  ['keep_audio', keepAudio],
  ['robust_tracking', robustTracking],
];

function saveSettings() {
  const values = {};
  persistedSettings.forEach(([name, element]) => {
    values[name] = element.type === 'checkbox' ? element.checked : element.value;
  });
  try {
    localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(values));
  } catch (_error) {
    // Private browsing or a restricted browser may disable localStorage.
  }
}

function loadSettings() {
  try {
    const values = JSON.parse(localStorage.getItem(SETTINGS_STORAGE_KEY) || '{}');
    persistedSettings.forEach(([name, element]) => {
      if (!(name in values)) return;
      if (element.type === 'checkbox') element.checked = Boolean(values[name]);
      else element.value = values[name];
    });
  } catch (_error) {
    // Invalid or unavailable saved settings fall back to the HTML defaults.
  }
}

function setSettingsDrawer(open) {
  settingsDrawer.hidden = !open;
  settingsDrawer.classList.toggle('drawer-open', open);
  settingsToggle.setAttribute('aria-expanded', String(open));
  document.body.classList.toggle('drawer-visible', open);
}

settingsToggle.addEventListener('click', () => setSettingsDrawer(true));
settingsClose.addEventListener('click', () => setSettingsDrawer(false));

function updateMaskControls() {
  const hairMode = maskMode.value !== 'face';
  hairModeNote.hidden = !hairMode;
  [...blurStyle.options].forEach(option => {
    option.disabled = hairMode && option.value !== 'mosaic';
  });
  if (hairMode && blurStyle.value !== 'mosaic') blurStyle.value = 'mosaic';
}

function updateBlurControls() {
  updateMaskControls();
  const style = blurStyle.value;
  shapeField.hidden = style !== 'blur';
  mosaicSizeField.hidden = style !== 'mosaic';
  replaceImageField.hidden = style !== 'img';
}

maskMode.addEventListener('change', updateBlurControls);
blurStyle.addEventListener('change', updateBlurControls);
persistedSettings.forEach(([_name, element]) => {
  element.addEventListener('change', saveSettings);
  element.addEventListener('input', saveSettings);
});
function syncRangeLabels() {
  maskScaleValue.textContent = maskScale.value;
  thresholdValue.textContent = Number(threshold.value).toFixed(2);
}

maskScale.addEventListener('input', syncRangeLabels);
threshold.addEventListener('input', syncRangeLabels);
loadSettings();
syncRangeLabels();
updateBlurControls();
saveSettings();

function resetStageTwo() {
  generationStepActive = false;
  previewStep.hidden = true;
  generateStep.hidden = true;
  nextStep.hidden = false;
  defacedVideo.removeAttribute('src');
  defacedVideo.load();
  generateForm.reset();
  generateSubmit.disabled = false;
  generateSubmit.textContent = '生成最终视频';
}

function render(job) {
  message.textContent = job.message;
  percent.textContent = `${job.progress}%`;
  bar.style.width = `${job.progress}%`;
  jobId.textContent = `任务 ${job.id}`;
  logs.innerHTML = (job.logs || []).map(item => `<div>${item}</div>`).join('');

  if (job.status === 'defaced' && job.defaced_url) {
    previewStep.hidden = false;
    nextStep.hidden = generationStepActive;
    defacedVideo.src = `${job.defaced_url}?v=${encodeURIComponent(job.updated_at || Date.now())}`;
    download.hidden = true;
    message.textContent = '打码完成，请先预览视频';
  }

  if (job.status === 'succeeded' && job.download_url) {
    download.hidden = false;
    download.href = job.download_url;
    download.textContent = job.provider === 'mock' ? '下载演示结果（打码视频）' : '下载 Seedance 结果视频';
  }

  if (job.status === 'failed') {
    message.textContent = job.error || '处理失败';
  }
}

async function poll(id) {
  const response = await fetch(`/api/jobs/${id}`);
  const job = await response.json();
  render(job);
  if (job.status === 'queued' || job.status === 'running') {
    window.setTimeout(() => poll(id), 1500);
  } else {
    submit.disabled = false;
    submit.textContent = job.status === 'defaced' ? '重新打码' : '开始打码';
    generateSubmit.disabled = false;
    generateSubmit.textContent = '生成最终视频';
  }
}

function formatError(detail, fallback) {
  if (Array.isArray(detail)) {
    const messages = detail.map(item => {
      if (item && typeof item === 'object') return item.msg || item.detail || JSON.stringify(item);
      return String(item);
    });
    return messages.join('；') || fallback;
  }
  if (detail && typeof detail === 'object') return detail.msg || detail.detail || JSON.stringify(detail);
  return detail || fallback;
}

function showSubmitError(payload, defaultMessage, button, defaultLabel) {
  message.textContent = formatError(payload.detail, defaultMessage);
  percent.textContent = '—';
  button.disabled = false;
  button.textContent = defaultLabel;
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  activeJobId = null;
  resetStageTwo();
  sourceStep.hidden = false;
  submit.disabled = true;
  submit.textContent = '正在打码…';
  result.hidden = false;
  download.hidden = true;
  logs.innerHTML = '';
  let response;
  let payload;
  try {
    response = await fetch('/api/jobs', { method: 'POST', body: new FormData(form) });
    payload = await response.json();
  } catch (_error) {
    showSubmitError({ detail: '无法连接到本地服务，请确认应用仍在运行。' }, '提交失败', submit, '开始打码');
    return;
  }
  if (!response.ok) {
    showSubmitError(payload, '提交失败', submit, '开始打码');
    return;
  }
  activeJobId = payload.id;
  render(payload);
  poll(payload.id);
});

nextStep.addEventListener('click', () => {
  generationStepActive = true;
  sourceStep.hidden = true;
  nextStep.hidden = true;
  generateStep.hidden = false;
  generateStep.scrollIntoView({ behavior: 'smooth', block: 'start' });
});

resetStep.addEventListener('click', () => window.location.reload());

generateForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!activeJobId) {
    message.textContent = '请先完成视频打码。';
    result.hidden = false;
    return;
  }
  generateSubmit.disabled = true;
  generateSubmit.textContent = '正在提交…';
  result.hidden = false;
  let response;
  let payload;
  try {
    response = await fetch(`/api/jobs/${activeJobId}/generate`, {
      method: 'POST',
      body: new FormData(generateForm),
    });
    payload = await response.json();
  } catch (_error) {
    showSubmitError({ detail: '无法连接到本地服务，请确认应用仍在运行。' }, '提交失败', generateSubmit, '生成最终视频');
    return;
  }
  if (!response.ok) {
    showSubmitError(payload, '提交失败', generateSubmit, '生成最终视频');
    return;
  }
  render(payload);
  poll(payload.id);
});
