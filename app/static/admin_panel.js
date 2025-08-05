// Admin Panel JS for ERPNext → WooCommerce
const $ = sel => document.querySelector(sel);
const $$ = sel => Array.from(document.querySelectorAll(sel));

// --- Overlay / Spinner ---
function showOverlay(msg = "Loading…") {
    const overlay = $("#overlay");
    if (overlay) {
        overlay.querySelector(".message")?.textContent !== undefined && (overlay.querySelector(".message").textContent = msg);
        overlay.classList.remove("hidden");
    }
}
function hideOverlay() {
    const overlay = $("#overlay");
    overlay?.classList.add("hidden");
}

// --- Alerts ---
function showAlert(msg, type = "info", timeout = 4000) {
    const area = $("#alert-area");
    if (!area) return;
    area.innerHTML = `<div class="alert ${type}">${msg}</div>`;
    if (timeout) setTimeout(() => { area.innerHTML = ""; }, timeout);
}

// --- Sync Preview Table ---
async function loadSyncPreviewTable() {
    showOverlay("Running preview…");
    const tbody = $("#sync-preview-tbody");
    tbody.innerHTML = `<tr><td colspan="5" class="centered">&nbsp;&nbsp;Running preview...</td></tr>`;
    try {
        const res = await axios.get('/admin/api/preview-sync');
        const previewObj = res.data.preview || {};
        // Only products requiring sync
        const rows = []
            .concat(previewObj.to_create || [])
            .concat(previewObj.to_update || [])
            .concat(previewObj.variant_parents || [])
            .concat(previewObj.variant_to_create || [])
            .concat(previewObj.variant_to_update || []);
        renderSyncPreviewTable(rows);
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="5" class="centered">&nbsp;&nbsp;Preview failed: ${esc(e.message)}</td></tr>`;
        showAlert("Sync preview failed: " + e.message, "error");
    }
    hideOverlay();
}

// --- Key UI rendering fix below ---
function renderSyncPreviewTable(rows) {
    const tbody = $("#sync-preview-tbody");
    if (!rows.length) {
        tbody.innerHTML = `<tr><td colspan="5" class="centered">&nbsp;&nbsp;No changes detected.</td></tr>`;
        return;
    }
    tbody.innerHTML = rows.map(row => {
        // Prefer ERP code, fallback to Woo name, then SKU.
        let erpItem =
            row.erp_item_code
            || row.item_code
            || (row.current && (row.current.erp_item_code || row.current.item_code))
            || row.name
            || (row.current && row.current.name)
            || "";

        let wcSku =
            row.sku
            || (row.current && row.current.sku)
            || row.wc_sku
            || "";

        let action = (row.action || "").toLowerCase();
        if (action === "create") action = "Add";
        else if (action === "delete") action = "Delete";
        else if (action === "update") action = "Update";
        else action = "Update"; // fallback

        let fieldsToUpdate = "";
        if (Array.isArray(row.fields_changed) && row.fields_changed.length > 0) {
            fieldsToUpdate = row.fields_changed.join(", ");
        } else if (row.fields_to_update) {
            fieldsToUpdate = row.fields_to_update;
        } else if (row.fields_diff && typeof row.fields_diff === "object") {
            fieldsToUpdate = Object.keys(row.fields_diff).join(", ");
        }

        let imagesChanged = (row.images_changed || row.image_diff) ? "Yes" : "No";

        return `
            <tr>
              <td>${esc(erpItem)}</td>
              <td>${esc(wcSku)}</td>
              <td>${esc(action)}</td>
              <td>${esc(fieldsToUpdate)}</td>
              <td>${imagesChanged}</td>
            </tr>
        `;
    }).join('');
}


// --- Product Mapping Table ---
let mappingData = [];
let mappingEdited = false;
let mappingLastSynced = "";

async function loadMappingTable() {
    showOverlay();
    const tbody = $("#mapping-tbody");
    tbody.innerHTML = `<tr><td colspan="5" class="centered">&nbsp;&nbsp;Loading mapping...</td></tr>`;
    try {
        const res = await axios.get('/admin/api/mapping');
        const mappingObj = res.data.mapping;
        let mapping = [];
        mappingLastSynced = "";
        if (mappingObj) {
            if (Array.isArray(mappingObj.products)) {
                mapping = mappingObj.products;
                mappingLastSynced = mappingObj.last_synced || "";
            } else if (Array.isArray(mappingObj)) {
                mapping = mappingObj;
            }
        }
        mappingData = mapping;
        mappingEdited = false;
        renderMappingTable(mappingData, mappingLastSynced);
        $("#save-mapping-btn").disabled = true;
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="5" class="centered">&nbsp;&nbsp;Failed to load mapping: ${esc(e.message)}</td></tr>`;
        showAlert("Failed to load mapping: " + e.message, "error");
    }
    hideOverlay();
}

function renderMappingTable(data, lastSynced) {
    const tbody = $("#mapping-tbody");
    if (!data.length) {
        tbody.innerHTML = `<tr><td colspan="5" class="centered">&nbsp;&nbsp;No mappings available.</td></tr>`;
        return;
    }
    tbody.innerHTML = data.map((row, idx) => `
        <tr data-row="${idx}">
          <td><input type="text" class="mapping-edit" data-field="erp_item_code" value="${esc(row.erp_item_code)}" /></td>
          <td><input type="text" class="mapping-edit" data-field="sku" value="${esc(row.sku || "")}" /></td>
          <td>${esc(row.woo_product_id || row.wc_product_id || "")}</td>
          <td>${esc(row.woo_status || row.status || "")}</td>
          <td>${esc(lastSynced)}</td>
        </tr>
    `).join('');
    $$(".mapping-edit").forEach(i => i.addEventListener("input", onMappingEdit));
}

function onMappingEdit() {
    const tr = this.closest("tr");
    const idx = parseInt(tr.dataset.row, 10);
    const field = this.dataset.field;
    mappingData[idx][field] = this.value;
    mappingEdited = true;
    $("#save-mapping-btn").disabled = !mappingEdited;
}

async function saveMapping() {
    if (!mappingEdited) return;
    showOverlay();
    $("#save-mapping-btn").disabled = true;
    try {
        await axios.post('/admin/api/mapping', { mapping: mappingData });
        showAlert('Mapping saved!', "success");
        await loadMappingTable();
    } catch (e) {
        showAlert("Failed to save mapping: " + e.message, "error");
        $("#save-mapping-btn").disabled = false;
    }
    mappingEdited = false;
    hideOverlay();
}

// --- Stock Adjustment Table ---
let stockData = [];
let stockEdited = false;

async function loadStockTable() {
    showOverlay();
    const tbody = $("#stock-tbody");
    tbody.innerHTML = `<tr><td colspan="3" class="centered">&nbsp;&nbsp;Loading stock adjustments...</td></tr>`;
    try {
        const res = await axios.get('/admin/api/stock-adjustment');
        const obj = res.data.stock_adjustment || {};
        stockData = Array.isArray(obj.stock) ? obj.stock : [];
        renderStockTable(stockData);
        $("#apply-stock-btn").disabled = true;
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="3" class="centered">&nbsp;&nbsp;Failed to load stock: ${esc(e.message)}</td></tr>`;
        showAlert("Failed to load stock adjustments: " + e.message, "error");
    }
    hideOverlay();
}

function renderStockTable(data) {
    const tbody = $("#stock-tbody");
    if (!data.length) {
        tbody.innerHTML = `<tr><td colspan="3" class="centered">&nbsp;&nbsp;No stock data.</td></tr>`;
        return;
    }
    tbody.innerHTML = data.map((row, idx) => `
        <tr data-row="${idx}">
          <td>${esc(row.item_code)}</td>
          <td>${esc(row.t_warehouse)}</td>
          <td><input type="number" class="stock-edit" data-field="qty" value="${esc(row.qty)}" /></td>
        </tr>
    `).join('');
    $$(".stock-edit").forEach(i => i.addEventListener("input", onStockEdit));
}

function onStockEdit() {
    const tr = this.closest("tr");
    const idx = parseInt(tr.dataset.row, 10);
    let v = parseFloat(this.value);
    if (Number.isNaN(v)) v = 0;
    stockData[idx][this.dataset.field] = v;
    stockEdited = true;
    $("#apply-stock-btn").disabled = !stockEdited;
}

async function saveStock() {
    if (!stockEdited) return;
    showOverlay();
    $("#apply-stock-btn").disabled = true;
    try {
        await axios.post('/admin/api/stock-adjustment', { stock: stockData });
        showAlert('Stock adjustments saved!', "success");
        await loadStockTable();
    } catch (e) {
        showAlert("Failed to save stock: " + e.message, "error");
        $("#apply-stock-btn").disabled = false;
    }
    stockEdited = false;
    hideOverlay();
}

async function applyStockAdjustment() {
    showOverlay("Updating stock in ERPNext…");
    try {
        await saveStock();
        await axios.post('/admin/api/apply-stock-adjustment');
        showAlert('Stock successfully updated in ERPNext!', "success");
        await loadStockTable();
    } catch (e) {
        showAlert("Failed to update ERPNext stock: " + e.message, "error");
    }
    hideOverlay();
}

function esc(s) {
    return String(s ?? "").replace(/[&<">']/g, c =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', '\'': '&#39;' })[c]
    );
}

window.addEventListener("DOMContentLoaded", async () => {
    await loadSyncPreviewTable();
    await loadMappingTable();
    await loadStockTable();
    $("#sync-btn").onclick = async () => { showOverlay("Synchronising…"); try { await axios.post('/admin/api/full-sync'); showAlert("Sync complete!", "success"); await loadSyncPreviewTable(); await loadMappingTable(); } catch (e) { showAlert("Sync failed: " + e.message, "error"); } hideOverlay(); };
    $("#save-mapping-btn").onclick = saveMapping;
    $("#preview-refresh").onclick = loadSyncPreviewTable;
    $("#apply-stock-btn").onclick = applyStockAdjustment;
    $("#partial-sync-btn").onclick = async () => {
        showOverlay("Partial synchronising…");
        try {
            await axios.post('/admin/api/partial-sync');
            showAlert("Partial sync complete!", "success");
            await loadSyncPreviewTable();
            await loadMappingTable();
        } catch (e) {
            showAlert("Partial sync failed: " + e.message, "error");
        }
        hideOverlay();
    };
});
