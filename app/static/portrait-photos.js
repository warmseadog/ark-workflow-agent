/* Browse people and their media in one dialog. Library writes live in the admin. */
(() => {
  if (!document.getElementById('person-picker')) return;
  const dialog = document.createElement('dialog');
  dialog.id = 'person-photos-dialog'; dialog.className = 'portrait-dialog person-photos-dialog';
  dialog.setAttribute('aria-labelledby','person-photos-title');
  dialog.innerHTML = `<div class="portrait-heading"><div><p class="eyebrow">人物库</p><h2 id="person-photos-title">选择人物</h2></div><button type="button" data-photo-close aria-label="关闭人物选择">×</button></div>
    <div data-photo-library></div>
    <div data-photo-detail hidden><div class="person-photos-toolbar"><button type="button" class="secondary" data-photo-back>返回人物列表</button><button type="button" class="secondary" data-photo-refresh>刷新</button></div>
    <p data-photo-intro>选择照片，设为本次主参考图。</p>
    <div class="person-photo-grid" data-photo-grid></div>
    <p class="portrait-small">需要给这个人物补充素材？<a href="/admin/settings#people" data-photo-manage>到后台管理人物库 ↗</a></p></div>
    <p data-photo-status role="status" aria-live="polite"></p>`;
  document.body.append(dialog);
  const find = name => dialog.querySelector('[data-photo-'+name+']');
  let mode = 'image', person = null, working = false, generation = 0, timer = null, photos = [];
  const request = (...args) => window.portraitPeople.request(...args);
  function releaseVideos() {
    dialog.querySelectorAll('video').forEach(video => { video.pause(); video.removeAttribute('src'); video.load(); });
  }
  function setWorking(value) {
    working = value;
    dialog.querySelectorAll('button').forEach(button => { button.disabled = value || button.dataset.unavailable === 'true'; });
  }
  function schedule() {
    clearTimeout(timer);
    if (dialog.open && person && photos.some(p => !['active','failed','stopped','removed','restore_held'].includes(p.status))) {
      timer = setTimeout(async () => {
        if (!working) { try { await refresh(); } catch (_) { find('status').textContent = '照片状态暂时读取失败，可点击“刷新”重试。'; } }
        schedule();
      },5000);
    }
  }
  async function refresh() {
    const token = generation, target = person.id;
    const data = await request('people/'+target+'/photos');
    if (!dialog.open || generation !== token || person?.id !== target) return;
    photos = (data.items || []).filter(photo => mode === 'video' ? photo.kind === 'person_video' : photo.kind !== 'person_video'); render(); schedule();
  }
  function render() {
    releaseVideos();
    const grid = find('grid'); grid.replaceChildren();
    const current = window.productionPortraits?.currentPhoto;
    for (const photo of photos) {
      const card = document.createElement('article'); card.className = 'person-photo-card'; card.dataset.photoId = photo.id;
      const image = document.createElement(mode === 'video' ? 'video' : 'img');
      if (mode === 'video') {
        image.controls = true; image.preload = 'none'; image.playsInline = true;
        if (photo.thumbnail_url) image.poster = photo.thumbnail_url;
      } else {
        image.alt = person.name+'的照片'; image.loading = 'lazy';
        if (photo.thumbnail_url) image.src = photo.thumbnail_url;
        else if (/^[a-f0-9]{32}$/.test(photo.asset_id || '')) image.src = '/api/production/assets/'+photo.asset_id+'/thumbnail';
      }
      const name = document.createElement('span'); name.className = 'person-photo-name'; name.textContent = photo.name; name.title = photo.name;
      const selected = window.portraitPeople.selected === person.id && current && (current.id === photo.asset_id || current.portrait?.remote_asset_id === photo.remote_asset_id);
      const active = photo.status === 'active' && photo.remote_asset_id;
      const label = document.createElement('small'); label.textContent = selected ? '当前主参考' : active ? '可用' : photo.status === 'stopped' ? '自动查询已停止' : photo.status === 'failed' ? '检查未通过' : photo.status === 'uncertain' ? '结果待确认' : '入库检查中…';
      label.className = active ? 'photo-ready' : 'photo-pending';
      const use = document.createElement('button'); use.type = 'button'; use.className = 'primary'; use.dataset.photoUse = ''; use.dataset.unavailable = String(!active); use.textContent = selected ? '继续使用' : mode === 'video' ? '使用这段' : '使用这张';
      use.addEventListener('click',() => run(async () => {
        find('status').textContent = '正在核实并加载照片…';
        const asset = await request('photos/'+encodeURIComponent(photo.id)+'/use','POST',{});
        await window.productionPortraits.importAsset(asset); dialog.close();
      }));
      card.append(image,name,label,use);
      if (mode === 'video') {
        const play = document.createElement('button'); play.type = 'button'; play.className = 'secondary'; play.textContent = '播放预览';
        play.addEventListener('click', () => { releaseVideos(); image.src = photo.url; image.play().catch(() => { find('status').textContent = '视频暂时无法播放，请重试。'; }); });
        card.append(play);
      }
      if (['failed','uncertain','stopped'].includes(photo.status) && photo.can_manage !== false) {
        const message = document.createElement('small'); message.textContent = photo.message; card.append(message);
        const retry = document.createElement('button'); retry.type = 'button'; retry.className = 'secondary'; retry.textContent = photo.status === 'stopped' ? '继续检查原记录' : '重新检查'; retry.dataset.photoRetry = '';
        retry.addEventListener('click',() => run(async () => { await request('photos/'+photo.id+'/retry','POST',{}); await refresh(); find('status').textContent = '已请求重新检查。'; })); card.append(retry);
      }
      grid.append(card);
    }
    if (!photos.length) { const empty = document.createElement('p'); empty.className = 'person-photos-empty'; empty.textContent = mode === 'video' ? '暂无人物视频，可到后台添加，或返回选择其他人物。' : '暂无人物照片，可到后台添加，或返回选择其他人物。'; grid.append(empty); }
    setWorking(working);
  }
  async function run(operation) {
    if (working) return;
    setWorking(true); find('status').textContent = ''; find('status').dataset.error = 'false';
    try { await operation(); }
    catch (error) { find('status').textContent = error.message; find('status').dataset.error = 'true'; }
    finally { if (mode === 'video') find('status').textContent = find('status').textContent.replaceAll('照片','视频').replaceAll('这张','这段').replaceAll('选照片','选视频'); setWorking(false); schedule(); }
  }
  find('close').addEventListener('click',() => { if (!working) dialog.close(); });
  dialog.addEventListener('cancel',event => { if (working) event.preventDefault(); });
  dialog.addEventListener('close',() => { generation++; clearTimeout(timer); releaseVideos(); window.portraitPeople.refresh().catch(() => {}); });
  window.addEventListener('pagehide', () => { clearTimeout(timer); releaseVideos(); });
  find('refresh').addEventListener('click',() => run(refresh));
  function openLibrary() {
    if (working) return;
    releaseVideos();
    person = null; photos = []; generation++; clearTimeout(timer);
    mode = window.productionPortraits?.referenceMode || 'image';
    find('library').hidden = false; find('detail').hidden = true;
    document.getElementById('person-photos-title').textContent = '选择人物';
    dialog.querySelector('.eyebrow').textContent = '人物库';
    find('status').textContent = '';
    if (!dialog.open) dialog.showModal();
    document.getElementById('person-search').focus();
  }
  find('back').addEventListener('click',openLibrary);
  window.portraitPhotos = {
    openLibrary,
    open(selected) {
      if (working) return;
      mode = window.productionPortraits?.referenceMode || 'image';
      person = selected; photos = []; generation++; clearTimeout(timer);
      find('library').hidden = true; find('detail').hidden = false;
      const videoMode = mode === 'video';
      dialog.querySelector('.eyebrow').textContent = videoMode ? '选择人物视频' : '选择人物照片';
      find('intro').textContent = videoMode ? '点击“使用这段”，设为本次人物身份参考。' : '点击照片下方“使用这张”，设为本次主参考图。';
      document.getElementById('person-photos-title').textContent = selected.name;
      find('grid').replaceChildren(); if (!dialog.open) dialog.showModal();
      run(async () => { find('status').textContent = '正在读取照片…'; await refresh(); find('status').textContent = ''; });
    }
  };
})();
