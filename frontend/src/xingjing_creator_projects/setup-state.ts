import type { ProjectSetup } from "./types";

export function initialSetup(ownerId: string): ProjectSetup {
  return {
    shortName: "", ownerId, projectType: "drama", sourceKind: "blank", sourceText: "", preset: "",
    episodeCount: 1, episodeDurationSeconds: 60, primaryPlatform: "douyin", secondaryPlatforms: [],
    aspectRatio: "9:16", deliveryType: "final", generationMode: "storyboard", directorStyle: "", visualStyle: "",
    modelStrategy: "balanced", imageModelKey: "", videoModelKey: "", candidateCount: 1,
    subtitleLanguages: ["zh"], reviewLanguage: "zh", budgetMinor: null, scheduleDays: null,
    teamSeats: 1, reviewSeats: 1, internalReviewRounds: 2, clientReviewRounds: 1,
    clientCanComment: true, clientCanDownload: false, clientCanApprove: true, rightsBasis: "original", rightsNote: "",
  };
}

export function missingSetup(name: string, value: ProjectSetup): string[] {
  const issues: string[] = [];
  if (!name.trim()) issues.push("missingName");
  if (["novel", "script"].includes(value.sourceKind) && !value.sourceText.trim()) issues.push("missingSource");
  if (value.sourceKind === "template" && !value.preset) issues.push("missingPreset");
  if (!value.subtitleLanguages.length) issues.push("missingLanguage");
  return issues;
}
