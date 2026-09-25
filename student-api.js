/* Shared student-portal data bridge. The existing screens keep their visual layouts,
   while this script replaces demo values with backend data whenever available. */
(() => {
  const A = window.UPSC_API;
  if (!A) return;
  // Questions and explanations imported from Word keep their line breaks (statements, pairs, bullets).
  const lineBreaks = document.createElement('style');
  lineBreaks.textContent = '#questionText,.q-text,.answer-cell.explanation .value{white-space:pre-line}';
  document.head.appendChild(lineBreaks);
  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const authPages = new Set(['student-dashboard.html','my-tests.html','question-bank.html','performance.html','bookmarks.html','mistake-notebook.html','notifications.html','subscription.html','profile.html','current-affairs-student.html','study-material-student.html','student-pyqs.html','test-instructions.html','test-engine.html','result.html']);
  const page = location.pathname.split('/').pop() || 'index.html';

  function token() { return localStorage.getItem('upsc_access_token'); }
  function gotoLogin() { const next = encodeURIComponent(page + location.search); location.href = `login.html?next=${next}`; }
  if (authPages.has(page) && !token()) { gotoLogin(); return; }

  function setText(id, value) { const el = $(id); if (el) el.textContent = value; }
  function money(paise) { return `₹${(Number(paise||0)/100).toLocaleString('en-IN')}`; }
  function fmtDate(iso) { if (!iso) return '—'; const d = new Date(iso); return isNaN(d) ? '—' : d.toLocaleDateString('en-IN', {day:'2-digit', month:'short', year:'numeric'}); }

  async function identity() {
    try {
      const u = await A.get('/auth/me');
      localStorage.setItem('upsc_user', JSON.stringify(u));
      document.querySelectorAll('.profile-name').forEach(e => e.textContent = `${u.first_name}${u.last_name ? ' '+u.last_name : ''}`);
      document.querySelectorAll('.profile-sub').forEach(e => e.textContent = u.target_exam_year ? `UPSC ${u.target_exam_year}` : 'UPSC Aspirant');
      document.querySelectorAll('.avatar').forEach(e => e.textContent = `${u.first_name?.[0]||''}${u.last_name?.[0]||''}`.toUpperCase());
      return u;
    } catch (_) { A.clearSession(); gotoLogin(); return null; }
  }

  function logout() {
    document.querySelectorAll('#logoutBtn').forEach(btn => { const clone = btn.cloneNode(true); btn.replaceWith(clone); clone.addEventListener('click', () => { A.clearSession(); location.href='login.html'; }); });
  }

  async function dashboard() {
    const d = await A.get('/student/dashboard');
    setText('testsAttempted', d.stats.tests_attempted);
    const statCards = document.querySelectorAll('.stat-value');
    if (statCards[0]) statCards[0].innerHTML = `${d.stats.tests_attempted}`;
    if (d.recent_results?.length) {
      const rows = document.querySelectorAll('.result-row');
      d.recent_results.forEach((r,i)=>{ if(!rows[i]) return; const a=rows[i].querySelector('.result-title'); const b=rows[i].querySelector('.result-sub'); const s=rows[i].querySelector('.score'); if(a)a.textContent=r.title; if(b)b.textContent=`Completed ${fmtDate(r.submitted_at)}`; if(s)s.textContent=`${Number(r.score||0).toFixed(1)}`; });
    }
    const upcoming = document.querySelectorAll('.test-card');
    d.upcoming_tests?.forEach((t,i)=>{ if(!upcoming[i]) return; const title=upcoming[i].querySelector('.test-title'); const meta=upcoming[i].querySelector('.test-meta'); if(title) title.textContent=t.title; if(meta) meta.innerHTML=`<span>${t.total_questions} Questions</span><span>${t.total_marks} Marks</span><span>${t.duration_minutes} Minutes</span>`; const btn=upcoming[i].querySelector('a.btn'); if(btn) btn.href=`test-instructions.html?test_id=${encodeURIComponent(t.id)}`; });
  }

  const opensAt = t => { const d = t && t.scheduled_at ? new Date(t.scheduled_at) : null; return d && d > new Date() ? d : null; };
  const whenText = d => d.toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
  function testBadge(t) {
    if (t.has_access === false) return '<span class="badge badge-locked">Premium</span>';
    const d = opensAt(t); if (d) return `<span class="badge badge-upcoming">Opens ${esc(whenText(d))}</span>`;
    return t.access_type === 'free' ? '<span class="badge badge-complete">Free</span>' : '<span class="badge badge-upcoming">Available</span>';
  }

  async function myTests() {
    const rows = await A.get('/student/tests');
    const list = $('testList'); if (!list) return;
    list.innerHTML = rows.map((t,i)=>`<article class="test" data-status="upcoming" data-series="${esc(t.series_name||'') }" data-type="${esc(t.test_type||'')}" data-title="${esc(t.title)}" data-order="${i}">
      <div class="test-main"><div class="test-top">${testBadge(t)}<span class="series">${esc(t.series_name||'Test Series')}</span></div><div class="test-title">${esc(t.title)}</div><p class="test-desc">${esc(t.description||'UPSC-focused practice test')}</p><div class="meta"><span>${t.total_questions} Questions</span><span>${t.total_marks} Marks</span><span>${t.duration_minutes} Minutes</span><span>${t.negative_mark} Negative</span></div></div>
      <div class="actions">${t.has_access===false?'<a class="btn btn-primary" href="test-series.html#plans">Unlock with a plan</a>':'<a class="btn btn-primary" href="test-instructions.html?test_id=${encodeURIComponent(t.id)}">View Test</a>'}<a class="btn btn-ghost" href="test-series-detail.html">Details</a></div></article>`).join('');
    document.querySelectorAll('.start-btn').forEach(b=>b.onclick=null);
  }

  async function instructions() {
    const testId = new URLSearchParams(location.search).get('test_id') || 'test1';
    const t = await A.get(`/tests/${encodeURIComponent(testId)}`);
    document.title = `${t.title} | Test Instructions`;
    const h = document.querySelector('.hero h1'); if(h) h.textContent=t.title;
    const p = document.querySelector('.hero p'); if(p) p.textContent=t.description||'Review the test parameters before starting.';
    const stats=document.querySelectorAll('.sum .value'); if(stats[0])stats[0].textContent=t.total_questions; if(stats[1])stats[1].textContent=t.total_marks; if(stats[2])stats[2].textContent=`${t.duration_minutes}m`; if(stats[3])stats[3].textContent=t.negative_mark;
    let start = $('startBtn');
    const opens = opensAt(t);
    if (start && (t.has_access === false || opens)) {
      const stop = document.createElement(t.has_access === false ? 'a' : 'div');
      stop.className = start.className; stop.id = 'startBtn';
      if (t.has_access === false) { stop.href = 'test-series.html#plans'; stop.textContent = 'Choose a plan to unlock this test →'; }
      else { stop.textContent = `Opens ${whenText(opens)}`; stop.style.cssText = 'opacity:.65;cursor:not-allowed'; }
      start.replaceWith(stop); start = null;
    }
    if (start) {
      // The button is switched off in the page; it must come on once the student ticks "I have read the instructions".
      const box = $('confirm'), warn = $('error');
      const sync = () => { start.disabled = !(box && box.checked); if (warn && box && box.checked) warn.style.display = 'none'; };
      if (box) box.addEventListener('change', sync);
      sync();
      start.addEventListener('click', () => {
        if (box && !box.checked) { if (warn) warn.style.display = 'block'; return; }
        localStorage.setItem('pending_test_id', testId);
        location.href = `test-engine.html?test_id=${encodeURIComponent(testId)}`;
      });
    }
  }

  async function testEngine() {
    const testId = new URLSearchParams(location.search).get('test_id') || localStorage.getItem('pending_test_id') || 'test1';
    let started;
    try { started = await A.post(`/tests/${encodeURIComponent(testId)}/start`, {}); }
    catch (err) { const msg = err.message || 'This test could not be started.'; alert(msg); location.href = /paid plan/i.test(msg) ? 'test-series.html#plans' : 'my-tests.html'; return; }
    localStorage.setItem('upsc_attempt_id', started.attempt_id); localStorage.setItem('upsc_attempt_test_id', testId);
    const qs = started.questions || [];
    let idx = 0; let dirty=false; const state=qs.map(q=>({answer:null,review:false,time:0}));
    (started.saved_answers || []).forEach(a => { const pos=qs.findIndex(q => q.id===a.question_id); if(pos>=0){ state[pos].answer=a.selected_option; state[pos].review=!!a.marked_for_review; state[pos].time=Number(a.time_spent_seconds||0); } });
    setText('qIndex',1); setText('qNumber','01');
    setText('answeredCount',0); setText('unansweredCount',qs.length); setText('remainingCount',qs.length); setText('reviewCount',0);
    const countEl=document.querySelector('.q-count'); if(countEl) countEl.innerHTML=`Question <span id="qIndex">1</span> of ${qs.length}`;
    const title=document.querySelector('.test-title'); if(title) title.textContent=started.test.title;
    const sub=document.querySelector('.test-sub'); if(sub) sub.textContent=`${started.test.series_id||''} · ${qs.length} Questions`;
    const timer=$('timer'); let seconds=Number(started.test.duration_minutes||30)*60; const renderTimer=()=>{const h=Math.floor(seconds/3600),m=Math.floor(seconds%3600/60),s=seconds%60;if(timer)timer.textContent=`${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`};
    const nav=$('navigator'), options=$('options');
    function render(){ const q=qs[idx]; if(!q)return; setText('qIndex',idx+1); setText('qNumber',String(idx+1).padStart(2,'0')); if($('questionText'))$('questionText').textContent=q.stem; if($('questionNote'))$('questionNote').textContent='Select one option. Your response is saved to the server when you move to another question.'; if(options) options.innerHTML=[q.option_a,q.option_b,q.option_c,q.option_d].map((txt,i)=>`<label class="option ${state[idx].answer===String.fromCharCode(65+i)?'selected':''}"><input type="radio" name="option" value="${String.fromCharCode(65+i)}" ${state[idx].answer===String.fromCharCode(65+i)?'checked':''}><span class="opt-letter">${String.fromCharCode(65+i)}</span><span class="opt-text">${esc(txt)}</span></label>`).join(''); if(options) options.querySelectorAll('label').forEach((l,i)=>l.addEventListener('click',()=>select(i))); buildNav(); update(); }
    async function select(i){ state[idx].answer=String.fromCharCode(65+i); dirty=true; render(); await save(); }
    async function save(){ const aid=localStorage.getItem('upsc_attempt_id'); const q=qs[idx]; await A.post(`/attempts/${encodeURIComponent(aid)}/questions/${encodeURIComponent(q.id)}/answer`,{selected_option:state[idx].answer,marked_for_review:state[idx].review,time_spent_seconds:state[idx].time||0}); dirty=false; setText('saveText','All changes saved'); }
    function update(){const answered=state.filter(x=>x.answer).length,review=state.filter(x=>x.review).length,un=qs.length-answered; setText('answeredCount',answered);setText('reviewCount',review);setText('unansweredCount',un);setText('remainingCount',un);setText('progressText',`${answered} answered · ${review} marked for review`);const bar=$('progressBar');if(bar)bar.style.width=qs.length?`${answered/qs.length*100}%`:'0%';setText('mAnswered',`${answered} / ${qs.length}`);setText('mReview',review);setText('mUnanswered',un);}
    function buildNav(){if(!nav)return;nav.innerHTML=state.map((s,i)=>`<button class="nav-q ${i===idx?'current':''} ${s.answer?'answered':''} ${s.review?'review':''}" data-i="${i}">${i+1}</button>`).join('');nav.querySelectorAll('[data-i]').forEach(b=>b.addEventListener('click',()=>{idx=Number(b.dataset.i);render();}));}
    $('prevBtn')?.addEventListener('click',()=>{if(idx>0){idx--;render();}}); $('nextBtn')?.addEventListener('click',async()=>{await save();if(idx<qs.length-1){idx++;render();}else openSubmit();}); $('clearBtn')?.addEventListener('click',async()=>{state[idx].answer=null;await save();render();}); $('reviewBtn')?.addEventListener('click',async()=>{state[idx].review=!state[idx].review;await save();render();});
    function openSubmit(){ $('submitModal')?.classList.add('show'); update(); } $('topSubmit')?.addEventListener('click',openSubmit); $('submitSide')?.addEventListener('click',openSubmit); $('cancelSubmit')?.addEventListener('click',()=>$('submitModal')?.classList.remove('show')); $('confirmSubmit')?.addEventListener('click',async()=>{try{await A.post(`/attempts/${encodeURIComponent(localStorage.getItem('upsc_attempt_id'))}/submit`,{});localStorage.removeItem('pending_test_id'); location.href=`result.html?test_id=${encodeURIComponent(testId)}`;}catch(e){alert(e.message)}});
    renderTimer(); render(); timerId=setInterval(()=>{seconds--;renderTimer();if(seconds<=0){clearInterval(timerId);A.post(`/attempts/${encodeURIComponent(localStorage.getItem('upsc_attempt_id'))}/submit`,{}).finally(()=>location.href=`result.html?test_id=${encodeURIComponent(testId)}`);}},1000);
  }

  async function result() {
    const testId = new URLSearchParams(location.search).get('test_id') || localStorage.getItem('upsc_attempt_test_id') || 'test1';
    const r = await A.get(`/tests/${encodeURIComponent(testId)}/result`); const a=r.attempt||{};
    setText('rank',a.rank ?? '—'); setText('percentile',a.percentile ?? '—'); setText('score',Number(a.score||0).toFixed(1)); setText('correct',a.correct||0); setText('incorrect',a.incorrect||0); setText('unattempted',a.unattempted||0); const attempted=Number(a.correct||0)+Number(a.incorrect||0); setText('accuracy',attempted?`${Math.round(Number(a.correct||0)/attempted*100)}%`:'0%'); setText('submittedChip',`Submitted ${fmtDate(a.submitted_at)}`);
    const subjects=$('subjects'); if(subjects) subjects.innerHTML=(r.subjects||[]).map(s=>`<div class="subject-row"><div class="subject-name">${esc(s.subject)}</div><div class="bar"><span style="width:${Number(s.accuracy||0)}%"></span></div><div class="pct">${s.accuracy ?? 0}%</div></div>`).join('')||'<div class="empty">No subject analysis yet.</div>';
    const topics=$('topics'); if(topics) topics.innerHTML=(r.topics||[]).map(t=>`<div class="topic"><div class="topic-top"><strong>${esc(t.topic)}</strong><span>${t.accuracy ?? 0}%</span></div><div class="mini-bar"><i style="width:${Number(t.accuracy||0)}%"></i></div></div>`).join('')||'<div class="empty">No topic analysis yet.</div>';
    const list=$('questionList'); if(list) list.innerHTML=(r.questions||[]).map((q,i)=>`<article class="q-card"><div class="q-main"><div class="q-index">${String(i+1).padStart(2,'0')}</div><div><div class="q-text">${esc(q.stem)}</div><div class="q-meta"><span class="badge ${q.is_correct===1?'correct':q.selected_option?'wrong':'skip'}">${q.selected_option ? (q.is_correct===1?'Correct':'Incorrect') : 'Unattempted'}</span></div></div></div><div class="answer-box" style="display:block"><div class="answer-grid"><div class="answer-cell"><div class="label">Your answer</div><div class="value">${esc(q.selected_option||'Unattempted')}</div></div><div class="answer-cell"><div class="label">Correct answer</div><div class="value">${esc(q.correct_option)}</div></div><div class="answer-cell explanation"><div class="label">Explanation</div><div class="value">${esc(q.explanation||'Explanation will be available with the published solution.')}</div></div></div></div></article>`).join('');
  }

  async function performance(){ const d=await A.get('/student/performance'); setText('testsAttempted',d.tests_attempted); const subjects=$('subjects'); if(subjects) subjects.innerHTML=(d.subjects||[]).map(s=>`<div class="subject-row"><div class="subject-name">${esc(s.subject)}</div><div class="bar"><span style="width:${Number(s.accuracy||0)}%"></span></div><div class="pct">${s.accuracy ?? 0}%</div></div>`).join(''); const topics=$('topicRows'); if(topics) topics.innerHTML=(d.topics||[]).map(t=>`<tr><td>${esc(t.topic)}</td><td>${t.accuracy ?? 0}%</td><td>${t.questions}</td><td>—</td></tr>`).join(''); }

  async function questionBank(){ const params=new URLSearchParams(); const search=$('search')?.value?.trim(); if(search) params.set('search',search); const rows=await A.get(`/student/questions?${params}`); const box=$('questions'); if(!box)return; box.innerHTML=rows.map((q,i)=>`<article class="question-card"><div class="q-top"><div class="q-tags"><span class="tag blue">${esc(q.subject_name||'')}</span><span class="tag">${esc(q.topic_name||'')}</span><span class="tag">${esc(q.difficulty)}</span><span class="tag">${q.upsc_year||''}</span></div><div class="q-no">Q${String(i+1).padStart(2,'0')}</div></div><div class="q-text">${esc(q.stem)}</div><div class="options">${[q.option_a,q.option_b,q.option_c,q.option_d].map((o,j)=>`<div class="option"><span class="option-letter">${String.fromCharCode(65+j)}</span><span>${esc(o)}</span></div>`).join('')}</div><div class="actions"><div class="meta">${esc(q.subject_name||'')} · ${esc(q.topic_name||'')}</div><div class="btns"><button class="btn" data-bookmark="${q.id}">☆ Bookmark</button><a class="btn btn-primary" href="test-instructions.html?test_id=test2">Practise Now</a></div></div></article>`).join(''); box.querySelectorAll('[data-bookmark]').forEach(b=>b.addEventListener('click',async()=>{await A.post(`/student/bookmarks/question/${b.dataset.bookmark}`,{});b.textContent='★ Saved';})); }

  async function notifications(){ const rows=await A.get('/student/notifications'); const list=$('notificationList'); if(!list)return; list.innerHTML=rows.map(n=>`<article class="notification ${n.is_read?'':'unread'}"><div class="nbody"><strong>${esc(n.title)}</strong><p>${esc(n.body)}</p><div class="nmeta">${fmtDate(n.created_at)}</div></div><div class="n-actions"><a class="small-btn" href="${esc(n.href||'student-dashboard.html')}">Open</a>${n.is_read?'':'<button class="small-btn" data-read="'+esc(n.id)+'">Mark read</button>'}</div></article>`).join('')||'<div class="empty">No notifications yet.</div>'; const unread=rows.filter(n=>!n.is_read).length; setText('heroUnread',unread);setText('sUnread',unread);setText('sTests',rows.filter(n=>n.type==='test').length);setText('sContent',rows.filter(n=>n.type==='content').length);list.querySelectorAll('[data-read]').forEach(b=>b.addEventListener('click',async()=>{await A.post(`/student/notifications/${b.dataset.read}/read`,{});notifications();})); }

  async function subscription(){ const s=await A.get('/student/subscription'); if(s.status!=='none'){ const cur=document.querySelector('.current'); if(cur){ const h=cur.querySelector('h2'); if(h)h.textContent=s.plan_name; const price=cur.querySelector('.price strong'); if(price)price.textContent=money(s.price_paise); const exp=cur.querySelector('.expiry strong'); if(exp)exp.textContent=`Expires ${fmtDate(s.expires_at)}`; } } const ph=$('paymentHistory'); if(ph){ try{ const rows=await A.get('/student/payments'); ph.innerHTML=rows.map(r=>`<div style="display:flex;justify-content:space-between;gap:14px;padding:12px 0;border-bottom:1px solid #eef0f3"><div><strong style="color:#10243e">${esc(r.plan_name||'Plan')}</strong><div style="font-size:11px;color:#667085">${esc(r.order_id||'')} · ${fmtDate(r.created_at)}</div></div><div style="text-align:right"><strong>${money(r.amount_paise)}</strong><div style="font-size:11px;text-transform:capitalize">${esc(r.status)}</div></div></div>`).join('')||'<div>No payments found.</div>'; }catch(e){ph.textContent='Payment history is unavailable.';} } }

  async function profile(){ const u=await identity(); if(!u)return; if($('firstName'))$('firstName').value=u.first_name||''; if($('lastName'))$('lastName').value=u.last_name||''; if($('email'))$('email').value=u.email||''; if($('mobile'))$('mobile').value=u.mobile||''; if($('target'))$('target').value=u.target_exam_year?`UPSC CSE ${u.target_exam_year}`:$('target').value; $('profileForm')?.addEventListener('submit',async(e)=>{e.preventDefault();const val=$('target')?.value||'';const year=(val.match(/(20\d{2})/)||[])[1];const out=await A.put('/student/profile',{first_name:$('firstName').value.trim(),last_name:$('lastName').value.trim(),mobile:$('mobile').value.trim()||null,target_exam_year:year?Number(year):null}); localStorage.setItem('upsc_user',JSON.stringify(out)); alert('Profile updated successfully.');}); }

  async function currentAffairs(){ const rows=await A.get('/public/current-affairs'); const list=$('articleList'); if(!list)return; list.innerHTML=rows.map((a,i)=>`<article class="article"><div class="article-top"><div class="chips"><span class="chip blue">${esc(a.category)}</span><span class="chip">${esc(a.gs||'')}</span></div></div><h3>${esc(a.title)}</h3><p>${esc(a.summary)}</p><div class="article-meta"><span>${fmtDate(a.published_at)}</span><span>${a.minutes} min read</span></div><div class="article-actions"><button class="btn btn-primary" data-ca="${a.id}">Read & revise</button><a class="btn btn-ghost" href="question-bank.html">Practise MCQs</a></div></article>`).join('')||'<div class="empty">No current affairs are published yet.</div>'; list.querySelectorAll('[data-ca]').forEach(b=>b.addEventListener('click',()=>alert('Key revision cue: '+(rows.find(x=>String(x.id)===b.dataset.ca)?.key_points||'Review the linked static concepts.')))); }

  async function studyMaterial(){ const rows=await A.get('/public/study-material'); const list=$('resourceList'); if(!list)return; list.innerHTML=rows.map(r=>`<article class="resource"><div class="resource-top"><div class="chips"><span class="chip blue">${esc(r.subject)}</span><span class="chip">${esc(r.format)}</span><span class="chip ${r.access_type==='premium'?'gold':'green'}">${r.access_type==='premium'?'Premium':'Free'}</span></div></div><h3>${esc(r.title)}</h3><p>${esc(r.summary)}</p><div class="resource-meta"><span>${esc(r.topic||'')}</span><span>${esc(r.pages||'')}</span></div><div class="resource-progress"><span style="width:${Number(r.progress||0)}%"></span></div><div class="resource-foot"><span class="progress-label">${r.progress}% complete</span><div class="article-actions"><button class="btn btn-primary" data-open-resource="${r.id}">Open Resource</button><a class="btn btn-ghost" href="question-bank.html">Practice MCQs</a></div></div></article>`).join(''); list.querySelectorAll('[data-open-resource]').forEach(b=>b.addEventListener('click',()=>alert('Resource viewer will load the stored file in production.'))); }

  async function studentPyqs(){ const params=new URLSearchParams(); const search=$('search')?.value?.trim(); const year=$('year')?.value; const subject=$('subject')?.value; if(search)params.set('search',search); if(year)params.set('year',year); if(subject)params.set('subject',subject.toLowerCase().replaceAll(' ','-').replaceAll('&','').replace('science-technology','science')); const rows=await A.get(`/student/questions?${params}`); const list=$('pyqList')||$('questions'); if(!list)return; list.innerHTML=rows.map((q,i)=>`<article class="question-card"><div class="q-top"><div class="q-tags"><span class="tag blue">${esc(q.subject_name||'')}</span><span class="tag">${esc(q.topic_name||'')}</span><span class="tag">${q.upsc_year||'Year'}</span></div><div class="q-no">PYQ ${String(i+1).padStart(2,'0')}</div></div><div class="q-text">${esc(q.stem)}</div><div class="actions"><span class="meta">UPSC question bank record</span><div class="btns"><button class="btn" data-pyq-bookmark="${esc(q.id)}">☆ Save</button><a class="btn btn-primary" href="test-instructions.html?test_id=test2">Practise Related Set</a></div></div></article>`).join('')||'<div class="empty">No PYQs match your filters.</div>'; list.querySelectorAll('[data-pyq-bookmark]').forEach(b=>b.addEventListener('click',async()=>{await A.post(`/student/bookmarks/pyq/${b.dataset.pyqBookmark}`,{});b.textContent='★ Saved';})); setText('count',`Showing ${rows.length} PYQ${rows.length===1?'':'s'}`); }

  window.addEventListener('student-pyq-refresh', () => studentPyqs());

  async function bookmarks(){ const rows=await A.get('/student/bookmarks'); const list=$('savedList'); if(!list)return; list.innerHTML=rows.map(r=>`<article class="saved-card"><div class="title">${esc(r.content_type)} · ${esc(r.content_id)}</div><div class="source">Saved ${fmtDate(r.created_at)}</div><div class="actions"><a class="btn" href="question-bank.html">Open</a><button class="btn btn-danger" data-remove="${esc(r.content_type)}" data-id="${esc(r.content_id)}">Remove</button></div></article>`).join('')||'<div class="empty">No saved items yet.</div>'; list.querySelectorAll('[data-remove]').forEach(b=>b.addEventListener('click',async()=>{await A.del(`/student/bookmarks/${encodeURIComponent(b.dataset.remove.dataset)}/${encodeURIComponent(b.dataset.id)}`);bookmarks();})); }

  async function run(){
    try { await identity(); } catch (_) {}
    logout();
    try {
      if(page==='student-dashboard.html') await dashboard();
      else if(page==='my-tests.html') await myTests();
      else if(page==='test-instructions.html') await instructions();
      else if(page==='test-engine.html') await testEngine();
      else if(page==='result.html') await result();
      else if(page==='performance.html') await performance();
      else if(page==='question-bank.html') await questionBank();
      else if(page==='notifications.html') await notifications();
      else if(page==='subscription.html') await subscription();
      else if(page==='profile.html') await profile();
      else if(page==='current-affairs-student.html') await currentAffairs();
      else if(page==='study-material-student.html') await studyMaterial();
      else if(page==='student-pyqs.html') await studentPyqs();
      else if(page==='bookmarks.html') await bookmarks();
    } catch (err) { console.warn('Student API bridge:', err); }
  }
  run();
})();
