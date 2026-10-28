import type { ProjectSummary } from "./types";

export function projectOwnerId(project: Pick<ProjectSummary, "ownerName"> & { configuration?: { ownerId?: string } }) {
  return project.configuration?.ownerId || project.ownerName;
}

export function projectOwnerName(ownerId: string, actor: { id: string; name: string } | undefined, members: { id: string; name: string }[]) {
  if (ownerId === actor?.id && actor.name.trim()) return actor.name;
  return members.find((member) => member.id === ownerId)?.name.trim() || undefined;
}
