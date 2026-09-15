const state = {
  manifest: null,
  currentIndex: 0,
  puzzleData: null,
  atlasImg: null,
  found: new Set(),
  lives: 5,
  maxLives: 5
};

const canvas = document.getElementById('gameCanvas');
const ctx = canvas.getContext('2d');
const heartsDiv = document.getElementById('hearts');
const puzzleLabel = document.getElementById('puzzleLabel');
const trayDiv = document.getElementById('trayRow');
const winOverlay = document.getElementById('winOverlay');
const loseOverlay = document.getElementById('loseOverlay');
const nextBtn = document.getElementById('nextBtn');
const retryBtn = document.getElementById('retryBtn');

function loadProgress(){
  const raw = localStorage.getItem('hog_progress');
  if (raw) { try { return JSON.parse(raw); } catch(e) {} }
  return { currentIndex: 0, completed: [] };
}
function saveProgress(p){ localStorage.setItem('hog_progress', JSON.stringify(p)); }

async function init(){
  const res = await fetch('manifest.json');
  state.manifest = await res.json();
  const progress = loadProgress();
  state.currentIndex = progress.currentIndex || 0;
  if (state.currentIndex >= state.manifest.puzzles.length) state.currentIndex = 0;
  await loadPuzzle(state.currentIndex);
}

async function loadPuzzle(index){
  hideOverlays();
  const entry = state.manifest.puzzles[index];
  const res = await fetch(`puzzles/${entry.puzzle_id}/data.json`);
  state.puzzleData = await res.json();
  state.found = new Set();
  state.lives = state.maxLives;

  const img = new Image();
  await new Promise((resolve, reject) => {
    img.onload = resolve;
    img.onerror = reject;
    img.src = `puzzles/${entry.puzzle_id}/${state.puzzleData.atlas}`;
  });
  state.atlasImg = img;

  canvas.width = state.puzzleData.canvas_width;
  canvas.height = state.puzzleData.canvas_height;
  fitCanvas();

  puzzleLabel.textContent = `Puzzle ${index+1} / ${state.manifest.puzzles.length}`;
  buildHearts();
  buildTray();
  render();
}

function fitCanvas(){
  const area = document.getElementById('gameArea');
  const maxW = area.clientWidth;
  const maxH = area.clientHeight;
  const scale = Math.min(maxW / canvas.width, maxH / canvas.height);
  canvas.style.width = (canvas.width * scale) + 'px';
  canvas.style.height = (canvas.height * scale) + 'px';
}
window.addEventListener('resize', fitCanvas);

function buildHearts(){
  heartsDiv.innerHTML = '';
  for (let i=0; i<state.maxLives; i++){
    const span = document.createElement('span');
    span.textContent = '❤️';
    span.id = 'heart_' + i;
    heartsDiv.appendChild(span);
  }
}
function updateHearts(){
  for (let i=0; i<state.maxLives; i++){
    const el = document.getElementById('heart_' + i);
    if (el) el.className = i < state.lives ? '' : 'lost';
  }
}

function cropToDataUrl(rect){
  const tc = document.createElement('canvas');
  tc.width = rect[2]; tc.height = rect[3];
  const tctx = tc.getContext('2d');
  tctx.drawImage(state.atlasImg, rect[0], rect[1], rect[2], rect[3], 0, 0, rect[2], rect[3]);
  return tc.toDataURL();
}

function buildTray(){
  trayDiv.innerHTML = '';
  state.puzzleData.items.forEach(item => {
    const div = document.createElement('div');
    div.className = 'trayItem';
    div.id = 'tray_' + item.index;
    const rect = item.thumb_rect || item.sprite_rect;
    div.style.backgroundImage = `url(${cropToDataUrl(rect)})`;
    trayDiv.appendChild(div);
  });
}

function render(){
  ctx.clearRect(0,0,canvas.width,canvas.height);
  const bg = state.puzzleData.background_rect;
  ctx.drawImage(state.atlasImg, bg[0], bg[1], bg[2], bg[3], 0, 0, canvas.width, canvas.height);

  let drawList = [];
  state.puzzleData.decor.forEach(d => drawList.push({type:'decor', zOrder:d.zOrder, data:d}));
  state.puzzleData.items.forEach(it => {
    if (!state.found.has(it.index)) drawList.push({type:'item', zOrder:it.zOrder, data:it});
  });
  drawList.sort((a,b) => a.zOrder - b.zOrder);

  drawList.forEach(entry => {
    const d = entry.data;
    const rect = d.sprite_rect;
    ctx.save();
    ctx.translate(d.x, d.y);
    if (d.rotation) ctx.rotate(d.rotation * Math.PI/180);
    ctx.drawImage(state.atlasImg, rect[0], rect[1], rect[2], rect[3], -rect[2]/2, -rect[3]/2, rect[2], rect[3]);
    ctx.restore();
  });
}

function pointInPolygon(px, py, poly){
  let inside = false;
  for (let i=0, j=poly.length-1; i<poly.length; j=i++){
    const xi=poly[i].x, yi=poly[i].y, xj=poly[j].x, yj=poly[j].y;
    const intersect = ((yi>py) !== (yj>py)) && (px < (xj-xi)*(py-yi)/(yj-yi)+xi);
    if (intersect) inside = !inside;
  }
  return inside;
}

function handleClick(clickX, clickY){
  const candidates = [];
  for (const item of state.puzzleData.items) {
    if (state.found.has(item.index)) continue;
    const rot = item.rotation || 0;
    const rad = -rot * Math.PI/180;
    const dx = clickX - item.x, dy = clickY - item.y;
    const localX = dx*Math.cos(rad) - dy*Math.sin(rad);
    const localY = dx*Math.sin(rad) + dy*Math.cos(rad);
    if (pointInPolygon(localX, localY, item.hitbox_polygon)) {
      candidates.push(item);
    }
  }
  if (candidates.length > 0){
    candidates.sort((a,b) => b.zOrder - a.zOrder);
    markFound(candidates[0].index);
  } else {
    registerMiss();
  }
}

function markFound(index){
  state.found.add(index);
  const trayEl = document.getElementById('tray_' + index);
  if (trayEl) trayEl.classList.add('found');
  render();
  if (state.found.size === state.puzzleData.items.length) {
    setTimeout(showWin, 400);
  }
}

function registerMiss(){
  state.lives--;
  updateHearts();
  if (state.lives <= 0) setTimeout(showLose, 200);
}

function showWin(){
  const progress = loadProgress();
  const entry = state.manifest.puzzles[state.currentIndex];
  if (!progress.completed.includes(entry.puzzle_id)) progress.completed.push(entry.puzzle_id);
  progress.currentIndex = state.currentIndex;
  saveProgress(progress);
  winOverlay.classList.remove('hidden');
}
function showLose(){ loseOverlay.classList.remove('hidden'); }
function hideOverlays(){
  winOverlay.classList.add('hidden');
  loseOverlay.classList.add('hidden');
}

nextBtn.addEventListener('click', async () => {
  state.currentIndex = (state.currentIndex + 1) % state.manifest.puzzles.length;
  const progress = loadProgress();
  progress.currentIndex = state.currentIndex;
  saveProgress(progress);
  await loadPuzzle(state.currentIndex);
});

retryBtn.addEventListener('click', async () => { await loadPuzzle(state.currentIndex); });

canvas.addEventListener('click', (e) => {
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width;
  const scaleY = canvas.height / rect.height;
  const x = (e.clientX - rect.left) * scaleX;
  const y = (e.clientY - rect.top) * scaleY;
  handleClick(x, y);
});

init();
