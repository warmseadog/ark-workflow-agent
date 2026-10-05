(function () {
  const state = { projectId: localStorage.getItem("workflow-project-id"), redactionTaskId: null, generationTaskId: null, generationPending: false, redactionPending: false };
  const $ = (id) => document.getElementById(id);
  const resumeButton = document.createElement("button");
  resumeButton.id = "resume-execution";
  resumeButton.textContent = "继续查询 / 下载原任务";
  resumeButton.hidden = true;
  $("start-execution").parentNode.appendChild(resumeButton);
  const json = async (url, options = {}) => {
    const response = await fetch(url, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || body.message || ("请求失败（" + response.status + "）"));
    return body;
  };
  const show = (name) => {
    ["source", "redaction", "materials", "execute"].forEach((step) => {
      $(step + "-panel").classList.toggle("hidden", step !== name);
    });
    document.querySelectorAll("#steps button").forEach((button) => button.classList.toggle("active", button.dataset.step === name));
  };
  const setStatus = (id, message, error = false) => {
    const node = $(id);
    node.textContent = message || "";
    node.classList.toggle("error", error);
  };
  const setProgress = (prefix, percent, message) => {
    $(prefix + "-bar").style.width = percent + "%";
    $(prefix + "-percent").textContent = percent + "%";
    $(prefix + "-message").textContent = message || "";
  };
  const projectUrl = (suffix = "") => "/api/workflow/projects/" + encodeURIComponent(state.projectId) + suffix;
  const move = (stage) => json(projectUrl("/stage"), { method: "POST", body: JSON.stringify({ stage }) });
  const requireProject = () => { if (!state.projectId) throw new Error("请先创建项目"); };
  const pendingKey = (kind) => "workflow-pending-" + kind + "-" + state.projectId;
  const submissionPayload = (kind, input) => {
    const saved = JSON.parse(localStorage.getItem(pendingKey(kind)) || "null");
    if (saved && Object.keys(input).some((key) => saved[key] !== input[key])) {
      throw new Error("上次提交结果尚未确认，请恢复上次设置后继续查看，避免重复提交。");
    }
    const payload = saved || { ...input, idempotency_key: kind + "-" + Array.from(crypto.getRandomValues(new Uint32Array(4)), (part) => part.toString(16).padStart(8, "0")).join("") };
    localStorage.setItem(pendingKey(kind), JSON.stringify(payload));
    return payload;
  };
  const syncTaskControls = (task, prefix) => {
    const pending = task.status === "queued" || task.status === "running";
    const held = task.requires_reconciliation || task.status === "restore_held";
    if (prefix === "execute") {
      state.generationPending = pending;
      $("start-execution").disabled = pending || task.can_resume || held;
      $("start-execution").textContent = pending ? "生成进行中" : "开始生成";
      resumeButton.hidden = !task.can_resume;
      resumeButton.disabled = false;
    } else {
      state.redactionPending = pending;
      $("start-redaction").disabled = pending || held || task.status === "succeeded";
      $("start-redaction").textContent = pending ? "打码进行中" : "生成打码预览";
    }
    const kind = prefix === "execute" ? "generation" : "redaction";
    const saved = JSON.parse(localStorage.getItem(pendingKey(kind)) || "null");
    if (saved && saved.idempotency_key === task.idempotency_key) localStorage.removeItem(pendingKey(kind));
  };
  const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[char]));
  const renderProject = (project) => {
    state.projectId = project.id;
    localStorage.setItem("workflow-project-id", project.id);
    $("project-badge").textContent = project.name + " · " + project.stage;
    ["source", "redaction", "materials", "execute"].forEach((prefix) => $(prefix + "-state").textContent = project.stage);
    const assets = (project.source_assets || []).concat(project.reference_assets || []);
    $("asset-list").innerHTML = assets.map((asset) => '<div class="asset-row"><span>' + escapeHtml(asset.kind) + '</span><code>' + escapeHtml(asset.uri || "") + '</code><em>v' + (asset.version || 1) + '</em></div>').join("");
    return project;
  };

  async function createProject() {
    const project = await json("/api/workflow/projects", {
      method: "POST",
      body: JSON.stringify({ name: $("project-name").value.trim() || "未命名项目" })
    });
    state.projectId = project.id;
    localStorage.setItem("workflow-project-id", project.id);
    const file = $("source-file").files[0];
    if (file) {
      const form = new FormData();
      form.append("video", file);
      form.append("rights_status", $("rights").value.trim() ? "declared" : "unknown");
      const response = await fetch(projectUrl("/source-upload"), { method: "POST", body: form });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || "视频上传失败");
    } else {
      const url = $("source-url").value.trim();
      if (!url) throw new Error("请上传视频文件或填写视频网址");
      await json(projectUrl("/source"), {
        method: "POST",
        body: JSON.stringify({ kind: "url", uri: url, metadata: { rights_status: $("rights").value.trim() ? "declared" : "unknown" } })
      });
    }
    for (const stage of ["SOURCE_INGESTING", "SOURCE_REVIEW", "SOURCE_READY", "REDACTION_EDITING", "REDACTION_REVIEW"]) await move(stage);
    renderProject(await json(projectUrl()));
    setStatus("source-status", "输入源已保存，已进入打码审核。");
    show("redaction");
  }

  async function startRedaction() {
    requireProject();
    $("start-redaction").disabled = true;
    if (state.redactionPending && state.redactionTaskId) {
      await pollTask(state.redactionTaskId, "redaction");
      return;
    }
    const payload = submissionPayload("redaction", {
      style: $("redaction-style").value,
      mask_mode: $("mask-mode").value,
      mask_scale: Number($("mask-scale").value) || 1.4,
      threshold: Number($("threshold").value) || 0.2
    });
    const response = await json(projectUrl("/redaction/render"), { method: "POST", body: JSON.stringify(payload) });
    state.redactionTaskId = response.task.id;
    state.redactionPending = true;
    localStorage.removeItem(pendingKey("redaction"));
    $("start-redaction").disabled = true;
    $("approve-redaction").disabled = true;
    setProgress("redaction", 10, "任务已排队");
    await pollTask(state.redactionTaskId, "redaction");
  }

  async function pollTask(taskId, prefix) {
    for (;;) {
      const task = await json(projectUrl("/tasks/" + encodeURIComponent(taskId)));
      const percent = task.status === "succeeded" ? 100 : task.status === "failed" ? 100 : task.status === "running" ? 55 : 10;
      const phases = { preparing: "准备素材", submitting: "正在提交，请勿重复操作", querying: "查询原任务", downloading: "下载原任务结果" };
      setProgress(prefix, percent, task.status === "running" ? (phases[(task.provider_state || {}).phase] || "处理中") : task.status);
      syncTaskControls(task, prefix);
      const log = $(prefix + "-log");
      if (log) log.textContent = JSON.stringify(task.status === "failed" ? task.error : task.output, null, 2);
      if (task.status === "succeeded") {
        if (prefix === "redaction") {
          $("redaction-video").src = projectUrl("/tasks/" + encodeURIComponent(taskId) + "/artifact") + "?v=" + Date.now();
          $("redaction-video").hidden = false;
          $("approve-redaction").disabled = false;
          setStatus("redaction-status", "打码完成，请预览并审核。");
        } else {
          $("result-video").src = projectUrl("/tasks/" + encodeURIComponent(taskId) + "/artifact") + "?v=" + Date.now();
          $("result-video").hidden = false;
          $("result-download").href = $("result-video").src;
          $("result-download").download = state.projectId + "-result.mp4";
          $("result-download").hidden = false;
          setStatus("execute-status", "生成完成，可以预览或下载。");
        }
        return task;
      }
      if (["failed", "cancelled", "restore_held"].includes(task.status)) {
        let message = (task.error || {}).message || "任务失败";
        if (task.status === "restore_held") message += " 此任务来自备份恢复，已暂停，请管理员核对后处理。";
        else if (task.requires_reconciliation) message += " 请先在服务商控制台核对提交结果，勿重复生成。";
        else if (task.can_resume) message += " 可继续查询或下载原任务，不会再次提交生成。";
        setStatus(prefix + "-status", message, true);
        if (prefix === "redaction") $("start-redaction").disabled = Boolean(task.requires_reconciliation);
        return task;
      }
      await new Promise((resolve) => setTimeout(resolve, 1200));
    }
  }

  async function approveRedaction() {
    requireProject();
    await json(projectUrl("/redaction/review"), {
      method: "POST",
      body: JSON.stringify({ approved: true, revision: $("redaction-revision").value, track: "Subject A", idempotency_key: "review-" + state.redactionTaskId })
    });
    setStatus("redaction-status", "打码已审核通过。");
    renderProject(await json(projectUrl()));
    show("materials");
  }

  async function uploadMaterial(kind) {
    requireProject();
    const file = $(kind + "-file").files[0];
    if (!file) throw new Error("请选择" + (kind === "face" ? "人脸" : "衣服") + "素材");
    const form = new FormData();
    form.append("kind", kind);
    form.append("material", file);
    form.append("approved", "true");
    const response = await fetch(projectUrl("/material-upload"), { method: "POST", body: form });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || "素材上传失败");
    renderProject(body.project);
    setStatus(kind + "-status", "素材已上传并批准。");
  }

  async function prepareExecution() {
    requireProject();
    await json(projectUrl("/snapshot"), { method: "POST", body: JSON.stringify({ run_id: state.projectId, idempotency_key: "snapshot-" + state.projectId }) });
    renderProject(await json(projectUrl()));
    show("execute");
  }

  async function startExecution() {
    requireProject();
    $("start-execution").disabled = true;
    if (state.generationPending && state.generationTaskId) {
      await pollTask(state.generationTaskId, "execute");
      return;
    }
    const pending = submissionPayload("generation", { prompt: $("prompt").value });
    const response = await json(projectUrl("/execute"), {
      method: "POST",
      body: JSON.stringify(pending)
    });
    state.generationTaskId = response.task.id;
    state.generationPending = true;
    localStorage.removeItem(pendingKey("generation"));
    setProgress("execute", 10, "任务已排队");
    await pollTask(state.generationTaskId, "execute");
  }

  async function resumeExecution() {
    resumeButton.disabled = true;
    await json(projectUrl("/tasks/" + encodeURIComponent(state.generationTaskId) + "/resume"), { method: "POST" });
    state.generationPending = true;
    setStatus("execute-status", "正在继续查询或下载原任务，不会再次提交生成。");
    await pollTask(state.generationTaskId, "execute");
  }

  $("create-project").addEventListener("click", () => createProject().catch((error) => setStatus("source-status", error.message, true)));
  $("start-redaction").addEventListener("click", () => startRedaction().catch((error) => {
    $("start-redaction").disabled = false;
    if (state.redactionPending) $("start-redaction").textContent = "继续查看任务";
    setStatus("redaction-status", error.message, true);
  }));
  $("approve-redaction").addEventListener("click", () => approveRedaction().catch((error) => setStatus("redaction-status", error.message, true)));
  $("upload-face").addEventListener("click", () => uploadMaterial("face").catch((error) => setStatus("face-status", error.message, true)));
  $("upload-garment").addEventListener("click", () => uploadMaterial("garment").catch((error) => setStatus("garment-status", error.message, true)));
  $("prepare-execution").addEventListener("click", () => prepareExecution().catch((error) => setStatus("materials-status", error.message, true)));
  $("start-execution").addEventListener("click", () => startExecution().catch((error) => {
    $("start-execution").disabled = false;
    if (state.generationPending) $("start-execution").textContent = "继续查看任务";
    setStatus("execute-status", error.message, true);
  }));
  resumeButton.addEventListener("click", () => resumeExecution().catch((error) => {
    resumeButton.disabled = false;
    setStatus("execute-status", error.message, true);
  }));

  (async function restore() {
    if (!state.projectId) return;
    $("start-execution").disabled = true;
    $("start-redaction").disabled = true;
    try {
      const project = renderProject(await json(projectUrl()));
      $("start-execution").disabled = false;
      $("start-redaction").disabled = false;
      if (project.stage === "READY_FOR_EXECUTION") show("execute");
      else if (project.stage.indexOf("MATERIAL") >= 0) show("materials");
      else if (project.stage.indexOf("REDACTION") >= 0) show("redaction");
      else show("source");
      const generation = [...(project.tasks || [])].reverse().find((task) => task.stage === "generation");
      const redaction = [...(project.tasks || [])].reverse().find((task) => task.stage === "redaction_render");
      if (generation) {
        state.generationTaskId = generation.id;
        syncTaskControls(generation, "execute");
        show("execute");
        await pollTask(generation.id, "execute");
      } else if (redaction && ["running", "queued", "failed", "restore_held"].includes(redaction.status)) {
        state.redactionTaskId = redaction.id;
        syncTaskControls(redaction, "redaction");
        show("redaction");
        await pollTask(redaction.id, "redaction");
      }
    } catch (_) {
      $("start-execution").disabled = true;
      $("start-redaction").disabled = true;
      setStatus("execute-status", "暂时无法读取任务状态，请刷新后继续查看；请勿重复生成。", true);
    }
  })();
})();
