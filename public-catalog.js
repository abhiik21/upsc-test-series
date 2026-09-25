/* Public catalogue pages: Test Series, Series detail, Free Tests, Pricing.
   Progressive enhancement: if the API cannot be reached the static content in each page is left untouched. */
(function () {
  'use strict';
  const A = window.UPSC_API;
  if (!A) return;
  const page = location.pathname.split('/').pop() || 'index.html';
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const rupees = (paise) => '\u20B9' + (paise / 100).toLocaleString('en-IN');
  const num = (n) => Number(n || 0).toLocaleString('en-IN');
  const TYPE_NAMES = { subject: 'Subject Test', full_length: 'Full Length', current_affairs: 'Current Affairs', pyq: 'PYQ Practice', csat: 'CSAT', mixed: 'Mixed Test' };
  const FEATURES = { subject: 'Subject-wise tests', full_length: 'Full-length simulations', pyq: 'PYQ practice', current_affairs: 'Current affairs tests', csat: 'CSAT practice', mixed: 'Mixed practice tests' };
  const typeName = (t) => TYPE_NAMES[t] || t || 'Test';
  const loggedIn = () => !!localStorage.getItem('upsc_access_token');

  function category(types) {
    if (types.length && types.every((t) => t === 'csat')) return 'csat';
    if (types.length && types.every((t) => t === 'current_affairs')) return 'current';
    return 'prelims';
  }
  const TAGS = { prelims: 'Prelims \u00B7 GS Paper I', csat: 'CSAT \u00B7 Paper II', current: 'Current Affairs' };
  function features(types) {
    const list = types.map((t) => FEATURES[t]).filter(Boolean);
    return list.concat(['Detailed solutions', 'Performance analytics']).slice(0, 5);
  }
  let plansCache = null;
  async function getPlans() {
    if (!plansCache) { try { plansCache = await A.get('/plans'); } catch (_) { plansCache = []; } }
    return plansCache;
  }
  async function cheapestPlan() {
    const plans = await getPlans();
    return plans.length ? plans[0].price_paise : null;
  }
  function planNameForTier(tier) {
    const plans = plansCache || [];
    const plan = plans.filter((p) => p.tier <= tier).sort((a, b) => b.tier - a.tier)[0] || plans[0];
    return plan ? plan.name : `Tier ${tier}`;
  }
  function wireFilters(cardSelector, matches) {
    const buttons = [...document.querySelectorAll('.filter')];
    buttons.forEach((btn) => btn.addEventListener('click', () => {
      buttons.forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      let shown = 0;
      document.querySelectorAll(cardSelector).forEach((card) => {
        const show = btn.dataset.filter === 'all' || matches(card, btn.dataset.filter);
        card.style.display = show ? 'flex' : 'none';
        if (show) shown++;
      });
      const empty = $('empty'); if (empty) empty.style.display = shown ? 'none' : 'block';
    }));
  }

  /* ---------------- Test Series ---------------- */
  async function seriesList() {
    wireFilters('.series-card', (card, f) => card.dataset.category === f);
    const [rows, from] = await Promise.all([A.get('/public/series'), cheapestPlan()]);
    const grid = $('seriesGrid'); if (!grid) return;
    if (!rows.length) { grid.innerHTML = '<p style="grid-column:1/-1;padding:30px;text-align:center;color:#667085">No test series are published yet. Please check back soon.</p>'; return; }
    grid.innerHTML = rows.map((s) => {
      const cat = category(s.test_types);
      return `<article class="series-card${s.featured ? ' featured' : ''}" data-category="${cat}">
        <div class="card-top"><span class="tag">${TAGS[cat]}</span>${s.featured ? '<span class="badge">Popular</span>' : ''}</div>
        <h3>${esc(s.name)}</h3>
        <p>${esc(s.description || 'Structured practice tests with detailed solutions and performance analysis.')}</p>
        <ul>${features(s.test_types).map((f) => `<li>${esc(f)}</li>`).join('')}</ul>
        <div class="meta"><div class="meta-box"><strong>${num(s.test_count)}</strong><span>Tests</span></div><div class="meta-box"><strong>${num(s.question_count)}</strong><span>Questions</span></div></div>
        <div class="price-row"><div class="price">${from != null ? esc(rupees(from)) + ' <small>with any plan</small>' : '<small>Included in plans</small>'}</div><a class="full-link" href="test-series-detail.html?series=${encodeURIComponent(s.slug)}">View details &rarr;</a></div>
      </article>`;
    }).join('');
  }

  /* ---------------- Series detail ---------------- */
  async function seriesDetail() {
    await getPlans();
    let slug = new URLSearchParams(location.search).get('series');
    if (!slug) { const all = await A.get('/public/series'); if (!all.length) return; slug = all[0].slug; }
    let s;
    try { s = await A.get('/public/series/' + encodeURIComponent(slug)); }
    catch (e) { if (e.status === 404) location.replace('test-series.html'); return; }
    const from = await cheapestPlan();
    const cat = category(s.test_types);
    document.title = `${s.name} | UPSC Test Series`;
    const set = (sel, html) => { const el = document.querySelector(sel); if (el) el.innerHTML = html; };
    set('.breadcrumbs', `<a href="test-series.html">Test Series</a> <span> / </span> ${esc(s.name)}`);
    set('.eyebrow', `<span></span> ${TAGS[cat]}`);
    set('.hero h1', esc(s.name));
    set('.hero-copy', esc(s.description || 'Structured practice tests with detailed solutions and performance analysis after every test.'));
    set('.hero-meta', `<span><strong>${num(s.test_count)}</strong> Tests</span><span><strong>${num(s.question_count)}</strong> Questions</span>${s.free_test_count ? `<span><strong>${num(s.free_test_count)}</strong> Free</span>` : ''}`);
    const stats = document.querySelectorAll('.stat-grid .stat strong');
    if (stats[0]) stats[0].textContent = num(s.test_count);
    if (stats[1]) stats[1].textContent = num(s.question_count);
    // purchase card: access is sold as plans, so point to them
    set('.purchase-card .small', 'Unlock every premium test with a plan');
    set('.purchase-card .price', from != null ? `${esc(rupees(from))} <span>plans start here</span>` : 'Plans');
    const strike = document.querySelector('.purchase-card .strike'); if (strike && strike.parentElement) strike.parentElement.style.display = 'none';
    const items = document.querySelectorAll('.purchase-card ul li');
    if (items[0]) items[0].textContent = `${num(s.test_count)} test${s.test_count === 1 ? '' : 's'} in this series`;
    if (items[1]) items[1].textContent = `${num(s.question_count)} practice questions`;
    const buy = document.querySelector('.purchase-card a.btn'); if (buy) { buy.href = 'test-series.html#plans'; buy.textContent = 'Choose a Plan'; }
    const heroBuy = document.querySelector('.hero-actions a.btn-primary'); if (heroBuy) heroBuy.textContent = 'Choose a Plan';
    // test structure
    const list = document.querySelector('.test-list');
    if (list) {
      list.innerHTML = s.tests.length ? s.tests.map((t, i) => `<div class="test-row"><div class="test-num">${String(i + 1).padStart(2, '0')}</div><div><div class="test-title">${esc(t.title)}</div><div class="test-sub">${esc(t.description || s.name)}</div></div><div class="test-format"><span class="pill">${esc(typeName(t.test_type))}</span></div><div class="test-meta">${num(t.total_questions)} Q &middot; ${num(t.duration_minutes)} min</div>${t.access_type === 'free' ? `<a class="link" href="${loggedIn() ? 'test-instructions.html?test_id=' + encodeURIComponent(t.id) : 'free-tests.html'}">Try free &rarr;</a>` : `<a class="link" href="test-series.html#plans">${esc(planNameForTier(t.required_tier))}+ &rarr;</a>`}</div>`).join('')
        : '<p style="padding:24px;color:#667085">Tests for this series will be published soon.</p>';
    }
  }

  /* ---------------- Free tests ---------------- */
  function startFreeTest(id) {
    const target = 'test-instructions.html?test_id=' + encodeURIComponent(id);
    if (loggedIn()) { location.href = target; return; }
    try { localStorage.setItem('post_login_redirect', JSON.stringify({ path: target, exp: Date.now() + 30 * 60 * 1000 })); } catch (_) {}
    location.href = 'register.html';
  }
  async function freeTests() {
    wireFilters('#testGrid .card', (card, f) => (card.dataset.category || '').split(' ').includes(f));
    const rows = await A.get('/public/free-tests');
    const grid = $('testGrid'); if (!grid) return;
    const empty = $('empty');
    if (!rows.length) {
      grid.innerHTML = '';
      if (empty) { empty.innerHTML = '<strong>No free tests are available right now.</strong>Please check back soon, or explore the paid test series.'; empty.style.display = 'block'; }
      return;
    }
    if (empty) empty.style.display = 'none';
    grid.innerHTML = rows.map((t) => `<article class="card" data-category="${esc(t.subjects.map((x) => x.id).join(' '))}">
      <div class="card-top"><span class="tag">${esc(t.subjects.length === 1 ? t.subjects[0].name : t.subjects.length ? 'Mixed subjects' : typeName(t.test_type))}</span><span class="level">${esc(typeName(t.test_type))}</span></div>
      <h3>${esc(t.title)}</h3>
      <p>${esc(t.description || 'A free UPSC-style practice test with detailed solutions.')}</p>
      <div class="meta"><div class="meta-box"><strong>${num(t.total_questions)}</strong><span>Questions</span></div><div class="meta-box"><strong>${num(t.duration_minutes)} min</strong><span>Duration</span></div><div class="meta-box"><strong>${+(+t.total_marks).toFixed(2)}</strong><span>Marks</span></div></div>
      <div class="card-footer"><span class="small-link">Includes solutions</span><button class="btn btn-primary" type="button" data-start="${esc(t.id)}">Start Test</button></div>
    </article>`).join('');
    grid.querySelectorAll('[data-start]').forEach((b) => b.addEventListener('click', () => startFreeTest(b.dataset.start)));
  }

  /* ---------------- Pricing ---------------- */
  async function pricing() {
    const plans = await getPlans();
    document.querySelectorAll('.plans .plan').forEach((card) => {
      const link = card.querySelector('a[href*="plan_id="]'); if (!link) return;
      const id = new URLSearchParams(link.getAttribute('href').split('?')[1]).get('plan_id');
      const plan = plans.find((p) => p.id === id);
      if (!plan) { card.style.display = 'none'; return; }
      const strong = card.querySelector('.plan-price strong'); if (strong) strong.textContent = rupees(plan.price_paise);
      const span = card.querySelector('.plan-price span'); if (span) span.textContent = `one-time \u00B7 ${plan.validity_days} days access`;
    });
    // no public plans at all: do not show an empty "Plans" section
    const section = $('plans');
    if (section && ![...section.querySelectorAll('.plan')].some((c) => c.style.display !== 'none')) section.style.display = 'none';
  }


  /* ---------------- Live counts (home page, series page) ---------------- */
  async function siteStats() {
    const st = await A.get('/public/stats');
    const set = (id, v) => { const el = $(id); if (el) el.textContent = num(v); };
    set('stQuestions', st.questions); set('stTests', st.tests); set('tsQuestions', st.questions); set('tsTests', st.tests);
    const row = $('siteStats'); if (row) row.hidden = false;
  }

  /* ---------------- Results (anonymous, aggregate) ---------------- */
  const RES_CATS = { prelims: 'Prelims GS', csat: 'CSAT', current: 'Current Affairs' };
  const resCategory = (t) => (t === 'csat' ? 'csat' : t === 'current_affairs' ? 'current' : 'prelims');
  async function results() {
    const data = await A.get('/public/results');
    const { overall, items } = data;
    const set = (id, v) => { const el = $(id); if (el) el.textContent = v; };
    set('rsMin', data.min_participants);
    set('rsParticipants', num(overall.participants)); set('rsTests', num(overall.tests));
    set('rsAvg', overall.avg_percent == null ? '\u2013' : overall.avg_percent + '%');
    set('rsTop', overall.top_percent == null ? '\u2013' : overall.top_percent + '%');
    const box = $('testCards');
    function draw() {
      const q = $('search').value.trim().toLowerCase(), cat = $('category').value, sort = $('sort').value;
      const rows = items.filter((t) => (!q || t.title.toLowerCase().includes(q)) && (cat === 'all' || resCategory(t.test_type) === cat));
      if (sort === 'participants') rows.sort((a, b) => b.participants - a.participants);
      else if (sort === 'score') rows.sort((a, b) => b.top_percent - a.top_percent);
      else rows.sort((a, b) => String(b.last_at).localeCompare(String(a.last_at)));
      set('resultCount', items.length ? `Showing ${rows.length} of ${items.length} test${items.length === 1 ? '' : 's'}` : 'No results yet');
      box.innerHTML = rows.length ? rows.map((t) => `<article class="test-card"><div class="top"><span class="badge">${RES_CATS[resCategory(t.test_type)]}</span><span style="font-size:10px;color:#7c8793">${esc(new Date(t.last_at).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }))}</span></div><h3>${esc(t.title)}</h3><p>${num(t.total_questions)} questions &middot; average score ${t.avg_percent}%</p><div class="metrics"><span>${num(t.participants)} participants</span><span>${+(+t.total_marks).toFixed(2)} marks</span><span>${num(t.duration_minutes)} min</span></div><div class="test-foot"><span class="score">Highest ${t.top_score}/${+(+t.total_marks).toFixed(2)}</span><a class="link" href="test-series.html">Explore tests &rarr;</a></div></article>`).join('')
        : `<p style="grid-column:1/-1;padding:28px;text-align:center;color:#667085">${items.length ? 'No tests match these filters.' : 'No results to show yet. Results appear here once enough students have completed a test.'}</p>`;
    }
    ['search', 'category', 'sort'].forEach((id) => $(id).addEventListener('input', draw));
    $('reset').addEventListener('click', () => { $('search').value = ''; $('category').value = 'all'; $('sort').value = 'latest'; draw(); });
    draw();
  }

  async function run() {
    try {
      if (page === 'index.html' || page === '') await siteStats();
      else if (page === 'test-series.html') await Promise.allSettled([siteStats(), seriesList(), pricing()]);
      else if (page === 'test-series-detail.html') await seriesDetail();
      else if (page === 'free-tests.html') await freeTests();
      else if (page === 'results.html') await results();
    } catch (err) { console.warn('Public catalogue:', err); }
  }
  run();
})();
