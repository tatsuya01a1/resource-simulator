export function lowerBoundApplied(snapshot) {
  const difference = snapshot.actual_delta - snapshot.requested_delta;
  const roundingTolerance = Number.EPSILON * Math.max(1, Math.abs(snapshot.requested_delta)) * 8;
  return snapshot.resource === 0 && snapshot.requested_delta < 0 && difference > roundingTolerance;
}

// Compatibility is representation-only; simulation always runs in the Worker.
export function resourceList(snapshot) {
  if (snapshot.schema_version === 1) return [
    { id: 'resource', name: 'Alpha', available: true, start_step: 0,
      resource: snapshot.resource, actual_delta: snapshot.actual_delta,
      requested_delta: snapshot.requested_delta, parameter: snapshot.parameter },
    { id: 'beta', name: 'Beta', available: false },
  ];
  if (snapshot.schema_version !== 2 || !Array.isArray(snapshot.resources)
      || snapshot.resources.length !== 2 || snapshot.resources[0].id !== 'resource'
      || snapshot.resources[1].id !== 'beta') throw Error('unsupported snapshot');
  return snapshot.resources;
}
