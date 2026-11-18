const express = require("express");
const cheerio = require("cheerio");
const router = express.Router();

const SOURCE = "https://ww4.animeonline.ninja";
const USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36";

async function getText(url, headers={}) {
  const response = await fetch(url,{headers:{"User-Agent":USER_AGENT,...headers},redirect:"follow"});
  if(!response.ok) throw new Error("HTTP "+response.status+" al consultar "+new URL(url).hostname);
  return response.text();
}

// Extractor deliberately limited to the publicly visible embed chain.
// It does not attempt to defeat Cloudflare, obtain session tokens, or bypass provider controls.
async function resolveEpisodePage(url) {
  const html = await getText(url,{Referer:SOURCE+"/"});
  const $ = cheerio.load(html);
  const iframe = $("iframe[src]").map((_,el)=>$(el).attr("src")).get()
    .find(src=>{try{return new URL(src,url).hostname.endsWith("saidochesto.top")}catch{return false}});
  if(!iframe) throw new Error("No se encontró iframe de Saidochesto en la página del episodio");
  return resolveSaido( new URL(iframe,url).href );
}

async function resolveSaido(url) {
  const html = await getText(url,{Referer:SOURCE+"/"});
  const $ = cheerio.load(html);
  const candidates = $("iframe[src]").map((_,el)=>$(el).attr("src")).get();
  let embed = candidates.find(src=>{try{return new URL(src,url).hostname.endsWith("filelions.top")}catch{return false}});
  if(!embed) {
    const match = html.match(/https?:\\?\\?\/\\?\\?\/filelions\\?\\?\.top\\?\\?\/embed\\?\\?\/[A-Za-z0-9_-]+/i);
    if(match) embed=match[0].replace(/\\/g,"");
  }
  if(!embed) throw new Error("Saidochesto no expuso un iframe FileLions reconocible");
  return resolveFileLions(new URL(embed,url).href);
}

async function resolveFileLions(embedUrl) {
  const html = await getText(embedUrl,{Referer:embedUrl});
  const $ = cheerio.load(html);
  const scripts = $("script").map((_,el)=>$(el).html()||"").get().join("\n");
  const haystack = html+"\n"+scripts;
  const urls = [...haystack.matchAll(/https?:\\?\\?\/\\?\\?\/[^"'\\s<>]+?master\\.m3u8[^"'\\s<>]*/gi)]
    .map(m=>m[0].replace(/\\/g,"").replace(/\\u0026/g,"&"));
  const direct = urls.find(u=>{try{return new URL(u).hostname.endsWith("filelions.top")}catch{return false}});
  if(!direct) throw new Error("No se pudo identificar master.m3u8 en el HTML de FileLions; puede requerir un flujo autorizado/documentado");
  return {url:direct,referer:embedUrl};
}

router.get("/stream/:type/:videoId",async(req,res)=>{
  const [provider,slug,episode] = req.params.videoId.split(":");
  if(provider!=="animeonline" || !slug) return res.json({streams:[]});
  try {
    const episodeUrl = /^https?:/.test(slug) ? slug : SOURCE+"/episodio/"+slug+(episode?"-cap-"+episode:"");
    const resolved = await resolveEpisodePage(episodeUrl);
    res.set("Cache-Control","no-store").json({streams:[{
      name:"AnimeOnline • FileLions",
      title:"HLS",
      url:resolved.url,
      behaviorHints:{proxyHeaders:{request:{Referer:resolved.referer}}}
    }]});
  } catch(error) {
    console.error("[AnimeOnline test]",error.message);
    res.set("Cache-Control","no-store").json({streams:[],message:error.message});
  }
});

router.get("/test/resolve",async(req,res)=>{
  const url=req.query.url;
  if(!url || !/^https:\/\/ww4\.animeonline\.ninja\//.test(url)) return res.status(400).json({error:"Pasa ?url= con la URL HTTPS de un episodio AnimeOnline.ninja"});
  try {res.set("Cache-Control","no-store").json(await resolveEpisodePage(url))}
  catch(error){res.status(502).json({error:error.message})}
});

module.exports={router};
