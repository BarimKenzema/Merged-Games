function loadGlobal(){
  const raw = localStorage.getItem('hog_global');
  if (raw) { try { return JSON.parse(raw); } catch(e) {} }
  return { hintCharges: 3, highestUnlockedIndex: 0, lastPlayedIndex: 0, completedIds: [] };
}
function saveGlobal(g){ localStorage.setItem('hog_global', JSON.stringify(g)); }

let manifest = null;
let cameFrom = 'menu';
let levelPage = 0;
const PER_PAGE = 16;

const screens = ['screenMenu','screenLevelSelect','screenGame'];
function showScreen(id){
  screens.forEach(s => document.getElementById(s).classList.toggle('hidden', s !== id));
}
function isScreenVisible(id){ return !document.getElementById(id).classList.contains('hidden'); }

async function init(){
  const res = await fetch('manifest.json');
  manifest = await res.json();
  setupMenuBackground();
  showScreen('screenMenu');
  setupBackButtonHandling();
}

function setupMenuBackground(){
  if (!manifest.puzzles.length) return;
  const pick = manifest.puzzles[Math.floor(Math.random()*manifest.puzzles.length)];
  const url = `puzzles/${pick.background}`;
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

function openLevelSelect(){
  const g = loadGlobal();
  levelPage = Math.min(levelPage, Math.floor(g.highestUnlockedIndex/PER_PAGE));
  renderLevelPage();
  showScreen('screenLevelSelect');
}

function renderLevelPage(){
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
    tile.style.backgroundImage = `url(puzzles/${entry.thumbnail})`;
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
  puzzleData: null,
  atlasImg: null,
  found: new Set(),
  pending: new Set(),
  lives: 5,
  maxLives: 5
};
let viewState = { scale:1, x:0, y:0 };
let touchStartDist=null, touchStartScale=1, panStart=null, didMove=false;
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
  const res = await fetch(`puzzles/${entry.puzzle_id}/data.json`);
  gs.puzzleData = await res.json();
  gs.found = new Set();
  gs.pending = new Set();
  gs.lives = gs.maxLives;

  const img = new Image();
  await new Promise((resolve,reject) => {
    img.onload = resolve; img.onerror = reject;
    img.src = `puzzles/${entry.puzzle_id}/${gs.puzzleData.atlas}`;
  });
  gs.atlasImg = img;

  // IMPORTANT: make the game screen visible BEFORE measuring/sizing the canvas,
  // otherwise gameArea has 0 width/height and the canvas collapses to nothing.
  showScreen('screenGame');

  canvas.width = gs.puzzleData.canvas_width;
  canvas.height = gs.puzzleData.canvas_height;
  fitCanvas();
  resetView();

  buildHearts();
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
}
window.addEventListener('resize', () => { if (isScreenVisible('screenGame')) { fitCanvas(); resetView(); } });

function resetView(){ viewState={scale:1,x:0,y:0}; applyTransform(); }
function applyTransform(){
  canvas.style.transformOrigin='0 0';
  canvas.style.transform = `translate(${viewState.x}px, ${viewState.y}px) scale(${viewState.scale})`;
}
function getTouchDist(t){ const dx=t[0].clientX-t[1].clientX, dy=t[0].clientY-t[1].clientY; return Math.sqrt(dx*dx+dy*dy); }

// Clamps viewState.x/y so the zoomed canvas can never drift off past its own
// content edges, and automatically re-centers when the (possibly zoomed-out)
// content is smaller than the viewport on a given axis. This fixes the bug
// where zooming back out left the view permanently off-center.
function clampPan(){
  const area = document.getElementById('gameArea');
  const areaW = area.clientWidth, areaH = area.clientHeight;
  const W = parseFloat(canvas.style.width);
  const H = parseFloat(canvas.style.height);
  const s = viewState.scale;
  // gameArea centers the unscaled canvas via flexbox; this is that natural offset.
  const flexOffsetX = (areaW - W) / 2;
  const flexOffsetY = (areaH - H) / 2;
  const scaledW = W * s, scaledH = H * s;

  if (scaledW <= areaW) {
    viewState.x = (areaW - scaledW) / 2 - flexOffsetX;
  } else {
    const minX = areaW - flexOffsetX - scaledW;
    const maxX = -flexOffsetX;
    viewState.x = Math.max(minX, Math.min(maxX, viewState.x));
  }
  if (scaledH <= areaH) {
    viewState.y = (areaH - scaledH) / 2 - flexOffsetY;
  } else {
    const minY = areaH - flexOffsetY - scaledH;
    const maxY = -flexOffsetY;
    viewState.y = Math.max(minY, Math.min(maxY, viewState.y));
  }
}

canvas.addEventListener('touchstart', e => {
  didMove=false;
  if (e.touches.length===2){ touchStartDist=getTouchDist(e.touches); touchStartScale=viewState.scale; }
  else if (e.touches.length===1){ panStart={x:e.touches[0].clientX-viewState.x, y:e.touches[0].clientY-viewState.y}; }
}, {passive:true});
canvas.addEventListener('touchmove', e => {
  didMove=true;
  if (e.touches.length===2 && touchStartDist){
    let s = touchStartScale*(getTouchDist(e.touches)/touchStartDist);
    viewState.scale = Math.max(1, Math.min(4,s));
    clampPan();
    applyTransform();
  } else if (e.touches.length===1 && panStart && viewState.scale>1){
    viewState.x = e.touches[0].clientX - panStart.x;
    viewState.y = e.touches[0].clientY - panStart.y;
    clampPan();
    applyTransform();
  }
}, {passive:true});
canvas.addEventListener('touchend', e => { if (e.touches.length===0){ touchStartDist=null; panStart=null; } });

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
  gs.puzzleData.items.forEach(item => {
    const div=document.createElement('div');
    div.className='trayItem'; div.id='tray_'+item.index;
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
  gs.puzzleData.decor.forEach(d => drawList.push({type:'decor', zOrder:d.zOrder, data:d}));
  gs.puzzleData.items.forEach(it => {
    if (!gs.found.has(it.index) && !gs.pending.has(it.index)) drawList.push({type:'item', zOrder:it.zOrder, data:it});
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

// Moves a tray item to the end of the tray row with a smooth slide animation
// (FLIP technique: record position before the DOM move, then animate away
// the visual jump). Makes found items settle at the end so remaining items
// are easier to scan.
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
  if (gs.found.size === gs.puzzleData.items.length) setTimeout(showWin, 250);
}

function registerMiss(){
  gs.lives--;
  updateHearts();
  if (gs.lives<=0) setTimeout(showLose, 200);
}

function showWin(){
  const g = loadGlobal();
  const entry = manifest.puzzles[gs.levelIndex];
  if (!g.completedIds.includes(entry.puzzle_id)) g.completedIds.push(entry.puzzle_id);
  if (gs.levelIndex >= g.highestUnlockedIndex) {
    g.highestUnlockedIndex = Math.min(gs.levelIndex+1, manifest.puzzles.length-1);
  }
  if (gs.lives === gs.maxLives) g.hintCharges = Math.min(9, g.hintCharges+1);
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
document.getElementById('retryBtn').addEventListener('click', () => { openGame(gs.levelIndex); });

canvas.addEventListener('click', e => {
  if (didMove){ didMove=false; return; }
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width/rect.width, scaleY = canvas.height/rect.height;
  handleClick((e.clientX-rect.left)*scaleX, (e.clientY-rect.top)*scaleY);
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

hintBtn.addEventListener('click', () => {
  const g = loadGlobal();
  if (g.hintCharges <= 0) return;
  const remaining = gs.puzzleData.items.filter(it => !gs.found.has(it.index) && !gs.pending.has(it.index));
  if (!remaining.length) return;
  const pick = remaining[Math.floor(Math.random()*remaining.length)];
  g.hintCharges--;
  saveGlobal(g);
  updateHintBadge();
  startFoundAnimation(pick);
});

// ---------------- BACK BUTTON / EXIT HANDLING ----------------
function handleBackNavigation(){
  if (isScreenVisible('screenGame')) {
    stopGlow();
    if (cameFrom === 'levelSelect') openLevelSelect();
    else { setupMenuBackground(); showScreen('screenMenu'); }
    return;
  }
  if (isScreenVisible('screenLevelSelect')) {
    setupMenuBackground();
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
