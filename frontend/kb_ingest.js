/* 任务详情的“添加到知识库”：选库或新建，可选连同对话一起入库。 */

(() => {
  const detail = document.querySelector("#detail-panel");
  if (!detail) return;

  let dialog = null;
  let bases = [];
  let taskId = null;

  const api = async (url, options = {}) => {
    const response = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail?.message || `请求失败（${response.status}）`);
    }
    return response.status === 204 ? null : response.json();
  };

  const escapeHtml = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[char]);

  const buildDialog = () => {
    dialog = document.createElement("dialog");
    dialog.id = "kb-add-dialog";
    dialog.className = "kb-dialog";
    dialog.innerHTML = `
      <form id="kb-add-form" class="kb-dialog-body">
        <header class="dialog-head">
          <div>
            <p class="eyebrow">添加到知识库</p>
            <h2>选择目标知识库</h2>
          </div>
          <button type="button" class="icon-button" id="kb-add-close" aria-label="关闭">×</button>
        </header>
        <p class="field">
          <label for="kb-add-select">知识库</label>
          <select id="kb-add-select"></select>
        </p>
        <p class="field" id="kb-add-name-field" hidden>
          <label for="kb-add-name">新知识库名称</label>
          <input id="kb-add-name" maxlength="40" placeholder="例如：框架选型资料" />
        </p>
        <label class="checkline">
          <input type="checkbox" id="kb-add-chat" checked />
          <span>同时存入这段对话（问题和回答）</span>
        </label>
        <p class="field-hint">入库后可在“知识库”页跨任务检索这些资料。</p>
        <p class="form-error" id="kb-add-error" hidden></p>
        <footer class="dialog-actions">
          <button type="button" class="button secondary" id="kb-add-cancel">取消</button>
          <button type="submit" class="button primary" id="kb-add-submit">确认入库</button>
        </footer>
      </form>
    `;
    document.body.append(dialog);
    dialog.querySelector("#kb-add-close").addEventListener("click", () => dialog.close());
    dialog.querySelector("#kb-add-cancel").addEventListener("click", () => dialog.close());
    dialog.querySelector("#kb-add-select").addEventListener("change", (event) => {
      dialog.querySelector("#kb-add-name-field").hidden = event.target.value !== "__new__";
    });
    dialog.querySelector("#kb-add-form").addEventListener("submit", submit);
  };

  const open = async () => {
    if (!dialog) buildDialog();
    dialog.querySelector("#kb-add-error").hidden = true;
    dialog.querySelector("#kb-add-chat").checked = true;
    const select = dialog.querySelector("#kb-add-select");
    select.innerHTML = '<option value="">加载中…</option>';
    dialog.showModal();
    try {
      bases = await api("/kb");
      select.innerHTML = [
        ...bases.map((kb) => `<option value="${kb.id}">${escapeHtml(kb.name)}</option>`),
        '<option value="__new__">＋ 新建知识库</option>',
      ].join("");
      dialog.querySelector("#kb-add-name-field").hidden = select.value !== "__new__";
    } catch (error) {
      select.innerHTML = `<option value="">${escapeHtml(error.message)}</option>`;
    }
  };

  const submit = async (event) => {
    event.preventDefault();
    const select = dialog.querySelector("#kb-add-select");
    const nameInput = dialog.querySelector("#kb-add-name");
    const errorBox = dialog.querySelector("#kb-add-error");
    const submitButton = dialog.querySelector("#kb-add-submit");
    const includeChat = dialog.querySelector("#kb-add-chat").checked;
    errorBox.hidden = true;
    submitButton.disabled = true;
    try {
      let kbId = select.value;
      if (!kbId) throw new Error("先选择一个知识库");
      if (kbId === "__new__") {
        const name = nameInput.value.trim();
        if (!name) throw new Error("给新知识库起个名字");
        const created = await api("/kb", {
          method: "POST",
          body: JSON.stringify({ name, note: "" }),
        });
        kbId = created.id;
      }
      await api(`/kb/${kbId}/ingest-task`, {
        method: "POST",
        body: JSON.stringify({ task_id: taskId, include_chat: includeChat }),
      });
      dialog.close();
      window.dispatchEvent(new CustomEvent("kb-ingested"));
    } catch (error) {
      errorBox.textContent = error.message;
      errorBox.hidden = false;
    } finally {
      submitButton.disabled = false;
    }
  };

  let checkToken = 0;

  const applyState = (button, rows) => {
    if (rows.length) {
      button.disabled = true;
      button.textContent = "已添加到数据库";
      button.classList.add("is-ingested");
      button.title = `已存入：${rows.map((row) => row.kb_name).join("、")}`;
    } else {
      button.disabled = false;
      button.textContent = "添加到知识库";
      button.classList.remove("is-ingested");
      button.title = "把任务结论、报告和产物存入知识库";
    }
  };

  const refreshState = async (button, id) => {
    const token = ++checkToken;
    try {
      const rows = await api(`/tasks/${id}/kb-documents`);
      if (token !== checkToken || taskId !== id) return;
      applyState(button, rows);
    } catch {
      if (token === checkToken) {
        button.disabled = false;
        button.textContent = "添加到知识库";
      }
    }
  };

  const inject = () => {
    const actions = detail.querySelector(".detail-actions");
    if (!actions) return;
    const row = document.querySelector('.task-row[aria-current="true"]');
    const id = row?.dataset.taskId || null;
    if (!id) return;
    taskId = id;
    let button = actions.querySelector("#add-to-kb");
    if (button && button.dataset.taskId === id) return;
    if (!button) {
      button = document.createElement("button");
      button.type = "button";
      button.id = "add-to-kb";
      button.className = "button secondary";
      button.textContent = "添加到知识库";
      button.addEventListener("click", () => open().catch(() => {}));
      actions.append(button);
    }
    button.dataset.taskId = id;
    refreshState(button, id);
  };

  new MutationObserver(inject).observe(detail, { childList: true, subtree: true });
  document.addEventListener("kb-ingested", () => {
    const button = detail.querySelector("#add-to-kb");
    if (button && taskId) refreshState(button, taskId);
  });
  document.addEventListener("kb-changed", () => {
    const button = detail.querySelector("#add-to-kb");
    if (button && taskId) refreshState(button, taskId);
  });
  inject();
})();
