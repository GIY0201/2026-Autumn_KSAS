export function captureInput(keys, state, generation) {
  return {keys:[...keys],run_id:state?.run_id,object_id:state?.selected_id,generation};
}
export function isCurrentInput(input, state, generation) {
  return input.run_id===state?.run_id && input.object_id===state?.selected_id && input.generation===generation;
}
