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
      window.dispatchEvent(new CustomEvent("lad-state", { detail: payload }));
      const availability = document.querySelector("#availability");
      if (availability) {
        availability.textContent = payload.reason || "Controls are unavailable right now.";
        availability.classList.toggle("hidden", payload.available);
      }
      for (const card of payload.cards) {
        for (const element of grid.querySelectorAll(`[data-entity-id="${CSS.escape(card.entity_id)}"]`)) {
        element.classList.toggle("active", card.active);
        element.classList.toggle("unavailable", !card.available);
        const state = element.querySelector(".state-value");
        if (state) state.textContent = `${card.state}${card.unit ? ` ${card.unit}` : ""}`;
        const climate = card.climate_controls;
        const current = element.querySelector('.climate-current');
        if (current) current.textContent = `Current: ${card.current_temperature == null ? 'Unavailable' : `${card.current_temperature} ${card.temperature_unit}`}`;
        const target = element.querySelector('.climate-target');
        if (target) target.textContent = climate ? `Target: ${climate.fields.map(field => climate.values[field] ?? '—').join(' / ')} ${card.temperature_unit}` : '';
        const climateForm = element.querySelector('[data-climate-fields]');
        const climateMismatch = climateForm && (!climate || climateForm.dataset.climateFields !== climate.fields.join(','));
        if (climateForm && climate && !climateMismatch) {
          for (const input of climateForm.querySelectorAll('input[type=number]')) {
            input.min = climate.min; input.max = climate.max;
            if (!input.dataset.edited && document.activeElement !== input) input.value = climate.values[input.name] ?? '';
            input.oninput = () => { input.dataset.edited = 'true'; };
          }
        }
        if (climateForm) climateForm.title = climateMismatch ? 'Reload the dashboard after a thermostat mode change.' : '';
        const operations = card.climate_operations;
        for (const form of element.querySelectorAll('[data-climate-operation]')) {
          const service = form.dataset.climateOperation;
          const select = form.querySelector('select');
          if (select) {
            const field = select.name;
            const choices = operations?.[field + 's'] || [];
            const selected = select.value;
            const current = operations?.[field];
            const desired = select.dataset.edited && choices.includes(selected) ? selected : current;
            if (!choices.includes(selected)) delete select.dataset.edited;
            const values = [...select.options].filter(option => option.value !== '').map(option => option.value);
            if (JSON.stringify(values) !== JSON.stringify(choices) || (!choices.includes(desired) && !select.querySelector('option[value=""]'))) {
              select.replaceChildren();
              const placeholder = new Option('Select mode', '', false, false);
              placeholder.disabled = true;
              select.append(placeholder);
              for (const choice of choices) select.append(new Option(choice.replaceAll('_', ' '), choice));
            }
            select.value = choices.includes(desired) ? desired : '';
            select.onchange = () => { select.dataset.edited = 'true'; };
            form.hidden = !choices.length;
          } else {
            form.hidden = !operations?.[service];
          }
        }
        for (const control of element.querySelectorAll("button, .tile-action, input[type='number'], select")) {
          const unsupported = Boolean(control.dataset.mediaAction) &&
            !card.media_controls.some(item => item.action === control.dataset.mediaAction);
          const disabled = !payload.available || !card.available || unsupported || Boolean(climateMismatch && climateForm.contains(control)) || Boolean(control.closest('[data-climate-operation]')?.hidden);
          control.toggleAttribute("disabled", disabled);
          control.classList.toggle("disabled", disabled);
        }
        }
      }
    } catch (_error) {
      // Keep the last known state. The next poll will retry.
    }
  };

  window.setInterval(refresh, 5000);
  refresh();
})();
