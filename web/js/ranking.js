import { api, avatarEl } from "/js/common.js";

const $ = (id) => document.getElementById(id);

/**
 * The record page has no UI to pick a show, so it follows the active one.
 *
 * `/shows/active` returns the newest show. Re-resolving it on every tick means
 * a browser source that stays open across shows switches to a new one on its
 * own, instead of pinning the record to whatever existed at page load.
 */
async function refresh() {
  let showId = null;
  try {
    const active = await api("/shows/active");
    showId = active.show_id ?? null;
  } catch {
    return; // transient API error: keep the last rendered table on screen
  }

  if (!showId) {
    renderRows([]);
    return;
  }

  let rows;
  try {
    rows = await api(`/shows/${showId}/ranking`);
  } catch {
    return; // transient API error: keep the last rendered table on screen
  }
  renderRows(rows);
}

// Mirrors `renderRanking` in control.js: same columns, same order (the endpoint
// already sorts by points desc, then nickname).
function renderRows(rows) {
  const body = $("rank-body");
  body.innerHTML = "";
  rows.forEach((r, i) => {
    const tr = document.createElement("tr");
    const who = document.createElement("td");
    const wrap = document.createElement("div");
    wrap.className = "who";
    wrap.append(
      avatarEl(r),
      Object.assign(document.createElement("span"), { textContent: r.nickname || r.unique_id }),
    );
    who.append(wrap);
    tr.append(
      Object.assign(document.createElement("td"), { textContent: i + 1 }),
      who,
      Object.assign(document.createElement("td"), { className: "num", textContent: r.correct_count }),
      Object.assign(document.createElement("td"), { className: "num", textContent: r.points }),
    );
    body.append(tr);
  });

  $("players").textContent = rows.length;
  $("empty").classList.toggle("hidden", rows.length > 0);
  const conn = $("conn");
  conn.textContent = "en vivo";
  conn.className = "pill pill-ok";
}

setInterval(() => { refresh().catch(() => {}); }, 2000);
refresh();
