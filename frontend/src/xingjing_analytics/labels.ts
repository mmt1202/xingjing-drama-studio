export function attributionLabel(id: string, label: string, unknownLabel: string, episode = false): string {
  const unknownEpisode = episode && (id === "unknown" || id.endsWith(":unknown"));
  if (unknownEpisode && label.endsWith(" · unknown")) return `${label.slice(0, -"unknown".length)}${unknownLabel}`;
  return id === "unknown" ? unknownLabel : label;
}
