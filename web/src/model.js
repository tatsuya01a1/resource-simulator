export function lowerBoundApplied(snapshot) {
  const difference = snapshot.actual_delta - snapshot.requested_delta;
  const roundingTolerance = Number.EPSILON * Math.max(1, Math.abs(snapshot.requested_delta)) * 8;
  return snapshot.resource === 0 && snapshot.requested_delta < 0 && difference > roundingTolerance;
}
