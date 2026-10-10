const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = { candidates: [], cases: [], jobs: [], run: null };
function escapeHTML(value) { return String(value ?? '').replace(/[&<>\"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[char])); }
function toast(message) { const node = $('#toast'); node.textContent = message; node.classList.add('show'); clearTimeout(window.__toast); window.__toast = setTimeout(() => node.classList.remove('show'), 2600); }
async function api(path, options = {}) { const response = await fetch(path, {headers: {'Content-Type':'application/json', ...(options.headers || {})}, ...options}); let data = {}; try { data = await response.json(); } catch (_) {} if (!response.ok) throw new Error(data.detail || data.message || `请求失败（${response.status}）`); return data; }
function renderCandidates() {
  $('#candidate-count').textContent = state.candidates.length;
  const node = $('#candidate-list');
  if (!state.candidates.length) { node.innerHTML = '<div class="empty">还没有候选素材。先生成查询计划，或粘贴一批平台链接。</div>'; return; }
  node.innerHTML = state.candidates.map(item => `<article class="candidate" data-id="${escapeHTML(item.id)}"><div><div class="candidate-top"><span class="source">${escapeHTML(item.source)} · ${escapeHTML(item.status)}</span><span class="match">${item.score == null ? '—' : escapeHTML(item.score + '%')}</span></div><h4>${escapeHTML(item.title || '待补充标题')}</h4><p>${escapeHTML(item.canonical_url)}</p><div class="candidate-tags">${(item.tags || []).map(tag => `<span class="tag">${escapeHTML(tag)}</span>`).join('') || '<span class="tag">未采集标签</span>'}</div></div><div class="candidate-foot"><button class="approve" data-action="approve" type="button">${item.status === 'approved' ? '已通过' : '通过审核'}</button><button class="blur" data-action="blur" type="button">进入 V1 打码</button></div></article>`).join('');
}
function renderCases() { $('#case-list').innerHTML = state.cases.length ? state.cases.map(item => `<div class="case-item"><div><strong>${escapeHTML(item.title)}</strong><small>${escapeHTML((item.tags || []).join(' · '))} · ${escapeHTML(item.shot_notes || '未填写镜头特点')}</small></div><span>${escapeHTML(item.outcome)}</span></div>`).join('') : '<div class="empty">还没有案例。先录入成功镜头和失败原因。</div>'; }
function renderJobs() { $('#job-list').innerHTML = state.jobs.length ? state.jobs.map(job => `<div class="job-item"><div><strong>任务 ${escapeHTML(job.id.slice(0, 8))}</strong><small>${escapeHTML(job.message)} · ${escapeHTML(job.status)}</small></div><a class="secondary" href="/v1#job=${encodeURIComponent(job.id)}">打开 V1</a></div>`).join('') : '<div class="empty">还没有制作任务。</div>'; }
async function refresh() { const [candidateData, caseData] = await Promise.all([api('/api/discovery/candidates'), api('/api/discovery/cases')]); state.candidates = candidateData.items; state.cases = caseData.items; renderCandidates(); renderCases(); }
function renderPlan(plan) { const node = $('#query-plan'); node.hidden = false; node.innerHTML = `<strong>查询计划已生成：${escapeHTML(plan.queries.length)} 个平台入口</strong><div class="query-links">${plan.queries.map(query => `<a target="_blank" rel="noreferrer" href="${escapeHTML(query.url)}">${escapeHTML(query.platform)} · ${escapeHTML(query.query)}</a>`).join('')}</div><p>${escapeHTML(plan.sources.http.message)}</p>`; }
$('#search-form').addEventListener('submit', async event => { event.preventDefault(); const platforms = $$('input[type="checkbox"]:checked', event.currentTarget).map(input => input.value); const tags = $('#case-tags').value.split(/[，,、\s]+/).filter(Boolean); try { const plan = await api('/api/discovery/runs', {method:'POST', body: JSON.stringify({topic: $('#topic').value, platforms, case_ids: state.cases.map(item => item.id)})}); state.run = plan; renderPlan(plan); $('#progress-bar').style.width = '35%'; toast('查询计划已生成，平台入口已准备好'); } catch (error) { toast(error.message); } });
$('#candidate-list').addEventListener('click', async event => { const button = event.target.closest('[data-action]'); const card = event.target.closest('[data-id]'); if (!button || !card) return; const candidate = state.candidates.find(item => item.id === card.dataset.id); if (!candidate) return; try { if (button.dataset.action === 'approve') { await api(`/api/discovery/candidates/${candidate.id}/review`, {method:'POST', body: JSON.stringify({decision:'approved', note:'在发现工作台通过'})}); toast('已通过审核'); await refresh(); return; } if (candidate.status !== 'approved') { await api(`/api/discovery/candidates/${candidate.id}/review`, {method:'POST', body: JSON.stringify({decision:'approved', note:'进入 V1 前自动通过'})}); } const body = new FormData(); body.append('candidate_id', candidate.id); body.append('video_url', candidate.canonical_url); body.append('mask_mode', 'face'); const response = await fetch('/api/jobs', {method:'POST', body}); const payload = await response.json(); if (!response.ok) throw new Error(payload.detail || '无法创建 V1 任务'); localStorage.setItem('active-v1-job', payload.id); toast('已创建 V1 打码任务'); window.location.href = `/v1#job=${encodeURIComponent(payload.id)}`; } catch (error) { toast(error.message); } });
$('#open-import').addEventListener('click', () => { $('#import-modal').hidden = false; $('#import-links').focus(); });
['close-import','cancel-import'].forEach(id => $('#' + id).addEventListener('click', () => { $('#import-modal').hidden = true; }));
$('#submit-import').addEventListener('click', async () => { const links = $('#import-links').value.split(/\n+/).map(item => item.trim()).filter(Boolean); if (!links.length) { toast('请先粘贴链接'); return; } try { const result = await api('/api/discovery/import', {method:'POST', body: JSON.stringify({links})}); $('#import-modal').hidden = true; $('#import-links').value = ''; await refresh(); toast(`已导入 ${result.imported.length} 条，重复 ${result.duplicates.length} 条`); } catch (error) { toast(error.message); } });
$('#case-form').addEventListener('submit', async event => { event.preventDefault(); const form = new FormData(event.currentTarget); try { await api('/api/discovery/cases', {method:'POST', body: JSON.stringify({title: form.get('title'), tags: String(form.get('tags')).split(/[，,、\s]+/).filter(Boolean), shot_notes: form.get('shot_notes'), outcome: form.get('outcome')})}); event.currentTarget.reset(); await refresh(); toast('案例已保存'); } catch (error) { toast(error.message); } });
$$('[data-view]').forEach(button => button.addEventListener('click', async () => { $$('[data-view]').forEach(item => item.classList.toggle('active', item === button)); $$('.view').forEach(view => view.hidden = view.id !== `${button.dataset.view}-view`); if (button.dataset.view === 'queue') { try { const data = await api('/api/jobs'); state.jobs = data.items || []; renderJobs(); } catch (_) { $('#job-list').innerHTML = '<div class="empty">任务列表暂不可用。</div>'; } } }));
$$('[data-toast]').forEach(button => button.addEventListener('click', () => toast(button.dataset.toast)));
refresh().catch(error => toast(error.message));


function updateProductionHeader(view) {
  document.body.classList.toggle('production-view', view === 'create');
  const titles = {create: ['制作流程', '参考视频 · 衣服 · 人物'], discover: ['素材发现', '把趋势内容筛成可制作的参考'], queue: ['制作队列', '查看制作任务'], cases: ['成功案例', '管理参考案例']};
  const title = titles[view] || titles.discover;
  $('.top-title h1').textContent = title[0];
  $('.top-title span').textContent = title[1];
}
$$('[data-view]').forEach(button => button.addEventListener('click', () => updateProductionHeader(button.dataset.view)));
const initialView = document.body.dataset.initialView || 'discover';
updateProductionHeader(initialView);
if (initialView !== 'discover') {
  const initialButton = document.querySelector(`[data-view="${initialView}"]`);
  if (initialButton) initialButton.click();
}



