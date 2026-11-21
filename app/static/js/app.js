const state = {
  tab: "library",
  locked: false,
  required: false,
  status: null,
  error: "",
  notice: "",
  keepScroll: false,
  library: {
    path: sessionStorage.getItem("cd-lib") || "",
    kind: "all",
    sort: "modified",
    q: "",
    mode: "browse",
    entries: [],
    total: 0,
    truncated: false,
    crumbs: [],
    recent: [],
    parent: "",
    loading: false,
  },
  organize: {
    mode: "drive",
    path: sessionStorage.getItem("cd-org") || "",
    entries: [],
    total: 0,
    crumbs: [],
    parent: "",
    selected: [],
    albums: [],
    album: null,
    loading: false,
  },
  upload: { batch: null, items: [], running: false, seq: 1, folders: [], draft: "" },
  viewer: null,
  sheet: null,
};

let libraryToken = 0;
let searchTimer = 0;
let touchX = null;
let swallowClick = false;
const removingFolders = new Set();
const swipeGesture = { id: null, x: 0, y: 0, dx: 0, row: null, active: false };

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

function bytes(size) {
  if (size == null) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = size;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  const digits = value >= 10 || index === 0 ? 0 : 1;
  return `${value.toFixed(digits)} ${units[index]}`;
}

function when(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

async function api(url, opts = {}) {
  const options = { credentials: "same-origin", ...opts };
  if (options.body && !(options.body instanceof FormData) && typeof options.body !== "string") {
    options.headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    options.body = JSON.stringify(options.body);
  }
  const response = await fetch(url, options);
  if (response.status === 401) {
    state.locked = true;
    render();
    throw new Error("Enter the access code.");
  }
  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text };
    }
  }
  if (!response.ok) {
    const detail = data && data.detail;
    throw new Error(typeof detail === "string" ? detail : "That did not work.");
  }
  return data;
}

function syncNav() {
  const nav = document.getElementById("nav");
  nav.hidden = state.locked || !!state.viewer;
  nav.querySelectorAll("button").forEach((button) => {
    const on = button.dataset.tab === state.tab;
    button.classList.toggle("on", on);
    if (on) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  const busy = state.library.loading || state.organize.loading || state.upload.running;
  document.body.classList.toggle("busy", !!busy);
}

function render() {
  const app = document.getElementById("app");
  const typing = document.activeElement && document.activeElement.id === "q";
  const results = document.getElementById("results");
  if (results && typing && state.tab === "library" && !state.viewer && !state.sheet) {
    results.innerHTML = libraryBody();
    syncNav();
    return;
  }
  const y = state.keepScroll ? app.scrollTop : 0;
  app.innerHTML = page() + overlay();
  syncNav();
  if (state.keepScroll) app.scrollTop = y;
  state.keepScroll = false;
}

function softRender() {
  state.keepScroll = true;
  render();
}

function banners() {
  let html = "";
  if (state.error) {
    html += `<div class="banner bad"><p>${esc(state.error)}</p><button type="button" data-act="clear-error">Close</button></div>`;
  }
  if (state.notice) {
    html += `<div class="banner good"><p>${esc(state.notice)}</p><button type="button" data-act="clear-notice">Close</button></div>`;
  }
  if (state.status && !state.status.ready) {
    html += `<div class="banner warn"><p>${esc(state.status.detail || "The portable drive is not mounted.")}</p></div>`;
  } else if (state.status && state.status.limited) {
    html += `<div class="banner warn"><p>This folder looks smaller than the portable drive. On the Pi, mount the Seagate disk before starting the container.</p></div>`;
  } else if (state.status && state.status.ready && !state.status.writable) {
    html += `<div class="banner warn"><p>The drive is read-only from here. You can look through it. Saving needs a writable mount.</p></div>`;
  }
  return html;
}

function meter() {
  const status = state.status;
  if (!status || !status.ready || !status.total) return "";
  const used = Math.max(0, Math.min(100, Math.round((status.used / status.total) * 100)));
  return `<div class="meter" aria-hidden="true"><span style="width:${used}%"></span></div>
    <p class="muted">${esc(bytes(status.free))} free of ${esc(bytes(status.total))}</p>`;
}

function page() {
  if (state.locked) return lockScreen();
  if (state.tab === "upload") return uploadScreen();
  if (state.tab === "organize") return organizeScreen();
  return libraryScreen();
}

function lockScreen() {
  return `<section class="lock">
    <div class="brand"><span class="led"></span> Cloud Disk</div>
    <h1>The drive is locked</h1>
    <p class="lede">Enter the access code for this Pi.</p>
    ${banners()}
    <form id="lock-form" class="stack">
      <input class="code" name="code" type="password" autocomplete="current-password" placeholder="Access code" aria-label="Access code">
      <button class="primary" type="submit">Unlock</button>
    </form>
  </section>`;
}

function libraryScreen() {
  const lib = state.library;
  return `<header class="top"><div class="brand"><span class="led"></span> Cloud Disk</div></header>
    ${meter()}
    ${banners()}
    <div class="field"><input id="q" class="search" type="search" placeholder="Search file names" value="${esc(lib.q)}" autocomplete="off" enterkeyhint="search"></div>
    <div class="chips">
      ${chip("all", "All")}${chip("image", "Photos")}${chip("video", "Videos")}${chip("other", "Other")}
    </div>
    ${lib.mode === "browse" ? `<div class="tools"><select id="sort" aria-label="Sort">
      <option value="modified" ${lib.sort === "modified" ? "selected" : ""}>Newest</option>
      <option value="name" ${lib.sort === "name" ? "selected" : ""}>Name</option>
      <option value="size" ${lib.sort === "size" ? "selected" : ""}>Size</option>
    </select></div>` : ""}
    <div id="results">${libraryBody()}</div>`;
}

function chip(kind, label) {
  const on = state.library.kind === kind ? " on" : "";
  return `<button type="button" class="chip${on}" data-act="kind" data-kind="${kind}">${label}</button>`;
}

function crumbs(items, act) {
  if (!items || !items.length) return "";
  return `<div class="crumbs">${items.map((item) => `<button type="button" data-act="${act}" data-path="${esc(item.path)}">${esc(item.name)}</button>`).join("")}</div>`;
}

function libraryBody() {
  const lib = state.library;
  if (lib.loading && !lib.entries.length) return `<p class="muted">Looking through the drive…</p>`;
  const searching = lib.mode === "search";
  let html = "";
  if (searching) {
    html += `<h2>Matches for “${esc(lib.q.trim())}”</h2>`;
    if (lib.truncated) html += `<p class="muted">Showing the first matches. Try more of the name.</p>`;
  } else {
    html += crumbs(lib.crumbs, "open");
    if (lib.path === "Uploads") {
      html += `<p class="muted">Swipe an empty folder to the left, then tap Remove. A folder with anything in it stays.</p>`;
    }
    if (!lib.path && lib.recent && lib.recent.length) {
      html += `<h2>Recent uploads</h2><div class="recent">${lib.recent.map((item) => `<button type="button" data-act="open" data-path="${esc(item.path)}"><b>${esc(item.label || item.name)}</b><span>Upload folder</span></button>`).join("")}</div>`;
    }
  }
  if (!lib.entries.length) {
    html += searching
      ? `<div class="empty"><h2>Nothing matches</h2><p>Try a different part of the file name.</p></div>`
      : `<div class="empty"><h2>Nothing here yet</h2><p>Upload photos from the Upload tab. Choose a folder under Uploads, or start one named with the date and time.</p></div>`;
    return html;
  }
  html += entriesHtml(lib.entries, searching ? "search" : "library");
  if (!searching && lib.entries.length < lib.total) {
    html += `<div class="stack"><button type="button" class="secondary" data-act="more">Show more</button></div>`;
  }
  return html;
}

function entriesHtml(entries, mode) {
  const folders = entries.filter((entry) => entry.kind === "folder");
  const media = entries.filter((entry) => entry.kind === "image" || entry.kind === "video");
  const rest = entries.filter((entry) => entry.kind !== "folder" && entry.kind !== "image" && entry.kind !== "video");
  let html = "";
  if (folders.length) {
    html += `<div class="rows">${folders.map((entry) => folderRow(entry, mode)).join("")}</div>`;
  }
  if (media.length) html += `<div class="grid">${media.map((entry) => shot(entry, mode)).join("")}</div>`;
  if (rest.length) html += `<div class="rows">${rest.map((entry) => fileRow(entry, mode)).join("")}</div>`;
  return html;
}

function canRemoveEmpty(entry, mode) {
  if (!entry || !entry.empty || entry.kind !== "folder") return false;
  if (mode === "search" || mode === "album") return false;
  const parent = mode === "organize" ? state.organize.path : mode === "upload" ? "Uploads" : state.library.path;
  return parent === "Uploads" && entry.path === `Uploads/${entry.name}`;
}

function swipeWrap(entry, inner) {
  return `<div class="swipe" data-path="${esc(entry.path)}"><button type="button" class="swipe-delete" data-act="ask-remove" data-path="${esc(entry.path)}" data-name="${esc(entry.name)}">Remove</button><div class="swipe-face">${inner}</div></div>`;
}

function folderRow(entry, mode) {
  const selected = state.organize.selected.includes(entry.path);
  const removable = canRemoveEmpty(entry, mode);
  const detail = mode === "search" ? entry.path : removable ? "Empty · swipe left" : "Folder";
  const row = mode === "organize"
    ? `<div class="row${selected ? " on" : ""}">
      <button type="button" class="check${selected ? " on" : ""}" data-act="select" data-path="${esc(entry.path)}" aria-label="Select ${esc(entry.name)}"></button>
      <button type="button" class="grow" data-act="org-open" data-path="${esc(entry.path)}"><b>${esc(entry.name)}</b><small>${esc(detail)}</small></button>
    </div>`
    : `<button type="button" class="row" data-act="open" data-path="${esc(entry.path)}"><span class="mark" aria-hidden="true">▣</span><span class="grow"><b>${esc(entry.name)}</b><small>${esc(detail)}</small></span></button>`;
  return removable ? swipeWrap(entry, row) : row;
}

function fileRow(entry, mode) {
  const selected = state.organize.selected.includes(entry.path);
  const meta = mode === "search" ? entry.path : `${entry.kind} · ${bytes(entry.size)}`;
  if (mode === "organize") {
    return `<div class="row${selected ? " on" : ""}">
      <button type="button" class="check${selected ? " on" : ""}" data-act="select" data-path="${esc(entry.path)}" aria-label="Select ${esc(entry.name)}"></button>
      <button type="button" class="grow" data-act="select" data-path="${esc(entry.path)}"><b>${esc(entry.name)}</b><small>${esc(meta)}</small></button>
    </div>`;
  }
  return `<button type="button" class="file" data-act="view" data-path="${esc(entry.path)}"><span class="mark" aria-hidden="true">▸</span><span class="grow"><b>${esc(entry.name)}</b><small>${esc(meta)} · ${esc(when(entry.modified))}</small></span></button>`;
}

function shot(entry, mode) {
  const selected = mode === "organize" && state.organize.selected.includes(entry.path);
  const act = mode === "organize" ? "select" : mode === "album" ? "album-view" : "view";
  const video = entry.kind === "video" ? `<span class="play">Video</span>` : "";
  const check = mode === "organize" ? `<span class="check${selected ? " on" : ""}"></span>` : "";
  const caption = mode === "search" ? `<span class="cap">${esc(entry.name)}</span>` : "";
  return `<button type="button" class="shot${selected ? " on" : ""}" data-act="${act}" data-path="${esc(entry.path)}">
    ${video}${check}${caption}<img alt="${esc(entry.name)}" loading="lazy" src="/api/media?size=thumb&path=${encodeURIComponent(entry.path)}&v=${encodeURIComponent(entry.modified || "")}">
  </button>`;
}

function uploadChoices() {
  const upload = state.upload;
  const chosen = upload.batch;
  const rows = [`<button type="button" class="choice${!chosen ? " on" : ""}" data-act="use-dated"><b>New folder</b><small>Named with the date and time when you save</small></button>`];
  for (const folder of upload.folders || []) {
    const on = chosen && chosen.id === folder.id ? " on" : "";
    const detail = folder.empty ? "Empty · swipe left" : "Folder under Uploads";
    const inner = `<button type="button" class="choice${on}" data-act="use-folder" data-id="${esc(folder.id)}"><b>${esc(folder.label || folder.name)}</b><small>${esc(detail)}</small></button>`;
    rows.push(folder.empty ? swipeWrap(folder, inner) : inner);
  }
  return rows.join("");
}

function uploadScreen() {
  const upload = state.upload;
  const ready = state.status && state.status.ready && state.status.writable;
  const count = upload.items.length;
  const pending = upload.items.some((item) => item.status !== "saved");
  const where = upload.batch ? upload.batch.folder : "a new folder named with the date and time";
  return `<header class="top"><div class="brand"><span class="led"></span> Cloud Disk</div></header>
    <h1>Upload</h1>
    <p class="lede">Save into a folder under Uploads. Photos already on the drive stay where they are.</p>
    ${banners()}
    <h2>Save into</h2>
    <div class="rows">${uploadChoices()}</div>
    <div class="composer">
      <input id="upload-folder-name" value="${esc(upload.draft || "")}" placeholder="New folder name" maxlength="80" autocomplete="off" ${upload.running ? "disabled" : ""}>
      <button type="button" class="secondary" data-act="create-upload-folder" ${ready && !upload.running ? "" : "disabled"}>Create</button>
    </div>
    <p class="muted">Creates the folder under Uploads. Swipe an empty one to the left, then tap Remove.</p>
    <p class="muted">Saving into ${esc(where)}.</p>
    <div class="stack">
      <button type="button" class="primary" data-act="pick" data-which="pick-photos" ${ready && !upload.running ? "" : "disabled"}>Photos & videos</button>
      <button type="button" class="secondary" data-act="pick" data-which="pick-any" ${ready && !upload.running ? "" : "disabled"}>Original files</button>
      <button type="button" class="secondary" data-act="save" ${ready && count && pending && !upload.running ? "" : "disabled"}>Save to the drive</button>
      ${upload.batch ? `<button type="button" class="quiet" data-act="open-batch">Open this folder</button>` : ""}
    </div>
    <input id="pick-photos" class="file-input" type="file" multiple accept="image/*,video/*,.heic,.heif,.jpg,.jpeg,.png,.gif,.webp,.mov,.mp4,.m4v">
    <input id="pick-any" class="file-input" type="file" multiple>
    <p id="upload-live" class="muted" aria-live="polite">${esc(uploadSummary())}</p>
    <div class="queue">${upload.items.map(queueItem).join("")}</div>
    <p class="muted">On an iPhone, choose Photos & videos, then Select, then the pictures. Add another group to the same folder if the picker stops you. iOS sometimes converts HEIC to JPEG in that picker. Original files keeps the camera's files unchanged. Keep this page open until the save finishes.</p>`;
}

function uploadSummary() {
  const items = state.upload.items;
  if (!items.length) return "";
  const saved = items.filter((item) => item.status === "saved").length;
  const current = items.find((item) => item.status === "uploading");
  if (current) return `Saving ${saved + 1} of ${items.length} · ${current.file.name}`;
  const failed = items.filter((item) => item.status === "failed").length;
  if (failed) return `${saved} saved. ${failed} still need another try.`;
  if (saved === items.length) return `Saved ${saved} to the drive.`;
  const total = items.reduce((sum, item) => sum + (item.file.size || 0), 0);
  return `${items.length} ready · ${bytes(total)}`;
}

function queueItem(item) {
  const pct = item.status === "saved" ? 100 : item.total ? Math.round((item.loaded / item.total) * 100) : 0;
  const leave = item.status === "waiting" && !state.upload.running
    ? `<button type="button" class="texty" data-act="queue-drop" data-id="${item.id}">Leave out</button>`
    : "";
  return `<article class="qitem ${esc(item.status)}" data-row="${item.id}">
    <header><b>${esc(item.file.name)}</b>${leave}</header>
    <p class="row-status muted">${esc(itemStatus(item))}</p>
    <div class="bar"><span class="fill" style="width:${pct}%"></span></div>
  </article>`;
}

function itemStatus(item) {
  if (item.status === "saved") return `Saved · ${bytes(item.file.size)}`;
  if (item.status === "failed") return item.error || "Not saved";
  if (item.status === "uploading") return `${bytes(item.loaded)} of ${bytes(item.total || item.file.size)}`;
  return bytes(item.file.size);
}

function organizeScreen() {
  const org = state.organize;
  const driveOn = org.mode === "drive" ? "on" : "";
  const albumOn = org.mode !== "drive" ? "on" : "";
  return `<header class="top"><div class="brand"><span class="led"></span> Cloud Disk</div></header>
    <h1>Organize</h1>
    <p class="lede">Move, copy, and rename. Files stay on the drive.</p>
    ${banners()}
    <div class="seg">
      <button type="button" class="${driveOn}" data-act="org-mode" data-mode="drive">On the drive</button>
      <button type="button" class="${albumOn}" data-act="org-mode" data-mode="albums">Albums</button>
    </div>
    ${org.mode === "drive" ? organizeDrive() : org.mode === "album" ? organizeAlbum() : organizeAlbums()}`;
}

function organizeDrive() {
  const org = state.organize;
  const count = org.selected.length;
  let html = `<div class="inline"><button type="button" class="secondary" data-act="new-folder">New folder</button>
    <button type="button" class="quiet" data-act="select-files">Select files</button></div>`;
  html += crumbs(org.crumbs, "org-open");
  if (org.path === "Uploads") html += `<p class="muted">Swipe an empty folder to the left, then tap Remove. A folder with anything in it stays.</p>`;
  if (org.loading && !org.entries.length) return `${html}<p class="muted">Looking through the drive…</p>`;
  if (!org.entries.length) html += `<div class="empty"><h2>This folder is empty</h2><p>New folders start here. Uploads arrive under Uploads.</p></div>`;
  else html += entriesHtml(org.entries, "organize");
  if (org.entries.length < org.total) html += `<div class="stack"><button type="button" class="secondary" data-act="org-more">Show more</button></div>`;
  if (count) {
    const one = count === 1;
    const selected = org.entries.find((entry) => entry.path === org.selected[0]);
    const file = one && selected && selected.kind !== "folder";
    html += `<div class="tray"><b>${count} selected</b>
      ${file ? `<button type="button" class="quiet" data-act="view-selected">View</button>` : ""}
      ${one ? `<button type="button" class="quiet" data-act="rename">Rename</button>` : ""}
      <button type="button" class="quiet" data-act="dest" data-op="move">Move</button>
      <button type="button" class="quiet" data-act="dest" data-op="copy">Copy</button>
      <button type="button" class="quiet" data-act="add-album">Album</button>
      <button type="button" class="quiet" data-act="clear-select">Clear</button>
    </div>`;
  }
  return html;
}

function organizeAlbums() {
  const albums = state.organize.albums || [];
  let html = `<div class="stack"><button type="button" class="primary" data-act="new-album">New album</button></div>`;
  if (!albums.length) {
    html += `<div class="empty"><h2>No albums yet</h2><p>An album is a list. Adding or removing a photo there does not change the file on the drive.</p></div>`;
  }
  html += albums.map((album) => `<button type="button" class="album" data-act="open-album" data-id="${album.id}">
      ${album.cover ? `<img alt="" src="/api/media?size=thumb&path=${encodeURIComponent(album.cover)}">` : `<span class="ph"></span>`}
      <span><b>${esc(album.name)}</b><small class="muted">${album.count} ${album.count === 1 ? "file" : "files"}</small></span>
    </button>`).join("");
  html += `<div class="stack">
    <a class="secondary" href="/api/backup/albums" style="display:grid;place-items:center;text-decoration:none">Save album list</a>
    <button type="button" class="quiet" data-act="pick" data-which="restore-albums">Restore album list</button>
  </div>
  <input id="restore-albums" class="file-input" type="file" accept="application/json,.json">
  <p class="muted">The album list lives on the Pi, not on the portable drive. Restoring it does not change files on the drive.</p>`;
  return html;
}

function organizeAlbum() {
  const album = state.organize.album;
  if (!album) return `<p class="muted">Opening the album…</p>`;
  const items = album.items || [];
  let html = `<button type="button" class="quiet" data-act="org-mode" data-mode="albums">Albums</button>
    <h2 style="text-transform:none;letter-spacing:-0.03em;color:inherit;font-size:28px">${esc(album.name)}</h2>
    <p class="muted">${items.length} ${items.length === 1 ? "file" : "files"}. Taking one out of the album leaves it on the drive.</p>`;
  if (!items.length) html += `<div class="empty"><h2>Nothing in this album</h2><p>On the drive, select files and choose Album.</p></div>`;
  else html += entriesHtml(items.filter((item) => !item.missing), "album");
  const missing = items.filter((item) => item.missing);
  if (missing.length) {
    html += `<h2>Not at the saved place</h2><div class="rows">${missing.map((item) => `<div class="row"><span class="grow"><b>${esc(item.name)}</b><small>${esc(item.path)}</small></span><button type="button" class="quiet" data-act="detach-one" data-path="${esc(item.path)}">Take out</button></div>`).join("")}</div>`;
  }
  html += `<div class="stack"><button type="button" class="quiet" data-act="remove-album">Remove album</button>
    <p class="muted">Removes this list only. Files stay on the drive.</p></div>`;
  return html;
}

function overlay() {
  return `${viewerHtml()}${sheetHtml()}`;
}

function viewerHtml() {
  if (!state.viewer) return "";
  const item = state.viewer.items[state.viewer.index];
  if (!item) return "";
  const position = `${state.viewer.index + 1} of ${state.viewer.items.length}`;
  let stage = "";
  if (item.kind === "image") {
    stage = `<img alt="${esc(item.name)}" src="/api/media?size=preview&path=${encodeURIComponent(item.path)}&v=${encodeURIComponent(item.modified || "")}">`;
  } else if (item.kind === "video") {
    stage = `<video controls playsinline src="/api/file?path=${encodeURIComponent(item.path)}&download=0"></video>`;
  } else {
    stage = `<div class="filecard"><b>${esc(item.name)}</b><p>${esc(bytes(item.size))}</p><a href="/api/file?path=${encodeURIComponent(item.path)}&download=1">Open</a></div>`;
  }
  return `<div class="viewer">
    <div class="viewer-bar"><button type="button" data-act="viewer-close">Close</button><b>${esc(item.name)}</b><span>${esc(position)}</span></div>
    <div class="stage" id="stage">${stage}</div>
    <div class="viewer-foot">
      <button type="button" data-act="viewer-prev">Prev</button>
      <button type="button" data-act="viewer-next">Next</button>
      <a href="/api/file?path=${encodeURIComponent(item.path)}&download=1">Open</a>
      <button type="button" data-act="show-folder" data-path="${esc(item.path)}">Folder</button>
    </div>
  </div>`;
}

function sheetHtml() {
  const sheet = state.sheet;
  if (!sheet) return "";
  let body = "";
  if (sheet.type === "new-folder" || sheet.type === "new-album" || sheet.type === "rename") {
    const title = sheet.type === "rename" ? "Rename" : sheet.type === "new-album" ? "New album" : "New folder";
    body = `<h2>${title}</h2>
      <div class="field"><input id="sheet-name" value="${esc(sheet.value || "")}" maxlength="80" autocomplete="off"></div>
      <button type="button" class="primary" data-act="sheet-save">${sheet.type === "rename" ? "Rename" : "Create"}</button>`;
  } else if (sheet.type === "dest") {
    const verb = sheet.op === "copy" ? "Copy" : "Move";
    body = `<h2>${verb} here</h2>${crumbs(sheet.crumbs, "sheet-open")}
      <div class="stack"><button type="button" class="primary" data-act="transfer">${verb} here</button>
      <button type="button" class="secondary" data-act="sheet-mkdir">${sheet.creating ? "Cancel new folder" : "New folder here"}</button></div>
      ${sheet.creating ? `<div class="field"><input id="sheet-name" placeholder="Folder name" autocomplete="off"></div><button type="button" class="primary" data-act="sheet-mkdir-save">Create folder</button>` : ""}
      <div>${(sheet.entries || []).map((entry) => `<button type="button" class="pick" data-act="sheet-open" data-path="${esc(entry.path)}">${esc(entry.name)}</button>`).join("") || `<p class="muted">No folders here.</p>`}</div>`;
  } else if (sheet.type === "add-album") {
    body = `<h2>Add to an album</h2>
      <p class="muted">The files stay where they are on the drive.</p>
      ${(sheet.albums || []).map((album) => `<button type="button" class="pick" data-act="album-choose" data-id="${album.id}">${esc(album.name)}</button>`).join("")}
      <div class="field"><input id="sheet-name" placeholder="New album name" autocomplete="off"></div>
      <button type="button" class="primary" data-act="album-create-add">Create and add</button>`;
  } else if (sheet.type === "confirm") {
    body = `<h2>${esc(sheet.title)}</h2><p class="lede">${esc(sheet.body)}</p>
      <div class="stack"><button type="button" class="primary" data-act="confirm-yes">${esc(sheet.yes || "Continue")}</button>
      <button type="button" class="secondary" data-act="sheet-close">Go back</button></div>`;
  }
  return `<div class="backdrop" data-act="sheet-close"><div class="sheet" data-act="noop">${body}</div></div>`;
}

function currentMedia(entries) {
  return entries.filter((entry) => entry.kind === "image" || entry.kind === "video");
}

function openViewer(entries, path) {
  const media = currentMedia(entries);
  let items = media;
  let index = media.findIndex((entry) => entry.path === path);
  if (index < 0) {
    const one = entries.find((entry) => entry.path === path);
    if (!one) return;
    items = [one];
    index = 0;
  }
  state.viewer = { items, index };
  history.pushState({ viewer: 1 }, "");
  render();
}

function closeViewer() {
  if (history.state && history.state.viewer) {
    history.back();
    return;
  }
  state.viewer = null;
  render();
}

function stepViewer(delta) {
  if (!state.viewer) return;
  const next = state.viewer.index + delta;
  if (next < 0 || next >= state.viewer.items.length) return;
  state.viewer.index = next;
  render();
}

async function refreshStatus() {
  try {
    state.status = await api("/api/status");
  } catch (err) {
    state.error = err.message;
  }
}

async function loadLibrary({ append = false } = {}) {
  const lib = state.library;
  const token = ++libraryToken;
  const searching = lib.q.trim().length >= 2;
  if (!append && !searching && !lib.entries.length) {
    lib.loading = true;
    render();
  }
  try {
    let data;
    if (searching) {
      data = await api(`/api/search?${new URLSearchParams({ q: lib.q.trim(), kind: lib.kind })}`);
    } else {
      const offset = append ? lib.entries.length : 0;
      data = await api(`/api/browse?${new URLSearchParams({
        path: lib.path,
        kind: lib.kind,
        sort: lib.sort,
        offset: String(offset),
        limit: "90",
      })}`);
    }
    if (token !== libraryToken) return;
    if (searching) {
      lib.mode = "search";
      lib.entries = data.entries;
      lib.total = data.entries.length;
      lib.truncated = !!data.truncated;
      lib.recent = [];
    } else if (append) {
      lib.mode = "browse";
      lib.entries = lib.entries.concat(data.entries);
      lib.total = data.total;
      lib.crumbs = data.crumbs;
      lib.recent = data.recent || [];
      lib.parent = data.parent;
    } else {
      lib.mode = "browse";
      lib.entries = data.entries;
      lib.total = data.total;
      lib.truncated = false;
      lib.crumbs = data.crumbs;
      lib.recent = data.recent || [];
      lib.parent = data.parent;
    }
    sessionStorage.setItem("cd-lib", lib.path);
  } catch (err) {
    if (token !== libraryToken) return;
    state.error = err.message;
  } finally {
    if (token === libraryToken) {
      lib.loading = false;
      render();
    }
  }
}

async function loadOrganize({ append = false } = {}) {
  const org = state.organize;
  org.loading = !append;
  if (!append) render();
  try {
    if (org.mode === "albums") {
      org.albums = (await api("/api/albums")).albums;
    } else if (org.mode === "album" && org.album) {
      const detail = await api(`/api/albums/${org.album.id}`);
      org.album = detail;
    } else {
      org.mode = "drive";
      const offset = append ? org.entries.length : 0;
      const data = await api(`/api/browse?${new URLSearchParams({
        path: org.path,
        sort: "name",
        offset: String(offset),
        limit: "120",
      })}`);
      org.entries = append ? org.entries.concat(data.entries) : data.entries;
      org.total = data.total;
      org.crumbs = data.crumbs;
      org.parent = data.parent;
      sessionStorage.setItem("cd-org", org.path);
    }
  } catch (err) {
    state.error = err.message;
  } finally {
    org.loading = false;
    if (append) softRender();
    else render();
  }
}

function setTab(tab) {
  state.tab = tab;
  state.notice = "";
  state.sheet = null;
  const next = `#${tab}`;
  if (location.hash !== next) history.replaceState(null, "", next);
  render();
  if (tab === "library") loadLibrary();
  else if (tab === "organize") loadOrganize();
  else {
    refreshStatus().then(render);
    loadUploadFolders().catch((err) => {
      state.error = err.message;
      render();
    });
  }
}

function applyHash() {
  const hash = location.hash || "#library";
  if (hash.startsWith("#album/")) {
    const id = Number(hash.slice(7));
    state.tab = "organize";
    state.organize.mode = "album";
    state.organize.album = { id, name: "", items: [] };
    return;
  }
  const tab = hash.slice(1);
  state.tab = ["upload", "organize"].includes(tab) ? tab : "library";
}

function addFiles(list) {
  const seen = new Set(state.upload.items.map((item) => `${item.file.name}:${item.file.size}:${item.file.lastModified}`));
  for (const file of list) {
    const key = `${file.name}:${file.size}:${file.lastModified}`;
    if (seen.has(key)) continue;
    seen.add(key);
    state.upload.items.push({
      id: state.upload.seq++,
      file,
      status: "waiting",
      loaded: 0,
      total: file.size,
      error: "",
    });
  }
  state.notice = "";
  render();
}

function uploadOne(batchId, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/uploads/batches/${encodeURIComponent(batchId)}/files`);
    xhr.withCredentials = true;
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded, event.total);
    };
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText || "{}"); } catch { /* ignore */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error(typeof data.detail === "string" ? data.detail : "The photo was not saved."));
    };
    xhr.onerror = () => reject(new Error("The upload stopped. Stay on this page and save again."));
    const body = new FormData();
    body.append("file", file, file.name);
    xhr.send(body);
  });
}

function patchItem(item) {
  const row = document.querySelector(`[data-row="${item.id}"]`);
  const live = document.getElementById("upload-live");
  if (live) live.textContent = uploadSummary();
  if (!row) return;
  row.className = `qitem ${item.status}`;
  const fill = row.querySelector(".fill");
  const label = row.querySelector(".row-status");
  const pct = item.status === "saved" ? 100 : item.total ? Math.round((item.loaded / item.total) * 100) : 0;
  if (fill) fill.style.width = `${pct}%`;
  if (label) label.textContent = itemStatus(item);
}

async function runUpload() {
  if (state.upload.running) return;
  const pending = state.upload.items.some((item) => item.status !== "saved");
  if (!pending) return;
  state.upload.running = true;
  state.error = "";
  render();
  let lock = null;
  try {
    lock = await navigator.wakeLock?.request("screen");
  } catch { /* the phone may still sleep */ }
  try {
    if (!state.upload.batch) {
      state.upload.batch = await api("/api/uploads/batches", { method: "POST", body: {} });
      render();
    }
    for (const item of state.upload.items) {
      if (item.status === "saved") continue;
      item.status = "uploading";
      item.loaded = 0;
      item.error = "";
      patchItem(item);
      try {
        item.saved = await uploadOne(state.upload.batch.id, item.file, (loaded, total) => {
          item.loaded = loaded;
          item.total = total || item.file.size;
          patchItem(item);
        });
        item.status = "saved";
      } catch (err) {
        item.status = "failed";
        item.error = err.message;
      }
      patchItem(item);
    }
    const failed = state.upload.items.some((item) => item.status === "failed");
    state.notice = failed ? "Some files still need another try. The ones that finished are on the drive." : "Saved to the drive.";
  } catch (err) {
    state.error = err.message;
  } finally {
    state.upload.running = false;
    try { await lock?.release(); } catch { /* ignore */ }
    try {
      await loadUploadFolders();
    } catch (err) {
      state.error = state.error || err.message;
      render();
    }
  }
}

async function loadSheet(path) {
  const data = await api(`/api/browse?${new URLSearchParams({ path, sort: "name", limit: "240" })}`);
  state.sheet.path = data.path;
  state.sheet.entries = data.entries.filter((entry) => entry.kind === "folder");
  state.sheet.crumbs = data.crumbs;
  state.sheet.parent = data.parent;
  render();
}

function selectedEntries() {
  const wanted = new Set(state.organize.selected);
  return state.organize.entries.filter((entry) => wanted.has(entry.path));
}

async function onClick(event) {
  const node = event.target.closest("[data-act]");
  if (!node || !event.currentTarget.contains(node)) return;
  const act = node.dataset.act;
  if (act === "noop") {
    event.stopPropagation();
    return;
  }
  try {
    if (act === "clear-error") state.error = "";
    else if (act === "clear-notice") state.notice = "";
    else if (act === "kind") {
      state.library.kind = node.dataset.kind;
      state.library.entries = [];
      await loadLibrary();
      return;
    } else if (act === "open") {
      state.library.path = node.dataset.path || "";
      state.library.q = "";
      state.library.entries = [];
      await loadLibrary();
      return;
    } else if (act === "more") {
      state.keepScroll = true;
      await loadLibrary({ append: true });
      return;
    } else if (act === "view") {
      const entries = state.tab === "organize" && state.organize.album
        ? (state.organize.album.items || [])
        : state.library.entries;
      openViewer(entries, node.dataset.path);
      return;
    } else if (act === "album-view") {
      openViewer((state.organize.album && state.organize.album.items) || [], node.dataset.path);
      return;
    } else if (act === "viewer-close") {
      closeViewer();
      return;
    } else if (act === "viewer-prev") stepViewer(-1);
    else if (act === "viewer-next") stepViewer(1);
    else if (act === "show-folder") {
      const path = node.dataset.path || "";
      const parent = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
      state.viewer = null;
      state.tab = "library";
      state.library.path = parent;
      state.library.q = "";
      state.library.entries = [];
      if (history.state && history.state.viewer) history.back();
      await loadLibrary();
      return;
    } else if (act === "pick") {
      document.getElementById(node.dataset.which)?.click();
      return;
    } else if (act === "queue-drop") {
      const id = Number(node.dataset.id);
      state.upload.items = state.upload.items.filter((item) => item.id !== id);
    } else if (act === "save") {
      await runUpload();
      return;
    } else if (act === "use-dated") {
      if (state.upload.running) return;
      state.upload.batch = null;
    } else if (act === "use-folder") {
      if (state.upload.running) return;
      const folder = (state.upload.folders || []).find((item) => item.id === node.dataset.id);
      if (folder) state.upload.batch = folder;
    } else if (act === "create-upload-folder") {
      if (state.upload.running) return;
      const name = document.getElementById("upload-folder-name").value;
      state.upload.draft = name;
      const made = await api("/api/uploads/folders", { method: "POST", body: { name } });
      state.upload.batch = made;
      state.upload.draft = "";
      state.notice = `Created ${made.folder}.`;
      await loadUploadFolders();
      return;
    } else if (act === "ask-remove") {
      const name = node.dataset.name || "this folder";
      state.sheet = {
        type: "confirm",
        title: `Remove ${name}?`,
        body: "This empty folder goes away. Photos and other files stay on the drive.",
        yes: "Remove",
        confirm: "remove-empty",
        path: node.dataset.path,
      };
    } else if (act === "open-batch") {
      if (!state.upload.batch) return;
      state.tab = "library";
      state.library.path = state.upload.batch.folder;
      state.library.q = "";
      state.library.entries = [];
      history.replaceState(null, "", "#library");
      await loadLibrary();
      return;
    } else if (act === "org-mode") {
      state.organize.mode = node.dataset.mode === "albums" ? "albums" : "drive";
      state.organize.album = null;
      state.organize.selected = [];
      history.replaceState(null, "", "#organize");
      await loadOrganize();
      return;
    } else if (act === "org-open") {
      state.organize.path = node.dataset.path || "";
      state.organize.selected = [];
      state.organize.entries = [];
      await loadOrganize();
      return;
    } else if (act === "org-more") {
      await loadOrganize({ append: true });
      return;
    } else if (act === "select") {
      const path = node.dataset.path;
      const index = state.organize.selected.indexOf(path);
      if (index >= 0) state.organize.selected.splice(index, 1);
      else state.organize.selected.push(path);
      softRender();
      return;
    } else if (act === "clear-select") state.organize.selected = [];
    else if (act === "select-files") {
      state.organize.selected = state.organize.entries.filter((entry) => entry.kind !== "folder").map((entry) => entry.path);
    } else if (act === "new-folder") {
      state.sheet = { type: "new-folder", value: "" };
    } else if (act === "rename") {
      const entry = selectedEntries()[0];
      if (!entry) return;
      state.sheet = { type: "rename", value: entry.name, path: entry.path };
    } else if (act === "view-selected") {
      const entry = selectedEntries()[0];
      if (entry) openViewer([entry], entry.path);
      return;
    } else if (act === "dest") {
      state.sheet = { type: "dest", op: node.dataset.op, path: state.organize.path, entries: [], crumbs: [], creating: false };
      render();
      await loadSheet(state.organize.path);
      return;
    } else if (act === "sheet-open") {
      await loadSheet(node.dataset.path || "");
      return;
    } else if (act === "sheet-mkdir") state.sheet.creating = !state.sheet.creating;
    else if (act === "sheet-mkdir-save") {
      const name = document.getElementById("sheet-name").value;
      await api("/api/folders", { method: "POST", body: { parent: state.sheet.path, name } });
      state.sheet.creating = false;
      await loadSheet(state.sheet.path);
      return;
    } else if (act === "transfer") {
      const op = state.sheet.op === "copy" ? "copy" : "move";
      await api(op === "copy" ? "/api/copy" : "/api/move", {
        method: "POST",
        body: { paths: state.organize.selected, destination: state.sheet.path },
      });
      state.notice = op === "copy" ? "Copied." : "Moved.";
      state.organize.selected = [];
      state.sheet = null;
      await loadOrganize();
      return;
    } else if (act === "sheet-save") {
      const name = document.getElementById("sheet-name").value;
      if (state.sheet.type === "new-folder") {
        await api("/api/folders", { method: "POST", body: { parent: state.organize.path, name } });
        state.notice = "Folder created.";
      } else if (state.sheet.type === "new-album") {
        await api("/api/albums", { method: "POST", body: { name } });
        state.notice = "Album created.";
        state.organize.mode = "albums";
      } else if (state.sheet.type === "rename") {
        await api("/api/rename", { method: "POST", body: { path: state.sheet.path, name } });
        state.notice = "Renamed.";
        state.organize.selected = [];
      }
      state.sheet = null;
      await loadOrganize();
      return;
    } else if (act === "add-album") {
      const data = await api("/api/albums");
      state.sheet = { type: "add-album", albums: data.albums };
    } else if (act === "album-choose") {
      await api(`/api/albums/${node.dataset.id}/items`, { method: "POST", body: { paths: fileSelection() } });
      state.notice = "Added to the album. The files stayed where they were.";
      state.sheet = null;
    } else if (act === "album-create-add") {
      const name = document.getElementById("sheet-name").value;
      const album = await api("/api/albums", { method: "POST", body: { name } });
      await api(`/api/albums/${album.id}/items`, { method: "POST", body: { paths: fileSelection() } });
      state.notice = "Added to the new album.";
      state.sheet = null;
    } else if (act === "new-album") state.sheet = { type: "new-album", value: "" };
    else if (act === "open-album") {
      state.organize.mode = "album";
      state.organize.album = { id: Number(node.dataset.id), name: "", items: [] };
      history.replaceState(null, "", `#album/${node.dataset.id}`);
      await loadOrganize();
      return;
    } else if (act === "detach-one") {
      await api(`/api/albums/${state.organize.album.id}/items/detach`, { method: "POST", body: { paths: [node.dataset.path] } });
      state.notice = "Taken out of the album. The file is still on the drive.";
      await loadOrganize();
      return;
    } else if (act === "remove-album") {
      state.sheet = {
        type: "confirm",
        title: "Remove this album?",
        body: "The list goes away. Every file stays on the drive.",
        yes: "Remove album",
        confirm: "remove-album",
      };
    } else if (act === "confirm-yes") {
      const kind = state.sheet.confirm;
      const path = state.sheet.path;
      state.sheet = null;
      if (kind === "remove-empty") {
        await removeEmptyFolder(path);
        return;
      }
      if (kind === "remove-album") {
        await api(`/api/albums/${state.organize.album.id}/remove`, { method: "POST", body: {} });
        state.organize.mode = "albums";
        state.organize.album = null;
        state.notice = "Album removed. Files stayed on the drive.";
        history.replaceState(null, "", "#organize");
        await loadOrganize();
        return;
      }
    } else if (act === "sheet-close") state.sheet = null;
    softRender();
  } catch (err) {
    state.error = err.message;
    render();
  }
}

async function loadUploadFolders() {
  const data = await api("/api/uploads/batches");
  state.upload.folders = data.batches || [];
  if (state.upload.batch) {
    state.upload.batch = state.upload.folders.find((folder) => folder.id === state.upload.batch.id) || null;
  }
  if (state.tab === "upload") render();
}

async function removeEmptyFolder(path) {
  if (!path || removingFolders.has(path)) return;
  removingFolders.add(path);
  try {
    await api("/api/uploads/folders/remove", { method: "POST", body: { path } });
    if (state.upload.batch && (state.upload.batch.folder === path || state.upload.batch.path === path)) {
      state.upload.batch = null;
    }
    state.notice = "Removed the empty folder.";
    state.error = "";
    if (state.tab === "upload") await loadUploadFolders();
    else if (state.tab === "organize") await loadOrganize();
    else await loadLibrary();
  } catch (err) {
    state.error = err.message;
    render();
  } finally {
    removingFolders.delete(path);
  }
}

function closeSwipe(row) {
  if (!row) return;
  row.dataset.open = "";
  row.classList.remove("dragging");
  const face = row.querySelector(".swipe-face");
  if (face) face.style.transform = "";
}

function fileSelection() {
  return selectedEntries().filter((entry) => entry.kind !== "folder").map((entry) => entry.path);
}

document.getElementById("nav").addEventListener("click", (event) => {
  const button = event.target.closest("[data-tab]");
  if (!button || state.locked) return;
  setTab(button.dataset.tab);
});

document.getElementById("app").addEventListener("click", (event) => {
  if (!swallowClick) {
    onClick(event);
    return;
  }
  swallowClick = false;
  event.preventDefault();
  event.stopPropagation();
});
document.getElementById("app").addEventListener("change", async (event) => {
  const target = event.target;
  if (target.id === "pick-photos" || target.id === "pick-any") {
    addFiles(target.files);
    target.value = "";
  } else if (target.id === "sort") {
    state.library.sort = target.value;
    state.library.entries = [];
    await loadLibrary();
  } else if (target.id === "restore-albums" && target.files && target.files[0]) {
    const body = new FormData();
    body.append("file", target.files[0], target.files[0].name);
    try {
      await api("/api/backup/albums", { method: "POST", body });
      state.notice = "Album list restored. Files on the drive were not changed.";
      await loadOrganize();
    } catch (err) {
      state.error = err.message;
      render();
    }
    target.value = "";
  }
});
document.getElementById("app").addEventListener("input", (event) => {
  if (event.target.id === "upload-folder-name") {
    state.upload.draft = event.target.value;
    return;
  }
  if (event.target.id !== "q") return;
  state.library.q = event.target.value;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    const length = state.library.q.trim().length;
    if (length === 0 || length >= 2) loadLibrary();
  }, 250);
});
document.getElementById("app").addEventListener("submit", async (event) => {
  if (event.target.id !== "lock-form") return;
  event.preventDefault();
  try {
    await api("/api/session", { method: "POST", body: { code: String(new FormData(event.target).get("code") || "") } });
    state.locked = false;
    state.error = "";
    await refreshStatus();
    await loadLibrary();
  } catch (err) {
    state.error = err.message;
    state.locked = true;
    render();
  }
});
document.getElementById("app").addEventListener("error", (event) => {
  if (event.target && event.target.tagName === "IMG") event.target.classList.add("broken");
}, true);

const appEl = document.getElementById("app");
appEl.addEventListener("pointerdown", (event) => {
  if (event.button !== 0) return;
  const row = event.target.closest(".swipe");
  if (!row) return;
  const bounds = row.getBoundingClientRect();
  const fromLeft = event.clientX <= bounds.left + bounds.width * 0.45;
  if (!fromLeft && row.dataset.open !== "1") return;
  swipeGesture.id = event.pointerId;
  swipeGesture.x = event.clientX;
  swipeGesture.y = event.clientY;
  swipeGesture.dx = 0;
  swipeGesture.row = row;
  swipeGesture.active = false;
});
appEl.addEventListener("pointermove", (event) => {
  if (event.pointerId !== swipeGesture.id || !swipeGesture.row) return;
  const dx = event.clientX - swipeGesture.x;
  const dy = event.clientY - swipeGesture.y;
  if (!swipeGesture.active) {
    if (Math.abs(dx) < 12 && Math.abs(dy) < 12) return;
    if (dx > -12 || Math.abs(dy) > Math.abs(dx)) {
      swipeGesture.row = null;
      return;
    }
    swipeGesture.active = true;
    swipeGesture.row.classList.add("dragging");
  }
  const next = Math.max(-128, Math.min(0, dx));
  swipeGesture.dx = next;
  const face = swipeGesture.row.querySelector(".swipe-face");
  if (face) face.style.transform = `translateX(${next}px)`;
  if (event.cancelable) event.preventDefault();
}, { passive: false });
appEl.addEventListener("pointerup", (event) => {
  if (event.pointerId !== swipeGesture.id) return;
  const row = swipeGesture.row;
  const dx = swipeGesture.dx;
  const active = swipeGesture.active;
  swipeGesture.row = null;
  swipeGesture.id = null;
  swipeGesture.active = false;
  if (!row) return;
  if (!active) {
    if (row.dataset.open === "1" && !event.target.closest(".swipe-delete")) {
      closeSwipe(row);
      swallowClick = true;
    }
    return;
  }
  swallowClick = true;
  row.classList.remove("dragging");
  const face = row.querySelector(".swipe-face");
  if (dx <= -32 && face) {
    row.dataset.open = "1";
    face.style.transform = "translateX(-112px)";
    return;
  }
  closeSwipe(row);
});
appEl.addEventListener("pointercancel", () => {
  closeSwipe(swipeGesture.row);
  swipeGesture.row = null;
  swipeGesture.id = null;
  swipeGesture.active = false;
});

document.addEventListener("keydown", (event) => {
  if (!state.viewer) return;
  if (event.key === "Escape") closeViewer();
  if (event.key === "ArrowRight") stepViewer(1);
  if (event.key === "ArrowLeft") stepViewer(-1);
});
document.addEventListener("touchstart", (event) => {
  if (!state.viewer) return;
  touchX = event.changedTouches[0].clientX;
}, { passive: true });
document.addEventListener("touchend", (event) => {
  if (!state.viewer || touchX == null) return;
  const dx = event.changedTouches[0].clientX - touchX;
  touchX = null;
  if (Math.abs(dx) < 50) return;
  stepViewer(dx < 0 ? 1 : -1);
}, { passive: true });
window.addEventListener("popstate", () => {
  if (state.viewer) {
    state.viewer = null;
    render();
  }
});
window.addEventListener("beforeunload", (event) => {
  if (!state.upload.running) return;
  event.preventDefault();
  event.returnValue = "";
});

applyHash();
api("/api/session").then(async (session) => {
  state.required = session.required;
  state.locked = session.required && !session.unlocked;
  if (state.locked) {
    render();
    return;
  }
  await refreshStatus();
  if (state.tab === "organize") await loadOrganize();
  else if (state.tab === "upload") await loadUploadFolders();
  else await loadLibrary();
}).catch((err) => {
  state.error = err.message;
  render();
});
