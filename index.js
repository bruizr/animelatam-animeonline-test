const express = require("express");
const animeOnline = require("./routes/animeonline");
const app = express();
app.use((req,res,next)=>{res.set("Access-Control-Allow-Origin","*");next()});
app.get("/manifest.json",(req,res)=>res.json({
  id:"com.animelatam.animeonline.test",
  version:"0.1.0",
  name:"AnimeOnline Test",
  description:"Proveedor experimental AnimeOnline.ninja / FileLions",
  resources:["stream"],
  types:["series","movie","anime"],
  idPrefixes:["animeonline:"]
}));
app.use(animeOnline);
app.get("/",(_req,res)=>res.type("text").send("AnimeOnline test addon is running"));
const port=process.env.PORT||3000;
if(require.main===module) app.listen(port,()=>console.log("AnimeOnline test listening on "+port));
module.exports=app;
