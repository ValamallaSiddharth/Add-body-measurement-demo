'use strict';
const $ = id => document.getElementById(id);
const photos = {front: null, side: null};
const previewURLs = {};
let busy = false;
let historyRequest = 0;
const isDemo = () => $('mode').value === 'demo';
function showError(message) { $('error').textContent = message; $('error').hidden = !message; }
async function request(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error(data?.error || data?.detail || `Server error (${response.status}). Check the app terminal.`);
  if (!data) throw new Error('The server returned an unreadable response.');
  return data;
}
function selectPhoto(view, input) {
  if (!input.files?.length) return;
  const file = input.files[0];
  if (previewURLs[view]) URL.revokeObjectURL(previewURLs[view]);
  photos[view] = null; $(view+'-preview').hidden = true;
  if (file.size > 15000000 || !/\.(jpe?g|png|webp)$/i.test(file.name)) {
    input.value = ''; $(view+'-name').textContent = 'No valid photo selected';
    showError('Choose JPEG, PNG or WebP, up to 15 MB. Convert HEIC photos to JPEG first.'); return;
  }
  photos[view] = file; previewURLs[view] = URL.createObjectURL(file);
  $(view+'-preview').src = previewURLs[view]; $(view+'-preview').hidden = false;
  $(view+'-name').textContent = `${file.name} · ${(file.size/1000000).toFixed(1)} MB`;
  showError(''); $('result').hidden = true;
}
for (const view of ['front','side']) {
  for (const source of ['camera','file']) $(view+'-'+source).addEventListener('change', e => selectPhoto(view,e.target));
}
function setBusy(value) {
  busy = value;
  $('measurement-form').querySelectorAll('input,button').forEach(el => {el.disabled = value;});
  $('processing').hidden = !value;
  $('mode').disabled = value;
  $('processing').textContent = isDemo() ? 'Saving demo sample…' : 'Processing images… Please keep this page open.';
  $('measure-button').textContent = value ? 'Processing…' : (isDemo() ? 'Run demo & save' : 'Measure body');
}
function showResult(data) {
  const m = data.measurements;
  const demo = data.mode === 'demo';
  $('result-label').textContent = demo ? 'SAVED DEMO — SAMPLE VALUES' : 'SAVED MEASUREMENT';
  $('result-demo-notice').hidden = !demo;
  $('confidence-label').textContent = demo ? 'Example confidence (simulated)' : 'Capture confidence';
  $('result-person').textContent = data.person_id;
  $('height-value').textContent = `${m.height_cm.toFixed(1)} cm`;
  $('shoulder-value').textContent = `${m.shoulder_cm.toFixed(1)} cm`;
  $('waist-value').textContent = `${m.waist_cm.toFixed(1)} cm`;
  $('front-waist-value').textContent = `${m.front_waist_width_cm.toFixed(1)} cm`;
  $('side-waist-value').textContent = `${m.side_waist_depth_cm.toFixed(1)} cm`;
  $('confidence-value').textContent = `${Math.round(data.confidence*100)}%`;
  $('result-status').textContent = demo ? 'DEMO ONLY' : (data.status === 'good' ? 'GOOD' : 'NEEDS REVIEW');
  $('result-status').className = 'badge' + (data.status === 'good' ? '' : ' review');
  $('date-value').textContent = new Date(data.created_at).toLocaleString();
  $('result-notes').textContent = data.notes;
  $('result').hidden = false; $('result').scrollIntoView({behavior:'smooth',block:'start'});
}
$('measurement-form').addEventListener('submit',async e => {
  e.preventDefault(); if (busy) return;
  showError(''); $('result').hidden = true;
  const person = $('person-id').value.trim();
  if (!person) {showError('Enter a Person ID.'); return;}
  if (!isDemo() && (!photos.front || !photos.side)) {showError('Select both front and side photos for real measurement.'); return;}
  const data = new FormData(); data.append('person_id',person);
  if (photos.front) data.append('front_image',photos.front);
  if (photos.side) data.append('side_image',photos.side);
  setBusy(true);
  try { showResult(await request(isDemo() ? '/api/demo/measure' : '/api/measure',{method:'POST',body:data})); await loadHistory(); }
  catch(error) { showError(error.message || 'Connection lost. Check that the app is running.'); }
  finally { setBusy(false); }
});
$('another').addEventListener('click',() => {
  $('measurement-form').reset(); $('result').hidden = true; showError('');
  for(const view of ['front','side']) {
    photos[view] = null;
    if(previewURLs[view]) URL.revokeObjectURL(previewURLs[view]);
    delete previewURLs[view]; $(view+'-preview').hidden = true; $(view+'-preview').removeAttribute('src');
    $(view+'-name').textContent = `No ${view} photo selected`;
  }
  $('person-id').focus();
});
async function loadHistory(person='') {
  const requestId = ++historyRequest;
  const mode = isDemo() ? 'demo' : 'real';
  $('history-message').textContent = 'Loading saved measurements…'; $('history').replaceChildren();
  try {
    const rows = await request(person ? `/api/measurements/${encodeURIComponent(person)}?mode=${mode}` : `/api/measurements?limit=20&mode=${mode}`);
    if (requestId !== historyRequest) return;
    for(const row of rows) {
      const tr=document.createElement('tr');
      for(const value of [row.person_id,`${row.height_cm} cm`,`${row.shoulder_cm} cm`,`${row.waist_cm} cm`,row.status]) {
        const td=document.createElement('td'); td.textContent=value; tr.appendChild(td);
      }
      $('history').appendChild(tr);
    }
    $('history-message').textContent = rows.length ? (mode === 'demo' ? 'Demo records only. All values are simulated examples.' : (person ? 'Matching saved records.' : 'Latest 20 records. Use Find to look up an older ID.')) : 'No saved records in this mode yet.';
  } catch(error) {if (requestId === historyRequest) $('history-message').textContent=error.message;}
}
function updateMode() {
  $('demo-notice').hidden = !isDemo();
  $('real-instructions').hidden = isDemo();
  $('photo-hint').hidden = !isDemo();
  $('capture-title').textContent = isDemo() ? 'Try the demo' : '2. Capture or upload';
  $('history-title').textContent = isDemo() ? 'Saved demos — sample data' : 'Saved real measurements';
  $('result').hidden = true; showError(''); setBusy(false);
  $('lookup-id').value = ''; loadHistory();
}
$('mode').addEventListener('change', updateMode);
$('lookup-form').addEventListener('submit',e=>{e.preventDefault();loadHistory($('lookup-id').value.trim());});
$('refresh').addEventListener('click',()=>{ $('lookup-id').value=''; loadHistory(); });
request('/health').then(()=>{$('health').textContent='API, database and pose model ready';}).catch(()=>{
  $('health').textContent='Server not ready — check the app terminal'; $('health').className='badge review';
});
updateMode();
