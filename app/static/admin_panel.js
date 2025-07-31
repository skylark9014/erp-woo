// app/static/admin_panel.js

const $ = sel => document.querySelector(sel);
const $$ = sel => Array.from(document.querySelectorAll(sel));

let mappingData = [];
let originalMappingJSON = '';
let mappingEdited = false;
let mappingLastSynced = "";

// --- UI Feedback ---
function showAlert(type, message, timeout = 6500) {
    const area = $("#alert-area");
    area.innerHTML = `<div class="mb-4 rounded ${type === 'error' ? 'bg-red-100 border border-red-300 text-red-900' : 'bg-green-100 border border-green-300 text-green-900'} px-4 py-3">
      <span>${message}</span>
    </div>`;
    if (timeout) setTimeout(() => area.innerHTML = '', timeout);
}

function showSyncStatus(msg) {
    $("#sync-status-message").textContent = msg;
    $("#sync-status-area").classList.remove("hidden");
}
function hideSyncStatus() {
    $("#sync-status-message").textContent = "";
    $("#sync-status-area").classList.add("hidden");
}
function appendSyncLog(msg) {
    const logArea = $("#sync-log-area");
    const entry = document.createElement("div");
    entry.textContent = msg;
    entry.className = "log-entry";
    logArea.appendChild(entry);
    logArea.classList.remove("hidden");
    logArea.scrollTop = logArea.scrollHeight;
}
function clearSyncLog() {
    $("#sync-log-area").innerHTML = "";
    $("#sync-log-area").classList.add("hidden");
}

// --- Load Sync Preview Table ---
async function loadSyncPreviewTable() {
    hideSyncStatus();
    showSyncStatus("Running preview…");
    clearSyncLog();
    appendSyncLog("[info] Preview started");
    $("#sync-preview-tbody").innerHTML = `<tr><td colspan="5" class="text-center text-gray-400 py-8">Running preview...</td></tr>`;
    try {
        const res = await axios.get('/admin/api/preview-sync');
        appendSyncLog("[info] Preview complete");
        const previewObj = res.data.preview || {};
        const rows = []
            .concat(previewObj.to_create || [])
            .concat((previewObj.to_update || []).map(r => r.new || r))
            .concat((previewObj.variant_parents || []).map(r => r.new || r));
        renderSyncPreviewTable(rows);
        setTimeout(() => { hideSyncStatus(); clearSyncLog(); }, 1000);
    } catch (e) {
        appendSyncLog("[error] " + (e.response?.data?.detail || e.message));
        showSyncStatus("Preview failed!");
        setTimeout(() => { hideSyncStatus(); clearSyncLog(); }, 3000);
    }
}

function renderSyncPreviewTable(rows) {
    if (!rows.length) {
        $("#sync-preview-tbody").innerHTML = `<tr><td colspan="5" class="text-center text-gray-400 py-8">No changes detected.</td></tr>`;
        return;
    }
    $("#sync-preview-tbody").innerHTML = rows.map(row => `
    <tr>
      <td>${esc(row.erp_item_code || row.sku || "")}</td>
      <td>${esc(row.sku || row.wc_sku || "")}</td>
      <td>${esc(row.action || (row.fields_to_update || row.image_diff ? "Update" : "No Change"))}</td>
      <td>${esc(row.fields_to_update || "")}</td>
      <td>${row.images_changed ? 'Yes' : (row.image_diff ? 'Yes' : 'No')}</td>
    </tr>
  `).join('');
}

// --- Load Mapping Table ---
async function loadMappingTable() {
    $("#mapping-tbody").innerHTML = `<tr><td colspan="7" class="text-center text-gray-400 py-8">Loading mapping...</td></tr>`;
    try {
        const res = await axios.get('/admin/api/mapping');
        let mappingObj = res.data.mapping;
        let mapping = [];
        mappingLastSynced = "";
        if (mappingObj) {
            if (Array.isArray(mappingObj.products)) {
                mapping = mappingObj.products;
                mappingLastSynced = mappingObj.last_synced || "";
            } else if (Array.isArray(mappingObj)) {
                mapping = mappingObj;
                mappingLastSynced = "";
            }
        }
        mappingData = mapping;
        originalMappingJSON = JSON.stringify(mappingData);
        mappingEdited = false;
        $("#save-mapping-btn").disabled = true;
        renderMappingTable(mappingData, mappingLastSynced);
    } catch (e) {
        $("#mapping-tbody").innerHTML = `<tr><td colspan="7" class="text-center text-red-500 py-8">Failed to load mapping: ${e.response?.data?.detail || e.message}</td></tr>`;
    }
}

function renderMappingTable(data, lastSynced) {
    if (!data.length) {
        $("#mapping-tbody").innerHTML = `<tr><td colspan="7" class="text-center text-gray-400 py-8">No mappings available.</td></tr>`;
        return;
    }
    $("#mapping-tbody").innerHTML = data.map((row, idx) => `
    <tr data-row="${idx}">
      <td>
        <input class="mapping-edit"
               data-field="erp_item_code" value="${esc(row.erp_item_code)}" />
      </td>
      <td>
        <input class="mapping-edit"
               data-field="sku" value="${esc(row.sku || "")}" />
      </td>
      <td>${esc(row.woo_product_id || row.wc_product_id || "")}</td>
      <td>${esc(row.woo_status || row.status || "")}</td>
      <td>${esc(lastSynced)}</td>
      <td>${esc(row.last_price || "")}</td>
    </tr>
  `).join('');
    $$(".mapping-edit").forEach(input =>
        input.addEventListener("input", onMappingEdit)
    );
}

function onMappingEdit() {
    const tr = this.closest("tr");
    const rowIdx = parseInt(tr.dataset.row);
    const field = this.dataset.field;
    mappingData[rowIdx][field] = this.value;
    mappingEdited = (JSON.stringify(mappingData) !== originalMappingJSON);
    $("#save-mapping-btn").disabled = !mappingEdited;
}

async function saveMapping() {
    if (!mappingEdited) return;
    $("#save-mapping-btn").disabled = true;
    try {
        await axios.post('/admin/api/mapping', { mapping: mappingData });
        showAlert('success', "Mapping saved!");
        await loadMappingTable();
    } catch (e) {
        showAlert('error', "Failed to save: " + (e.response?.data?.detail || e.message));
        $("#save-mapping-btn").disabled = false;
    }
}

async function runSync() {
    hideSyncStatus();
    clearSyncLog();
    showSyncStatus("Synchronisation started…");
    appendSyncLog("[info] Synchronisation started");
    $("#sync-btn").disabled = true;
    try {
        appendSyncLog("[info] Sending sync request to server");
        let res = await axios.post('/admin/api/full-sync', {}, { timeout: 120000 });
        showSyncStatus("Sync complete!");
        appendSyncLog("[success] Sync complete.");
        await Promise.all([loadMappingTable(), loadSyncPreviewTable()]);
        setTimeout(() => { hideSyncStatus(); clearSyncLog(); }, 3000);
    } catch (e) {
        showSyncStatus("Sync failed!");
        appendSyncLog("[error] " + (e.response?.data?.detail || e.message));
        setTimeout(() => { hideSyncStatus(); clearSyncLog(); }, 5000);
    } finally {
        $("#sync-btn").disabled = false;
    }
}

function esc(str) {
    return String(str ?? "")
        .replace(/[&<>"']/g, s =>
            ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[s]
        );
}

window.addEventListener("DOMContentLoaded", async () => {
    await loadMappingTable();
    await loadSyncPreviewTable();
    $("#sync-btn").onclick = runSync;
    $("#save-mapping-btn").onclick = saveMapping;
    $("#preview-refresh").onclick = loadSyncPreviewTable;
});
