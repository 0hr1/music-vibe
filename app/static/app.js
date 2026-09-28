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
