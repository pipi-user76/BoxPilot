/* 知识库独立页：跨任务文档、上传、检索问答与导出。 */

(() => {
  const $ = (selector) => document.querySelector(selector);
  const kbSelect = $("#kb-select");
  const docList = $("#kb-doc-list");
  const docEmpty = $("#kb-doc-empty");
  const docCount = $("#kb-doc-count");
  const messages = $("#kb-messages");
  const scopeLabel = $("#kb-scope");
  const newChatButton = $("#kb-new-chat");
  const convList = $("#kb-conv-list");
  const docsDrawer = $("#kb-docs-drawer");
  const useHistoryInput = $("#kb-use-history");
  const questionInput = $("#kb-question");
  const askForm = $("#kb-ask-form");
  const kbDialog = $("#kb-dialog");
  const uploadButton = $("#kb-upload");
  const uploadInput = $("#kb-file");
  const exportButton = $("#kb-export");
  const kbMeta = $("#kb-meta");
  const deleteButton = $("#kb-delete");
  const deleteDialog = $("#kb-delete-dialog");
  const hint = $("#kb-hint");
  const stopButton = $("#kb-stop");
  const docSearch = $("#kb-doc-search");
  const docKind = $("#kb-doc-kind");

  let bases = [];
  let current = null;
  let conversationId = "default";
  let chatLog = [];
  let allDocs = [];
  let abortController = null;
  const DOC_KINDS = {
    web_digest: "网页整理",
    web_compare: "网页对比",
    items: "条目表",
    conversation: "对话",
    text: "文本",
  };

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

  const setHint = (text = "") => {
    hint.textContent = text;
  };

  let savedSelection = new Set();
  let draftSelection = new Set();
  let knownSelection = new Set();

  const selectionKey = () => (current ? `kb.selection.${current.id}` : "");

  const readSelection = () => {
    if (!current) return null;
    try {
      const raw = localStorage.getItem(selectionKey());
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      return {
        selected: new Set(Array.isArray(parsed.selected) ? parsed.selected : []),
        known: new Set(Array.isArray(parsed.known) ? parsed.known : []),
      };
    } catch {
      return null;
    }
  };

  const writeSelection = () => {
    if (!current) return;
    localStorage.setItem(selectionKey(), JSON.stringify({
      selected: [...savedSelection],
      known: [...knownSelection],
    }));
  };

  const selectionLabel = (size) => {
    if (!size) return "检索：未选择文档";
    if (allDocs.length && size >= allDocs.length) return "检索：全部文档";
    return `检索：已选 ${size} 篇`;
  };

  const updateSelectionUi = () => {
    const dirty = draftSelection.size !== savedSelection.size
      || [...draftSelection].some((id) => !savedSelection.has(id));
    const scope = $("#kb-rag-scope");
    if (scope) {
      scope.textContent = dirty
        ? `待保存：已选 ${draftSelection.size} 篇`
        : selectionLabel(savedSelection.size);
      scope.classList.toggle("is-dirty", dirty);
    }
    const counter = $("#kb-doc-selection");
    if (counter) counter.textContent = `已选 ${draftSelection.size} / ${allDocs.length}`;
    const save = $("#kb-doc-save");
    const reset = $("#kb-doc-reset");
    if (save) save.disabled = !dirty;
    if (reset) reset.disabled = !dirty;
  };

  const emptyChatHtml = () => `
    <div class="kb-chat-empty">
      <span class="kb-empty-icon" aria-hidden="true">◍</span>
      <h3>从一个问题开始</h3>
      <p>检索范围是本知识库的全部资料，可以跨任务提问。</p>
      <div class="kb-suggestion-list">
        <button type="button" class="kb-suggestion" data-question="总结这批资料的主要结论">总结这批资料的主要结论</button>
        <button type="button" class="kb-suggestion" data-question="这些资料之间有哪些关键差异？">这些资料之间有哪些关键差异？</button>
        <button type="button" class="kb-suggestion" data-question="这些资料分别来自哪里？">这些资料分别来自哪里？</button>
      </div>
    </div>`;

  const setDocsOpen = (open, persist = true) => {
    if (!docsDrawer) return;
    docsDrawer.hidden = !open;
    document.querySelector(".kb-layout")?.classList.toggle("is-docs-open", open);
    $("#kb-docs-toggle")?.setAttribute("aria-expanded", String(open));
    $("#kb-docs-close")?.setAttribute("aria-expanded", String(open));
    if (persist) localStorage.setItem("kb.docsOpen", open ? "1" : "0");
  };

  const renderDocuments = (docs) => {
    docCount.textContent = `${docs.length}`;
    docEmpty.hidden = docs.length > 0;
    docList.innerHTML = docs.map((doc) => `
      <article class="kb-doc-card${draftSelection.has(doc.id) ? " is-selected" : ""}" data-id="${doc.id}">
        <label class="kb-check"><input type="checkbox" class="kb-doc-check" aria-label="选择文档参与检索"${draftSelection.has(doc.id) ? " checked" : ""} /></label>
        <div class="kb-doc-main">
          <div class="kb-doc-head">
            <button type="button" class="kb-doc-title" data-action="ask">${escapeHtml(doc.title)}</button>
            <span class="kb-doc-kind" data-kind="${escapeHtml(doc.kind || "text")}">${escapeHtml(DOC_KINDS[doc.kind] || doc.kind || "文本")}</span>
          </div>
          <p class="kb-doc-preview">${escapeHtml(doc.preview || "暂无正文摘要")}</p>
          <div class="kb-doc-meta">
            <span>${new Date(doc.created_at).toLocaleString("zh-CN", { hour12: false })}</span>
            <span>${doc.chunk_count} 段</span>
            ${doc.orphaned ? '<span class="kb-tag muted">来源任务已删除</span>' : ""}
          </div>
        </div>
        <button type="button" class="kb-doc-remove" data-action="delete" title="删除文档" aria-label="删除文档">×</button>
      </article>
    `).join("");
    updateSelectionUi();
  };

  const applyDocFilter = () => {
    const term = (docSearch?.value || "").trim().toLowerCase();
    const kind = docKind?.value || "";
    const docs = allDocs.filter((doc) =>
      (!kind || doc.kind === kind) &&
      (!term || `${doc.title} ${doc.url || ""}`.toLowerCase().includes(term)));
    renderDocuments(docs);
  };

  const loadDocuments = async () => {
    if (!current) {
      allDocs = [];
      renderDocuments([]);
      return;
    }
    allDocs = await api(`/kb/${current.id}/documents`);
    const ids = allDocs.map((doc) => doc.id);
    const stored = readSelection();
    if (!stored) {
      savedSelection = new Set(ids);
      knownSelection = new Set(ids);
    } else {
      savedSelection = new Set([...stored.selected].filter((id) => ids.includes(id)));
      knownSelection = new Set(stored.known);
      ids.filter((id) => !knownSelection.has(id)).forEach((id) => savedSelection.add(id));
      knownSelection = new Set(ids);
    }
    draftSelection = new Set(savedSelection);
    writeSelection();
    applyDocFilter();
  };

  const renderMarkdown = (text) => {
    const lines = escapeHtml(text).split(/\r?\n/);
    const html = [];
    let list = null;
    const closeList = () => {
      if (list) {
        html.push(`</${list}>`);
        list = null;
      }
    };
    const inline = (line) => line
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\[(\d+)\]/g, '<span class="cite">[$1]</span>');

    for (const raw of lines) {
      const line = raw.trimEnd();
      if (!line.trim()) {
        closeList();
        continue;
      }
      const heading = line.match(/^#{2,4}\s+(.*)$/);
      if (heading) {
        closeList();
        html.push(`<h4>${inline(heading[1])}</h4>`);
        continue;
      }
      const bullet = line.match(/^\s*[-*]\s+(.*)$/);
      const ordered = line.match(/^\s*\d+[.)]\s+(.*)$/);
      if (bullet || ordered) {
        const tag = bullet ? "ul" : "ol";
        if (list !== tag) {
          closeList();
          html.push(`<${tag}>`);
          list = tag;
        }
        html.push(`<li>${inline((bullet || ordered)[1])}</li>`);
        continue;
      }
      closeList();
      html.push(`<p>${inline(line)}</p>`);
    }
    closeList();
    return html.join("");
  };

  const renderMessage = (role, text, citations = []) => {
    const article = document.createElement("article");
    article.className = `kb-message ${role}`;
    const roleLabel = document.createElement("div");
    roleLabel.className = "kb-role";
    roleLabel.textContent = role === "user" ? "你" : "助手";
    const bubble = document.createElement("div");
    bubble.className = "kb-bubble";
    if (role === "assistant") {
      bubble.innerHTML = renderMarkdown(text);
    } else {
      bubble.textContent = text;
    }
    if (citations.length) {
      const details = document.createElement("details");
      details.className = "kb-sources";
      const summary = document.createElement("summary");
      summary.textContent = `来源 ${citations.length}`;
      const list = document.createElement("ol");
      citations.forEach((item) => {
        const li = document.createElement("li");
        li.className = item.used === false ? "unused" : "";
        li.textContent = `${item.title || "文档"}${item.heading ? ` · ${item.heading}` : ""}`;
        list.append(li);
      });
      details.append(summary, list);
      article.append(details);
    }
    if (role === "assistant") {
      const actions = document.createElement("div");
      actions.className = "kb-actions";
      const copy = document.createElement("button");
      copy.type = "button";
      copy.textContent = "复制";
      copy.addEventListener("click", () => navigator.clipboard?.writeText(text).then(() => setHint("已复制")));
      actions.append(copy);
      article.append(actions);
    }
    article.prepend(roleLabel, bubble);
    messages.append(article);
    messages.scrollTop = messages.scrollHeight;
  };

  const loadHistory = async () => {
    messages.innerHTML = "";
    chatLog = [];
    if (!current) return;
    const rows = await api(
      `/kb/${current.id}/messages?limit=20&conversation_id=${encodeURIComponent(conversationId)}`,
    );
    if (!rows.length) {
      messages.innerHTML = emptyChatHtml();
      return;
    }
    rows.slice().reverse().forEach((row) => {
      chatLog.push({ role: "user", text: row.question });
      chatLog.push({ role: "assistant", text: row.answer });
      renderMessage("user", row.question);
      renderMessage("assistant", row.answer, row.citations || []);
    });
  };

  const loadConversations = async () => {
    if (!current || !convList) return;
    const rows = await api(`/kb/${current.id}/conversations?limit=50`);
    convList.innerHTML = rows.map((row) => `
      <div class="kb-conv-item${row.id === conversationId ? " is-active" : ""}" data-id="${row.id}" role="button" tabindex="0">
        ${escapeHtml(row.title.slice(0, 30))}
        <small>${row.count} 问 · ${new Date(row.updated_at).toLocaleDateString("zh-CN")}</small>
        <span class="kb-conv-actions">
          <button type="button" data-act="rename" title="重命名">✎</button>
          <button type="button" data-act="delete" title="删除会话">×</button>
        </span>
      </div>
    `).join("") || '<div class="kb-side-empty"><strong>暂无会话</strong><span>提问后会自动保存到这里</span></div>';
  };

  const startNewChat = () => {
    conversationId = `chat-${Date.now()}`;
    messages.innerHTML = emptyChatHtml();
    chatLog = [];
    convList?.querySelectorAll(".kb-conv-item").forEach((item) => item.classList.remove("is-active"));
  };

  const selectBase = async (kb) => {
    current = kb;
    scopeLabel.textContent = kb ? `${kb.name} · 仅检索本知识库` : "当前知识库";
    if (kbMeta) kbMeta.textContent = kb ? (kb.note?.trim() || "跨任务检索本库全部资料") : "还没有知识库";
    setHint("");
    deleteButton && (deleteButton.disabled = !kb);
    conversationId = "default";
    setDocsOpen(false, false);
    await loadConversations();
    await Promise.all([loadDocuments(), loadHistory()]);
    if (kbMeta && kb) {
      kbMeta.textContent = `${allDocs.length} 篇文档 · ${kb.note?.trim() || "跨任务检索"}`;
    }
  };

  const loadBases = async () => {
    bases = await api("/kb");
    kbSelect.innerHTML = bases
      .map((kb) => `<option value="${kb.id}">${escapeHtml(kb.name)}</option>`)
      .join("");
    if (!bases.length) {
      current = null;
      deleteButton && (deleteButton.disabled = true);
      if (kbMeta) kbMeta.textContent = "还没有知识库";
      renderDocuments([]);
      setDocsOpen(false, false);
      messages.innerHTML = `
        <div class="kb-chat-empty">
          <span class="kb-empty-icon" aria-hidden="true">◍</span>
          <h3>还没有知识库</h3>
          <p>新建一个知识库，上传资料或从任务详情把结果存进来。</p>
          <button type="button" class="button primary" id="kb-empty-create">新建知识库</button>
        </div>`;
      scopeLabel.textContent = "当前知识库";
      return;
    }
    const wanted = current ? bases.find((kb) => kb.id === current.id) : null;
    const next = wanted || bases[0];
    kbSelect.value = next.id;
    await selectBase(next);
  };

  const ask = async (question) => {
    if (!current) throw new Error("先选择或新建知识库");
    if (!savedSelection.size) {
      setHint("先勾选至少一篇文档并保存，再进行提问。");
      setDocsOpen(true);
      return;
    }
    renderMessage("user", question);
    chatLog.push({ role: "user", text: question });
    const article = document.createElement("article");
    article.className = "kb-message assistant";
    const bubble = document.createElement("div");
    bubble.className = "kb-bubble kb-cursor";
    bubble.innerHTML = '<span class="kb-status">正在连接知识库…</span>';
    article.append(bubble);
    messages.append(article);
    messages.scrollTop = messages.scrollHeight;
    abortController = new AbortController();
    if (stopButton) stopButton.hidden = false;
    let full = "";
    let citations = [];
    const response = await fetch(`/kb/${current.id}/ask/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        top_k: 6,
        min_score: 0,
        conversation_id: conversationId,
        use_history: Boolean(useHistoryInput?.checked),
        document_ids: [...savedSelection],
      }),
      signal: abortController.signal,
    });
    try {
      if (!response.ok || !response.body) {
        throw new Error(`提问失败（${response.status}）`);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let cut = buffer.indexOf("\n\n");
        while (cut >= 0) {
          const block = buffer.slice(0, cut);
          buffer = buffer.slice(cut + 2);
          cut = buffer.indexOf("\n\n");
          const eventLine = block.match(/^event:\s*(.+)$/m);
          const dataLine = block.match(/^data:\s*(.+)$/m);
          if (!eventLine || !dataLine) continue;
          const payload = JSON.parse(dataLine[1]);
          if (eventLine[1] === "status" && !full) {
            bubble.innerHTML = `<span class="kb-status">${escapeHtml(payload.text || "正在处理…")}</span>`;
          }
          if (eventLine[1] === "citations") citations = payload.citations || [];
          if (eventLine[1] === "delta") {
            full += payload.text || "";
            bubble.innerHTML = renderMarkdown(full);
            messages.scrollTop = messages.scrollHeight;
          }
          if (eventLine[1] === "error") throw new Error(payload.message || "回答失败");
        }
      }
      if (!full.trim()) throw new Error("模型没有返回内容");
      bubble.classList.remove("kb-cursor");
      bubble.innerHTML = renderMarkdown(full);
      if (citations.length) {
        const details = document.createElement("details");
        details.className = "kb-sources";
        details.innerHTML = `<summary>来源 ${citations.length}</summary><ol>${citations
          .map((item) => `<li class="${item.used === false ? "unused" : ""}">${escapeHtml(item.title || "文档")}${item.heading ? " · " + escapeHtml(item.heading) : ""}</li>`)
          .join("")}</ol>`;
        article.append(details);
      }
      const actions = document.createElement("div");
      actions.className = "kb-actions";
      const copy = document.createElement("button");
      copy.type = "button";
      copy.textContent = "复制";
      copy.addEventListener("click", () => navigator.clipboard?.writeText(full).then(() => setHint("已复制")));
      const regen = document.createElement("button");
      regen.type = "button";
      regen.textContent = "重生成";
      regen.addEventListener("click", () => ask(question).catch((error) => setHint(error.message)));
      actions.append(copy, regen);
      article.append(actions);
      chatLog.push({ role: "assistant", text: full });
      await loadConversations().catch(() => {});
    } catch (error) {
      if (error.name === "AbortError") {
        bubble.classList.remove("kb-cursor");
        bubble.innerHTML = renderMarkdown(full || "已停止。");
      } else {
        bubble.classList.remove("kb-cursor");
        bubble.innerHTML = renderMarkdown(`请求失败：${error.message}`);
      }
    } finally {
      bubble.classList.remove("kb-cursor");
      abortController = null;
      if (stopButton) stopButton.hidden = true;
    }
  };

  const exportChat = () => {
    if (!chatLog.length) return;
    const title = current ? current.name : "知识库";
    const lines = [
      `# ${title} 问答记录`, "",
      `导出时间：${new Date().toLocaleString("zh-CN", { hour12: false })}`, "",
    ];
    chatLog.forEach((item) => {
      if (item.role === "user") lines.push(`## 问：${item.text}`, "");
      else lines.push(`答：${item.text}`, "");
    });
    const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${title}-问答.md`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const upload = async (file) => {
    if (!current || !file) return;
    const form = new FormData();
    form.append("file", file);
    uploadButton.disabled = true;
    setHint("上传并解析中…");
    try {
      const response = await fetch(`/kb/${current.id}/upload`, { method: "POST", body: form });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail?.message || "上传失败");
      }
      await loadDocuments();
      setHint("上传完成。");
    } catch (error) {
      setHint(`上传失败：${error.message}`);
    } finally {
      uploadButton.disabled = false;
      uploadInput.value = "";
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
      kbDialog.close();
      current = created;
      await loadBases();
    } catch (error) {
      const errorBox = $("#kb-error");
      errorBox.textContent = error.message;
      errorBox.hidden = false;
    }
  };

  const renameBase = async () => {
    if (!current) return;
    const name = prompt("知识库名称", current.name);
    if (name === null) return;
    const trimmed = name.trim();
    if (!trimmed || trimmed === current.name) return;
    try {
      const updated = await api(`/kb/${current.id}`, {
        method: "PATCH",
        body: JSON.stringify({ name: trimmed }),
      });
      current = updated;
      await loadBases();
      setHint("知识库已重命名。");
    } catch (error) {
      setHint(`重命名失败：${error.message}`);
    }
  };

  const openDeleteDialog = () => {
    if (!current || !deleteDialog) return;
    $("#kb-delete-title").textContent = `删除「${current.name}」`;
    $("#kb-delete-docs").textContent = String(allDocs.length);
    deleteDialog.showModal();
    document.activeElement?.blur();
  };

  const deleteBase = async () => {
    if (!current) return;
    const kb = current;
    deleteButton.disabled = true;
    try {
      await api(`/kb/${kb.id}`, { method: "DELETE" });
      deleteDialog?.close();
      current = null;
      allDocs = [];
      chatLog = [];
      conversationId = "default";
      messages.innerHTML = "";
      await loadBases();
      document.dispatchEvent(new CustomEvent("kb-changed"));
    } catch (error) {
      setHint(`删除失败：${error.message}`);
    } finally {
      deleteButton.disabled = !current;
    }
  };

  const handleDocAction = async (event) => {
    const card = event.target.closest(".kb-doc-card");
    if (!card) return;
    const docId = card.dataset.id;
    if (event.target.closest("button[data-action='ask']")) {
      questionInput.value = `关于《${card.querySelector(".kb-doc-title").textContent}》，`;
      questionInput.focus();
      return;
    }
    if (event.target.closest("button[data-action='delete']")) {
      if (!confirm("删除这篇文档及其向量？")) return;
      await api(`/kb/${current.id}/documents/${docId}`, { method: "DELETE" });
      savedSelection.delete(docId);
      draftSelection.delete(docId);
      knownSelection.delete(docId);
      writeSelection();
      await loadDocuments();
      return;
    }
    if (event.target.closest("button, input, label")) return;
    if (draftSelection.has(docId)) draftSelection.delete(docId);
    else draftSelection.add(docId);
    card.classList.toggle("is-selected", draftSelection.has(docId));
    const box = card.querySelector(".kb-doc-check");
    if (box) box.checked = draftSelection.has(docId);
    updateSelectionUi();
  };

  const boot = async () => {
    $("#today").textContent = new Date().toLocaleDateString("zh-CN", {
      year: "numeric", month: "long", day: "numeric", weekday: "long",
    });
    api("/health").then((health) => {
      const badge = $("#health");
      badge.dataset.state = health.status === "ok" ? "ok" : "error";
      badge.textContent = health.status === "ok" ? "环境正常" : "环境异常";
    }).catch(() => {
      const badge = $("#health");
      badge.dataset.state = "error";
      badge.textContent = "环境异常";
    });
    try {
      const payload = await api("/auth/me");
      const user = payload.user || payload;
      $("#user-card").hidden = false;
      $("#current-user").textContent = user.username;
      $("#current-role").textContent = user.is_admin ? "管理员" : "成员";
      $("#user-avatar").textContent = user.username.slice(0, 1).toUpperCase();
      if (user.is_admin) $("#users-link").hidden = false;
    } catch {
      return;
    }
    $("#logout-rail").addEventListener("click", async () => {
      await api("/auth/logout", { method: "POST" }).catch(() => {});
      location.href = "/ui/login.html";
    });
    $("#kb-refresh").addEventListener("click", () => loadBases().catch((error) => setHint(error.message)));
    kbSelect.addEventListener("change", () => {
      const kb = bases.find((item) => item.id === kbSelect.value);
      if (kb) selectBase(kb);
    });
    $("#kb-new").addEventListener("click", () => {
      $("#kb-error").hidden = true;
      $("#kb-name").value = "";
      $("#kb-note").value = "";
      kbDialog.showModal();
    });
    $("#kb-dialog-cancel").addEventListener("click", () => kbDialog.close());
    $("#kb-rename")?.addEventListener("click", () => renameBase());
    $("#kb-cancel").addEventListener("click", () => kbDialog.close());
    $("#kb-form").addEventListener("submit", createBase);
    uploadButton.addEventListener("click", () => uploadInput.click());
    uploadInput.addEventListener("change", () => {
      upload(uploadInput.files[0]);
      uploadInput.value = "";
    });
    const chatArea = $(".kb-chat");
    if (chatArea) {
      ["dragenter", "dragover"].forEach((name) =>
        chatArea.addEventListener(name, (event) => {
          event.preventDefault();
          chatArea.classList.add("is-drag");
        }),
      );
      ["dragleave", "drop"].forEach((name) =>
        chatArea.addEventListener(name, () => chatArea.classList.remove("is-drag")),
      );
      chatArea.addEventListener("drop", (event) => {
        event.preventDefault();
        const file = event.dataTransfer?.files?.[0];
        if (file) upload(file);
      });
    }
    askForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const question = questionInput.value.trim();
      if (!question || !current) return;
      questionInput.value = "";
      try {
        await ask(question);
      } catch (error) {
        renderMessage("assistant", `提问失败：${error.message}`);
      }
    });
    newChatButton.addEventListener("click", startNewChat);
    convList?.addEventListener("click", async (event) => {
      const item = event.target.closest(".kb-conv-item");
      if (!item) return;
      const action = event.target.closest("[data-act]")?.dataset.act;
      if (action === "rename" && current) {
        const title = prompt("会话名称", item.childNodes[0]?.textContent?.trim() || "");
        if (title) {
          await api(`/kb/${current.id}/conversations/${item.dataset.id}`, {
            method: "PATCH", body: JSON.stringify({ title }),
          }).catch((error) => setHint(error.message));
          await loadConversations();
        }
        return;
      }
      if (action === "delete" && current) {
        if (!confirm("删除这个会话及其记录？")) return;
        await api(`/kb/${current.id}/conversations/${item.dataset.id}`, { method: "DELETE" })
          .catch((error) => setHint(error.message));
        if (conversationId === item.dataset.id) conversationId = "default";
        await loadConversations();
        await loadHistory();
        return;
      }
      conversationId = item.dataset.id;
      convList.querySelectorAll(".kb-conv-item").forEach((node) => node.classList.toggle("is-active", node === item));
      await loadHistory();
    });
    stopButton?.addEventListener("click", () => abortController?.abort());
    docSearch?.addEventListener("input", applyDocFilter);
    docKind?.addEventListener("change", applyDocFilter);
    questionInput.addEventListener("input", () => {
      questionInput.style.height = "auto";
      questionInput.style.height = `${Math.min(questionInput.scrollHeight, 160)}px`;
    });
    questionInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        askForm.requestSubmit();
      }
    });
    $("#kb-docs-toggle")?.addEventListener("click", () => {
      setDocsOpen(Boolean(docsDrawer?.hidden));
    });
    $("#kb-docs-close")?.addEventListener("click", () => setDocsOpen(false));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && docsDrawer && !docsDrawer.hidden) setDocsOpen(false);
    });
    deleteButton?.addEventListener("click", openDeleteDialog);
    $("#kb-delete-close")?.addEventListener("click", () => deleteDialog?.close());
    $("#kb-delete-cancel")?.addEventListener("click", () => deleteDialog?.close());
    $("#kb-delete-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      deleteBase();
    });
    messages.addEventListener("click", (event) => {
      const suggestion = event.target.closest("[data-question]");
      if (suggestion) {
        questionInput.value = suggestion.dataset.question;
        questionInput.focus();
        return;
      }
      if (event.target.closest("#kb-empty-create")) {
        $("#kb-new")?.click();
        return;
      }
    });
    exportButton.addEventListener("click", exportChat);
    docList.addEventListener("click", (event) => handleDocAction(event).catch((error) => setHint(error.message)));
    docList.addEventListener("change", (event) => {
      const box = event.target.closest(".kb-doc-check");
      if (!box) return;
      const row = box.closest(".kb-doc-card");
      if (box.checked) draftSelection.add(row.dataset.id);
      else draftSelection.delete(row.dataset.id);
      row.classList.toggle("is-selected", box.checked);
      updateSelectionUi();
    });
    $("#kb-doc-save")?.addEventListener("click", () => {
      savedSelection = new Set(draftSelection);
      writeSelection();
      applyDocFilter();
      setHint("检索范围已保存。");
    });
    $("#kb-doc-reset")?.addEventListener("click", () => {
      draftSelection = new Set(savedSelection);
      applyDocFilter();
    });
    $("#kb-rag-scope")?.addEventListener("click", () => setDocsOpen(true));
    await loadBases();
    if (location.hash === "#docs" || localStorage.getItem("kb.docsOpen") === "1") setDocsOpen(true);
  };

  boot().catch((error) => setHint(error.message));
})();
