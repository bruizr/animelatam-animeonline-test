const express = require("express");
const { addonBuilder } = require("stremio-addon-sdk");

const PORT = process.env.PORT || 3000;
const TIMEOUT_MS = Number(process.env.PROVIDER_TIMEOUT_MS || 9000);
const MAX_RESULTS = Number(process.env.MAX_RESULTS_PER_PROVIDER || 30);

const manifest = {
  id: "com.animelatam.torrents.latino",
  version: "1.0.0",
  name: "AnimeLatam Torrents Latino",
  description: "Buscador de torrents de anime. Solo muestra publicaciones que declaran explícitamente audio español latino.",
  resources: ["catalog", "stream"],
  types: ["series", "movie"],
  catalogs: [{ type: "series", id: "anime-latino-torrents", name: "Torrents Anime Latino", extra: [{ name: "search", isRequired: true }] }],
  idPrefixes: ["altt:"]
};

const providers = [
  { id: "nyaa", name: "Nyaa", status: "active", kind: "nyaa-rss", url: "https://nyaa.si/" },
  { id: "tokyotosho", name: "Tokyo Toshokan", status: "active", kind: "tokyo-rss", url: "https://www.tokyotosho.info/" },
  { id: "thepiratebay", name: "The Pirate Bay", status: "active", kind: "tpb-api", url: "https://thepiratebay.org/" },
  { id: "subsplease", name: "SubsPlease", status: "pending", kind: "manual", url: "https://subsplease.org/", reason: "Su catálogo habitual no declara audio español latino de forma verificable." },
  { id: "animetosho", name: "AnimeTosho", status: "offline", kind: "manual", url: "https://animetosho.org/", reason: "El servicio anunció el cierre de sus feeds y API en mayo de 2026." },
  { id: "animetosho-nzb", name: "AnimeTosho NZB", status: "offline", kind: "manual", url: "https://animetosho.org/", reason: "Depende del servicio AnimeTosho, cuyos feeds/API fueron cerrados." },
  { id: "tsukihime-nzb", name: "TsukiHime NZB - EasyNews", status: "excluded", kind: "manual", url: "https://tsukihime.org/", reason: "Excluido por decisión del proyecto: no se integrará Easynews." },
  { id: "frai-raws", name: "Frai-raws", status: "pending", kind: "manual", url: "https://f-r.site/" },
  { id: "seadex", name: "SeaDex", status: "pending", kind: "manual", url: "https://releases.moe/" },
  { id: "nekobt", name: "NekoBT", status: "pending", kind: "manual", url: "https://nekobt.to/" },
  { id: "animegarden", name: "AnimeGarden", status: "pending", kind: "manual", url: "https://animes.garden/" },
  { id: "tsukihime", name: "TsukiHime Torrents", status: "pending", kind: "manual", url: "https://tsukihime.org/" },
  { id: "mikan", name: "Mikan Project", status: "pending", kind: "manual", url: "https://mikanani.me/" },
  { id: "dmhy", name: "DMHY", status: "pending", kind: "manual", url: "https://dmhy.org/" },
  { id: "anibt", name: "AniBT", status: "pending", kind: "manual", url: "https://anitb.org/" },
  { id: "agrip", name: "AG.RIP", status: "pending", kind: "manual", url: "https://ag-rip.com/" },
  { id: "knaben", name: "Knaben", status: "pending", kind: "manual", url: "https://knaben.eu/" },
  { id: "torrentgalaxy", name: "TorrentGalaxy", status: "pending", kind: "manual", url: "https://torrentgalaxy.to/" },
  { id: "torlock", name: "Torlock", status: "pending", kind: "manual", url: "https://www.torlock.com/" },
  { id: "zilean", name: "Zilean DMM", status: "pending", kind: "manual", url: "https://zilean.dev/" },
  { id: "comet", name: "Comet", status: "pending", kind: "manual", url: "https://comet.elfhosted.com/" },
  { id: "stremthru", name: "StremThru Torz", status: "pending", kind: "manual", url: "https://stremthru.1337x.to/" }
];

const esc = s => encodeURIComponent(String(s || "").trim());
const decodeXml = s => String(s || "").replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'");
function latinAudio(title) {
  const t = String(title || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  // Deliberately strict: generic "Spanish", "MULTI", "dual audio" and subtitle tags do not qualify.
  return /(?:\bspanish\s*lat(?:ino|am)\b|\bespanol\s*lat(?:ino|am)\b|\baudio\s*latino\b|\blatino\s*audio\b|\blatam\s*(?:dub|audio)\b|\bdual\s*audio\s*lat(?:ino|am)\b|\[lat(?:ino|am)\])/i.test(t);
}
function makeHashLink(hash, trackers = []) {
  if (!hash) return "";
  return "magnet:?xt=urn:btih:" + hash + trackers.map(t => "&tr=" + encodeURIComponent(t)).join("");
}
async function fetchText(url) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const r = await fetch(url, { signal: controller.signal, headers: { "user-agent": "AnimeLatamTorrentAddon/1.0 (+Stremio)" } });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return await r.text();
  } finally { clearTimeout(timer); }
}
async function nyaaSearch(q) {
  const xml = await fetchText("https://nyaa.si/?page=rss&c=1_2&f=0&q=" + esc(q));
  return [...xml.matchAll(/<item>([\s\S]*?)<\/item>/gi)].slice(0, MAX_RESULTS).map(m => {
    const x = m[1], get = tag => decodeXml((x.match(new RegExp("<" + tag + "[^>]*>([\\s\\S]*?)<\/" + tag + ">", "i")) || [])[1] || "");
    const title = get("title"), link = get("link");
    const hash = (x.match(/nyaa:infoHash>([^<]+)/i) || [])[1] || "";
    const seed = Number((x.match(/nyaa:seeders>([^<]+)/i) || [])[1] || 0);
    const size = get("nyaa:size");
    return { title, link, magnet: makeHashLink(hash), seeders: seed, size };
  }).filter(x => x.title && x.magnet);
}
async function tokyoSearch(q) {
  const html = await fetchText("https://www.tokyotosho.info/search.php?terms=" + esc(q) + "&type=0");
  const rows = [...html.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/gi)].slice(0, MAX_RESULTS * 3);
  return rows.map(m => {
    const row = m[1];
    const title = decodeXml((row.match(/<a[^>]+href=["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/i) || [])[2] || "").replace(/<[^>]+>/g, "");
    const link = (row.match(/href=["'](magnet:\?[^"']+)/i) || [])[1] || "";
    const page = (row.match(/<a[^>]+href=["']([^"']+)["'][^>]*>/i) || [])[1] || "";
    return { title, link: link || new URL(page, "https://www.tokyotosho.info").href, magnet: link.startsWith("magnet:") ? link : "" };
  }).filter(x => x.title && x.magnet);
}
async function tpbSearch(q) {
  const data = await fetchText("https://apibay.org/q.php?q=" + esc(q));
  const json = JSON.parse(data);
  if (!Array.isArray(json)) return [];
  return json.filter(x => x.id && x.id !== "0").slice(0, MAX_RESULTS).map(x => ({
    title: x.name, magnet: makeHashLink(x.info_hash), link: "https://thepiratebay.org/description.php?id=" + x.id,
    seeders: Number(x.seeders || 0), size: Number(x.size || 0)
  })).filter(x => x.magnet);
}
const adapters = { nyaa: nyaaSearch, tokyotosho: tokyoSearch, thepiratebay: tpbSearch };

async function searchAll(query) {
  const active = providers.filter(p => p.status === "active" && adapters[p.id]);
  const settled = await Promise.allSettled(active.map(async p => ({ provider: p, items: await adapters[p.id](query) })));
  const found = [];
  const errors = [];
  settled.forEach((r, i) => {
    if (r.status === "fulfilled") {
      const { provider, items } = r.value;
      for (const item of items) {
        if (!latinAudio(item.title)) continue;
        found.push({ ...item, provider: provider.name, providerId: provider.id });
      }
    } else errors.push({ provider: active[i].name, error: String(r.reason?.message || r.reason) });
  });
  const unique = new Map();
  for (const x of found) {
    const key = x.magnet.match(/btih:([a-f0-9]+)/i)?.[1] || x.title.toLowerCase();
    if (!unique.has(key)) unique.set(key, x);
  }
  return { results: [...unique.values()].sort((a,b) => (b.seeders || 0) - (a.seeders || 0)), errors };
}
const builder = new addonBuilder(manifest);
builder.defineCatalogHandler(async ({ type, id, extra }) => {
  if (id !== "anime-latino-torrents" || !extra?.search) return { metas: [] };
  const { results } = await searchAll(extra.search);
  return { metas: results.slice(0, 100).map((x, i) => ({
    id: "altt:" + encodeURIComponent(x.magnet),
    type: "series",
    name: x.title,
    poster: "",
    description: "Proveedor: " + x.provider + " · Audio latino declarado en el nombre del release",
    releaseInfo: "Audio latino",
    genres: ["Anime", "Español latino"],
    runtime: x.size ? "Tamaño: " + x.size : undefined
  })) };
});
builder.defineStreamHandler(async ({ type, id }) => {
  if (!id.startsWith("altt:")) return { streams: [] };
  const magnet = decodeURIComponent(id.slice(5));
  if (!magnet.startsWith("magnet:?xt=urn:btih:")) return { streams: [] };
  return { streams: [{ name: "AnimeLatam • Torrent", title: "Audio español latino (etiqueta del release)", url: magnet, behaviorHints: { notWebReady: true } }] };
});
const addonInterface = builder.getInterface();
const app = express();
app.use((req, res, next) => { res.set("Access-Control-Allow-Origin", "*"); res.set("Access-Control-Allow-Headers", "Content-Type"); next(); });
app.get("/", (_req, res) => res.type("html").send("<h1>AnimeLatam Torrents Latino</h1><p>Addon Stremio activo. Busca desde Discover → Torrents Anime Latino.</p><p><a href='/manifest.json'>Instalar addon (manifest)</a></p>"));
app.get("/health", (_req, res) => res.json({ ok: true, name: manifest.name, version: manifest.version }));
app.get("/providers", (_req, res) => res.json({ providers: providers.map(({id,name,status,url,reason}) => ({id,name,status,url,reason:reason || null})) }));
app.get("/debug/search", async (req, res) => {
  if (!req.query.q) return res.status(400).json({ error: "Falta ?q=" });
  const data = await searchAll(req.query.q);
  res.json({ query: req.query.q, filter: "Spanish Latin audio explicitly tagged in release title", ...data });
});
app.use(addonInterface.getRouter());
app.listen(PORT, () => console.log("AnimeLatam Torrents Latino listening on " + PORT));
