// Printing is a read-only view of saved letters on the existing term-owned
// chart. The server alone assigns versions and orders the current memberships.
export function savedExamVersionList(chart) {
  const list = chart.exam_versions;
  if (!list?.count) throw new Error("Assign exam versions before printing the list.");
  if (chart.room.review_required?.length) {
    throw new Error("Review this room’s seat labels and locations before printing a version list.");
  }
  if (chart.check?.ok === false) {
    throw new Error("Update seating to resolve this chart’s roster and seat issues before printing a version list.");
  }
  if (list.unassigned?.length) {
    throw new Error(`Assign an exam version to every occupied seat before printing. Version needed at ${list.unassigned.join(", ")}.`);
  }
  if (!list.ready || !list.total) throw new Error("Seat students and assign their exam versions before printing the list.");
  return list;
}
