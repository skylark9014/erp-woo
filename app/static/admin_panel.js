// app/static/admin_panel.js
// ==============================
// JS for ERPNext → WooCommerce Admin Panel
// ==============================

// Utility: Query selector shortcuts
const $ = sel => document.querySelector(sel);
const $$ = sel => Array.from(document.querySelectorAll(sel));

// UI Feedback Utilities
function showAlert(type, message, timeout = 6500) {
    const area = $("#alert-area");
    area.innerHTML = `
    <div class="mb-4 rounded bg-${type === 'error' ? 'red' : 'green'}-100 border border-${type === 'error' ? 'red' : 'green'}-300 px-4 py-3 text-${type === 'error' ? 'red' : 'green'}-900">
      <span>${message}</span>
    </div>
  `;
    if (timeout) setTimeout(() => area.innerHTML = '', timeout);
}

function setProgress(percent, show = true) {
    $("#progress-bar").classList.toggle("hidden", !show);
    $("#progress-bar-inner").style.width = `${percent}%`;
}

// State
let mappingData = [];          // Current mapping file
let originalMappingJSON = '';  // Serialized, for change detection
let mappingEdited = false;

// --- Load Sync Preview Table ---
async function loadSyncPreviewTable() {
    setProgress(10, true);
    $("#sync-preview-tbody").innerHTML = `<tr><td colspan="5" class="text-center text-gray-400 py-8">Running preview...</td></tr>`;
    try {
        const res = await axios.get('/admin/api/preview-sync');
        setProgress(60);
        const rows = res.data.preview || [];
        renderSyncPreviewTable(rows);
        setProgress(100);
        setTimeout(() => setProgress(0, false), 500);
    } catch (e) {
        $("#sync-preview-tbody").innerHTML = `<tr><td colspan="5" class="text-center text-red-500 py-8">Preview failed: ${e.response?.data?.detail || e.message}</td></tr>`;
        setProgress(0, false);
    }
}

function renderSyncPreviewTable(rows) {
    if (!rows.length) {
        $("#sync-preview-tbody").innerHTML = `<tr><td colspan="5" class="text-center text-gray-400 py-8">No changes detected.</td></tr>`;
        return;
    }
    $("#sync-preview-tbody").innerHTML = rows.map(row => `
    <tr>
      <td class="px-4 py-3 text-sm whitespace-nowrap">${esc(row.erp_item_code)}</td>
      <td class="px-4 py-3 text-sm whitespace-nowrap">${esc(row.wc_sku)}</td>
      <td class="px-4 py-3 text-sm whitespace-nowrap font-semibold ${actionClass(row.action)}">${esc(row.action)}</td>
      <td class="px-4 py-3 text-sm whitespace-nowrap">${esc(row.fields_to_update || '').replace(/,/g, ', ')}</td>
      <td class="px-4 py-3 text-sm whitespace-nowrap">${row.images_changed ? 'Yes' : 'No'}</td>
    </tr>
  `).join('');
}

function actionClass(action) {
    if (action === "Create") return "text-green-600";
    if (action === "Update") return "text-yellow-700";
    if (action === "Delete") return "text-red-500";
    return "";
}

// --- Load Mapping Table ---
async function loadMappingTable() {
    setProgress(10, true);
    $("#mapping-tbody").innerHTML = `<tr><td colspan="7" class="text-center text-gray-400 py-8">Loading mapping...</td></tr>`;
    try {
        const res = await axios.get('/admin/api/mapping');
        mappingData = res.data.mapping || [];
        originalMappingJSON = JSON.stringify(mappingData);
        mappingEdited = false;
        $("#save-mapping-btn").disabled = true;
        setProgress(60);
        renderMappingTable(mappingData);
        setProgress(100);
        setTimeout(() => setProgress(0, false), 500);
    } catch (e) {
        $("#mapping-tbody").innerHTML = `<tr><td colspan="7" class="text-center text-red-500 py-8">Failed to load mapping: ${e.response?.data?.detail || e.message}</td></tr>`;
        setProgress(0, false);
    }
}

function renderMappingTable(data) {
    if (!data.length) {
        $("#mapping-tbody").innerHTML = `<tr><td colspan="7" class="text-center text-gray-400 py-8">No mappings available.</td></tr>`;
        return;
    }
    $("#mapping-tbody").innerHTML = data.map((row, idx) => `
    <tr data-row="${idx}">
      <td class="px-4 py-3 text-sm whitespace-nowrap">
        <input class="border border-gray-200 rounded px-2 py-1 w-32 bg-gray-50 mapping-edit"
               data-field="erp_item_code" value="${esc(row.erp_item_code)}" />
      </td>
      <td class="px-4 py-3 text-sm whitespace-nowrap">
        <input class="border border-gray-200 rounded px-2 py-1 w-32 bg-gray-50 mapping-edit"
               data-field="wc_sku" value="${esc(row.wc_sku)}" />
      </td>
      <td class="px-4 py-3 text-sm whitespace-nowrap">${esc(row.wc_product_id)}</td>
      <td class="px-4 py-3 text-sm whitespace-nowrap">${esc(row.status)}</td>
      <td class="px-4 py-3 text-sm whitespace-nowrap">${esc(row.last_synced)}</td>
      <td class="px-4 py-3 text-sm whitespace-nowrap">${esc(row.last_price)}</td>
      <td class="px-4 py-3 text-sm whitespace-nowrap">
        <span class="text-indigo-500 cursor-pointer mapping-edit-row" data-row="${idx}">Edit</span>
      </td>
    </tr>
  `).join('');
    // Attach edit listeners
    $$(".mapping-edit").forEach(input =>
        input.addEventListener("input", onMappingEdit)
    );
}

// --- Table Edit Detection ---
function onMappingEdit() {
    // Save back to mappingData
    const tr = this.closest("tr");
    const rowIdx = parseInt(tr.dataset.row);
    const field = this.dataset.field;
    mappingData[rowIdx][field] = this.value;
    // Enable Save if changed
    mappingEdited = (JSON.stringify(mappingData) !== originalMappingJSON);
    $("#save-mapping-btn").disabled = !mappingEdited;
}

// --- Save Mapping File ---
async function saveMapping() {
    if (!mappingEdited) return;
    setProgress(20, true);
    $("#save-mapping-btn").disabled = true;
    try {
        await axios.post('/admin/api/mapping', { mapping: mappingData });
        showAlert('success', "Mapping saved!");
        // Reload to confirm and lock-in
        await loadMappingTable();
        setProgress(100);
        setTimeout(() => setProgress(0, false), 400);
    } catch (e) {
        showAlert('error', "Failed to save: " + (e.response?.data?.detail || e.message));
        setProgress(0, false);
        $("#save-mapping-btn").disabled = false;
    }
}

// --- Synchronise (Full Sync) ---
async function runSync() {
    setProgress(5, true);
    showAlert('success', "Synchronisation started…", 2000);
    $("#sync-btn").disabled = true;
    try {
        // POST to trigger sync; expect a streaming or batch response
        const res = await axios.post('/admin/api/full-sync', {}, {
            onUploadProgress: p => setProgress(25 + (p.progress || 0) * 50),
            timeout: 120000 // generous timeout for big sync
        });
        setProgress(80);
        showAlert('success', "Sync complete!");
        // Refresh mapping table and preview
        await Promise.all([loadMappingTable(), loadSyncPreviewTable()]);
        setProgress(100);
        setTimeout(() => setProgress(0, false), 700);
    } catch (e) {
        showAlert('error', "Sync failed: " + (e.response?.data?.detail || e.message));
        setProgress(0, false);
    } finally {
        $("#sync-btn").disabled = false;
    }
}

// --- Escape HTML ---
function esc(str) {
    return String(str ?? "")
        .replace(/[&<>"']/g, s =>
            ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[s]
        );
}

// --- DOM Ready Bootstrapping ---
window.addEventListener("DOMContentLoaded", async () => {
    setProgress(0, false);
    await loadMappingTable();
    await loadSyncPreviewTable();

    // Buttons
    $("#sync-btn").onclick = runSync;
    $("#save-mapping-btn").onclick = saveMapping;
    $("#preview-refresh").onclick = loadSyncPreviewTable;
});
