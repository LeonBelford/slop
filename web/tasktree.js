/* Overview = the project graph (the one place where the plan lives), the AI ideas and standard processes, the build layers and, on request, the history of finished and cancelled tasks. */
function historyTasks(d){
  return `<details class="panel" style="margin-top:16px"><summary><b>Finished tasks</b> <span class="meta">history; removed tasks are listed in the project dashboard</span></summary><div style="margin-top:10px">${finishedTasks(d)}</div><details class="panel" style="margin-top:12px" data-k="failed-log"><summary><b>Failed attempts</b> <span class="meta">${(d.failed_log||[]).length} · log, information only</span></summary><div style="margin-top:10px">${failedLog(d.failed_log)}</div></details></details>`;
}
function overviewBody(d){
  const P=pref();
  const plan=(d.plan||[]).length?`<details class="panel" style="margin-top:16px"><summary><b>Build plan (core first)</b> <span class="meta">${d.plan.length} layers</span></summary>${d.plan.map(l=>`<div class="cap"><div><b>L${l.layer} ${esc(l.name)}</b> <span class="meta">${esc(l.goal)}</span></div><div style="min-width:140px">${pctBar(l.total?Math.round(100*l.done/l.total):0)} <span class="meta">${l.done}/${l.total}</span></div></div>`).join("")}</details>`:"";
  return proposalCard(d)+historyPanel()+tgPanel(d)+(typeof routinePanels==="function"?routinePanels(d):"")+plan+historyTasks(d);
}
