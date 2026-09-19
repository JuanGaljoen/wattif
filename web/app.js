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

// The 30-day mean is the SUBDUED layer: it is a guide through the data,
// not the data. The daily series carries the full validated hue, because
// that is what actually happened -- and at close zoom the mean averages a
// large share of the visible window and says correspondingly little.
const SOLAR_MEAN = 'rgba(200, 125, 34, 0.55)';
const WIND_MEAN  = 'rgba(53, 163, 189, 0.55)';

const HOURS_PER_DAY = 24;

// Daily capacity factor is mostly variance: wind swings from 0 to near 1.0
// day to day, and at full resolution its spikes bury solar's seasonal wave
// entirely. A centred 30-day mean shows the signal the chart exists for --
// and it brings the two series to comparable magnitudes, which matters
// because two y-scales are not an option.
const SMOOTH_DAYS = 30;

let chart = null;       // the uPlot instance
let series = null;      // { days, pvRaw, windRaw, pv, wind } -- cf, unscaled
let lull = null;        // the window the chart shades: {start, end} unix s
let currentSite = null;
let relTimer = null;    // debounce for the range-driven metric refresh

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
  lull = null;                       // don't shade the old site's window
  currentSite = siteId;
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

/** Paint the selected lull as a band across the plot.
 *
 *  A uPlot draw hook rather than a series: the band is an annotation over
 *  the x range, not data. Drawn under the lines by running in `draw`, which
 *  fires before the series are stroked.
 */
function shadeLull(u) {
  if (!lull) return;

  const { ctx } = u;
  const left = u.bbox.left;
  const right = left + u.bbox.width;

  // Clamp to the plot area: at a wide zoom one edge is often off-screen,
  // and an unclamped rect paints over the axis gutter.
  const raw0 = u.valToPos(lull.start, 'x', true);
  const raw1 = u.valToPos(lull.end, 'x', true);
  if (raw1 < left || raw0 > right) return;   // entirely outside the view
  const x0 = Math.max(left, Math.min(right, raw0));
  const x1 = Math.max(left, Math.min(right, raw1));

  ctx.save();

  // The fill alone is only legible when the band is a middling fraction of
  // the view: a few pixels wide at 5y, and nearly the whole plot at 1w,
  // where an even tint reads as no band at all. The EDGES are what make it
  // identifiable at any zoom, so they are drawn whenever they are on
  // screen and the fill is secondary.
  ctx.fillStyle = 'rgba(232, 237, 242, 0.10)';
  ctx.fillRect(x0, u.bbox.top, Math.max(2, x1 - x0), u.bbox.height);

  ctx.strokeStyle = 'rgba(232, 237, 242, 0.55)';
  ctx.lineWidth = 1;
  ctx.setLineDash([3, 3]);
  for (const [pos, onScreen] of [
    [x0, raw0 >= left && raw0 <= right],
    [x1, raw1 >= left && raw1 <= right],
  ]) {
    if (!onScreen) continue;
    ctx.beginPath();
    ctx.moveTo(pos, u.bbox.top);
    ctx.lineTo(pos, u.bbox.top + u.bbox.height);
    ctx.stroke();
  }

  ctx.restore();
}

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
  const data = [
    series.days,
    energy(series.pv, mw),        // 30-day mean, drawn first (underneath)
    energy(series.wind, mw),
    energy(series.pvRaw, mw),     // daily, drawn on top
    energy(series.windRaw, mw),
  ];

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
      hooks: {
        draw: [shadeLull],
        // Metrics follow the view: every change of the x scale -- a range
        // button, a drag-zoom, a jump to a lull -- re-asks for the numbers
        // over what is now on screen.
        setScale: [(u, key) => { if (key === 'x' && currentSite) scheduleMetricRefresh(); }],
      },
      // Order is draw order. The mean is wide and soft and goes down
      // first; the daily line is thin and full-strength and sits on top,
      // because it is the measurement and the mean is the guide.
      series: [
        { label: 'Date',
          value: (u, t) =>
            t == null ? '--' : new Date(t * 1000).toISOString().slice(0, 10) },
        { label: 'Solar, 30-day', stroke: SOLAR_MEAN, width: 2.5,
          points: { show: false },
          value: (u, v) => (v == null ? '--' : v.toFixed(0) + ' MWh') },
        { label: 'Wind, 30-day', stroke: WIND_MEAN, width: 2.5,
          points: { show: false },
          value: (u, v) => (v == null ? '--' : v.toFixed(0) + ' MWh') },
        { label: 'Solar, daily', stroke: SOLAR, width: 1,
          points: { show: false },
          value: (u, v) => (v == null ? '--' : v.toFixed(0) + ' MWh') },
        { label: 'Wind, daily', stroke: WIND, width: 1,
          points: { show: false },
          value: (u, v) => (v == null ? '--' : v.toFixed(0) + ' MWh') },
      ],
    },
    data,
    document.getElementById('chart'),
  );
}

/** The metrics panel.
 *
 *  Worst-24h and worst-7d cells are buttons: clicking one shades that
 *  window on the chart. The numbers are the answer; the shading is what
 *  makes an abstract 0.0141 mean something.
 */
function renderReliability(body) {
  const tbody = document.getElementById('reliability-body');
  tbody.replaceChildren();

  const row = (label) => {
    const tr = document.createElement('tr');
    tr.append(Object.assign(document.createElement('th'),
      { scope: 'row', textContent: label }));
    tbody.append(tr);
    return tr;
  };

  const blank = (tr, n, why) => {
    for (let i = 0; i < n; i++) {
      tr.append(Object.assign(document.createElement('td'),
        { textContent: '\u2014', className: 'na', title: why }));
    }
  };

  const TOO_SHORT = 'the visible range is too short for this';

  for (const [label, key] of [['Worst 24 hours', 'worst_24h'],
                              ['Worst 7 days', 'worst_7d']]) {
    const tr = row(label);
    if (!body[key] || !body[key].pv) { blank(tr, 3, TOO_SHORT); continue; }
    for (const resource of ['pv', 'wind', 'hybrid']) {
      const cell = body[key][resource];
      const td = document.createElement('td');
      const b = document.createElement('button');
      b.textContent = cell.cf.toFixed(3);
      b.title = `${cell.start.slice(0, 10)} to ${cell.end.slice(0, 10)}`;
      b.addEventListener('click', () => markLull(key, resource, body));
      td.append(b);
      tr.append(td);
    }
  }

  const h = body.hours_below_10pct;
  const hoursRow = row(h.per === 'year'
    ? 'Hours below 10%, a year'
    : 'Hours below 10%, in view');
  hoursRow.append(Object.assign(document.createElement('td'),
    { textContent: h.pv_daylight.toLocaleString() }));
  hoursRow.append(Object.assign(document.createElement('td'),
    { textContent: h.wind.toLocaleString() }));
  blank(hoursRow, 1, 'not meaningful for a mixed farm');

  for (const [label, pick] of [['P50 annual', 'p50'], ['P90 annual', 'p90']]) {
    const tr = row(label);
    if (!body.annual) { blank(tr, 3, 'needs at least two full years in view'); continue; }
    tr.append(Object.assign(document.createElement('td'),
      { textContent: body.annual.pv[pick].toFixed(3) }));
    tr.append(Object.assign(document.createElement('td'),
      { textContent: body.annual.wind[pick].toFixed(3) }));
    blank(tr, 1, 'not meaningful for a mixed farm');
  }

  const w = body.window;
  document.getElementById('reliability-range').textContent =
    w.start && w.end ? `${w.start} to ${w.end}` : 'all ten years';
}

const RESOURCE_LABEL = { pv: 'Solar', wind: 'Wind', hybrid: '50/50' };

const isoDay = (unixSeconds) =>
  new Date(unixSeconds * 1000).toISOString().slice(0, 10);

/** The metrics describe whatever is on screen, not always the whole decade.
 *
 *  Debounced because a drag-zoom fires setScale continuously and each call
 *  is a real query over the aggregate.
 */
function scheduleMetricRefresh() {
  clearTimeout(relTimer);
  relTimer = setTimeout(() => {
    const { min, max } = chart.scales.x;
    loadReliability(currentSite, isoDay(min), isoDay(max));
  }, 300);
}

async function loadReliability(siteId, start, end) {
  const q = start && end ? `?start=${start}&end=${end}` : '';
  const body = await fetch(`/api/sites/${siteId}/reliability${q}`)
    .then((r) => r.json());
  // A late response from an earlier range must not overwrite a newer one.
  if (siteId !== currentSite) return;
  renderReliability(body);
}

const YEAR = 365.25 * 86400;

// Anchored on the CURRENT view's centre, not on the end of the data: after
// jumping to a lull in 2017, "3 months" should mean three months around
// that lull, not a jump back to 2025.
const RANGES = [
  ['All', null],
  ['5y', 5 * YEAR],
  ['1y', YEAR],
  ['3m', YEAR / 4],
  ['1w', 7 * 86400],
];

function dataBounds() {
  return [series.days[0], series.days[series.days.length - 1]];
}

function setRange(span, label) {
  const [lo, hi] = dataBounds();

  if (span === null) {
    chart.setScale('x', { min: lo, max: hi });
  } else {
    const { min, max } = chart.scales.x;
    const centre = (min + max) / 2;
    let a = centre - span / 2;
    let b = centre + span / 2;
    if (a < lo) [a, b] = [lo, Math.min(hi, lo + span)];
    if (b > hi) [a, b] = [Math.max(lo, hi - span), hi];
    chart.setScale('x', { min: a, max: b });
  }
  markRangeButton(label);
}

function markRangeButton(label) {
  for (const b of document.querySelectorAll('#ranges button')) {
    b.setAttribute('aria-pressed', String(b.textContent === label));
  }
}

// uPlot's own legend already toggles a single series -- that is what the
// hint points at. These toggle a whole LAYER, which is the thing actually
// wanted: at close zoom a 30-day mean averages a third of the visible
// window and says very little, so being able to drop it in one click
// matters more than hiding solar alone.
const LAYERS = [
  ['Daily', [3, 4]],
  ['30-day', [1, 2]],
];

function buildLayerToggles() {
  const box = document.getElementById('layers');
  box.replaceChildren();
  for (const [label, indices] of LAYERS) {
    const b = document.createElement('button');
    b.textContent = label;
    b.setAttribute('aria-pressed', 'true');
    b.addEventListener('click', () => {
      const on = b.getAttribute('aria-pressed') !== 'true';
      b.setAttribute('aria-pressed', String(on));
      for (const i of indices) chart.setSeries(i, { show: on });
    });
    box.append(b);
  }
}

function buildRangeButtons() {
  const box = document.getElementById('ranges');
  box.replaceChildren();
  for (const [label, span] of RANGES) {
    const b = document.createElement('button');
    b.textContent = label;
    b.setAttribute('aria-pressed', String(label === 'All'));
    b.addEventListener('click', () => setRange(span, label));
    box.append(b);
  }
}

function markLull(key, resource, body) {
  const cell = body[key][resource];
  const asChartTime = (v) =>
    Date.parse(v.length === 10 ? v + 'T00:00:00Z' : v + 'Z') / 1000;
  lull = { start: asChartTime(cell.start), end: asChartTime(cell.end) };
  const span = key === 'worst_24h' ? 'worst 24 hours' : 'worst 7 days';
  document.getElementById('reliability-hint').textContent =
    `${RESOURCE_LABEL[resource]}: ${span}, ` +
    `${cell.start.slice(0, 10)} to ${cell.end.slice(0, 10)}`;

  // Zoom to it, with the window itself about a third of the view -- enough
  // surrounding weather to see that it IS a lull rather than the norm.
  if (chart) {
    const pad = Math.max((lull.end - lull.start), 7 * 86400);
    const [lo, hi] = dataBounds();
    chart.setScale('x', {
      min: Math.max(lo, lull.start - pad),
      max: Math.min(hi, lull.end + pad),
    });
    markRangeButton(null);
  }

  for (const b of document.querySelectorAll('.reliability button')) {
    b.setAttribute('aria-pressed', 'false');
  }
  const idx = ['pv', 'wind', 'hybrid'].indexOf(resource);
  const row = key === 'worst_24h' ? 0 : 1;
  document.querySelectorAll('.reliability tbody tr')[row]
    .querySelectorAll('button')[idx].setAttribute('aria-pressed', 'true');

  if (chart) chart.redraw();
}

async function loadSeries(siteId, siteName) {
  const body = await fetch(`/api/sites/${siteId}/daily`).then((r) => r.json());

  // The API sends calendar dates ('2016-01-01'); uPlot wants unix seconds.
  // Parsed as UTC midnight, which is what the date means here -- the day is
  // already the site's own local day (docs/adr/0001).
  series = {
    days: body.days.map((d) => Date.parse(d + 'T00:00:00Z') / 1000),
    pvRaw: body.pv_cf,
    windRaw: body.wind_cf,
    pv: smooth(body.pv_cf, SMOOTH_DAYS),
    wind: smooth(body.wind_cf, SMOOTH_DAYS),
  };

  document.getElementById('chart-title').textContent =
    `${siteName} — daily generation, 2016-2025`;
  drawChart();

  buildRangeButtons();
  buildLayerToggles();

  const rel = await fetch(`/api/sites/${siteId}/reliability`).then((r) => r.json());
  renderReliability(rel);
  // Open on wind's worst week: the deepest lull at every site, and the one
  // that makes the case for the hybrid figure beside it. Only on load --
  // doing it after every refresh would move the view that triggered it.
  markLull('worst_7d', 'wind', rel);
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
