// Canvas Coach collector. Runs inside a logged-in Canvas page.
// Read-only: it only sends GET requests, and only to the Canvas site it is running on.
// coach.py appends the call that starts it; what it reads is listed in resources.json.
window.__ccCollect = async function (CFG) {
  const origin = location.origin;
  if (CFG.host && origin !== CFG.host) return { error: 'wrong_origin', origin, expected: CFG.host };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const HEADERS = { Accept: 'application/json+canvas-string-ids, application/json' };

  // Keep only the listed fields. "a.b" reaches into nested objects and lists.
  const pick = (obj, keys) => {
    if (!keys || obj === null || typeof obj !== 'object') return obj;
    if (Array.isArray(obj)) return obj.map((x) => pick(x, keys));
    const out = {}, nested = {};
    for (const key of keys) {
      const dot = key.indexOf('.');
      if (dot < 0) { if (key in obj) out[key] = obj[key]; continue; }
      const head = key.slice(0, dot);
      (nested[head] = nested[head] || []).push(key.slice(dot + 1));
    }
    for (const head of Object.keys(nested)) {
      if (head in obj && !(head in out)) out[head] = pick(obj[head], nested[head]);
    }
    return out;
  };

  const get = async (url) => {
    for (let attempt = 0; ; attempt++) {
      const res = await fetch(url, { credentials: 'same-origin', headers: HEADERS });
      const text = await res.text();
      const throttled = res.status === 429 || (res.status === 403 && /rate limit/i.test(text));
      if ((throttled || res.status >= 500) && attempt < 2) { await sleep(1500 * (attempt + 1)); continue; }
      let body;
      try { body = JSON.parse(text.replace(/^while\(1\);/, '')); } catch (e) { body = undefined; }
      if (!res.ok || body === undefined) {
        const why = (body && (body.status || (body.errors && body.errors[0] && body.errors[0].message))) || 'not JSON';
        const err = new Error('HTTP ' + res.status + ' ' + why);
        err.status = res.status;
        err.down = throttled || res.status >= 500;
        throw err;
      }
      return { res, body };
    }
  };

  const out = {
    format: 'canvas-coach/1', host: origin, collected_at: new Date().toISOString(),
    user: null, courses: [], data: {}, global: {}, errors: [], truncated: [], flags: [],
  };

  // "inspect" in resources.json names text that is looked at once and never saved (assignment instructions).
  // Such text is passed on only when it shows a sign of something hidden from a reader; coach.py then decides
  // and keeps a plain yes/no. These patterns only have to be generous: a false alarm costs nothing but bytes.
  const SUSPECT = new RegExp([
    'color\\s*[:=]', 'background\\s*:', '<font\\b',                       // any colour at all: text or background
    'display\\s*:\\s*none', 'visibility\\s*:', 'opacity\\s*:', 'clip(?:-path)?\\s*:', 'transform\\s*:',
    'font-size\\s*:\\s*(?:0|\\.|1(?:px|pt|%))', 'font\\s*:[^;"\']*\\b0',
    '(?:left|top|right|bottom|text-indent|margin-left|margin-top)\\s*:\\s*-\\d{2,}',
    '(?:height|width)\\s*:\\s*(?:[01](?![\\d.])|0?\\.\\d)', '<[a-z][^>]*\\shidden(?=[\\s>=/])',
    '[\\u200b-\\u200d\\u2060\\ufeff]{8,}', '\\udb40[\\udc00-\\udc7f]',
  ].join('|'), 'i');

  // Follow Canvas pagination (Link: <...>; rel="next") until the list ends.
  const getAll = async (path, label) => {
    let url = path, items = [], pages = 0;
    while (url) {
      const { res, body } = await get(url);
      if (!Array.isArray(body)) return body;
      items = items.concat(body);
      const next = /<([^>]+)>;\s*rel="next"/.exec(res.headers.get('Link') || '');
      url = next ? next[1] : null;
      // Never follow a link off this site, and never pretend a cut-off list is complete.
      if (url && (url.indexOf(origin + '/') !== 0 || ++pages >= (CFG.max_pages || 50))) { out.truncated.push(label); break; }
    }
    return items;
  };

  try {
    out.user = pick((await get('/api/v1/users/self')).body, ['id', 'name', 'short_name']);
  } catch (e) {
    // An outage or throttling is not the same as being logged out.
    return { error: e.down ? 'canvas_unavailable' : 'unauthenticated', status: e.status || 0, detail: String(e.message || e) };
  }

  const R = CFG.resources;
  out.courses = R.courses ? (await getAll(R.courses.path, 'courses')).map((c) => pick(c, R.courses.keep)) : [];
  const skip = new Set((CFG.skip_courses || []).map(String));
  const only = new Set((CFG.only_courses || []).map(String));
  const todo = out.courses.filter((c) => !c.access_restricted_by_date && !skip.has(String(c.id)) &&
    (!only.size || only.has(String(c.id))));
  const contexts = todo.map((c) => 'context_codes[]=course_' + c.id).join('&');
  // Placeholders in resources.json paths: {course_id}, {course_contexts}, {id} and {date:-21} (days from today).
  const fill = (path, courseId, rowId) => path.replace('{course_id}', courseId || '').replace('{id}', rowId || '')
    .replace('{course_contexts}', contexts)
    .replace(/\{date:([+-]?\d+)\}/g, (m, n) => new Date(Date.now() + Number(n) * 864e5).toISOString().slice(0, 10));

  const store = async (target, r, path, courseId) => {
    try {
      const label = r.name + (courseId ? ':' + courseId : '');
      const items = await getAll(fill(path, courseId), label);
      // "expand": Canvas leaves a nested list out when it is long (module items); fetch it separately.
      if (r.expand && Array.isArray(items)) {
        for (const row of items) {
          if (row[r.expand.field] !== undefined && row[r.expand.field] !== null) continue;
          try { row[r.expand.field] = await getAll(fill(r.expand.path, courseId, String(row.id)), label + ':' + row.id); } catch (e) { /* stays missing; the view says so */ }
        }
      }
      let keep = r.keep;
      if (r.inspect && !CFG.slim_days && Array.isArray(items)) {   // never in a result that is copied by hand
        for (const flag of Object.keys(r.inspect)) {
          for (const row of items) {
            const text = row && row[r.inspect[flag]];
            if (typeof text === 'string' && SUSPECT.test(text)) (row._inspect = row._inspect || {})[flag] = text.slice(0, 200000);
          }
          if (out.flags.indexOf(flag) < 0) out.flags.push(flag);
        }
        keep = keep && keep.concat(['_inspect']);
      }
      target[r.name] = Array.isArray(items) ? items.map((x) => pick(x, keep)) : pick(items, keep);
    } catch (e) {
      out.errors.push({ resource: r.name, course_id: courseId || null, status: e.status || 0,
        message: String(e.message || e).slice(0, 200) });
    }
  };
  let cursor = 0;
  const worker = async () => {
    while (cursor < todo.length) {
      const cid = String(todo[cursor++].id);
      out.data[cid] = {};
      await Promise.all((R.per_course || []).map((r) => store(out.data[cid], r, r.path, cid)));
    }
  };
  await Promise.all([worker(), worker(), worker()]);
  for (const r of R.global || []) await store(out.global, r, r.path, null);

  // Derived: points per assignment group, counted before anything is dropped below.
  for (const cid of Object.keys(out.data)) {
    const d = out.data[cid];
    if (!Array.isArray(d.assignments) || !Array.isArray(d.assignment_groups)) continue;
    for (const g of d.assignment_groups) {
      const mine = d.assignments.filter((a) => String(a.assignment_group_id) === String(g.id) && !a.omit_from_final_grade);
      g.points_total = mine.reduce((sum, a) => sum + (Number(a.points_possible) || 0), 0);
      g.assignment_count = mine.length;
    }
  }

  // Slim mode (used when an AI agent has to copy the result by hand): drop finished work that is long past.
  if (CFG.slim_days) {
    const cutoff = Date.now() - CFG.slim_days * 864e5;
    let dropped = 0;
    for (const cid of Object.keys(out.data)) {
      const d = out.data[cid];
      if (!Array.isArray(d.assignments)) continue;
      d.assignments = d.assignments.filter((a) => {
        const s = a.submission || {};
        const done = s.excused || (s.submitted_at && !s.missing && !s.redo_request);
        const old = a.due_at && Date.parse(a.due_at) < cutoff;
        if (done && old) { dropped++; return false; }
        return true;
      });
    }
    out.slim = { days: CFG.slim_days, dropped };
  }

  // Integrity check that coach.py recomputes, so a damaged copy is caught instead of trusted.
  const crc32 = (str) => {
    let crc = -1;
    for (const b of new TextEncoder().encode(str)) {
      let c = (crc ^ b) & 0xff;
      for (let k = 0; k < 8; k++) c = c & 1 ? (c >>> 1) ^ 0xedb88320 : c >>> 1;
      crc = (crc >>> 8) ^ c;
    }
    return ((crc ^ -1) >>> 0).toString(16).padStart(8, '0');
  };
  // Text and true/false fields only: numbers print differently in JavaScript and Python.
  const flag = (v) => (v === true ? '1' : '0');
  const lines = [];
  for (const c of out.courses) {
    const t = c.term || {};
    lines.push(['C', c.id, t.end_at || '', c.end_at || '', flag(c.access_restricted_by_date),
      flag(c.apply_assignment_group_weights), (c.enrollments || []).map((e) => e.type || '').join(',')].join('|'));
  }
  for (const cid of Object.keys(out.data)) {
    for (const a of (Array.isArray(out.data[cid].assignments) ? out.data[cid].assignments : [])) {
      const s = a.submission || {};
      lines.push([cid, a.id, a.due_at || '', s.workflow_state || '', s.submitted_at || '', a.name || '',
        a.lock_at || '', a.unlock_at || '', flag(a.locked_for_user), (a.submission_types || []).join(','),
        flag(s.excused), flag(s.missing), flag(s.redo_request), flag(a.omit_from_final_grade)].join('|'));
    }
  }
  lines.sort();
  out.check = { assignments: lines.length - out.courses.length, crc: crc32(lines.join('\n')) };

  // Slim mode also leaves out empty fields (null, false, '', [], {}): a missing field reads the same as an
  // empty one everywhere, including in the check above, and the result is much shorter to copy.
  if (CFG.slim_days) {
    const lean = (v) => {
      if (Array.isArray(v)) return v.map(lean);
      if (v === null || typeof v !== 'object') return v;
      const o = {};
      for (const k of Object.keys(v)) {
        const x = lean(v[k]);
        const empty = x === null || x === false || x === '' || (typeof x === 'object' && Object.keys(x).length === 0);
        if (!empty) o[k] = x;
      }
      return o;
    };
    out.courses = out.courses.map(lean);
    for (const cid of Object.keys(out.data)) {
      for (const name of Object.keys(out.data[cid])) {
        if (Array.isArray(out.data[cid][name])) out.data[cid][name] = out.data[cid][name].map(lean);
      }
    }
  }
  return out;
};
