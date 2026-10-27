# Dashboard editor and compatibility testing

Version 0.6.1 includes the owner appearance editor, the 0.5.0 session and login
fixes, and the fix for light buttons changing enabled state on every refresh.
Custom Lovelace/HACS card support remains experimental.

## Try the editor without changing Home Assistant

From the repository, run:

```powershell
.venv/Scripts/python.exe tests/preview_editor.py --port 8768
```

Open <http://127.0.0.1:8768/editor> for the owner editor and
<http://127.0.0.1:8768/limited-access-dashboard/demo> for the guest page.
These pages use fictional devices. Their data resets when the fixture restarts;
they do not operate real devices. The preview server binds only to loopback and
has simulated owner authentication; do not expose it as a real guest dashboard.

## Install a manual integration build for HA testing

HACS users can update normally. To create a manual archive from the repository,
commit your changes, then run `python scripts/build_release.py`; the archive is
written to `dist/`. The builder reads committed Git blobs (HEAD by default),
ignores working-tree and untracked files, and uses uncompressed fixed-metadata
ZIP entries for the same bytes across operating systems and zlib versions.
Pass `--ref v0.6.1` to build a particular tag. Tagged releases are built and
published by CI only after the full CI, HACS and Hassfest jobs pass.
Extract it into the HA configuration directory after backing up the existing
integration and its data.

1. Keep a copy of the existing `custom_components/limited_access_dashboard` folder.
2. Copy the build's `custom_components/limited_access_dashboard` folder into the
   same location under the HA configuration directory. Keep all existing config
   entries and the `limited-access-dashboard-data` directory.
3. Restart HA and refresh the browser. Use an HTTPS address for guest sign-in;
   the default secure cookies cannot be used over plain HTTP.
4. Sign in as the HA **owner** (the original/main admin account). Open **Guest
   dashboard editor** in the sidebar and choose an existing guest dashboard.
5. Use the integration's **Configure** flow to add/remove approved entities,
   change credentials or access hours. The new setup/options toggle enables
   guest customization. The editor has the same toggle.

Configure starts with an **Edit appearance** link that opens the selected
dashboard directly in the owner editor. The final share screen includes it too.
New setup also includes **Choose dashboard appearance** for theme, columns,
spacing, accent and optional tabs grouped by entity type. Save setup before
opening the full editor. These controls work with guest customization off.

The editor API and UI flow configuration require the HA owner. Other admins can
still manage HA itself, including removing integrations; this feature does not
change HA's overall administrator permissions.

## Layout editing

- Create tabs, such as Media, Lights, Plugs, and Sensors.
- Within each tab, add named sections and entity cards.
- Expand a card to rename it, select an accent, make it full width, or choose
  tile, compact or state-only presentation. State-only hides its controls; it does
  not revoke permission to operate that entity through another approved card.
- Move cards up/down or into a different tab/section using **Move to section**.
- Set one to four desktop columns, dark/light appearance, colors and density.
  Small screens automatically use fewer columns. The preview shows placement;
  use the guest page for functional card testing.
- Save the dashboard. Data persists in the per-dashboard SQLite database.
- After Configure adds entities to a saved layout, the owner editor lists them
  under **Approved entities not placed**. Choose where to place them. Removed
  entities are pruned from owner/guest presentation without resetting other
  placements. A custom card explicitly referencing a removed entity is removed
  as a whole; backend permission checks still apply to every custom-card action.
- **View recent activity** below the owner editor shows the latest 100 audit rows.

The owner controls available entities/actions and module URLs. When **Allow guest
customization** is on, guests can change presentation and arrange the already
approved cards. They cannot add module URLs or alter Lovelace configurations.
Their design is shared across sessions of that dashboard's single guest account.
Saving the owner's design resets the guest override. Turning customization off
shows the owner's design on the next page load and rejects further guest saves.

## Try a HACS card

Install the card in HACS as usual. In the owner editor:

1. Expand **Experimental Lovelace / HACS resources**.
2. Enter its installed module URL, one per line, for example
   `/hacsfiles/example-card/example-card.js`. `/local/` module URLs and query
   version strings are also supported.
3. Add a **Lovelace / HACS card**, expand it, and paste its configuration as JSON:

```json
{
  "type": "custom:example-card",
  "entity": "sensor.guest_room_temperature"
}
```

Use the actual card type and an entity already approved for that dashboard.
Save, open the guest page, and observe the result. Missing modules/elements,
bridge failures, explicit unsupported API calls and unsupported `hass-action` /
`hass-more-info` events produce visible messages. Failures in asynchronous card
code may appear only in browser developer tools; not every incompatibility can
be intercepted. Modules load concurrently with a 10-second timeout window.

This host loads real JavaScript modules and passes their elements `setConfig`
and `hass`. It supplies approved states and a restricted `callService` bridge,
plus basic formatting functions and `loadCardHelpers`. It does not supply a
Home Assistant access token or full HA API connection.

| Feature | This build |
| --- | --- |
| Local `custom:` modules | Experimental loading and state updates |
| Entities, glance, tile, button, entity, sensor, media-control | Guest-host equivalents using existing approved controls |
| Vertical/horizontal stacks and grids | Supported, including nested cards |
| Heading / markdown | Heading supported; markdown content is rendered as plain text |
| Lights, switches, toggle helpers | Approved on/off/toggle actions |
| Media players | Existing supported power/playback/volume actions |
| Sensors / climate | Display only |
| Sprinklers | Existing native timed/confirmation controls; custom cards cannot bypass these |
| Brightness/color service parameters | Not implemented in the service bridge yet |
| History, recorder, camera streams, arbitrary WebSocket/REST calls | Not implemented; explicit adapter calls fail visibly |
| Full HA frontend component library, JavaScript templates/eval | Not supplied; cards requiring these may fail |

Module code is trusted code running on the guest page's origin; this is not an
iframe sandbox. Only the owner may add resource URLs. Do not treat filtering the
`hass` object as a sandbox for malicious JavaScript. The API still enforces guest
entity permissions, schedules and CSRF for service calls. External module URLs
are not accepted. Use a separate browser profile for guest-card compatibility
tests so installed cards do not share the owner's browser storage.

Additional host limits:

- The guest host only defines basic `ha-card` and `ha-icon` elements. The icon
  primitive renders a generic diamond; the full HA frontend is not loaded.
- Entity/device/area registries are empty placeholders; `hass.connection` is not
  provided. Cards requiring registry metadata or HA's internal Lit components
  may fail to initialize or render.
- The state payload exposes a limited attribute allowlist, not every HA state
  attribute. For example, entity pictures and climate control metadata are not
  supplied. Language is English and the adapter unit-system metadata is metric;
  this does not convert the actual entity values or their units.
- Polling creates a new adapter and state objects every five seconds. Cards
  depending on HA subscription/connection behavior may not update correctly.
- Dashboard CSP blocks eval/JavaScript templates, external network requests,
  external images, fonts and blob URLs. Packages such as button-card templates,
  card-mod, or cards depending on the Lovelace frontend need individual testing;
  no compatibility claim is made for those packages.

On upgrade, data under HA's configured `limited-access-dashboard-data` directory
moves from slug filenames to stable configuration entry IDs. URL-name edits then
keep layouts and history. Existing external YAML database paths are copied into
the managed directory and the original is retained. The integration never deletes
external databases. Old orphaned files from URL renames made before this fix are
not automatically recovered or removed; restore needed layouts from a backup
before editing them. Removing an entry deletes its managed layouts, sessions and
audit history.

This is intended to discover compatibility gaps with the actual cards you use.
It does not claim universal compatibility with Lovelace or HACS cards.

## Validation

```powershell
.venv/Scripts/python.exe -m pytest -q
node --test tests/test_refresh.cjs
python -m ruff check .
python -m ruff format --check .
```

With HA's dependencies installed, run `tests/verify_ha_flow.py REPOSITORY_ROOT`
and `tests/verify_editor_backend.py REPOSITORY_ROOT`. The backend checks use
real HTTP views and session storage with fake device services.

With the preview server running and Playwright installed, run
`tests/verify_editor_browser.py OUTPUT_DIRECTORY` to verify save/reload, tabs,
custom module loading, guest editing, polling and mobile layout.
