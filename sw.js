/* Kayseri Parsel Rota - cevrimdisi calisma (service worker) */
const SURUM = "r57";
const KABUK = "kabuk-" + SURUM;
const DINAMIK = "dinamik-v1";
const UYDU = "uydu-v1";
const ON_YUKLE = [
  "./",
  "./index.html",
  "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css",
  "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js",
  "https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js"
];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(KABUK).then(c => Promise.allSettled(ON_YUKLE.map(u => c.add(u)))).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k.startsWith("kabuk-") && k !== KABUK).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});

function agOnce(req, cacheName) { // once ag, olmazsa onbellek
  return fetch(req).then(res => {
    if (res && res.ok) { const kopya = res.clone(); caches.open(cacheName).then(c => c.put(req, kopya)); }
    return res;
  }).catch(() => caches.match(req).then(r => r || new Response("", { status: 504, statusText: "cevrimdisi" })));
}
function onbellekOnce(req, cacheName) { // once onbellek, yoksa ag
  return caches.match(req).then(r => r || fetch(req).then(res => {
    if (res && res.ok) { const kopya = res.clone(); caches.open(cacheName).then(c => c.put(req, kopya)); }
    return res;
  }));
}

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const u = new URL(req.url);
  if (u.pathname.includes("/api/interpreter") || u.hostname.includes("project-osrm.org") || u.hostname.includes("docs.google.com")) return;
  if (u.origin === self.location.origin) {
    if (u.pathname.endsWith("/sw.js")) return;
    if (u.pathname.includes("/d/") && u.pathname.endsWith(".bin")) return; // sifreli veri, yol agi ve sinir karolari uygulama icinde (IndexedDB) saklanir
    e.respondWith(agOnce(req, u.pathname.includes("/d/") ? DINAMIK : KABUK));
    return;
  }
  if (u.hostname === "tiles.maps.eox.at") { e.respondWith(onbellekOnce(req, UYDU)); return; }
  if (u.hostname.includes("cdnjs.cloudflare.com") || u.hostname.includes("fonts.googleapis.com") || u.hostname.includes("fonts.gstatic.com")) { e.respondWith(onbellekOnce(req, KABUK)); return; }
  if (u.hostname.includes("arcgisonline.com") || u.hostname.includes("tile.openstreetmap.org")) { e.respondWith(agOnce(req, DINAMIK)); return; }
});
