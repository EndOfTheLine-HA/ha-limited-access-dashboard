(() => {
  const grid = document.querySelector("[data-state-url]");
  if (!grid) return;

  const refresh = async () => {
    try {
      const response = await fetch(grid.dataset.stateUrl, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return;
      const payload = await response.json();
      const availability = document.querySelector("#availability");
      if (availability) {
        availability.textContent = payload.reason || "Controls are unavailable right now.";
        availability.classList.toggle("hidden", payload.available);
      }
      for (const card of payload.cards) {
        const element = grid.querySelector(`[data-entity-id="${CSS.escape(card.entity_id)}"]`);
        if (!element) continue;
        element.classList.toggle("active", card.active);
        element.classList.toggle("unavailable", !card.available);
        const state = element.querySelector(".state-value");
        if (state) state.textContent = `${card.state}${card.unit ? ` ${card.unit}` : ""}`;
        const control = element.querySelector("button, .tile-action");
        if (control) {
          const disabled = !payload.available || !card.available;
          control.toggleAttribute("disabled", disabled);
          control.classList.toggle("disabled", disabled);
        }
      }
    } catch (_error) {
      // Keep the last known state. The next poll will retry.
    }
  };

  window.setInterval(refresh, 5000);
})();
