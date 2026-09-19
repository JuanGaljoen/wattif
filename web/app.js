// wattif — the map. CP3 adds the generation chart.
//
// Same-origin relative paths throughout: never an absolute URL. That is what
// makes putting a reverse proxy in front of this later a config change with
// no code change, and no CORS (specs/slice-5a.md, Containers).

const CARTO_TILES =
  'https://basemaps.cartocdn.com/rastertiles/dark_all/{z}/{x}/{y}.png?key=';
const OSM_TILES = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';

// Verbatim from each provider's own terms. CARTO serves OpenStreetMap data
// in its own styling, so it requires both credits.
const OSM_CREDIT =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';
const CARTO_CREDIT =
  OSM_CREDIT +
  ', &copy; <a href="https://carto.com/attributions">CARTO</a>';

// Fallback view only. The real framing is fitBounds() over the sites once
// they load -- hardcoded centre/zoom put Polokwane and Gqeberha off-screen
// on a short window, and any hand-picked pair is wrong at some viewport.
const FALLBACK_VIEW = [-29.0, 24.5];
const FALLBACK_ZOOM = 6;

const map = L.map('map', {
  // Not top-left: the masthead is there. Leaflet stacks it above the
  // attribution in the bottom-right corner.
  zoomControl: false,
  attributionControl: true,
}).setView(FALLBACK_VIEW, FALLBACK_ZOOM);

L.control.zoom({ position: 'bottomright' }).addTo(map);

const markers = new Map();

/** Dark Matter when a key is set; OSM inverted in CSS when it is not.
 *
 *  Without a key CARTO serves tiles stamped "API KEY REQUIRED", which looks
 *  broken rather than unconfigured — so a clone with no key gets OSM and the
 *  CSS filter instead. The filter belongs ONLY to that path: applied to
 *  Dark Matter it would invert an already-dark map back to white.
 */
function addBasemap(cartoKey) {
  if (cartoKey) {
    L.tileLayer(CARTO_TILES + cartoKey, {
      attribution: CARTO_CREDIT,
      maxZoom: 19,
    }).addTo(map);
  } else {
    L.tileLayer(OSM_TILES, { attribution: OSM_CREDIT, maxZoom: 19 }).addTo(map);
    document.getElementById('map').classList.add('invert-tiles');
  }
}

/** Circle markers, not Leaflet's default pin.
 *
 *  Two reasons: a pin is a place, a circle is a measurement — and CP3 sizes
 *  and colours these by capacity factor. It also sidesteps Leaflet's
 *  Icon.Default.imagePath detection, which breaks under a nested static
 *  directory (docs/research/2026-09-19-frontend-deps.md).
 */
function addMarker(site) {
  const marker = L.circleMarker([site.latitude, site.longitude], {
    radius: 7,
    color: '#f2a03d',
    weight: 2,
    fillColor: '#f2a03d',
    fillOpacity: 0.25,
  })
    .addTo(map)
    .bindTooltip(site.name, { direction: 'top', offset: [0, -8] })
    .on('click', () => select(site.id));

  markers.set(site.id, marker);
}

function select(siteId) {
  const site = sites.get(siteId);

  for (const [id, marker] of markers) {
    const on = id === siteId;
    marker.setStyle({ radius: on ? 10 : 7, fillOpacity: on ? 0.65 : 0.25 });
    if (on) marker.bringToFront();
  }

  for (const button of document.querySelectorAll('.sites button')) {
    button.setAttribute('aria-current', String(Number(button.dataset.id) === siteId));
  }

  const detail = document.getElementById('detail');
  detail.hidden = false;
  document.getElementById('detail-name').textContent = site.name;
  document.getElementById('detail-lat').textContent = site.latitude.toFixed(2) + '°';
  document.getElementById('detail-lon').textContent = site.longitude.toFixed(2) + '°';
  document.getElementById('detail-elev').textContent =
    site.elevation_m === null ? 'unknown' : Math.round(site.elevation_m) + ' m';
  document.getElementById('detail-tilt').textContent = Math.round(site.tilt_deg) + '°';
  // 180 means NORTH. Southern-hemisphere arrays face the equator, and the
  // stored number reads backwards to anyone who assumes otherwise — so the
  // page says the direction rather than the degrees (CLAUDE.md, The traps).
  document.getElementById('detail-facing').textContent =
    site.azimuth_deg === 180 ? 'north' : Math.round(site.azimuth_deg) + '°';
}

const sites = new Map();

async function start() {
  const [config, siteList] = await Promise.all([
    fetch('/api/config').then((r) => r.json()),
    fetch('/api/sites').then((r) => r.json()),
  ]);

  addBasemap(config.carto_key);

  const nav = document.getElementById('sites');
  for (const site of siteList) {
    sites.set(site.id, site);
    addMarker(site);

    const button = document.createElement('button');
    button.textContent = site.name;
    button.dataset.id = site.id;
    button.setAttribute('aria-current', 'false');
    button.addEventListener('click', () => select(site.id));
    nav.append(button);
  }

  if (siteList.length) {
    // Frame every site, whatever the window. The padding keeps markers off
    // the panels that float over the map's corners.
    map.fitBounds(
      L.latLngBounds(siteList.map((s) => [s.latitude, s.longitude])),
      { padding: [80, 80], maxZoom: 7 },
    );
    // Open on a site, never an empty shell (PLAN.md, build order item 5).
    select(siteList[0].id);
  }
}

start();
