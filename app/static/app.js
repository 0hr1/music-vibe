// Filter drawer on phones
document.addEventListener("click", (e) => {
  if (e.target.closest("[data-open-filters]")) document.body.classList.add("filters-open");
  if (e.target.closest("[data-close-filters]")) document.body.classList.remove("filters-open");
});

// A background request that fails (server error, dropped connection, a restart) says so, instead of
// looking as if it worked. Lookups that only fill in extra details stay quiet.
function showError(text) {
  let box = document.getElementById("request-error");
  if (!box) {
    box = Object.assign(document.createElement("div"), { id: "request-error", className: "notice request-error" });
    box.setAttribute("role", "alert");
    const close = Object.assign(document.createElement("button"), { type: "button", className: "btn", textContent: "OK" });
    close.addEventListener("click", () => box.remove());
    box.append(document.createElement("span"), close);
    document.body.append(box);
  }
  box.firstChild.textContent = text;
}
function requestFailed(e) {
  if (e.detail.elt?.closest?.("[data-quiet-errors]")) return;
  const verb = (e.detail.requestConfig?.verb || "get").toLowerCase();
  showError(verb === "get" ? "⚠ Couldn't load that. Check your connection and try again."
    : "⚠ Couldn't save your last change. Check your connection and try again.");
}
document.addEventListener("htmx:responseError", requestFailed);
document.addEventListener("htmx:sendError", requestFailed);

// After picking a search result, bring the prefilled form into view
document.addEventListener("htmx:afterSwap", (e) => {
  if (e.detail.target.id === "album-fields") {
    document.getElementById("album-window")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
});

// Don't let the background MusicBrainz lookup overwrite year/genres the user already edited
document.addEventListener("htmx:beforeSwap", (e) => {
  const box = e.detail.target;
  if (!box.matches?.("[data-refine]")) return;
  const val = (name) => box.querySelector(`input[name=${name}]`)?.value ?? "";
  if (`${val("year")}|${val("genres")}` !== box.dataset.original) {
    e.detail.shouldSwap = false;
    box.querySelector("[data-lookup-note]")?.remove();
  }
});

// Same for the automatic Spotify lookup: a link typed in meanwhile wins
document.addEventListener("htmx:beforeSwap", (e) => {
  const box = e.detail.requestConfig?.elt;
  if (box?.matches?.("[data-spotify-auto]") && box.querySelector("input[name=spotify_url]").value.trim()) {
    e.detail.shouldSwap = false;
    box.querySelector("[data-lookup-note]")?.remove();
  }
});

// Slow form posts (import, adding an album): show that something is happening and prevent double
// submits. Runs after the confirm handlers, so a cancelled submit leaves the button alone.
document.addEventListener("submit", (e) => {
  const form = e.target;
  if (!form.dataset.busy || e.defaultPrevented) return;
  const btn = form.querySelector("button[type=submit]");
  if (btn) {
    btn.dataset.idleText ??= btn.textContent;
    btn.disabled = true;
    btn.textContent = form.dataset.busy;
  }
});
// Back can bring the page back from the browser's cache just as it was left, button still busy
window.addEventListener("pageshow", (e) => {
  if (!e.persisted) return;
  document.querySelectorAll("form[data-busy] button[data-idle-text]").forEach((btn) => {
    btn.disabled = false;
    btn.textContent = btn.dataset.idleText;
  });
});

// Enter in an inline search box runs its search instead of submitting the surrounding form
document.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || !e.target.matches?.("[data-enter-click]")) return;
  e.preventDefault();
  e.target.parentElement.querySelector("button")?.click();
});

// Library select mode (bulk edit): toggle cards, keep the count and buttons in sync. The selection
// lives here rather than in the checkboxes, so it outlasts the grid being redrawn by a filter change;
// albums selected but filtered out of view ride along as hidden inputs.
const selection = new Set();
let deleting = false;

function syncSelection() {
  if (!document.querySelector(".library.selecting")) selection.clear();
  const shown = new Set();
  document.querySelectorAll("#grid .select-box").forEach((b) => {
    shown.add(b.value);
    b.checked = selection.has(b.value);
  });
  const offscreen = [...selection].filter((id) => !shown.has(id));
  const grid = document.getElementById("grid");
  if (grid) {  // in the grid, which the bulk form sends: htmx lets its `sel` values replace the form's own
    let box = grid.querySelector("[data-offscreen-selection]");
    if (!box) box = grid.appendChild(Object.assign(document.createElement("div"), { hidden: true }));
    box.dataset.offscreenSelection = "";
    box.replaceChildren(...offscreen.map((id) => Object.assign(document.createElement("input"),
      { type: "hidden", name: "sel", value: id })));
  }
  document.querySelectorAll("[data-bulk-count]").forEach((el) => (el.textContent = selection.size));
  document.querySelectorAll("[data-bulk-offscreen]").forEach((el) => {
    el.hidden = !offscreen.length;
    el.textContent = ` (${offscreen.length} not shown)`;
  });
  document.querySelectorAll("[data-needs-selection]").forEach((b) => (b.disabled = !selection.size));
}

document.addEventListener("click", (e) => {
  const library = document.querySelector(".library");
  if (!library) return;
  if (e.target.closest("[data-select-mode]")) {
    const on = library.classList.toggle("selecting");
    document.querySelectorAll("[data-select-mode][aria-pressed]").forEach((b) => b.setAttribute("aria-pressed", on));
    syncSelection();
  } else if (e.target.closest("[data-select-all]")) {
    document.querySelectorAll("#grid .select-box").forEach((b) => selection.add(b.value));
    syncSelection();
  } else if (e.target.closest("[data-select-none]")) {
    selection.clear();
    syncSelection();
  } else if (library.classList.contains("selecting")) {
    const card = e.target.closest("#grid .card");
    if (!card) return;
    const box = card.querySelector(".select-box");
    if (!e.target.matches(".select-box")) {
      e.preventDefault();
      box.checked = !box.checked;
    }
    if (box.checked) selection.add(box.value); else selection.delete(box.value);
    syncSelection();
  }
});

document.addEventListener("htmx:afterSwap", (e) => {
  if (e.detail.target.id !== "grid") return;
  if (deleting) selection.clear();  // they're gone
  deleting = false;
  syncSelection();
});
document.addEventListener("htmx:afterRequest", (e) => {
  if (e.detail.elt?.id === "bulk-form" && !e.detail.successful) deleting = false;
});
document.addEventListener("htmx:historyRestore", syncSelection);
syncSelection();

// The bulk bar floats over the bottom of the library; leave room for however tall it wraps
{
  const bar = document.querySelector(".bulk-bar");
  if (bar) new ResizeObserver(() => {
    bar.closest(".library").style.setProperty("--bulk-bar-h", `${bar.offsetHeight}px`);
  }).observe(bar);
}

// Deleting many albums deserves a confirm (capture phase: runs before htmx sees the submit)
document.addEventListener("submit", (e) => {
  if (e.submitter?.value !== "delete" || e.target.id !== "bulk-form") return;
  const n = selection.size;
  if (!confirm(`Delete ${n} album${n === 1 ? "" : "s"} from your library? This can't be undone.`)) {
    e.preventDefault();
    e.stopImmediatePropagation();
  } else {
    deleting = true;
  }
}, true);

// Triage (tagging albums one at a time): arrow keys, swipes, and never leaving a card before its edits are saved
{
  // Tracked by request, not element: when a save swaps out its own element (removing a genre),
  // htmx reports the end of the request on an ancestor instead
  const saving = new Set();
  let waiting = [];
  document.addEventListener("htmx:beforeRequest", (e) => {
    if (e.detail.elt?.closest?.("[data-save]")) saving.add(e.detail.xhr);
  });
  document.addEventListener("htmx:afterRequest", (e) => {
    if (!saving.delete(e.detail.xhr)) return;
    if (!e.detail.successful) {
      waiting = [];  // stay on this card until the error message is seen (see go())
      const box = e.detail.requestConfig?.triggeringEvent?.target;
      if (box?.matches?.(".vibe-toggles input[type=checkbox]")) {  // show the vibe as it's saved
        box.checked = !box.checked;
        count();
      }
      return;
    }
    setTimeout(() => {  // htmx starts a save queued behind this one just after it ends
      if (saving.size) return;
      waiting.forEach((go) => go());
      waiting = [];
    });
  });

  // Going through albums without vibes, the ones you tag leave the list, so Back follows the trail of
  // cards seen in this tab instead. It starts afresh whenever you arrive from outside triage.
  const TRAIL = "triage-trail";
  const readTrail = () => { try { return JSON.parse(sessionStorage.getItem(TRAIL)) || []; } catch { return []; } };
  const writeTrail = (t) => { try { sessionStorage.setItem(TRAIL, JSON.stringify(t.slice(-200))); } catch {} };
  const backLink = document.querySelector("[data-history]");
  if (backLink) {
    const here = location.pathname;
    let trail = readTrail();
    let fromCard = false;
    try { fromCard = /^\/triage\/\d+$/.test(new URL(document.referrer).pathname); } catch {}
    if (!fromCard) trail = [];
    if (trail.at(-2) === here) trail.pop();  // came back
    else if (trail.at(-1) !== here) trail.push(here);  // (the last one is a reload)
    writeTrail(trail);
    if (trail.length > 1) {
      backLink.href = trail.at(-2);
      backLink.hidden = false;
    }
  }

  const go = (dir) => {
    const link = document.querySelector(`[data-triage-nav="${dir}"]:not([hidden])`);
    if (!link) return;
    const error = document.getElementById("request-error");
    if (error) {  // an edit here didn't save: don't let it go unnoticed by moving on
      error.querySelector("button").focus();
      return;
    }
    const leave = () => { location.href = link.href; };
    if (saving.size) waiting.push(leave); else leave();
  };

  document.addEventListener("click", (e) => {
    const link = e.target.closest("[data-triage-nav]");
    if (!link || e.ctrlKey || e.metaKey || e.shiftKey) return;
    e.preventDefault();
    go(link.dataset.triageNav);
  });

  document.addEventListener("keydown", (e) => {
    if (!document.querySelector("[data-triage-card]") || e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target;
    if (t.matches?.("textarea, select, input:not([type=checkbox])")) return;
    if (e.key === "ArrowRight" || (e.key === "Enter" && !t.closest?.("a, button"))) {
      e.preventDefault();
      go("next");
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      go("prev");
    }
  });

  // Swipe the card left for the next album, right to go back
  let start = null;
  document.addEventListener("touchstart", (e) => {
    const card = e.target.closest("[data-triage-card]");
    start = card && !e.target.closest("input, button, a") && e.touches.length === 1
      ? { card, x: e.touches[0].clientX, y: e.touches[0].clientY } : null;
  }, { passive: true });
  document.addEventListener("touchmove", (e) => {
    if (!start) return;
    const dx = e.touches[0].clientX - start.x, dy = e.touches[0].clientY - start.y;
    if (Math.abs(dx) > Math.abs(dy)) start.card.style.transform = `translateX(${dx * 0.6}px) rotate(${dx / 40}deg)`;
  }, { passive: true });
  document.addEventListener("touchend", (e) => {
    if (!start) return;
    const { card, x, y } = start;
    start = null;
    const dx = e.changedTouches[0].clientX - x, dy = e.changedTouches[0].clientY - y;
    card.style.transform = "";
    if (Math.abs(dx) > 70 && Math.abs(dx) > 2 * Math.abs(dy)) go(dx < 0 ? "next" : "prev");
  });

  // Keep "N tagged · M left" in step with the vibes on this card
  const count = () => {
    const bar = document.querySelector(".triage-progress");
    if (!bar) return;
    const here = document.querySelector("[data-triage-card] input[name=vibes]:checked") ? 1 : 0;
    if (bar.dataset.leftOthers !== undefined) {  // going through albums without vibes
      bar.querySelector("[data-left]").textContent = Number(bar.dataset.leftOthers) + 1 - here;
      return;
    }
    const tagged = Number(bar.dataset.taggedOthers) + here;
    bar.querySelector("[data-tagged]").textContent = tagged;
    bar.querySelector("[data-left]").textContent = Number(bar.dataset.total) - tagged;
  };
  document.addEventListener("change", (e) => { if (e.target.closest("[data-triage-card]")) count(); });
  document.addEventListener("htmx:afterSwap", (e) => {
    if (e.detail.target.id !== "triage-vibes") return;
    count();
    if (!document.activeElement || document.activeElement === document.body) {
      document.querySelector("[data-new-vibe-open]")?.focus();  // the name box it had is gone
    }
  });

  // "+ new vibe" opens into a name box in its place; Esc (or leaving it empty) closes it again
  const closeNewVibe = (form, refocus) => {
    form.hidden = true;
    form.reset();
    form.previousElementSibling.hidden = false;
    if (refocus) form.previousElementSibling.focus();  // so arrow keys move between albums again
  };
  document.addEventListener("click", (e) => {
    const open = e.target.closest("[data-new-vibe-open]");
    if (!open) return;
    open.hidden = true;
    open.nextElementSibling.hidden = false;
    open.nextElementSibling.querySelector("input[name=name]").focus();
  });
  document.addEventListener("keydown", (e) => {
    const form = e.target.closest?.(".new-vibe");
    if (form && e.key === "Escape") closeNewVibe(form, true);
  });
  document.addEventListener("focusout", (e) => {
    const form = e.target.closest?.(".new-vibe");
    if (!form || form.contains(e.relatedTarget)) return;
    if (!form.querySelector("input[name=name]").value.trim()) closeNewVibe(form);
  });

  document.addEventListener("submit", (e) => {
    const title = e.target.dataset?.confirmRemove;
    if (title === undefined) return;
    if (!confirm(`Remove “${title}” from your library? This can't be undone.`)) e.preventDefault();
    else writeTrail(readTrail().filter((p) => p !== location.pathname));  // or Back would lead to it
  });

  // Coming back with the browser's back button shows a cached page; reload it so vibes are current
  window.addEventListener("pageshow", (e) => {
    if (e.persisted && document.querySelector("[data-triage-card]")) location.reload();
  });
}

// Random order: a new seed each time Random is picked or Shuffle is pressed (the seed rides along
// in the URL so the order holds until then). Capture phase, so it's set before htmx reads the form.
function newSeed(form) {
  form.querySelector("input[name=seed]").value = Math.floor(Math.random() * 1e9);
}
document.addEventListener("change", (e) => {
  if (e.target.matches?.("#filter-form select[name=order]") && e.target.value === "random") newSeed(e.target.form);
}, true);
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-shuffle]");
  if (!btn) return;
  newSeed(btn.form);
  htmx.trigger(btn.form, "change");
});
