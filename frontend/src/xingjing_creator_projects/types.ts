export type Permission = "project.view" | "project.manage" | "script.view" | "script.manage";

export interface SessionContext {
  user: { id: string; name: string };
  workspace: { id: string; name: string };
  permissions: Permission[];
  featureFlags: Record<string, boolean>;
}

export interface ProjectSummary { id: string; name: string; type: string; targetPlatform: string; ownerName: string; productionStatus: string; archived: boolean; version: number; updatedAt: string; setupState?: "draft" | "ready"; configuration?: ProjectSetup }
export interface ProjectDetail extends ProjectSummary { budgetMinor?: number; budgetUnit?: string; episodes?: Array<{ id: string; title: string; status: string }> }

export interface ScriptScene { sceneId: string; heading: { location?: string; timeOfDay?: string | null; setting?: string | null }; paragraphs: Array<{ paragraphId: string; kind: string; text: string; sourceText: string; speaker: string | null }> }
export interface CharacterInsight { name: string; appearances: number; description: string }
export interface ContentAnalysis { wordCount: number; characterCount: number; sceneCount: number; dialogueCount: number; estimatedDurationMs: number; estimatedEpisodeCount: number; sensitiveTerms: string[]; characters: CharacterInsight[]; locations: string[]; props: string[]; relationships: Array<{ source: string; target: string; relation: string }>; storyBeats: Array<{ label: string; summary: string; sceneIds: string[] }>; episodeSuggestions: string[] }
export interface ScriptTextMapping { targetId: string; sourceStart: number; sourceEnd: number }
export interface ScriptVersion { number: number; sourceContent: string; sourcePreamble?: string; scenes: ScriptScene[]; sourceMappings: ScriptTextMapping[]; scriptMappings?: ScriptTextMapping[]; scriptMappingCoordinateSystem?: "script_version"; sourceMappingCoordinateSystem?: "original_source" | "unmapped"; unmappedParagraphIds?: string[]; validationErrors: Array<{ code?: string; field?: string; path?: string; severity?: "error" | "warning"; message?: string }>; changeSummary: string; analysis: ContentAnalysis }
export interface DirectorProfile { audience: string | null; pacing: string | null; visualStyle: string | null; cameraLanguage: string | null; productionConstraints: string[] }
export interface ScriptDocument { id: string; projectId: string; title: string; revision: number; sourceDocument: { filename: string; mediaType: string; content: string }; versions: ScriptVersion[]; aiInputLimitCharacters?: number; lockedVersionNumber: number | null; directorProfile: DirectorProfile }
export interface ScriptComparison { baselineVersion: number; candidateVersion: number; changedFields: string[] }
export interface PageResult<T> { items: T[]; nextToken: string | null; total?: number; offset?: number; limit?: number }

export type ProjectAction = "create" | "update" | "updateOverview" | "archive" | "restore" | "advance" | "rollback" | "saveDraft" | "activate";
export type ScriptAction = "import" | "revise" | "setDirectorProfile" | "freeze" | "compareVersions" | "analyze" | "generateDirector" | "rewrite";
export type ScriptActionResult = ScriptDocument | ScriptComparison;

export interface CreatorProjectsPort {
  getSessionContext(): Promise<SessionContext>;
  listProjects(params?: { archived?: boolean; pageToken?: string; sort?: string }): Promise<PageResult<ProjectSummary>>;
  getProject(projectId: string): Promise<ProjectDetail>;
  getProjectSetupOptions?(): Promise<ProjectSetupOptions>;
  projectAction(projectId: string, action: ProjectAction, payload?: unknown, version?: number): Promise<ProjectDetail>;
  listScripts(projectId: string, params?: { offset?: number; limit?: number; query?: string; locked?: boolean }): Promise<PageResult<ScriptDocument>>;
  getScript?(projectId: string, scriptId: string, signal?: AbortSignal): Promise<ScriptDocument>;
  importScriptFile(projectId: string, title: string, file: File): Promise<ScriptDocument>;
  scriptAction(projectId: string, action: ScriptAction, payload?: unknown, revision?: number, targetId?: string): Promise<ScriptActionResult>;
}

export type TargetPlatform = "douyin" | "kuaishou" | "wechat" | "bilibili" | "web";
export type ProjectLanguage = "zh" | "en" | "vi";
export interface ProjectSetup {
  shortName: string;
  ownerId: string;
  projectType: "drama" | "comic" | "brand" | "adaptation";
  sourceKind: "blank" | "novel" | "script" | "template";
  sourceText: string;
  preset: "" | "suspense" | "comic" | "brand";
  episodeCount: number;
  episodeDurationSeconds: number;
  primaryPlatform: TargetPlatform;
  secondaryPlatforms: TargetPlatform[];
  aspectRatio: "9:16" | "16:9" | "1:1";
  deliveryType: "final" | "proposal";
  generationMode: "storyboard" | "reference_video";
  directorStyle: string;
  visualStyle: string;
  modelStrategy: "balanced" | "quality" | "economy";
  imageModelKey: string;
  videoModelKey: string;
  candidateCount: number;
  subtitleLanguages: ProjectLanguage[];
  reviewLanguage: ProjectLanguage;
  budgetMinor: number | null;
  scheduleDays: number | null;
  teamSeats: number;
  reviewSeats: number;
  internalReviewRounds: number;
  clientReviewRounds: number;
  clientCanComment: boolean;
  clientCanDownload: boolean;
  clientCanApprove: boolean;
  rightsBasis: "original" | "licensed" | "pending";
  rightsNote: string;
}
export interface ProjectSetupOptions {
  members: Array<{ id: string; name: string }>;
  models: Array<{ key: string; name: string; mediaTypes: string[] }>;
}
