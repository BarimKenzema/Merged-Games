function loadGlobal(){
  const raw = localStorage.getItem('hog_global');
  if (raw) { try { return JSON.parse(raw); } catch(e) {} }
  return { hintCharges: 99, highestUnlockedIndex: 0, lastPlayedIndex: 0, completedIds: [], inProgress: {} };
}
function saveGlobal(g){ localStorage.setItem('hog_global', JSON.stringify(g)); }

function saveProgress(){
  const entry = manifest.puzzles[gs.levelIndex];
  if (!entry || !gs.puzzleData) return;
  const g = loadGlobal();
  if (!g.inProgress) g.inProgress = {};
  g.inProgress[entry.puzzle_id] = { found: [...gs.found], lives: gs.lives };
  saveGlobal(g);
}
function clearProgress(puzzleId){
  const g = loadGlobal();
  if (g.inProgress && g.inProgress[puzzleId]) {
    delete g.inProgress[puzzleId];
    saveGlobal(g);
  }
}

// ---------------------------------------------------------------------------
// PUZZLE PACK SUPPORT
//
// Two puzzle sources exist:
//   - "bundled": the ~16 puzzles baked into www/puzzles/ at build time, listed
//     in www/manifest.json, loaded via plain relative fetch()/img paths.
//   - "pack": external puzzle folders placed by the user (via Root Explorer
//     or `adb push`, no in-app download feature) into this app's own
//     external files directory at puzzle_packs/<packName>/, each containing
//     its own self-contained manifest.json (same shape as the bundled one).
//
// The MERGED manifest order is: bundled puzzles first (fixed, permanent),
// then each discovered pack folder in ascending name order, each pack's
// internal order preserved exactly as generated. This order is never
// reshuffled after the fact - new packs are only ever appended - because
// progress tracking (highestUnlockedIndex/lastPlayedIndex) is POSITIONAL
// (an index into this merged array), so silently reordering it would corrupt
// existing players' unlock progress. (completedIds is keyed by puzzle_id
// string, so it's safe either way, but the index-based fields are not.)
// ---------------------------------------------------------------------------

const PACKS_DIR = 'puzzle_packs';
const PACKS_DIRECTORY_TYPE = 'EXTERNAL'; // maps to context.getExternalFilesDir() - no permissions needed

function getFilesystemPlugin(){
  return (window.Capacitor && Capacitor.Plugins && Capacitor.Plugins.Filesystem) || null;
}

async function buildMergedManifest(){
  const result = [];

  const res = await fetch('manifest.json');
  const bundled = await res.json();
  bundled.puzzles.forEach(p => result.push(Object.assign({}, p, { origin: 'bundled' })));

  const fs = getFilesystemPlugin();
  if (fs) {
    let packNames = [];
    try {
      const listing = await fs.readdir({ path: PACKS_DIR, directory: PACKS_DIRECTORY_TYPE });
      packNames = (listing.files || [])
        .map(f => (typeof f === 'string' ? f : f.name))
        .filter(Boolean)
        .sort();
    } catch (e) {
      packNames = []; // puzzle_packs/ doesn't exist yet - totally normal, no packs installed
    }

    for (const packName of packNames) {
      try {
        const manifestPath = `${PACKS_DIR}/${packName}/manifest.json`;
        const readResult = await fs.readFile({ path: manifestPath, directory: PACKS_DIRECTORY_TYPE, encoding: 'utf8' });
        const packManifest = JSON.parse(readResult.data);
        packManifest.puzzles.forEach(p => result.push(Object.assign({}, p, { origin: 'pack', packName })));
      } catch (e) {
        console.warn('Skipping unreadable/invalid pack:', packName, e);
      }
    }
  }

  return { puzzles: result };
}

// Resolves a path that is relative to the PUZZLE-SOURCE ROOT (i.e. relative
// to "puzzles/" for bundled, or relative to "puzzle_packs/<packName>/" for
// a pack) into an actually-loadable URL for <img>/canvas/background-image use.
async function resolveUrl(entry, relativePath){
  if (entry.origin !== 'pack') {
    return `puzzles/${relativePath}`;
  }
  const fs = getFilesystemPlugin();
  if (!entry._packRootUri) {
    const res = await fs.getUri({ path: `${PACKS_DIR}/${entry.packName}`, directory: PACKS_DIRECTORY_TYPE });
    entry._packRootUri = res.uri;
  }
  return Capacitor.convertFileSrc(`${entry._packRootUri}/${relativePath}`);
}

// data.json needs real text content (not just a displayable URL), so it's
// read directly via the Filesystem plugin rather than through convertFileSrc.
async function getDataJson(entry){
  const relPath = `${entry.puzzle_id}/data.json`;
  if (entry.origin !== 'pack') {
    const res = await fetch(`puzzles/${relPath}`);
    return res.json();
  }
  const fs = getFilesystemPlugin();
  const fileRes = await fs.readFile({
    path: `${PACKS_DIR}/${entry.packName}/${relPath}`,
    directory: PACKS_DIRECTORY_TYPE,
    encoding: 'utf8'
  });
  return JSON.parse(fileRes.data);
}

let manifest = null;
let cameFrom = 'menu';
let levelPage = 0;
const PER_PAGE = 8;

const screens = ['screenMenu','screenLevelSelect','screenGame'];
function showScreen(id){
  screens.forEach(s => document.getElementById(s).classList.toggle('hidden', s !== id));
}
function isScreenVisible(id){ return !document.getElementById(id).classList.contains('hidden'); }

async function init(){
  manifest = await buildMergedManifest();
  await setupMenuBackground();
  showScreen('screenMenu');
  setupBackButtonHandling();
}

async function setupMenuBackground(){
  if (!manifest.puzzles.length) return;
  const pick = manifest.puzzles[Math.floor(Math.random()*manifest.puzzles.length)];
  const url = await resolveUrl(pick, pick.background);
  document.getElementById('menuBg').style.backgroundImage = `url(${url})`;
  document.getElementById('menuBgClear').style.backgroundImage = `url(${url})`;
}

document.getElementById('playBtn').addEventListener('click', () => {
  const g = loadGlobal();
  cameFrom = 'menu';
  openGame(Math.min(g.lastPlayedIndex, manifest.puzzles.length-1));
});
document.getElementById('chooseLevelBtn').addEventListener('click', () => { openLevelSelect(); });
document.getElementById('exitBtn').addEventListener('click', () => { confirmExit(); });
document.getElementById('levelBackBtn').addEventListener('click', () => { handleBackNavigation(); });
document.getElementById('gameBackBtn').addEventListener('click', () => { handleBackNavigation(); });

async function openLevelSelect(){
  const g = loadGlobal();
  levelPage = Math.min(levelPage, Math.floor(g.highestUnlockedIndex/PER_PAGE));
  await renderLevelPage();
  showScreen('screenLevelSelect');
}

async function renderLevelPage(){
  const g = loadGlobal();
  const grid = document.getElementById('levelGrid');
  grid.innerHTML = '';
  const startIdx = levelPage * PER_PAGE;
  const maxPage = Math.floor(g.highestUnlockedIndex/PER_PAGE);
  for (let i=0; i<PER_PAGE; i++){
    const idx = startIdx + i;
    if (idx >= manifest.puzzles.length) break;
    const entry = manifest.puzzles[idx];
    const unlocked = idx <= g.highestUnlockedIndex;
    const completed = g.completedIds.includes(entry.puzzle_id);
    const tile = document.createElement('div');
    tile.className = 'levelTile' + (unlocked ? '' : ' locked');
    try {
      const thumbUrl = await resolveUrl(entry, entry.thumbnail);
      tile.style.backgroundImage = `url(${thumbUrl})`;
    } catch (e) {
      console.warn('Failed to resolve thumbnail for', entry.puzzle_id, e);
    }
    tile.innerHTML = unlocked
      ? `<div class="tileLabel">Level ${idx+1}</div>${completed ? '<div class="checkIcon">✔</div>' : ''}`
      : `<div class="lockIcon">🔒</div>`;
    if (unlocked) {
      tile.addEventListener('click', () => { cameFrom='levelSelect'; openGame(idx); });
    }
    grid.appendChild(tile);
  }
  document.getElementById('pageIndicator').textContent = `Page ${levelPage+1} / ${maxPage+1}`;
}

function changeLevelPage(delta){
  const g = loadGlobal();
  const maxPage = Math.floor(g.highestUnlockedIndex/PER_PAGE);
  const newPage = levelPage + delta;
  if (newPage < 0 || newPage > maxPage) return;
  levelPage = newPage;
  renderLevelPage();
}

let lvlTouchStartX=null, lvlTouchStartY=null;
const levelGridWrap = document.getElementById('levelGridWrap');
levelGridWrap.addEventListener('touchstart', e => {
  if (e.touches.length===1){ lvlTouchStartX=e.touches[0].clientX; lvlTouchStartY=e.touches[0].clientY; }
});
levelGridWrap.addEventListener('touchend', e => {
  if (lvlTouchStartX===null) return;
  const dx = e.changedTouches[0].clientX - lvlTouchStartX;
  const dy = e.changedTouches[0].clientY - lvlTouchStartY;
  if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy)*1.5){
    changeLevelPage(dx < 0 ? 1 : -1);
  }
  lvlTouchStartX=null; lvlTouchStartY=null;
});

const gs = {
  levelIndex: 0,
  entry: null,
  puzzleData: null,
  atlasImg: null,
  found: new Set(),
  pending: new Set(),
  lives: 5,
  maxLives: 5
};
let viewState = { scale:1, x:0, y:0 };
let panStart=null, didMove=false;
let touchStartX=0, touchStartY=0;
let lastPinchDist=null, lastPinchMid=null;
let naturalRect = {left:0, top:0, width:0, height:0};
let glowInterval = null;

const canvas = document.getElementById('gameCanvas');
const ctx = canvas.getContext('2d');
const heartsDiv = document.getElementById('hearts');
const trayDiv = document.getElementById('trayRow');
const winOverlay = document.getElementById('winOverlay');
const loseOverlay = document.getElementById('loseOverlay');
const hintBtn = document.getElementById('hintBtn');
const hintCountEl = document.getElementById('hintCount');

async function openGame(index){
  gs.levelIndex = index;
  const g = loadGlobal();
  g.lastPlayedIndex = index;
  saveGlobal(g);

  hideOverlays();
  const entry = manifest.puzzles[index];
  gs.entry = entry;

  try {
    gs.puzzleData = await getDataJson(entry);

    // CONFIRMED FIX: restore in-progress state (found items + remaining lives)
    // if this puzzle was left mid-way via back button / app exit, instead of
    // always resetting to a fresh start.
    const saved = (g.inProgress && g.inProgress[entry.puzzle_id]) || null;
    gs.found = new Set(saved ? saved.found : []);
    gs.pending = new Set();
    gs.lives = saved ? saved.lives : gs.maxLives;

    const bgUrl = await resolveUrl(entry, `${entry.puzzle_id}/${gs.puzzleData.background}`);
    document.getElementById('gameAreaBg').style.backgroundImage = `url(${bgUrl})`;

    const atlasUrl = await resolveUrl(entry, `${entry.puzzle_id}/${gs.puzzleData.atlas}`);
    const img = new Image();
    await new Promise((resolve,reject) => {
      img.onload = resolve; img.onerror = reject;
      img.src = atlasUrl;
    });
    gs.atlasImg = img;
  } catch (e) {
    console.error('Failed to load puzzle', entry, e);
    alert('This puzzle could not be loaded (missing or corrupt files). Please pick another.');
    return;
  }

  showScreen('screenGame');

  canvas.width = gs.puzzleData.canvas_width;
  canvas.height = gs.puzzleData.canvas_height;
  fitCanvas();
  resetView();

  buildHearts();
  updateHearts();
  buildTray();
  updateHintBadge();
  startGlow();
  render();
}

function fitCanvas(){
  const area = document.getElementById('gameArea');
  const scale = Math.min(area.clientWidth/canvas.width, area.clientHeight/canvas.height);
  canvas.style.width = (canvas.width*scale)+'px';
  canvas.style.height = (canvas.height*scale)+'px';
  updateNaturalRect();
}

function updateNaturalRect(){
  const area = document.getElementById('gameArea');
  const areaRect = area.getBoundingClientRect();
  const W = parseFloat(canvas.style.width);
  const H = parseFloat(canvas.style.height);
  naturalRect = {
    left: areaRect.left + (areaRect.width - W) / 2,
    top: areaRect.top + (areaRect.height - H) / 2,
    width: W,
    height: H
  };
}
window.addEventListener('resize', () => { if (isScreenVisible('screenGame')) { fitCanvas(); resetView(); } });

function resetView(){ viewState={scale:1,x:0,y:0}; applyTransform(); }
function applyTransform(){
  canvas.style.transformOrigin='0 0';
  canvas.style.transform = `translate(${viewState.x}px, ${viewState.y}px) scale(${viewState.scale})`;
}
function getTouchDist(t){ const dx=t[0].clientX-t[1].clientX, dy=t[0].clientY-t[1].clientY; return Math.sqrt(dx*dx+dy*dy); }
function getMidpoint(t){ return { x:(t[0].clientX+t[1].clientX)/2, y:(t[0].clientY+t[1].clientY)/2 }; }

function clampAxis(value, areaSize, flexOffset, scaledSize){
  const optionA = areaSize - flexOffset - scaledSize;
  const optionB = -flexOffset;
  const lo = Math.min(optionA, optionB);
  const hi = Math.max(optionA, optionB);
  return Math.max(lo, Math.min(hi, value));
}

function clampPan(){
  const area = document.getElementById('gameArea');
  const areaW = area.clientWidth, areaH = area.clientHeight;
  const W = parseFloat(canvas.style.width);
  const H = parseFloat(canvas.style.height);
  const s = viewState.scale;
  const flexOffsetX = (areaW - W) / 2;
  const flexOffsetY = (areaH - H) / 2;
  const scaledW = W * s, scaledH = H * s;
  if (scaledW <= areaW + 0.5) {
    viewState.x = (areaW - scaledW) / 2 - flexOffsetX;
  } else {
    viewState.x = clampAxis(viewState.x, areaW, flexOffsetX, scaledW);
  }
  if (scaledH <= areaH + 0.5) {
    viewState.y = (areaH - scaledH) / 2 - flexOffsetY;
  } else {
    viewState.y = clampAxis(viewState.y, areaH, flexOffsetY, scaledH);
  }
}

canvas.addEventListener('touchstart', e => {
  e.preventDefault();
  didMove = false;
  if (e.touches.length === 2) {
    lastPinchDist = getTouchDist(e.touches);
    lastPinchMid = getMidpoint(e.touches);
  } else if (e.touches.length === 1) {
    touchStartX = e.touches[0].clientX;
    touchStartY = e.touches[0].clientY;
    panStart = (viewState.scale > 1.01)
      ? { x: e.touches[0].clientX - viewState.x, y: e.touches[0].clientY - viewState.y }
      : null;
  }
}, {passive:false});

canvas.addEventListener('touchmove', e => {
  e.preventDefault();
  if (e.touches.length === 2 && lastPinchDist) {
    didMove = true;
    const newDist = getTouchDist(e.touches);
    const mid = getMidpoint(e.touches);
    const ratio = newDist / lastPinchDist;
    const newScale = Math.max(1, Math.min(4, viewState.scale * ratio));
    const localX = (lastPinchMid.x - naturalRect.left - viewState.x) / viewState.scale;
    const localY = (lastPinchMid.y - naturalRect.top - viewState.y) / viewState.scale;
    viewState.x = mid.x - naturalRect.left - newScale * localX;
    viewState.y = mid.y - naturalRect.top - newScale * localY;
    viewState.scale = newScale;
    lastPinchDist = newDist;
    lastPinchMid = mid;
    clampPan();
    applyTransform();
  } else if (e.touches.length === 1) {
    const dx = e.touches[0].clientX - touchStartX;
    const dy = e.touches[0].clientY - touchStartY;
    if (Math.abs(dx) > 8 || Math.abs(dy) > 8) didMove = true;
    if (panStart) {
      viewState.x = e.touches[0].clientX - panStart.x;
      viewState.y = e.touches[0].clientY - panStart.y;
      clampPan();
      applyTransform();
    }
  }
}, {passive:false});

canvas.addEventListener('touchend', e => {
  e.preventDefault();
  if (!didMove && e.changedTouches.length === 1) {
    const t = e.changedTouches[0];
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width/rect.width, scaleY = canvas.height/rect.height;
    handleClick((t.clientX-rect.left)*scaleX, (t.clientY-rect.top)*scaleY);
  }
  if (e.touches.length === 0) {
    lastPinchDist = null; lastPinchMid = null; panStart = null;
  } else if (e.touches.length === 1) {
    lastPinchDist = null; lastPinchMid = null;
    touchStartX = e.touches[0].clientX; touchStartY = e.touches[0].clientY;
    panStart = (viewState.scale > 1.01)
      ? { x: e.touches[0].clientX - viewState.x, y: e.touches[0].clientY - viewState.y }
      : null;
  }
}, {passive:false});

canvas.addEventListener('click', e => {
  if (didMove){ didMove=false; return; }
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width/rect.width, scaleY = canvas.height/rect.height;
  handleClick((e.clientX-rect.left)*scaleX, (e.clientY-rect.top)*scaleY);
});

function buildHearts(){
  heartsDiv.innerHTML='';
  for (let i=0;i<gs.maxLives;i++){
    const span=document.createElement('span'); span.textContent='❤️'; span.id='heart_'+i;
    heartsDiv.appendChild(span);
  }
}
function updateHearts(){
  for (let i=0;i<gs.maxLives;i++){
    const el=document.getElementById('heart_'+i);
    if (el) el.className = i<gs.lives ? '' : 'lost';
  }
}

function cropToDataUrl(rect){
  const tc=document.createElement('canvas'); tc.width=rect[2]; tc.height=rect[3];
  tc.getContext('2d').drawImage(gs.atlasImg, rect[0], rect[1], rect[2], rect[3], 0, 0, rect[2], rect[3]);
  return tc.toDataURL();
}

function buildTray(){
  trayDiv.innerHTML='';
  // Items already found (restored from saved progress) are built directly
  // into the "found" visual state and placed at the end, so resuming a
  // puzzle looks the same as if you'd found them in-session.
  const notFound = gs.puzzleData.items.filter(it => !gs.found.has(it.index));
  const found = gs.puzzleData.items.filter(it => gs.found.has(it.index));
  [...notFound, ...found].forEach(item => {
    const div=document.createElement('div');
    div.className='trayItem' + (gs.found.has(item.index) ? ' found' : '');
    div.id='tray_'+item.index;
    const rect = item.thumb_rect || item.sprite_rect;
    div.style.backgroundImage = `url(${cropToDataUrl(rect)})`;
    div.addEventListener('click', () => showZoomPreview(item, div));
    trayDiv.appendChild(div);
  });
}

let currentZoomEl = null;
function showZoomPreview(item, trayEl){
  if (currentZoomEl) { currentZoomEl.remove(); currentZoomEl=null; }
  const rect = item.thumb_rect || item.sprite_rect;
  const trayRect = trayEl.getBoundingClientRect();
  const w = trayRect.width*2.5, h = trayRect.height*2.5;
  const el = document.createElement('div');
  el.className='zoomPreview';
  el.style.backgroundImage = `url(${cropToDataUrl(rect)})`;
  el.style.width = w+'px'; el.style.height = h+'px';
  let left = trayRect.left + trayRect.width/2 - w/2;
  left = Math.max(8, Math.min(window.innerWidth-w-8, left));
  el.style.left = left+'px';
  el.style.top = (trayRect.top - h - 12)+'px';
  document.body.appendChild(el);
  currentZoomEl = el;
  setTimeout(() => { if (currentZoomEl===el){ el.remove(); currentZoomEl=null; } }, 1400);
}

function render(){
  if (!gs.atlasImg) return;
  ctx.clearRect(0,0,canvas.width,canvas.height);
  const bg = gs.puzzleData.background_rect;
  ctx.drawImage(gs.atlasImg, bg[0], bg[1], bg[2], bg[3], 0, 0, canvas.width, canvas.height);

  let drawList = [];
  gs.puzzleData.decor.forEach(d => {
    if (d.linked_item_index !== undefined &&
        (gs.found.has(d.linked_item_index) || gs.pending.has(d.linked_item_index))) {
      return;
    }
    drawList.push({type:'decor', zOrder:d.zOrder, data:d});
  });
  gs.puzzleData.items.forEach(it => {
    if (!gs.found.has(it.index) && !gs.pending.has(it.index)) {
      drawList.push({type:'item', zOrder:it.zOrder, data:it});
    }
  });
  drawList.sort((a,b) => a.zOrder-b.zOrder);

  drawList.forEach(entry => {
    const d = entry.data, rect = d.sprite_rect;
    ctx.save();
    ctx.translate(d.x, d.y);
    if (d.rotation) ctx.rotate(d.rotation*Math.PI/180);
    ctx.drawImage(gs.atlasImg, rect[0], rect[1], rect[2], rect[3], -rect[2]/2, -rect[3]/2, rect[2], rect[3]);
    ctx.restore();
  });
}

function pointInPolygon(px,py,poly){
  let inside=false;
  for (let i=0,j=poly.length-1;i<poly.length;j=i++){
    const xi=poly[i].x, yi=poly[i].y, xj=poly[j].x, yj=poly[j].y;
    const intersect = ((yi>py)!==(yj>py)) && (px < (xj-xi)*(py-yi)/(yj-yi)+xi);
    if (intersect) inside=!inside;
  }
  return inside;
}

function handleClick(clickX, clickY){
  const candidates=[];
  for (const item of gs.puzzleData.items){
    if (gs.found.has(item.index) || gs.pending.has(item.index)) continue;
    const rot = item.rotation||0, rad=-rot*Math.PI/180;
    const dx=clickX-item.x, dy=clickY-item.y;
    const lx = dx*Math.cos(rad)-dy*Math.sin(rad);
    const ly = dx*Math.sin(rad)+dy*Math.cos(rad);
    if (pointInPolygon(lx,ly,item.hitbox_polygon)) candidates.push(item);
  }
  if (candidates.length){
    candidates.sort((a,b) => b.zOrder-a.zOrder);
    startFoundAnimation(candidates[0]);
  } else {
    registerMiss();
  }
}

function startFoundAnimation(item){
  gs.pending.add(item.index);
  render();

  const canvasRect = canvas.getBoundingClientRect();
  const scaleX = canvasRect.width/canvas.width, scaleY = canvasRect.height/canvas.height;
  const rect = item.sprite_rect;
  const startW = rect[2]*scaleX, startH = rect[3]*scaleY;
  const startX = canvasRect.left + item.x*scaleX - startW/2;
  const startY = canvasRect.top + item.y*scaleY - startH/2;

  const fly = document.createElement('div');
  fly.className='flyingItem';
  fly.style.backgroundImage = `url(${cropToDataUrl(rect)})`;
  fly.style.left=startX+'px'; fly.style.top=startY+'px';
  fly.style.width=startW+'px'; fly.style.height=startH+'px';
  fly.style.transition = 'all 0.18s ease';
  document.body.appendChild(fly);

  requestAnimationFrame(() => {
    const cx=startX+startW/2, cy=startY+startH/2;
    const bigW=startW*2.5, bigH=startH*2.5;
    fly.style.left=(cx-bigW/2)+'px'; fly.style.top=(cy-bigH/2)+'px';
    fly.style.width=bigW+'px'; fly.style.height=bigH+'px';
  });

  setTimeout(() => {
    fly.style.transition = 'all 0.45s cubic-bezier(.4,0,.2,1)';
    const trayEl = document.getElementById('tray_'+item.index);
    const tr = trayEl ? trayEl.getBoundingClientRect() : {left:window.innerWidth-40, top:window.innerHeight-40, width:40, height:40};
    fly.style.left=tr.left+'px'; fly.style.top=tr.top+'px';
    fly.style.width=tr.width+'px'; fly.style.height=tr.height+'px';
    fly.style.opacity='0.4';
  }, 190);

  setTimeout(() => { fly.remove(); finalizeFound(item.index); }, 190+470);
}

function moveTrayItemToEnd(trayEl){
  const firstRect = trayEl.getBoundingClientRect();
  trayDiv.appendChild(trayEl);
  const lastRect = trayEl.getBoundingClientRect();
  const dx = firstRect.left - lastRect.left;
  if (dx !== 0){
    trayEl.style.transition = 'none';
    trayEl.style.transform = `translateX(${dx}px)`;
    requestAnimationFrame(() => {
      trayEl.style.transition = 'transform 0.35s ease';
      trayEl.style.transform = 'translateX(0)';
    });
  }
}

function finalizeFound(index){
  gs.pending.delete(index);
  gs.found.add(index);
  const trayEl = document.getElementById('tray_'+index);
  if (trayEl) {
    trayEl.classList.add('found');
    moveTrayItemToEnd(trayEl);
  }
  render();
  saveProgress();
  if (gs.found.size === gs.puzzleData.items.length) setTimeout(showWin, 250);
}

function registerMiss(){
  gs.lives--;
  updateHearts();
  saveProgress();
  if (gs.lives<=0) setTimeout(showLose, 200);
}

function showWin(){
  const g = loadGlobal();
  const entry = manifest.puzzles[gs.levelIndex];
  if (!g.completedIds.includes(entry.puzzle_id)) g.completedIds.push(entry.puzzle_id);
  if (gs.levelIndex >= g.highestUnlockedIndex) {
    g.highestUnlockedIndex = Math.min(gs.levelIndex+1, manifest.puzzles.length-1);
  }
  if (gs.lives === gs.maxLives) g.hintCharges = Math.min(99, g.hintCharges+1);
  if (g.inProgress && g.inProgress[entry.puzzle_id]) delete g.inProgress[entry.puzzle_id];
  saveGlobal(g);
  updateHintBadge();
  stopGlow();
  winOverlay.classList.remove('hidden');
}
function showLose(){ stopGlow(); loseOverlay.classList.remove('hidden'); }
function hideOverlays(){ winOverlay.classList.add('hidden'); loseOverlay.classList.add('hidden'); }

document.getElementById('nextBtn').addEventListener('click', () => {
  const next = Math.min(gs.levelIndex+1, manifest.puzzles.length-1);
  openGame(next);
});
document.getElementById('retryBtn').addEventListener('click', () => {
  const entry = manifest.puzzles[gs.levelIndex];
  clearProgress(entry.puzzle_id);
  openGame(gs.levelIndex);
});

function updateHintBadge(){
  const g = loadGlobal();
  hintCountEl.textContent = g.hintCharges;
}
function startGlow(){
  stopGlow();
  glowInterval = setInterval(() => {
    const g = loadGlobal();
    if (g.hintCharges > 0){
      hintBtn.classList.add('glow');
      setTimeout(() => hintBtn.classList.remove('glow'), 1100);
    }
  }, 5000);
}
function stopGlow(){ if (glowInterval){ clearInterval(glowInterval); glowInterval=null; } hintBtn.classList.remove('glow'); }

function screenCenterForItem(item, targetScale){
  const area = document.getElementById('gameArea');
  const areaRect = area.getBoundingClientRect();
  const localX = (item.x/canvas.width) * naturalRect.width;
  const localY = (item.y/canvas.height) * naturalRect.height;
  const areaCenterX = areaRect.left + areaRect.width/2;
  const areaCenterY = areaRect.top + areaRect.height/2;
  return {
    x: areaCenterX - naturalRect.left - targetScale*localX,
    y: areaCenterY - naturalRect.top - targetScale*localY,
    scale: targetScale
  };
}

function showHintRing(item){
  const screenX = naturalRect.left + viewState.x + (item.x/canvas.width) * naturalRect.width * viewState.scale;
  const screenY = naturalRect.top + viewState.y + (item.y/canvas.height) * naturalRect.height * viewState.scale;
  const size = 90;
  const ring = document.createElement('div');
  ring.className = 'hintRing';
  ring.style.left = (screenX - size/2) + 'px';
  ring.style.top = (screenY - size/2) + 'px';
  ring.style.width = size + 'px';
  ring.style.height = size + 'px';
  document.body.appendChild(ring);
  return ring;
}

function useHintOnItem(item){
  const prevView = {x:viewState.x, y:viewState.y, scale:viewState.scale};
  const target = screenCenterForItem(item, 4);

  canvas.style.transition = 'transform 0.4s ease';
  viewState.x = target.x; viewState.y = target.y; viewState.scale = target.scale;
  clampPan();
  applyTransform();

  setTimeout(() => {
    canvas.style.transition = '';
    const ring = showHintRing(item);
    setTimeout(() => {
      ring.remove();
      startFoundAnimation(item);
      setTimeout(() => {
        canvas.style.transition = 'transform 0.4s ease';
        viewState.x = prevView.x; viewState.y = prevView.y; viewState.scale = prevView.scale;
        clampPan();
        applyTransform();
        setTimeout(() => { canvas.style.transition = ''; }, 420);
      }, 700);
    }, 900);
  }, 450);
}

hintBtn.addEventListener('click', () => {
  const g = loadGlobal();
  if (g.hintCharges <= 0) return;
  const remaining = gs.puzzleData.items.filter(it => !gs.found.has(it.index) && !gs.pending.has(it.index));
  if (!remaining.length) return;
  const pick = remaining[Math.floor(Math.random()*remaining.length)];
  g.hintCharges--;
  saveGlobal(g);
  updateHintBadge();
  useHintOnItem(pick);
});

async function handleBackNavigation(){
  if (isScreenVisible('screenGame')) {
    stopGlow();
    if (cameFrom === 'levelSelect') { await openLevelSelect(); }
    else { await setupMenuBackground(); showScreen('screenMenu'); }
    return;
  }
  if (isScreenVisible('screenLevelSelect')) {
    await setupMenuBackground();
    showScreen('screenMenu');
    return;
  }
  confirmExit();
}

function confirmExit(){
  const ok = window.confirm('Exit the game?');
  if (ok) {
    if (window.Capacitor && Capacitor.Plugins && Capacitor.Plugins.App) {
      Capacitor.Plugins.App.exitApp();
    }
  }
}

function setupBackButtonHandling(){
  if (window.Capacitor && Capacitor.Plugins && Capacitor.Plugins.App) {
    Capacitor.Plugins.App.addListener('backButton', handleBackNavigation);
  }
}

init();
