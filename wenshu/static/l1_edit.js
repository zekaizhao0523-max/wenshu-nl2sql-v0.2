/** L1 扩展元数据：JOIN / 指标 / 同义词 / 知识库 */

(function () {
  const esc = (s) =>
    String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/"/g, "&quot;");

  let l1Tables = [];
  let l1ColumnsCache = {};
  let relationsCache = [];
  let relationFilterCombos = null;
  let wsComboboxDocBound = false;
  let currentDocId = null;
  let activeMetaPanel = "staging";

  async function ensureTables() {
    if (l1Tables.length) return l1Tables;
    const data = await api("/api/l1/tables");
    l1Tables = data.items || [];
    return l1Tables;
  }

  async function getColumns(tableId) {
    if (!tableId) return [];
    if (l1ColumnsCache[tableId]) return l1ColumnsCache[tableId];
    const data = await api(`/api/l1/tables/${tableId}/columns`);
    l1ColumnsCache[tableId] = data.items || [];
    return l1ColumnsCache[tableId];
  }

  function bindWsComboboxDocClose() {
    if (wsComboboxDocBound) return;
    wsComboboxDocBound = true;
    document.addEventListener("click", (e) => {
      if (e.target.closest(".ws-combobox-toggle")) return;
      document.querySelectorAll(".ws-combobox").forEach((root) => {
        const panel = root.querySelector(".ws-combobox-panel");
        if (panel && !root.contains(e.target)) panel.classList.add("hidden");
      });
    });
  }

  /** 可搜索下拉：点箭头展开全部；输入时按关键字过滤 */
  function mountWsCombobox(parent, config) {
    const parentEl = typeof parent === "string" ? $(parent) : parent;
    if (!parentEl) return null;
    bindWsComboboxDocClose();

    const inputId = config.id;
    parentEl.innerHTML = `
      <div class="ws-combobox">
        <input id="${inputId}" type="text" class="ws-combobox-input rel-combobox" placeholder="${esc(config.placeholder || "")}" value="${esc(config.value || "")}" autocomplete="off" />
        <button type="button" class="ws-combobox-toggle" tabindex="-1" aria-label="展开全部选项">▾</button>
        <ul class="ws-combobox-panel hidden" role="listbox"></ul>
      </div>`;

    const root = parentEl.querySelector(".ws-combobox");
    const input = parentEl.querySelector(".ws-combobox-input");
    const toggle = parentEl.querySelector(".ws-combobox-toggle");
    const panel = parentEl.querySelector(".ws-combobox-panel");
    let allOptions = [...(config.options || [])];
    let open = false;
    let panelMode = "filter";

    const close = () => {
      open = false;
      panel.classList.add("hidden");
    };

    const renderPanel = (showAll) => {
      panelMode = showAll ? "all" : "filter";
      const q = input.value.trim().toLowerCase();
      const items = showAll
        ? allOptions
        : allOptions.filter((o) => !q || o.toLowerCase().includes(q));
      if (!items.length) {
        panel.innerHTML = '<li class="ws-combobox-empty hint">无匹配项</li>';
      } else {
        panel.innerHTML = items
          .map((o) => `<li class="ws-combobox-option" role="option">${esc(o)}</li>`)
          .join("");
      }
      panel.classList.remove("hidden");
      open = true;
    };

    toggle.addEventListener("mousedown", (e) => {
      e.preventDefault();
      if (open && panelMode === "all") {
        close();
        return;
      }
      renderPanel(true);
    });

    input.addEventListener("input", () => {
      renderPanel(false);
    });

    panel.addEventListener("mousedown", (e) => {
      e.preventDefault();
      const opt = e.target.closest(".ws-combobox-option");
      if (!opt) return;
      input.value = opt.textContent || "";
      close();
      config.onSelect?.(input.value);
      config.onChange?.(input.value);
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });

    input.addEventListener("blur", () => {
      setTimeout(() => {
        if (!panel.matches(":hover") && !toggle.matches(":hover")) close();
      }, 120);
      config.onBlur?.(input.value);
    });

    input.addEventListener("change", () => config.onChange?.(input.value));

    return {
      getInput: () => input,
      getValue: () => input.value,
      setValue: (v) => {
        input.value = v || "";
      },
      setOptions: (opts) => {
        allOptions = [...(opts || [])];
        if (open) renderPanel(panelMode === "all");
      },
      openAll: () => renderPanel(true),
      focus: () => input.focus(),
    };
  }

  function tableByName(db, table) {
    return l1Tables.find((t) => t.db_name === db && t.table_name === table);
  }

  function tableDisplayLabel(db, table) {
    if (!table) return "";
    return db ? `${table} (${db})` : table;
  }

  function allTableComboboxOptions() {
    return l1Tables.map((t) => tableDisplayLabel(t.db_name, t.table_name));
  }

  function parseTableInput(inputVal) {
    const v = (inputVal || "").trim();
    if (!v) return { db: "", table: "" };
    const paren = v.match(/^(.+?)\s*\(([^)]+)\)\s*$/);
    if (paren) {
      const table = paren[1].trim();
      const db = paren[2].trim();
      if (tableByName(db, table)) return { db, table };
    }
    const exact = l1Tables.filter((t) => t.table_name === v);
    if (exact.length === 1) return { db: exact[0].db_name, table: exact[0].table_name };
    const lower = v.toLowerCase();
    const ci = l1Tables.filter((t) => t.table_name.toLowerCase() === lower);
    if (ci.length === 1) return { db: ci[0].db_name, table: ci[0].table_name };
    const labelHit = l1Tables.filter(
      (t) => tableDisplayLabel(t.db_name, t.table_name).toLowerCase() === lower
    );
    if (labelHit.length === 1) return { db: labelHit[0].db_name, table: labelHit[0].table_name };
    const partial = l1Tables.filter((t) => t.table_name.toLowerCase().includes(lower));
    if (partial.length === 1) return { db: partial[0].db_name, table: partial[0].table_name };
    return { db: "", table: v };
  }

  function parseColumnInput(inputVal, columns) {
    const v = (inputVal || "").trim();
    if (!v) return "";
    if (!columns?.length) return v;
    const exact = columns.find((c) => c.column_name === v);
    if (exact) return exact.column_name;
    const lower = v.toLowerCase();
    const ci = columns.find((c) => c.column_name.toLowerCase() === lower);
    if (ci) return ci.column_name;
    const partial = columns.filter((c) => c.column_name.toLowerCase().includes(lower));
    if (partial.length === 1) return partial[0].column_name;
    return v;
  }

  function comboboxMountHtml(wrapId) {
    return `<div id="${wrapId}" class="ws-combobox-mount"></div>`;
  }

  async function columnNamesForTable(db, table) {
    const t = tableByName(db, table);
    if (!t) return [];
    const cols = await getColumns(t.table_id);
    return cols.map((c) => c.column_name);
  }

  function normalizeTableInput(inputEl) {
    if (!inputEl) return;
    const parsed = parseTableInput(inputEl.value);
    if (parsed.db && parsed.table) {
      inputEl.value = tableDisplayLabel(parsed.db, parsed.table);
    }
  }

  function findSameNameColumn(columns, name) {
    if (!name || !columns?.length) return null;
    const key = String(name).toLowerCase();
    return columns.find((c) => String(c.column_name).toLowerCase() === key) || null;
  }

  function relationEndpointsEqual(a, b) {
    return (
      a.left_db === b.left_db &&
      a.left_table === b.left_table &&
      a.left_column === b.left_column &&
      a.right_db === b.right_db &&
      a.right_table === b.right_table &&
      a.right_column === b.right_column
    );
  }

  function findConflictingRelation(body, editingId = "") {
    return relationsCache.find(
      (r) => r.relation_id !== editingId && relationEndpointsEqual(r, body)
    );
  }

  function setRelationListVisible(visible) {
    $("#relation-browse-wrap")?.classList.toggle("hidden", !visible);
  }

  function collectRelationTableNames() {
    const names = new Set();
    relationsCache.forEach((r) => {
      if (r.left_table) names.add(r.left_table);
      if (r.right_table) names.add(r.right_table);
    });
    l1Tables.forEach((t) => {
      if (t.table_name) names.add(t.table_name);
    });
    return [...names].sort((a, b) => a.localeCompare(b, "zh-CN"));
  }

  function ensureRelationFilterCombos() {
    if (relationFilterCombos) return relationFilterCombos;
    const rerenderRelations = debounce(() => renderRelationsList(), 200);
    relationFilterCombos = {
      left: mountWsCombobox("#relation-filter-left-wrap", {
        id: "relation-filter-left",
        placeholder: "输入或选择左表名…",
        options: [],
        onChange: rerenderRelations,
      }),
      right: mountWsCombobox("#relation-filter-right-wrap", {
        id: "relation-filter-right",
        placeholder: "输入或选择右表名…",
        options: [],
        onChange: rerenderRelations,
      }),
    };
    return relationFilterCombos;
  }

  function populateRelationFilterOptions() {
    const names = collectRelationTableNames();
    const combos = ensureRelationFilterCombos();
    combos.left.setOptions(names);
    combos.right.setOptions(names);
  }

  function getRelationFilterValues() {
    const combos = ensureRelationFilterCombos();
    return {
      left: combos.left?.getValue() || "",
      right: combos.right?.getValue() || "",
    };
  }

  function matchRelationTableFilter(tableName, filterText) {
    const q = (filterText || "").trim().toLowerCase();
    if (!q) return true;
    return String(tableName || "").toLowerCase().includes(q);
  }

  function filterRelations(items) {
    const { left: leftQ, right: rightQ } = getRelationFilterValues();
    return items.filter(
      (r) =>
        matchRelationTableFilter(r.left_table, leftQ) &&
        matchRelationTableFilter(r.right_table, rightQ)
    );
  }

  function renderRelationsList() {
    const el = $("#relation-list");
    const countEl = $("#relation-filter-count");
    if (!el) return;
    const items = filterRelations(relationsCache);
    if (countEl) {
      const total = relationsCache.length;
      const { left: leftQ, right: rightQ } = getRelationFilterValues();
      const leftTrim = leftQ.trim();
      const rightTrim = rightQ.trim();
      if (!leftTrim && !rightTrim) {
        countEl.textContent = total ? `共 ${total} 条` : "";
      } else {
        countEl.textContent = `匹配 ${items.length} / ${total} 条`;
      }
    }
    if (!relationsCache.length) {
      el.innerHTML = '<div class="hint">暂无 JOIN 关系，点击「新建关系」添加</div>';
      return;
    }
    if (!items.length) {
      el.innerHTML = '<div class="hint">无匹配的 JOIN 关系，请调整左表/右表筛选条件</div>';
      return;
    }
    el.innerHTML = items
      .map(
        (r) => `
      <div class="l1-item" data-relation-id="${esc(r.relation_id)}">
        <div class="title">${esc(r.left_table)}.${esc(r.left_column)} → ${esc(r.right_table)}.${esc(r.right_column)}</div>
        <div class="meta">${esc(r.join_type)} · ${esc(r.description || "无说明")}${r.is_enabled ? "" : " · 已禁用"}</div>
      </div>`
      )
      .join("");
    el.querySelectorAll(".l1-item").forEach((node) => {
      node.addEventListener("click", () => {
        const item = items.find((x) => x.relation_id === node.dataset.relationId);
        if (item) renderRelationForm(item);
      });
    });
  }

  function askRelationDuplicate(existing) {
    return new Promise((resolve) => {
      const overlay = document.createElement("div");
      overlay.className = "relation-dup-overlay";
      overlay.innerHTML = `
        <div class="relation-dup-dialog panel" role="dialog" aria-modal="true">
          <h4>已存在相同 JOIN 关系</h4>
          <p class="hint" style="margin:0.5rem 0 0.75rem">
            与已有关系字段相同：
            <strong>${esc(existing.left_table)}.${esc(existing.left_column)} → ${esc(existing.right_table)}.${esc(existing.right_column)}</strong>
          </p>
          <p class="hint" style="margin:0 0 0.85rem;font-size:0.8rem">
            相同表字段组合在系统中只能保留一条记录，请选择如何处理。
          </p>
          <div class="btn-row" style="flex-wrap:wrap">
            <button type="button" class="btn primary" data-act="replace">替换原有关系</button>
            <button type="button" class="btn secondary" data-act="keep">保留原有关系</button>
            <button type="button" class="btn secondary" data-act="cancel">继续编辑</button>
          </div>
        </div>`;
      const close = (act) => {
        overlay.remove();
        resolve(act);
      };
      overlay.addEventListener("click", (ev) => {
        if (ev.target === overlay) close("cancel");
      });
      overlay.querySelectorAll("[data-act]").forEach((btn) => {
        btn.addEventListener("click", () => close(btn.dataset.act));
      });
      document.body.appendChild(overlay);
    });
  }

  async function runIndex(body, title) {
    await withLoading(title || "更新向量", ["提交索引", "写入 Qdrant", "完成"], async (update) => {
      update({ message: "构建 embedding…", step: 1, progress: 40 });
      await runPipeline("index", body, $("#vector-log") || $("#job-log"), update);
      update({ message: "完成", step: 2, progress: 100, stepStatus: "done" });
    }, { cancellable: true });
  }

  function showMetaPanel(name) {
    activeMetaPanel = name;
    document.querySelectorAll(".meta-subtab").forEach((b) => {
      b.classList.toggle("active", b.dataset.metaPanel === name);
    });
    document.querySelectorAll(".meta-panel").forEach((p) => {
      p.classList.toggle("hidden", !p.id.endsWith(name));
    });
    if (name === "relations") loadRelations();
    if (name === "metrics") loadMetrics();
    if (name === "synonyms") loadSynonyms($("#synonym-filter")?.value || "");
  }

  // ---------- JOIN ----------

  async function loadRelations() {
    const el = $("#relation-list");
    if (!el) return;
    try {
      const data = await api("/api/l1/relations");
      relationsCache = data.items || [];
    } catch (e) {
      el.innerHTML = `<div class="hint">${esc(e.message)}</div>`;
      relationsCache = [];
      populateRelationFilterOptions();
      const countEl = $("#relation-filter-count");
      if (countEl) countEl.textContent = "";
      return;
    }
    await ensureTables().catch(() => {});
    populateRelationFilterOptions();
    renderRelationsList();
  }

  async function renderRelationForm(data = null) {
    await ensureTables();
    const wrap = $("#relation-form-wrap");
    if (!wrap) return;
    wrap.classList.remove("hidden");
    setRelationListVisible(!!data);
    const d = data || {
      relation_id: "",
      left_db: l1Tables[0]?.db_name || "",
      left_table: l1Tables[0]?.table_name || "",
      left_column: "",
      right_db: l1Tables[0]?.db_name || "",
      right_table: l1Tables[0]?.table_name || "",
      right_column: "",
      join_type: "LEFT JOIN",
      description: "",
      is_enabled: true,
    };
    wrap.innerHTML = `
      <h4>${data ? "编辑" : "新建"} JOIN</h4>
      <div class="l1-form-grid">
        <div class="form-field"><label>左表</label>${comboboxMountHtml("rel-left-table-wrap")}</div>
        <div class="form-field"><label>左字段</label>${comboboxMountHtml("rel-left-col-wrap")}</div>
        <div class="form-field"><label>右表</label>${comboboxMountHtml("rel-right-table-wrap")}</div>
        <div class="form-field"><label>右字段</label>${comboboxMountHtml("rel-right-col-wrap")}</div>
        <p class="hint" style="grid-column:1/-1;margin:0;font-size:0.8rem">表/字段框支持<strong>输入搜索</strong>；点右侧 ▾ 展开<strong>全部</strong>选项。选左字段后自动匹配右表同名字段；若无同名则清空右字段。</p>
        <div class="form-field"><label>JOIN 类型</label>
          <select id="rel-join-type">
            ${["LEFT JOIN", "INNER JOIN", "RIGHT JOIN", "FULL JOIN"]
              .map((j) => `<option${d.join_type === j ? " selected" : ""}>${j}</option>`)
              .join("")}
          </select>
        </div>
        <div class="form-field"><label title="勾选后该 JOIN 参与向量召回与 M-Schema；取消勾选仅保留记录、不参与问数">启用</label><input id="rel-enabled" type="checkbox" ${d.is_enabled !== false ? "checked" : ""} /></div>
      </div>
      <div class="form-field"><label>说明</label><input id="rel-desc" type="text" value="${esc(d.description)}" placeholder="业务关联说明" /></div>
      <div class="btn-row">
        <button id="rel-save" class="btn primary" type="button">保存</button>
        ${data ? `<button id="rel-del" class="btn secondary" type="button">删除</button>` : ""}
        <button id="rel-cancel" class="btn secondary" type="button">取消</button>
      </div>`;

    const tableOpts = allTableComboboxOptions();
    const relCombo = {
      leftTable: mountWsCombobox("#rel-left-table-wrap", {
        id: "rel-left-table",
        placeholder: "输入或选择表…",
        value: tableDisplayLabel(d.left_db, d.left_table),
        options: tableOpts,
      }),
      leftCol: mountWsCombobox("#rel-left-col-wrap", {
        id: "rel-left-col",
        placeholder: "输入或选择字段…",
        value: d.left_column || "",
        options: [],
      }),
      rightTable: mountWsCombobox("#rel-right-table-wrap", {
        id: "rel-right-table",
        placeholder: "输入或选择表…",
        value: tableDisplayLabel(d.right_db, d.right_table),
        options: tableOpts,
      }),
      rightCol: mountWsCombobox("#rel-right-col-wrap", {
        id: "rel-right-col",
        placeholder: "输入或选择字段…",
        value: d.right_column || "",
        options: [],
      }),
    };

    const refreshSide = async (side, selectedCol = "") => {
      const tableCombo = side === "left" ? relCombo.leftTable : relCombo.rightTable;
      const colCombo = side === "left" ? relCombo.leftCol : relCombo.rightCol;
      const { db, table } = parseTableInput(tableCombo.getValue());
      const names = await columnNamesForTable(db, table);
      colCombo.setOptions(names);
      colCombo.setValue(selectedCol || "");
      return names;
    };

    const syncRightColumnFromLeft = async () => {
      const leftColRaw = relCombo.leftCol.getValue().trim();
      const { db: rightDb, table: rightTable } = parseTableInput(relCombo.rightTable.getValue());
      if (!leftColRaw) {
        relCombo.rightCol.setValue("");
        await refreshSide("right");
        return;
      }
      const t = tableByName(rightDb, rightTable);
      if (!t) {
        relCombo.rightCol.setValue("");
        return;
      }
      const rightColNames = await refreshSide("right");
      const leftT = parseTableInput(relCombo.leftTable.getValue());
      let leftCol = leftColRaw;
      if (leftT.db && leftT.table) {
        const leftCols = await getColumns(tableByName(leftT.db, leftT.table)?.table_id);
        leftCol = parseColumnInput(leftColRaw, leftCols) || leftColRaw;
      }
      const match = findSameNameColumn(
        rightColNames.map((n) => ({ column_name: n })),
        leftCol
      );
      relCombo.rightCol.setValue(match ? match.column_name : "");
    };

    const onTableSideChange = async (side) => {
      normalizeTableInput(relCombo[side === "left" ? "leftTable" : "rightTable"].getInput());
      await refreshSide(side);
      if (side === "right") await syncRightColumnFromLeft();
    };

    relCombo.leftTable.getInput().addEventListener("change", () => onTableSideChange("left"));
    relCombo.leftTable.getInput().addEventListener("blur", () => {
      normalizeTableInput(relCombo.leftTable.getInput());
      void onTableSideChange("left");
    });
    relCombo.rightTable.getInput().addEventListener("change", () => onTableSideChange("right"));
    relCombo.rightTable.getInput().addEventListener("blur", () => {
      normalizeTableInput(relCombo.rightTable.getInput());
      void onTableSideChange("right");
    });
    relCombo.leftCol.getInput().addEventListener("change", () => syncRightColumnFromLeft());
    relCombo.leftCol.getInput().addEventListener("blur", () => syncRightColumnFromLeft());

    await refreshSide("left", d.left_column || "");
    await refreshSide("right", d.right_column || "");

    const parseSideAsync = async (side) => {
      const tableCombo = side === "left" ? relCombo.leftTable : relCombo.rightTable;
      const colCombo = side === "left" ? relCombo.leftCol : relCombo.rightCol;
      normalizeTableInput(tableCombo.getInput());
      const { db, table } = parseTableInput(tableCombo.getValue());
      const colRaw = colCombo.getValue().trim();
      const t = tableByName(db, table);
      if (!t) return { db, table, column: colRaw };
      const cols = await getColumns(t.table_id);
      return { db, table, column: parseColumnInput(colRaw, cols) || colRaw };
    };

    const buildBody = async () => {
      const L = await parseSideAsync("left");
      const R = await parseSideAsync("right");
      return {
        relation_id: d.relation_id || undefined,
        left_db: L.db,
        left_table: L.table,
        left_column: L.column,
        right_db: R.db,
        right_table: R.table,
        right_column: R.column,
        join_type: $("#rel-join-type")?.value || "LEFT JOIN",
        description: $("#rel-desc")?.value?.trim() || "",
        is_enabled: $("#rel-enabled")?.checked !== false,
      };
    };

    const persistRelation = async (body) => {
      const res = await api("/api/l1/relations", { method: "POST", body: JSON.stringify(body) });
      wrap.classList.add("hidden");
      setRelationListVisible(true);
      await loadRelations();
      if (res.vector_purge?.vectors_deleted) {
        window.notifyUser?.("已保存；JOIN 向量已从向量库清理", { variant: "ok", title: "表关系" });
      } else if (res.vector_purge_error) {
        window.notifyUser?.(res.message || res.vector_purge_error, { variant: "warn", title: "表关系" });
      }
      return res;
    };

    $("#rel-cancel")?.addEventListener("click", () => {
      wrap.classList.add("hidden");
      setRelationListVisible(true);
    });
    $("#rel-save")?.addEventListener("click", async () => {
      try {
        const body = await buildBody();
        if (!body.left_column || !body.right_column) {
          window.notifyUser?.("请选择左、右字段", { variant: "warn", title: "表关系" });
          return;
        }
        const isNew = !d.relation_id;
        if (isNew) {
          const conflict = findConflictingRelation(body);
          if (conflict) {
            const act = await askRelationDuplicate(conflict);
            if (act === "cancel") return;
            if (act === "keep") {
              wrap.classList.add("hidden");
              setRelationListVisible(true);
              window.notifyUser?.("已保留原有关系，未保存新建内容", {
                variant: "info",
                title: "表关系",
              });
              return;
            }
            body.relation_id = conflict.relation_id;
          }
        }
        await persistRelation(body);
      } catch (e) {
        if (!isLoadingCancelled(e)) window.notifyError?.(e);
      }
    });
    $("#rel-del")?.addEventListener("click", async () => {
      if (!confirm("确定删除此 JOIN 关系？\n将同时从元数据库和向量库中移除。")) return;
      try {
        const res = await api(`/api/l1/relations/${d.relation_id}`, { method: "DELETE" });
        wrap.classList.add("hidden");
        setRelationListVisible(true);
        await loadRelations();
        const n = res.vector_purge?.vectors_deleted ?? 0;
        if (res.vector_purge_error) {
          window.notifyUser?.(
            "JOIN 已从元数据库删除，但向量库清理失败，请稍后重试或联系管理员",
            { variant: "warn", title: "表关系" }
          );
        } else {
          window.notifyUser?.(
            n > 0 ? "已删除 JOIN，向量库中的对应向量已清理" : "已删除 JOIN",
            { variant: "ok", title: "表关系" }
          );
        }
      } catch (e) {
        window.notifyError?.(e);
      }
    });
  }

  // ---------- 指标 ----------

  async function loadMetrics() {
    const el = $("#metric-list");
    if (!el) return;
    let items;
    try {
      const data = await api("/api/l1/metrics");
      items = data.items || [];
    } catch (e) {
      el.innerHTML = `<div class="hint">${esc(e.message)}</div>`;
      return;
    }
    if (!items.length) {
      el.innerHTML = '<div class="hint">暂无指标定义</div>';
      return;
    }
    el.innerHTML = items
      .map(
        (m) => `
      <div class="l1-item" data-metric-id="${esc(m.metric_id)}">
        <div class="title">${esc(m.metric_name)}${m.cn_name ? ` · ${esc(m.cn_name)}` : ""}</div>
        <div class="meta">${esc((m.definition || "").slice(0, 80))}${m.is_enabled ? "" : " · 已禁用"}</div>
      </div>`
      )
      .join("");
    el.querySelectorAll(".l1-item").forEach((node) => {
      node.addEventListener("click", () => {
        const item = items.find((x) => x.metric_id === node.dataset.metricId);
        if (item) renderMetricForm(item);
      });
    });
  }

  function renderMetricForm(data = null) {
    const wrap = $("#metric-form-wrap");
    if (!wrap) return;
    wrap.classList.remove("hidden");
    const d = data || {
      metric_id: "",
      metric_name: "",
      cn_name: "",
      aliases: [],
      definition: "",
      sql_template: "",
      related_tables: [],
      domain: "",
      is_enabled: true,
    };
    const aliases = Array.isArray(d.aliases) ? d.aliases.join(", ") : d.aliases || "";
    const related = Array.isArray(d.related_tables) ? d.related_tables.join(", ") : d.related_tables || "";
    wrap.innerHTML = `
      <h4>${data ? "编辑" : "新建"}指标</h4>
      <div class="l1-form-grid">
        <div class="form-field"><label>指标英文名 *</label><input id="met-name" value="${esc(d.metric_name)}" /></div>
        <div class="form-field"><label>中文名</label><input id="met-cn" value="${esc(d.cn_name)}" /></div>
        <div class="form-field"><label>领域</label><input id="met-domain" value="${esc(d.domain)}" /></div>
        <div class="form-field"><label>启用</label><input id="met-enabled" type="checkbox" ${d.is_enabled !== false ? "checked" : ""} /></div>
      </div>
      <div class="form-field"><label>别名（逗号分隔）</label><input id="met-aliases" value="${esc(aliases)}" /></div>
      <div class="form-field"><label>定义 *</label><textarea id="met-def">${esc(d.definition)}</textarea></div>
      <div class="form-field"><label>SQL 模板</label><textarea id="met-sql">${esc(d.sql_template)}</textarea></div>
      <div class="form-field"><label>关联表（逗号分隔表名）</label><input id="met-related" value="${esc(related)}" /></div>
      <div class="btn-row">
        <button id="met-save" class="btn primary" type="button">保存</button>
        <button id="met-save-index" class="btn secondary" type="button">保存并更新向量</button>
        ${data ? `<button id="met-del" class="btn secondary" type="button">删除</button>` : ""}
        <button id="met-cancel" class="btn secondary" type="button">取消</button>
      </div>`;

    const buildBody = () => ({
      metric_id: d.metric_id || undefined,
      metric_name: $("#met-name")?.value?.trim(),
      cn_name: $("#met-cn")?.value?.trim() || "",
      aliases: ($("#met-aliases")?.value || "").split(",").map((s) => s.trim()).filter(Boolean),
      definition: $("#met-def")?.value?.trim(),
      sql_template: $("#met-sql")?.value?.trim() || "",
      related_tables: ($("#met-related")?.value || "").split(",").map((s) => s.trim()).filter(Boolean),
      domain: $("#met-domain")?.value?.trim() || "",
      is_enabled: $("#met-enabled")?.checked !== false,
    });

    $("#met-cancel")?.addEventListener("click", () => wrap.classList.add("hidden"));
    $("#met-save")?.addEventListener("click", async () => {
      try {
        await api("/api/l1/metrics", { method: "POST", body: JSON.stringify(buildBody()) });
        wrap.classList.add("hidden");
        await loadMetrics();
      } catch (e) {
        window.notifyError?.(e);
      }
    });
    $("#met-save-index")?.addEventListener("click", async () => {
      try {
        const res = await api("/api/l1/metrics", { method: "POST", body: JSON.stringify(buildBody()) });
        wrap.classList.add("hidden");
        await loadMetrics();
        await runIndex({ types: "metric", metric_ids: [res.metric_id] }, "更新指标向量");
      } catch (e) {
        if (!isLoadingCancelled(e)) window.notifyError?.(e);
      }
    });
    $("#met-del")?.addEventListener("click", async () => {
      if (!confirm("确定删除此指标？")) return;
      try {
        await api(`/api/l1/metrics/${d.metric_id}`, { method: "DELETE" });
        wrap.classList.add("hidden");
        await loadMetrics();
      } catch (e) {
        window.notifyError?.(e);
      }
    });
  }

  // ---------- 同义词 ----------

  async function loadSynonyms(q = "") {
    const el = $("#synonym-list");
    if (!el) return;
    let items;
    try {
      const url = q ? `/api/l1/synonyms?q=${encodeURIComponent(q)}` : "/api/l1/synonyms";
      const data = await api(url);
      items = data.items || [];
    } catch (e) {
      el.innerHTML = `<div class="hint">${esc(e.message)}</div>`;
      return;
    }
    if (!items.length) {
      el.innerHTML = '<div class="hint">暂无同义词。可点「导入召回词典」写入领域口语种子，或新建一条。召回只读本表，不读代码内置词典。</div>';
      return;
    }
    const typeLabel = { table: "表", column: "字段", metric: "指标" };
    el.innerHTML = items
      .map(
        (s) => `
      <div class="l1-item" data-synonym-id="${esc(s.synonym_id)}">
        <div class="title">${esc(s.term)} → ${esc(s.target_label || s.target_id)}</div>
        <div class="meta">${esc(typeLabel[s.target_type] || s.target_type)} · ${s.is_enabled ? "启用" : "已禁用"}</div>
      </div>`
      )
      .join("");
    el.querySelectorAll(".l1-item").forEach((node) => {
      node.addEventListener("click", () => {
        const item = items.find((x) => x.synonym_id === node.dataset.synonymId);
        if (item) renderSynonymForm(item);
      });
    });
  }

  async function renderSynonymForm(data = null) {
    await ensureTables();
    const wrap = $("#synonym-form-wrap");
    if (!wrap) return;
    wrap.classList.remove("hidden");
    const d = data || {
      synonym_id: "",
      term: "",
      target_type: "table",
      target_id: l1Tables[0]?.table_id || "",
      table_name: l1Tables[0]?.table_name || "",
      column_name: "",
      is_enabled: true,
    };

    const tableByName = (name) => l1Tables.find((t) => t.table_name === name);
    let selectedTableId = "";
    if (d.target_type === "table") {
      selectedTableId = d.target_id || tableByName(d.table_name)?.table_id || l1Tables[0]?.table_id || "";
    } else if (d.target_type === "column") {
      selectedTableId = tableByName(d.table_name)?.table_id || "";
      if (!selectedTableId && d.target_id) {
        for (const t of l1Tables) {
          const cols = l1ColumnsCache[t.table_id] || [];
          if (cols.some((c) => c.column_id === d.target_id)) {
            selectedTableId = t.table_id;
            break;
          }
        }
      }
      if (!selectedTableId) selectedTableId = l1Tables[0]?.table_id || "";
    }

    let columnOptions = "";
    if (d.target_type === "column" && selectedTableId) {
      const cols = await getColumns(selectedTableId);
      columnOptions = cols
        .map(
          (c) =>
            `<option value="${esc(c.column_id)}"${c.column_id === d.target_id ? " selected" : ""}>${esc(c.column_name)}${c.description ? " · " + esc(c.description) : ""}</option>`
        )
        .join("");
    }

    const tableOptions = l1Tables
      .map(
        (t) =>
          `<option value="${esc(t.table_id)}"${t.table_id === selectedTableId ? " selected" : ""}>${esc(t.table_name)}</option>`
      )
      .join("");

    wrap.innerHTML = `
      <h4>${data ? "编辑" : "新建"}同义词</h4>
      <div class="l1-form-grid">
        <div class="form-field"><label>口语词条 *</label><input id="syn-term" value="${esc(d.term)}" placeholder="如：申请额度、应还、产品名称" /></div>
        <div class="form-field"><label>目标类型</label>
          <select id="syn-type">
            <option value="table"${d.target_type === "table" ? " selected" : ""}>表</option>
            <option value="column"${d.target_type === "column" ? " selected" : ""}>字段</option>
            <option value="metric"${d.target_type === "metric" ? " selected" : ""}>指标</option>
          </select>
        </div>
      </div>
      <div class="form-field" id="syn-table-wrap" style="${d.target_type === "metric" ? "display:none" : ""}">
        <label>${d.target_type === "column" ? "所属表" : "目标表"}</label>
        <select id="syn-table">${tableOptions}</select>
      </div>
      <div class="form-field" id="syn-col-wrap" style="${d.target_type === "column" ? "" : "display:none"}">
        <label>目标字段</label>
        <select id="syn-column">${columnOptions || '<option value="">请先选表</option>'}</select>
      </div>
      <div class="form-field" id="syn-metric-wrap" style="${d.target_type === "metric" ? "" : "display:none"}">
        <label>指标 ID</label>
        <input id="syn-target-manual" type="text" value="${esc(d.target_type === "metric" ? d.target_id : "")}" placeholder="metric_id" />
      </div>
      <div class="form-field"><label>启用</label><input id="syn-enabled" type="checkbox" ${d.is_enabled !== false ? " checked" : ""} /></div>
      <div class="btn-row">
        <button id="syn-save" class="btn primary" type="button">保存</button>
        <button id="syn-save-index" class="btn secondary" type="button">保存并重索引目标</button>
        ${data ? `<button id="syn-del" class="btn secondary" type="button">删除</button>` : ""}
        <button id="syn-cancel" class="btn secondary" type="button">取消</button>
      </div>`;

    const snapshot = () => ({
      synonym_id: d.synonym_id || "",
      term: $("#syn-term")?.value?.trim() || d.term,
      target_type: $("#syn-type")?.value || d.target_type,
      target_id: "",
      table_name: l1Tables.find((t) => t.table_id === $("#syn-table")?.value)?.table_name || "",
      column_name: "",
      is_enabled: $("#syn-enabled")?.checked !== false,
    });

    $("#syn-type")?.addEventListener("change", async () => {
      await renderSynonymForm({ ...snapshot(), target_id: "" });
    });
    $("#syn-table")?.addEventListener("change", async () => {
      if (($("#syn-type")?.value || "") !== "column") return;
      await renderSynonymForm({ ...snapshot(), target_id: "" });
    });

    const buildBody = () => {
      const type = $("#syn-type")?.value || "table";
      let targetId = "";
      if (type === "metric") targetId = $("#syn-target-manual")?.value?.trim() || "";
      else if (type === "column") targetId = $("#syn-column")?.value || "";
      else targetId = $("#syn-table")?.value || "";
      return {
        synonym_id: d.synonym_id || undefined,
        term: $("#syn-term")?.value?.trim(),
        target_type: type,
        target_id: targetId,
        is_enabled: $("#syn-enabled")?.checked !== false,
      };
    };

    const indexTarget = async (body) => {
      if (body.target_type === "table") {
        await runIndex({ types: "table,column", table_ids: [body.target_id] }, "重索引表/字段");
      } else if (body.target_type === "column") {
        await runIndex({ types: "column", column_ids: [body.target_id] }, "重索引字段");
      } else if (body.target_type === "metric") {
        await runIndex({ types: "metric", metric_ids: [body.target_id] }, "重索引指标");
      }
    };

    $("#syn-cancel")?.addEventListener("click", () => wrap.classList.add("hidden"));
    $("#syn-save")?.addEventListener("click", async () => {
      try {
        const body = buildBody();
        if (!body.term || !body.target_id) {
          window.notifyUser?.("请填写词条并选择目标", { variant: "warn", title: "同义词" });
          return;
        }
        await api("/api/l1/synonyms", { method: "POST", body: JSON.stringify(body) });
        wrap.classList.add("hidden");
        await loadSynonyms($("#synonym-filter")?.value || "");
        window.notifyUser?.("已保存，召回将使用该词条", { variant: "ok", title: "同义词" });
      } catch (e) {
        window.notifyError?.(e);
      }
    });
    $("#syn-save-index")?.addEventListener("click", async () => {
      try {
        const body = buildBody();
        if (!body.term || !body.target_id) {
          window.notifyUser?.("请填写词条并选择目标", { variant: "warn", title: "同义词" });
          return;
        }
        await api("/api/l1/synonyms", { method: "POST", body: JSON.stringify(body) });
        wrap.classList.add("hidden");
        await loadSynonyms($("#synonym-filter")?.value || "");
        await indexTarget(body);
      } catch (e) {
        if (!isLoadingCancelled(e)) window.notifyError?.(e);
      }
    });
    $("#syn-del")?.addEventListener("click", async () => {
      if (!confirm("确定删除？")) return;
      try {
        await api(`/api/l1/synonyms/${d.synonym_id}`, { method: "DELETE" });
        wrap.classList.add("hidden");
        await loadSynonyms($("#synonym-filter")?.value || "");
      } catch (e) {
        window.notifyError?.(e);
      }
    });
  }

  // ---------- 知识库 ----------

  async function loadDocuments() {
    const el = $("#doc-list");
    if (!el) return;
    let items;
    try {
      const data = await api("/api/l1/documents");
      items = data.items || [];
    } catch (e) {
      el.innerHTML = `<div class="hint">${esc(e.message)}</div>`;
      return;
    }
    if (!items.length) {
      el.innerHTML = '<div class="hint">暂无文档</div>';
      $("#chunk-hint").textContent = "请先新建文档";
      return;
    }
    el.innerHTML = items
      .map(
        (doc) => `
      <div class="l1-item${doc.doc_id === currentDocId ? " active" : ""}" data-doc-id="${esc(doc.doc_id)}">
        <div class="title">${esc(doc.title)}</div>
        <div class="meta">${esc(doc.doc_type)} · ${doc.chunk_count} 切片${doc.is_enabled ? "" : " · 已禁用"}</div>
      </div>`
      )
      .join("");
    el.querySelectorAll(".l1-item").forEach((node) => {
      node.addEventListener("click", () => selectDocument(node.dataset.docId, items));
    });
  }

  async function selectDocument(docId, docs) {
    currentDocId = docId;
    const doc = (docs || []).find((d) => d.doc_id === docId);
    $("#btn-chunk-new").disabled = false;
    $("#chunk-hint").textContent = doc ? `编辑「${doc.title}」的切片` : "";
    await loadDocuments();
    await loadChunks(docId);
  }

  function renderDocForm(data = null) {
    const wrap = $("#doc-form-wrap");
    if (!wrap) return;
    wrap.classList.remove("hidden");
    const d = data || {
      doc_id: "",
      title: "",
      doc_type: "wiki",
      source_path: "",
      domain: "",
      is_enabled: true,
    };
    wrap.innerHTML = `
      <h4>${data ? "编辑" : "新建"}文档</h4>
      <div class="form-field"><label>标题 *</label><input id="doc-title" value="${esc(d.title)}" /></div>
      <div class="l1-form-grid">
        <div class="form-field"><label>类型</label>
          <select id="doc-type">
            ${["wiki", "faq", "policy", "other"]
              .map((t) => `<option value="${t}"${d.doc_type === t ? " selected" : ""}>${t}</option>`)
              .join("")}
          </select>
        </div>
        <div class="form-field"><label>领域</label><input id="doc-domain" value="${esc(d.domain)}" /></div>
      </div>
      <div class="form-field"><label>来源路径</label><input id="doc-source" value="${esc(d.source_path)}" /></div>
      <div class="form-field"><label>启用</label><input id="doc-enabled" type="checkbox" ${d.is_enabled !== false ? "checked" : ""} /></div>
      <div class="btn-row">
        <button id="doc-save" class="btn primary" type="button">保存</button>
        ${data ? `<button id="doc-del" class="btn secondary" type="button">删除</button>` : ""}
        <button id="doc-cancel" class="btn secondary" type="button">取消</button>
      </div>`;

    const buildBody = () => ({
      doc_id: d.doc_id || undefined,
      title: $("#doc-title")?.value?.trim(),
      doc_type: $("#doc-type")?.value || "wiki",
      source_path: $("#doc-source")?.value?.trim() || "",
      domain: $("#doc-domain")?.value?.trim() || "",
      is_enabled: $("#doc-enabled")?.checked !== false,
    });

    $("#doc-cancel")?.addEventListener("click", () => wrap.classList.add("hidden"));
    $("#doc-save")?.addEventListener("click", async () => {
      try {
        const res = await api("/api/l1/documents", { method: "POST", body: JSON.stringify(buildBody()) });
        wrap.classList.add("hidden");
        currentDocId = res.doc_id;
        await loadDocuments();
        await loadChunks(currentDocId);
      } catch (e) {
        window.notifyError?.(e);
      }
    });
    $("#doc-del")?.addEventListener("click", async () => {
      if (!confirm("删除文档将同时删除所有切片，确定？")) return;
      try {
        await api(`/api/l1/documents/${d.doc_id}`, { method: "DELETE" });
        wrap.classList.add("hidden");
        if (currentDocId === d.doc_id) currentDocId = null;
        await loadDocuments();
        $("#chunk-list").innerHTML = "";
        $("#btn-chunk-new").disabled = true;
      } catch (e) {
        window.notifyError?.(e);
      }
    });
  }

  async function loadChunks(docId) {
    const el = $("#chunk-list");
    if (!el || !docId) {
      if (el) el.innerHTML = "";
      return;
    }
    let items;
    try {
      const data = await api(`/api/l1/documents/${docId}/chunks`);
      items = data.items || [];
    } catch (e) {
      el.innerHTML = `<div class="hint">${esc(e.message)}</div>`;
      return;
    }
    if (!items.length) {
      el.innerHTML = '<div class="hint">暂无切片，点击「新建切片」</div>';
      return;
    }
    el.innerHTML = items
      .map(
        (c) => `
      <div class="l1-item" data-chunk-id="${esc(c.chunk_id)}">
        <div class="title">#${c.chunk_index}</div>
        <div class="meta">${esc((c.content || "").slice(0, 100))}${c.is_enabled ? "" : " · 已禁用"}</div>
      </div>`
      )
      .join("");
    el.querySelectorAll(".l1-item").forEach((node) => {
      node.addEventListener("click", () => {
        const item = items.find((x) => x.chunk_id === node.dataset.chunkId);
        if (item) renderChunkForm(item);
      });
    });
  }

  function renderChunkForm(data = null) {
    const wrap = $("#chunk-form-wrap");
    if (!wrap || !currentDocId) return;
    wrap.classList.remove("hidden");
    const d = data || {
      chunk_id: "",
      doc_id: currentDocId,
      chunk_index: 0,
      content: "",
      is_enabled: true,
    };
    wrap.innerHTML = `
      <h4>${data ? "编辑" : "新建"}切片</h4>
      <div class="l1-form-grid">
        <div class="form-field"><label>序号</label><input id="chk-idx" type="number" min="0" value="${d.chunk_index}" /></div>
        <div class="form-field"><label>启用</label><input id="chk-enabled" type="checkbox" ${d.is_enabled !== false ? "checked" : ""} /></div>
      </div>
      <div class="form-field"><label>内容 *</label><textarea id="chk-content" rows="6">${esc(d.content)}</textarea></div>
      <div class="btn-row">
        <button id="chk-save" class="btn primary" type="button">保存</button>
        <button id="chk-save-index" class="btn secondary" type="button">保存并更新向量</button>
        ${data ? `<button id="chk-del" class="btn secondary" type="button">删除</button>` : ""}
        <button id="chk-cancel" class="btn secondary" type="button">取消</button>
      </div>`;

    const buildBody = () => ({
      chunk_id: d.chunk_id || undefined,
      doc_id: currentDocId,
      chunk_index: parseInt($("#chk-idx")?.value || "0", 10),
      content: $("#chk-content")?.value?.trim(),
      is_enabled: $("#chk-enabled")?.checked !== false,
    });

    $("#chk-cancel")?.addEventListener("click", () => wrap.classList.add("hidden"));
    $("#chk-save")?.addEventListener("click", async () => {
      try {
        await api("/api/l1/chunks", { method: "POST", body: JSON.stringify(buildBody()) });
        wrap.classList.add("hidden");
        await loadDocuments();
        await loadChunks(currentDocId);
      } catch (e) {
        window.notifyError?.(e);
      }
    });
    $("#chk-save-index")?.addEventListener("click", async () => {
      try {
        const res = await api("/api/l1/chunks", { method: "POST", body: JSON.stringify(buildBody()) });
        wrap.classList.add("hidden");
        await loadDocuments();
        await loadChunks(currentDocId);
        await runIndex({ types: "doc_chunk", chunk_ids: [res.chunk_id] }, "更新文档切片向量");
      } catch (e) {
        if (!isLoadingCancelled(e)) window.notifyError?.(e);
      }
    });
    $("#chk-del")?.addEventListener("click", async () => {
      if (!confirm("确定删除此切片？")) return;
      try {
        await api(`/api/l1/chunks/${d.chunk_id}`, { method: "DELETE" });
        wrap.classList.add("hidden");
        await loadDocuments();
        await loadChunks(currentDocId);
      } catch (e) {
        window.notifyError?.(e);
      }
    });
  }

  function bindEvents() {
    document.querySelectorAll(".meta-subtab").forEach((btn) => {
      btn.addEventListener("click", () => showMetaPanel(btn.dataset.metaPanel));
    });
    $("#btn-relation-new")?.addEventListener("click", () => renderRelationForm());
    ensureRelationFilterCombos();
    $("#relation-filter-clear")?.addEventListener("click", () => {
      relationFilterCombos?.left?.setValue("");
      relationFilterCombos?.right?.setValue("");
      renderRelationsList();
    });
    $("#btn-metric-new")?.addEventListener("click", () => renderMetricForm());
    $("#btn-synonym-new")?.addEventListener("click", () => renderSynonymForm());
    $("#btn-synonym-seed")?.addEventListener("click", async () => {
      try {
        const res = await api("/api/l1/synonyms/seed-retrieval", { method: "POST" });
        await loadSynonyms($("#synonym-filter")?.value || "");
        window.notifyUser?.(
          `新增 ${res.inserted || 0} 条，已有 ${res.reused || 0} 条，跳过 ${res.skipped || 0} 条`,
          { variant: "ok", title: "导入召回词典" }
        );
      } catch (e) {
        window.notifyError?.(e);
      }
    });
    $("#synonym-filter")?.addEventListener(
      "input",
      debounce(() => loadSynonyms($("#synonym-filter")?.value || ""), 300)
    );
    $("#btn-doc-new")?.addEventListener("click", () => renderDocForm());
    $("#btn-chunk-new")?.addEventListener("click", () => renderChunkForm());
  }

  function debounce(fn, ms) {
    let t;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), ms);
    };
  }

  async function refreshL1Cache() {
    l1Tables = [];
    l1ColumnsCache = {};
    await ensureTables().catch(() => {});
  }

  async function onTabShow(tab) {
    await refreshL1Cache();
    if (tab === "metadata") {
      if (activeMetaPanel === "relations") await loadRelations();
      if (activeMetaPanel === "metrics") await loadMetrics();
      if (activeMetaPanel === "synonyms") await loadSynonyms($("#synonym-filter")?.value || "");
    }
    if (tab === "knowledge") await loadDocuments();
  }

  window.L1Edit = { onTabShow, refreshL1Cache, showMetaPanel };

  bindEvents();
})();
