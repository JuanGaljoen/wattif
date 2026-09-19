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

// The two series, matching --solar / --wind in style.css. Validated for the
// dark surface: lightness band, chroma floor, protan dE 19.5, 3:1 contrast.
const SOLAR = '#c87d22';
const WIND  = '#35a3bd';

const HOURS_PER_DAY = 24;

// Daily capacity factor is mostly variance: wind swings from 0 to near 1.0
// day to day, and at full resolution its spikes bury solar's seasonal wave
// entirely. A centred 30-day mean shows the signal the chart exists for --
// and it brings the two series to comparable magnitudes, which matters
// because two y-scales are not an option.
const SMOOTH_DAYS = 30;

let chart = null;       // the uPlot instance
let series = null;      // { days: Int32Array(unix s), pv: [], wind: [] }

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
    color: SOLAR,
    weight: 2,
    fillColor: SOLAR,
    fillOpacity: 0.25,
  })
    .addTo(map)
    .bindTooltip(site.name, { direction: 'top', offset: [0, -8] })
    .on('click', () => select(site.id));

  markers.set(site.id, marker);
}

function select(siteId) {
  const site = sites.get(siteId);
  loadSeries(siteId, site.name);

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

/** Capacity factor is dimensionless; the farm's size is a choice, not data.
 *
 *  MWh/day = cf x rated MW x 24. Done here rather than server-side so that
 *  changing the capacity is instant and refetches nothing -- the API stays a
 *  pure read of what the aggregate holds (specs/slice-5a.md).
 */
function energy(cf, ratedMw) {
  return cf.map((v) => v * ratedMw * HOURS_PER_DAY);
}

/** Centred moving mean. Centred, not trailing: a trailing window shifts
 *  every peak half a window later, which would move the December solar
 *  maximum into January and quietly misstate the seasonality.
 *
 *  Windows are clipped at the ends rather than dropped, so the series keeps
 *  its length and its first and last days stay on the chart.
 */
function smooth(values, window) {
  const half = Math.floor(window / 2);
  const out = new Array(values.length);
  for (let i = 0; i < values.length; i++) {
    const lo = Math.max(0, i - half);
    const hi = Math.min(values.length - 1, i + half);
    let sum = 0;
    for (let j = lo; j <= hi; j++) sum += values[j];
    out[i] = sum / (hi - lo + 1);
  }
  return out;
}

function ratedMw() {
  const v = Number(document.getElementById('capacity').value);
  return Number.isFinite(v) && v > 0 ? v : 100;
}

// uPlot renders its legend as a sibling BELOW the canvas, inside the same
// box, and does not subtract it from the height you give it. Size the canvas
// against the flex-sized #chart box minus that strip, or the legend is
// pushed past the dock's edge and clipped.
const LEGEND_STRIP = 34;

function chartSize() {
  const box = document.getElementById('chart');
  return {
    width: box.clientWidth,
    height: Math.max(120, box.clientHeight - LEGEND_STRIP),
  };
}

function drawChart() {
  const mw = ratedMw();
  // series.pv / series.wind are ALREADY smoothed, once, at load. Smoothing
  // is linear, so scaling after it gives the same numbers as scaling before
  // -- and a capacity keystroke costs one multiply per point instead of
  // re-running a 30-wide window over 3,653 of them, twice.
  const data = [series.days, energy(series.pv, mw), energy(series.wind, mw)];

  if (chart) {
    chart.setData(data);
    return;
  }

  chart = new uPlot(
    {
      ...chartSize(),
      // uPlot's own legend carries the series names and their values under
      // the cursor, so identity never rests on colour alone.
      legend: { live: true },
      cursor: { drag: { x: true, y: false, setScale: true } },
      scales: { x: { time: true } },
      axes: [
        { stroke: '#93a3b4', grid: { stroke: '#2a3846', width: 1 },
          ticks: { stroke: '#2a3846' }, font: '13px Barlow Semi Condensed' },
        { stroke: '#93a3b4', grid: { stroke: '#2a3846', width: 1 },
          ticks: { stroke: '#2a3846' }, font: '13px Barlow Semi Condensed',
          label: 'MWh per day', labelFont: '14px Barlow Semi Condensed',
          labelSize: 34 },
      ],
      series: [
        { value: (u, t) =>
            t == null ? '--' : new Date(t * 1000).toISOString().slice(0, 10) },
        { label: 'Solar', stroke: SOLAR, width: 1.25,
          value: (u, v) => (v == null ? '--' : v.toFixed(0) + ' MWh') },
        { label: 'Wind', stroke: WIND, width: 1.25,
          value: (u, v) => (v == null ? '--' : v.toFixed(0) + ' MWh') },
      ],
    },
    data,
    document.getElementById('chart'),
  );
}

async function loadSeries(siteId, siteName) {
  const body = await fetch(`/api/sites/${siteId}/daily`).then((r) => r.json());

  // The API sends calendar dates ('2016-01-01'); uPlot wants unix seconds.
  // Parsed as UTC midnight, which is what the date means here -- the day is
  // already the site's own local day (docs/adr/0001).
  series = {
    days: body.days.map((d) => Date.parse(d + 'T00:00:00Z') / 1000),
    pv: smooth(body.pv_cf, SMOOTH_DAYS),
    wind: smooth(body.wind_cf, SMOOTH_DAYS),
  };

  document.getElementById('chart-title').textContent =
    `${siteName} — 30-day average generation, 2016-2025`;
  drawChart();
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
    // Asymmetric padding: the panels floating over the map are not
    // symmetrical. The site list takes ~256px of the top right, the detail
    // panel ~292px of the bottom left, and a marker under either of them is
    // a marker nobody can click.
    map.fitBounds(
      L.latLngBounds(siteList.map((s) => [s.latitude, s.longitude])),
      {
        paddingTopLeft: [300, 60],
        paddingBottomRight: [290, 60],
        maxZoom: 7,
      },
    );
    // Open on a site, never an empty shell (PLAN.md, build order item 5).
    select(siteList[0].id);
  }
}

document.getElementById('capacity').addEventListener('input', () => {
  if (series) drawChart();
});

window.addEventListener('resize', () => {
  if (chart) chart.setSize(chartSize());
});

start();
