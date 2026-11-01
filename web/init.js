if(location.hash.startsWith("#dash"))showDash();
refresh(); setInterval(refresh,3000); setInterval(renderNow,1000);
let fastBusy=false,slowTick=0;
setInterval(async()=>{
  const tool=view==="tool"&&toolTab_==="reason"&&extra&&extra.act&&extra.act.running;
  const proj=view==="project"&&tab==="reasoning"&&sel&&detail&&detail.running;
  if((!tool&&!proj)||fastBusy)return;
  fastBusy=true;
  try{
    const r=await api(tool?"/api/tool/stream":`/api/project/${encodeURIComponent(sel)}/stream`);
    const prev=LS&&LS.stream&&LS.stream.tag,prevLlm=LS&&LS.llm&&LS.llm.since;
    LS=r;reasoningLive();
    const finished=(prev&&!r.stream)||(prevLlm&&r.llm&&r.llm.since!==prevLlm);
    if(finished||++slowTick%6===0){ // a call ended (new card) or periodic sync of the card list
      if(tool){const act=await api("/api/tool/activity");const n=extra.act.entries.length;extra.act=act;if(act.entries.length!==n)renderTool()}
      else{const f=await api(`/api/project/${encodeURIComponent(sel)}/thoughts`);const n=extra&&extra.entries?extra.entries.length:-1;extra={kind:"reasoning",...f};if(f.entries.length!==n)renderProject()}
    }
  }catch(e){}
  fastBusy=false;
},450);
