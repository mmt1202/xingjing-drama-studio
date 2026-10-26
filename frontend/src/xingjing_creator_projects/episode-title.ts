export function compactEpisodeTitle(title: string, limit = 28): string {
  const characters = Array.from(title.replace(/\s+/gu, " ").trim());
  return characters.length <= limit
    ? characters.join("")
    : `${characters.slice(0, limit - 1).join("")}…`;
}
