/* 网页资料整理页的“我的知识库”Tab：文档列表、RAG 问答与入库入口。 */

(() => {
  const params = new URLSearchParams(location.search);
  const pageType = (params.get("type") || "").toUpperCase();
  if (pageType !== "WEB_DIGEST") return;

  const $ = (selector) => document.querySelector(selector);
  const tabs = $("#feature-tabs");
  const tabTasks = $("#feature-tab-tasks");
  const tabKb = $("#feature-tab-kb");
  const statusTabs = $(".tabs");
  const taskPanel = $(".panel");
  const kbPanel = $("#kb-panel");
  const kbSelect = $("#kb-select");
  const docList = $("#kb-doc-list");
  const docEmpty = $("#kb-doc-empty");
  const docCount = $("#kb-doc-count");
  const messages = $("#kb-messages");
  const questionInput = $("#kb-question");
  const askForm = $("#kb-ask-form");
  const kbDialog = $("#kb-dialog");
  const ingestDialog = $("#ingest-dialog");
  const ingestSelect = $("#ingest-select");
  const uploadButton = $("#kb-upload");
  const uploadInput = $("#kb-file");
  const topkInput = $("#kb-topk");
  const topkValue = $("#kb-topk-value");
  const scoreInput = $("#kb-score");
  const scoreValue = $("#kb-score-value");
  const exportButton = $("#kb-export");

  let bases = [];
  let current = null;
  let ingestIds = [];
  let mode = "tasks";
  let chatLog = [];

  const api = async (url, options = {}) => {
    const response = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    if (response.status === 401) {
      location.href = "/ui/login.html";
      throw new Error("unauthorized");
    }
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      const detail = payload.detail || {};
      throw new Error(detail.message || `请求失败（${response.status}）`);
    }
    return response.status === 204 ? null : response.json();
  };

  const escapeHtml = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[char]);

  const setMode = (next) => {
    mode = next;
    tabTasks.classList.toggle("is-active", next === "tasks");
    tabKb.classList.toggle("is-active", next === "kb");
    statusTabs.hidden = next !== "tasks";
    taskPanel.hidden = next !== "tasks";
    kbPanel.hidden = next !== "kb";
    const shell = document.querySelector(".app-shell");
    if (shell) shell.classList.toggle("kb-mode", next === "kb");
    const detail = document.querySelector("#detail-panel");
    if (detail) detail.hidden = next === "kb";
    if (next === "kb") loadDocuments();
  };

  const renderBases = () => {
    kbSelect.innerHTML = bases.length
      ? bases.map((kb) => `<option value="${kb.id}">${escapeHtml(kb.name)}</option>`).join("")
      : '<option value="">还没有知识库</option>';
    if (current && bases.some((kb) => kb.id === current.id)) {
      kbSelect.value = current.id;
    } else {
      current = bases[0] || null;
    }
    $("#kb-hint").textContent = current
      ? (current.note || "选择知识库后可提问")
      : "先新建一个知识库";
  };

  const loadBases = async () => {
    bases = await api("/kb");
    renderBases();
    if (current) {
      await loadDocuments();
      await loadHistory();
    }
  };

  const renderDocuments = (docs) => {
    docCount.textContent = `${docs.length} 篇`;
    docEmpty.hidden = docs.length > 0;
    docList.innerHTML = docs.map((doc) => `
      <article class="kb-doc" data-id="${doc.id}">
        <strong>${escapeHtml(doc.title)}</strong>
        <small>${escapeHtml(doc.url || "无来源链接")}</small>
        <p>${escapeHtml((doc.content || "").slice(0, 120))}</p>
        <div class="kb-doc-meta">
          <span>${new Date(doc.created_at).toLocaleString("zh-CN", { hour12: false })}</span>
          <span>${doc.chunk_count} 段</span>
        </div>
        <div class="kb-doc-actions">
          <button type="button" class="link-button" data-action="ask">加入问答</button>
          <button type="button" class="link-button" data-action="refresh">刷新向量</button>
          <button type="button" class="link-button danger" data-action="delete">删除</button>
        </div>
      </article>
    `).join("");
  };

  const loadDocuments = async () => {
    if (!current) {
      renderDocuments([]);
      return;
    }
    const docs = await api(`/kb/${current.id}/documents`);
    renderDocuments(docs);
  };

  const citationBlock = (citations) => {
    if (!citations || !citations.length) return "";
    const items = citations.map((item) => `
      <li>
        <span class="cite-index">[${item.index}]</span>
        <a href="${escapeHtml(item.url || "#")}" target="_blank" rel="noreferrer">${escapeHtml(item.title)}</a>
        <p>${escapeHtml(item.snippet)}</p>
      </li>
    `).join("");
    return `<details class="kb-citations"><summary>引用资料 ${citations.length} 条</summary><ul>${items}</ul></details>`;
  };

  const appendMessage = (role, text, citations = []) => {
    const holder = document.createElement("div");
    holder.className = `kb-message ${role}`;
    holder.innerHTML = `
      <div class="kb-bubble">${escapeHtml(text).replace(/\n/g, "<br>")}</div>
      ${role === "assistant" ? citationBlock(citations) : ""}
    `;
    messages.append(holder);
    messages.scrollTop = messages.scrollHeight;
    chatLog.push({ role, text, citations });
  };

  const loadHistory = async () => {
    messages.innerHTML = "";
    chatLog = [];
    if (!current) return;
    const rows = await api(`/kb/${current.id}/messages?limit=20`);
    if (!rows.length) {
      messages.innerHTML = '<p class="empty-hint">选中知识库后，在下方输入问题。</p>';
      return;
    }
    rows.slice().reverse().forEach((row) => {
      appendMessage("user", row.question);
      appendMessage("assistant", row.answer, row.citations);
    });
  };

  const ask = async (event) => {
    event.preventDefault();
    const question = questionInput.value.trim();
    if (!current || !question) return;
    appendMessage("user", question);
    questionInput.value = "";
    const button = $("#kb-ask");
    button.disabled = true;
    try {
      const answer = await api(`/kb/${current.id}/ask`, {
        method: "POST",
        body: JSON.stringify({
          question,
          top_k: Number(topkInput.value),
          min_score: Number(scoreInput.value) / 100,
        }),
      });
      appendMessage("assistant", answer.answer, answer.citations);
    } catch (error) {
      appendMessage("assistant", `提问失败：${error.message}`);
    } finally {
      button.disabled = false;
    }
  };

  const uploadDocument = async (file) => {
    if (!current || !file) return;
    const form = new FormData();
    form.append("file", file);
    const button = uploadButton;
    button.disabled = true;
    try {
      const response = await fetch(`/kb/${current.id}/upload`, { method: "POST", body: form });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail?.message || `上传失败（${response.status}）`);
      }
      document.getElementById("kb-hint").textContent = `已入库：${file.name}`;
      await loadDocuments();
    } catch (error) {
      document.getElementById("kb-hint").textContent = error.message;
    } finally {
      button.disabled = false;
      uploadInput.value = "";
    }
  };

  const exportChat = () => {
    if (!chatLog.length) return;
    const title = current ? current.name : "知识库";
    const lines = [`# ${title} 问答记录`, "", `导出时间：${new Date().toLocaleString("zh-CN", { hour12: false })}`, ""];
    chatLog.forEach((item) => {
      if (item.role === "user") {
        lines.push(`## 问：${item.text}`, "");
      } else {
        lines.push(`答：${item.text}`, "");
        if (item.citations && item.citations.length) {
          lines.push("引用资料：", "");
          item.citations.forEach((cite) => {
            lines.push(`${cite.index}. ${cite.title}（${cite.url || "本地文档"}）`);
            if (cite.snippet) lines.push(`   > ${cite.snippet.replace(/\n/g, " ")}`);
          });
          lines.push("");
        }
      }
    });
    const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `${title}-问答.md`;
    link.click();
    URL.revokeObjectURL(link.href);
  };

  const openIngest = (ids) => {
    if (!bases.length) {
      window.alert("请先新建一个知识库。");
      return;
    }
    ingestIds = ids;
    ingestSelect.innerHTML = bases
      .map((kb) => `<option value="${kb.id}">${escapeHtml(kb.name)}</option>`)
      .join("");
    if (current) ingestSelect.value = current.id;
    $("#ingest-title").textContent = ids.length > 1
      ? `存入 ${ids.length} 个任务`
      : "选择目标知识库";
    ingestDialog.showModal();
  };

  const confirmIngest = async () => {
    const kbId = ingestSelect.value;
    if (!kbId || !ingestIds.length) return;
    const button = $("#ingest-confirm");
    button.disabled = true;
    try {
      if (ingestIds.length === 1) {
        await api(`/kb/${kbId}/documents`, {
          method: "POST",
          body: JSON.stringify({ task_id: ingestIds[0] }),
        });
      } else {
        await api(`/kb/${kbId}/documents/batch`, {
          method: "POST",
          body: JSON.stringify({ task_ids: ingestIds }),
        });
      }
      ingestDialog.close();
      current = bases.find((kb) => kb.id === kbId) || current;
      kbSelect.value = kbId;
      await loadDocuments();
    } catch (error) {
      $("#ingest-hint").textContent = `存入失败：${error.message}`;
    } finally {
      button.disabled = false;
    }
  };

  const createBase = async (event) => {
    event.preventDefault();
    const name = $("#kb-name").value.trim();
    if (!name) return;
    try {
      const created = await api("/kb", {
        method: "POST",
        body: JSON.stringify({ name, note: $("#kb-note").value.trim() }),
      });
      bases.unshift(created);
      current = created;
      renderBases();
      kbDialog.close();
      $("#kb-form").reset();
      await loadDocuments();
      messages.innerHTML = '<p class="empty-hint">知识库已创建，先从任务列表存入文档。</p>';
    } catch (error) {
      $("#kb-hint").textContent = `创建失败：${error.message}`;
    }
  };

  const onDocAction = async (event) => {
    const button = event.target.closest("[data-action]");
    if (!button || !current) return;
    const card = button.closest(".kb-doc");
    const docId = card.dataset.id;
    const action = button.dataset.action;
    try {
      if (action === "delete") {
        if (!window.confirm("删除这篇文档及其向量？")) return;
        await api(`/kb/${current.id}/documents/${docId}`, { method: "DELETE" });
        await loadDocuments();
      } else if (action === "refresh") {
        await api(`/kb/${current.id}/documents/${docId}/refresh`, { method: "POST" });
        await loadDocuments();
      } else if (action === "ask") {
        const title = card.querySelector("strong").textContent;
        questionInput.value = `关于《${title}》：`;
        questionInput.focus();
      }
    } catch (error) {
      window.alert(error.message);
    }
  };

  const injectDetailButton = () => {
    const panel = document.querySelector("#detail-panel");
    if (!panel || !document.body.classList.contains("detail-open")) return;
    if (panel.querySelector("#save-to-kb")) return;
    const heading = panel.querySelector(".detail-tabs") || panel.querySelector(".detail-heading");
    if (!heading) return;
    const button = document.createElement("button");
    button.id = "save-to-kb";
    button.className = "button secondary small";
    button.type = "button";
    button.textContent = "存入知识库";
    button.addEventListener("click", () => {
      const match = location.search.match(/[?&]task=([^&]+)/);
      const ids = match ? [decodeURIComponent(match[1])] : [];
      if (!ids.length) {
        window.alert("请先在列表里点开一个任务。");
        return;
      }
      openIngest(ids);
    });
    heading.append(button);
  };

  const selectedTaskIds = () =>
    [...document.querySelectorAll(".task-row input[type=checkbox]:checked")]
      .map((box) => box.closest(".task-row").dataset.taskId)
      .filter(Boolean);

  // 初始化
  tabs.hidden = false;
  tabTasks.addEventListener("click", () => setMode("tasks"));
  tabKb.addEventListener("click", () => setMode("kb"));
  kbSelect.addEventListener("change", async () => {
    current = bases.find((kb) => kb.id === kbSelect.value) || null;
    await loadDocuments();
    await loadHistory();
  });
  $("#kb-new").addEventListener("click", () => kbDialog.showModal());
  uploadButton.addEventListener("click", () => uploadInput.click());
  uploadInput.addEventListener("change", () => uploadDocument(uploadInput.files[0]));
  topkInput.addEventListener("input", () => {
    topkValue.textContent = topkInput.value;
  });
  scoreInput.addEventListener("input", () => {
    scoreValue.textContent = `${scoreInput.value}%`;
  });
  exportButton.addEventListener("click", exportChat);
  $("#kb-cancel").addEventListener("click", () => kbDialog.close());
  $("#kb-dialog-cancel").addEventListener("click", () => kbDialog.close());
  $("#kb-form").addEventListener("submit", createBase);
  docList.addEventListener("click", onDocAction);
  askForm.addEventListener("submit", ask);
  $("#ingest-cancel").addEventListener("click", () => ingestDialog.close());
  $("#ingest-dialog-cancel").addEventListener("click", () => ingestDialog.close());
  $("#ingest-confirm").addEventListener("click", confirmIngest);
  $("#kb-import-selected").addEventListener("click", () => {
    const ids = selectedTaskIds();
    if (!ids.length) return;
    openIngest(ids);
  });
  document.addEventListener("selectionchange", () => {
    $("#kb-import-selected").disabled = selectedTaskIds().length === 0;
  });

  new MutationObserver(injectDetailButton).observe(document.body, {
    subtree: true, childList: true,
  });

  loadBases().then(() => {
    if (location.hash === "#kb") setMode("kb");
  }).catch((error) => {
    $("#kb-hint").textContent = `知识库加载失败：${error.message}`;
  });
})();
