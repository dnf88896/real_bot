export const name='brain-tool-guard';
export const inject=['tools'];
export function apply(ctx){
  // This dedicated profile has no QQ/shell/file tool plugins.
  try{ctx.tools.restrict({deny:['working_directory']});}catch{}
  ctx.tools.guard(exec=>{
    if(typeof exec?.name==='string' && exec.name.startsWith('mcp__brain__'))return;
    return 'This brain can only read scoped observations and record a decision.';
  });
}
