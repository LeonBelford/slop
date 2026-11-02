/* Overview = the project graph (the one place where the plan lives), the AI ideas and standard processes, the build layers and, on request, the history of finished and cancelled tasks. */
function historyTasks(d){
  const gone=(d.backlog_items||[]).filter(i=>i.status==="cancelled");
  return `<details class="panel" style="margin-top:16px"><summary><b>Finished and cancelled tasks</b> <span class="meta">history, the graph keeps them as links</span></summary><div style="margin-top:10px">${finishedTasks(d)}</div>${gone.length?`<h3 style="margin-top:14px">Cancelled <span class="meta">${gone.length}</span></h3><table>${gone.map(i=>`<tr><td>${esc(i.id)}</td><td>${esc(i.title)}${i.reason?`<div class="meta reason">${esc(i.reason)}</div>`:""}</td><td style="white-space:nowrap">${taskBtns(i,d.running)}</td></tr>`).join("")}</table>`:""}</details>`;
}
function overviewBody(d){
  const P=pref();
  const plan=(d.plan||[]).length?`<details class="panel" style="margin-top:16px"><summary><b>Build plan (core first)</b> <span class="meta">${d.plan.length} layers</span></summary>${d.plan.map(l=>`<div class="cap"><div><b>L${l.layer} ${esc(l.name)}</b> <span class="meta">${esc(l.goal)}</span></div><div style="min-width:140px">${pctBar(l.total?Math.round(100*l.done/l.total):0)} <span class="meta">${l.done}/${l.total}</span></div></div>`).join("")}</details>`:"";
  return proposalCard(d)+historyPanel()+tgPanel(d)+(typeof routinePanels==="function"?routinePanels(d):"")+plan+historyTasks(d);
}
