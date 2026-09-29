// Filter drawer on phones
document.addEventListener("click", (e) => {
  if (e.target.closest("[data-open-filters]")) document.body.classList.add("filters-open");
  if (e.target.closest("[data-close-filters]")) document.body.classList.remove("filters-open");
});

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

// Slow form posts (import): show that something is happening and prevent double submits
document.addEventListener("submit", (e) => {
  const form = e.target;
  if (!form.dataset.busy) return;
  const btn = form.querySelector("button[type=submit]");
  if (btn) {
    btn.disabled = true;
    btn.textContent = form.dataset.busy;
  }
});

// Enter in an inline search box runs its search instead of submitting the surrounding form
document.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || !e.target.matches?.("[data-enter-click]")) return;
  e.preventDefault();
  e.target.parentElement.querySelector("button")?.click();
});

// Library select mode (bulk edit): toggle cards, keep the count and buttons in sync
function syncSelection() {
  const boxes = [...document.querySelectorAll("#grid .select-box")];
  const n = boxes.filter((b) => b.checked).length;
  document.querySelectorAll("[data-bulk-count]").forEach((el) => (el.textContent = n));
  document.querySelectorAll("[data-needs-selection]").forEach((b) => (b.disabled = n === 0));
}

document.addEventListener("click", (e) => {
  const library = document.querySelector(".library");
  if (!library) return;
  if (e.target.closest("[data-select-mode]")) {
    const on = library.classList.toggle("selecting");
    document.querySelectorAll("[data-select-mode][aria-pressed]").forEach((b) => b.setAttribute("aria-pressed", on));
    if (!on) document.querySelectorAll("#grid .select-box").forEach((b) => (b.checked = false));
    syncSelection();
  } else if (e.target.closest("[data-select-all], [data-select-none]")) {
    const on = !!e.target.closest("[data-select-all]");
    document.querySelectorAll("#grid .select-box").forEach((b) => (b.checked = on));
    syncSelection();
  } else if (library.classList.contains("selecting")) {
    const card = e.target.closest("#grid .card");
    if (!card) return;
    if (!e.target.matches(".select-box")) {
      e.preventDefault();
      const box = card.querySelector(".select-box");
      box.checked = !box.checked;
    }
    syncSelection();
  }
});

document.addEventListener("htmx:afterSwap", (e) => {
  if (e.detail.target.id === "grid") syncSelection();
});
syncSelection();

// Deleting many albums deserves a confirm (capture phase: runs before htmx sees the submit)
document.addEventListener("submit", (e) => {
  if (e.submitter?.value !== "delete" || e.target.id !== "bulk-form") return;
  const n = document.querySelectorAll("#grid .select-box:checked").length;
  if (!confirm(`Delete ${n} album${n === 1 ? "" : "s"} from your library? This can't be undone.`)) {
    e.preventDefault();
    e.stopImmediatePropagation();
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
    setTimeout(() => {  // htmx starts a save queued behind this one just after it ends
      if (saving.size) return;
      waiting.forEach((go) => go());
      waiting = [];
    });
  });

  // Came here from another card? Then Back can simply go back in the browser's history.
  const fromCard = (() => {
    try {
      const from = new URL(document.referrer);
      return from.origin === location.origin && /^\/triage\/\d+$/.test(from.pathname);
    } catch { return false; }
  })();
  if (fromCard) document.querySelectorAll("[data-history]").forEach((link) => (link.hidden = false));

  const go = (dir) => {
    const link = document.querySelector(`[data-triage-nav="${dir}"]:not([hidden])`);
    if (!link) return;
    const leave = () => {
      if (link.dataset.history !== undefined && fromCard) history.back();
      else location.href = link.href;
    };
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
    if (title !== undefined && !confirm(`Remove “${title}” from your library? This can't be undone.`)) e.preventDefault();
  });

  // Coming back with the browser's back button shows a cached page; reload it so vibes are current
  window.addEventListener("pageshow", (e) => {
    if (e.persisted && document.querySelector("[data-triage-card]")) location.reload();
  });
}
