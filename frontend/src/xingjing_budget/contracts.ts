export type BudgetScope = { kind: "episode"; episodeId: number } | { kind: "project_shared" };

export interface BudgetOptions {
  episodes: readonly { episodeId: number; title: string }[];
  requiresAttribution: boolean;
  canUseSharedScope: boolean;
}

export interface BudgetAmount {
  currency: string;
  limitMinor: number | null;
  consumedMinor: number;
  reservedMinor: number;
  remainingMinor: number | null;
}
