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
    box.querySelector("small")?.remove();
  }
});

// Same for the automatic Spotify lookup: a link typed in meanwhile wins
document.addEventListener("htmx:beforeSwap", (e) => {
  const box = e.detail.requestConfig?.elt;
  if (box?.matches?.("[data-spotify-auto]") && box.querySelector("input[name=spotify_url]").value.trim()) {
    e.detail.shouldSwap = false;
    box.querySelector("small")?.remove();
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
  } else if (e.target.closest("[data-select-all]")) {
    const boxes = [...document.querySelectorAll("#grid .select-box")];
    const all = boxes.every((b) => b.checked);
    boxes.forEach((b) => (b.checked = !all));
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
