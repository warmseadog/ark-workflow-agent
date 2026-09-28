/* Choose a photo before changing the editor; uploads stay in this person's library. */
(() => {
  if (!document.getElementById('person-picker')) return;
  const dialog = document.createElement('dialog');
  dialog.id = 'person-photos-dialog'; dialog.className = 'portrait-dialog person-photos-dialog';
  dialog.setAttribute('aria-labelledby','person-photos-title');
  dialog.innerHTML = `<div class="portrait-heading"><div><p class="eyebrow">选择人物照片</p><h2 id="person-photos-title"></h2></div><button type="button" data-photo-close aria-label="关闭照片选择">×</button></div>
    <p data-photo-intro>点击照片下方“使用这张”，设为本次主参考图。</p>
    <div class="person-photos-toolbar"><button type="button" class="secondary" data-photo-upload>＋ 上传新照片</button><button type="button" class="secondary" data-photo-refresh>刷新</button><input type="file" accept=".png,.jpg,.jpeg,.webp" data-photo-file hidden aria-label="上传这个人的新照片"></div>
    <p class="portrait-small" data-photo-help>新照片自动入库检查，通过后可选；已入库照片下次直接使用。</p>
    <p data-photo-status role="status" aria-live="polite"></p><div class="person-photo-grid" data-photo-grid></div>`;
  document.body.append(dialog);
  const find = name => dialog.querySelector('[data-photo-'+name+']');
  let mode = 'image', person = null, working = false, generation = 0, timer = null, photos = [];
  const request = (...args) => window.portraitPeople.request(...args);
  function setWorking(value) {
    working = value;
    dialog.querySelectorAll('button').forEach(button => { button.disabled = value || button.dataset.unavailable === 'true'; });
  }
  function schedule() {
    clearTimeout(timer);
    if (dialog.open && photos.some(p => !['active','failed'].includes(p.status))) {
      timer = setTimeout(async () => {
        if (!working) { try { await refresh(); } catch (_) { find('status').textContent = '照片状态暂时读取失败，可点击“刷新”重试。'; } }
        schedule();
      },5000);
    }
  }
  async function refresh() {
    const token = generation, target = person.id;
    const data = await request('people/'+target+'/photos');
    if (!dialog.open || generation !== token) return;
    photos = (data.items || []).filter(photo => mode === 'video' ? photo.kind === 'person_video' : photo.kind !== 'person_video'); render(); schedule();
  }
  function render() {
    const grid = find('grid'); grid.replaceChildren();
    const current = window.productionPortraits?.currentPhoto;
    for (const photo of photos) {
      const card = document.createElement('article'); card.className = 'person-photo-card'; card.dataset.photoId = photo.id;
      const image = document.createElement(mode === 'video' ? 'video' : 'img'); image.src = photo.url;
      if (mode === 'video') { image.controls = true; image.preload = 'metadata'; image.playsInline = true; }
      else { image.alt = person.name+'的照片'; image.loading = 'lazy'; }
      const name = document.createElement('span'); name.className = 'person-photo-name'; name.textContent = photo.name; name.title = photo.name;
      const selected = window.portraitPeople.selected === person.id && current && (current.id === photo.asset_id || current.portrait?.remote_asset_id === photo.remote_asset_id);
      const active = photo.status === 'active' && photo.remote_asset_id;
      const label = document.createElement('small'); label.textContent = selected ? '当前主参考' : active ? '可用' : photo.status === 'failed' ? '检查未通过' : photo.status === 'uncertain' ? '结果待确认' : '入库检查中…';
      label.className = active ? 'photo-ready' : 'photo-pending';
      const use = document.createElement('button'); use.type = 'button'; use.className = 'primary'; use.dataset.photoUse = ''; use.dataset.unavailable = String(!active); use.textContent = selected ? '继续使用' : mode === 'video' ? '使用这段' : '使用这张';
      use.addEventListener('click',() => run(async () => {
        find('status').textContent = '正在核实并加载照片…';
        const asset = mode === 'video' ? await request('photos/'+photo.id+'/use','POST',{}) : await request('import','POST',{remote_asset_id:photo.remote_asset_id,person_type:person.person_type});
        await window.productionPortraits.importAsset(asset); dialog.close();
      }));
      card.append(image,name,label,use);
      if (['failed','uncertain'].includes(photo.status)) {
        const message = document.createElement('small'); message.textContent = photo.message; card.append(message);
        const retry = document.createElement('button'); retry.type = 'button'; retry.className = 'secondary'; retry.textContent = '重新检查'; retry.dataset.photoRetry = '';
        retry.addEventListener('click',() => run(async () => { await request('photos/'+photo.id+'/retry','POST',{}); await refresh(); find('status').textContent = '已请求重新检查。'; })); card.append(retry);
      }
      grid.append(card);
    }
    if (!photos.length) { const empty = document.createElement('p'); empty.className = 'person-photos-empty'; empty.textContent = mode === 'video' ? '这个人还没有视频。上传一段新视频，检查通过后即可使用。' : '这个人还没有照片。点击“上传新照片”，检查通过后即可使用。'; grid.append(empty); }
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
  dialog.addEventListener('close',() => { generation++; clearTimeout(timer); window.portraitPeople.refresh().catch(() => {}); });
  find('refresh').addEventListener('click',() => run(refresh));
  find('upload').addEventListener('click',() => find('file').click());
  find('file').addEventListener('change',() => {
    const file = find('file').files[0]; if (!file) return;
    run(async () => {
      find('status').textContent = '正在上传并入库…';
      const body = new FormData(); body.append('file',file); body.append('kind',mode === 'video' ? 'person_video' : 'face');
      const response = await fetch('/api/production/assets',{method:'POST',body});
      const asset = await response.json();
      if (!response.ok) throw new Error(typeof asset.detail === 'string' ? asset.detail : '照片上传失败，请重试。');
      const job = await request('photos','POST',{person_id:person.id,asset_id:asset.id});
      await refresh();
      find('status').textContent = job.status === 'active' ? '这张照片已入库，可以直接选择。' : job.status === 'failed' ? job.message : '照片已添加，检查通过后“使用这张”会自动亮起。可以先关闭，下次从“选照片”继续。';
    }); find('file').value = '';
  });
  window.portraitPhotos = {
    open(selected) {
      if (dialog.open) return;
      mode = window.productionPortraits?.referenceMode || 'image';
      person = selected; photos = []; generation++;
      const videoMode = mode === 'video';
      dialog.querySelector('.eyebrow').textContent = videoMode ? '选择人物视频' : '选择人物照片';
      find('intro').textContent = videoMode ? '点击“使用这段”，设为本次人物身份参考。' : '点击照片下方“使用这张”，设为本次主参考图。';
      find('help').textContent = videoMode ? 'MP4 / MOV，2–15 秒、50 MB 内；清晰单人视频。新视频自动入库检查，通过后可选。' : '新照片自动入库检查，通过后可选；已入库照片下次直接使用。';
      find('upload').textContent = videoMode ? '＋ 上传新视频' : '＋ 上传新照片';
      find('file').accept = videoMode ? '.mp4,.mov' : '.png,.jpg,.jpeg,.webp';
      find('file').setAttribute('aria-label',videoMode ? '上传这个人的新视频' : '上传这个人的新照片');
      document.getElementById('person-photos-title').textContent = selected.name;
      find('grid').replaceChildren(); dialog.showModal();
      run(async () => { find('status').textContent = '正在读取照片…'; await refresh(); find('status').textContent = ''; });
    }
  };
})();
