export type DrawerVersion = "original" | "recitation" | "keywords";

export function toggleExpandedVersions(current: ReadonlySet<DrawerVersion>, version: DrawerVersion): Set<DrawerVersion> {
  const next = new Set(current);
  if (next.has(version)) next.delete(version);
  else next.add(version);
  return next;
}
