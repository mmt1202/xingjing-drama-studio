export type Permission = "project.view" | "project.manage" | "script.view" | "script.manage";

export interface SessionContext {
  user: { id: string; name: string };
  workspace: { id: string; name: string };
  permissions: Permission[];
  featureFlags: Record<string, boolean>;
}

export interface ProjectSummary { id: string; name: string; type: string; targetPlatform: string; ownerName: string; productionStatus: string; archived: boolean; version: number; updatedAt: string }
export interface ProjectDetail extends ProjectSummary { budgetMinor?: number; budgetUnit?: string; episodes?: Array<{ id: string; title: string; status: string }> }

export interface ScriptScene { sceneId: string; heading: { location?: string; timeOfDay?: string | null; setting?: string | null }; paragraphs: Array<{ paragraphId: string; kind: string; text: string; sourceText: string; speaker: string | null }> }
export interface CharacterInsight { name: string; appearances: number; description: string }
export interface ContentAnalysis { wordCount: number; characterCount: number; sceneCount: number; dialogueCount: number; estimatedDurationMs: number; estimatedEpisodeCount: number; sensitiveTerms: string[]; characters: CharacterInsight[]; locations: string[]; props: string[]; relationships: Array<{ source: string; target: string; relation: string }>; storyBeats: Array<{ label: string; summary: string; sceneIds: string[] }>; episodeSuggestions: string[] }
export interface ScriptVersion { number: number; sourceContent: string; scenes: ScriptScene[]; sourceMappings: unknown[]; validationErrors: Array<{ code?: string; field?: string; message?: string }>; changeSummary: string; analysis: ContentAnalysis }
export interface DirectorProfile { audience: string | null; pacing: string | null; visualStyle: string | null; cameraLanguage: string | null; productionConstraints: string[] }
export interface ScriptDocument { id: string; projectId: string; title: string; revision: number; sourceDocument: { filename: string; mediaType: string; content: string }; versions: ScriptVersion[]; lockedVersionNumber: number | null; directorProfile: DirectorProfile }
export interface ScriptComparison { baselineVersion: number; candidateVersion: number; changedFields: string[] }
export interface PageResult<T> { items: T[]; nextToken: string | null; total?: number; offset?: number; limit?: number }

export type ProjectAction = "create" | "update" | "archive" | "restore" | "advance" | "rollback";
export type ScriptAction = "import" | "revise" | "setDirectorProfile" | "freeze" | "compareVersions" | "analyze" | "generateDirector" | "rewrite";
export type ScriptActionResult = ScriptDocument | ScriptComparison;

export interface CreatorProjectsPort {
  getSessionContext(): Promise<SessionContext>;
  listProjects(params?: { archived?: boolean; pageToken?: string; sort?: string }): Promise<PageResult<ProjectSummary>>;
  getProject(projectId: string): Promise<ProjectDetail>;
  projectAction(projectId: string, action: ProjectAction, payload?: unknown, version?: number): Promise<ProjectDetail>;
  listScripts(projectId: string, params?: { offset?: number; limit?: number; query?: string; locked?: boolean }): Promise<PageResult<ScriptDocument>>;
  importScriptFile(projectId: string, title: string, file: File): Promise<ScriptDocument>;
  scriptAction(projectId: string, action: ScriptAction, payload?: unknown, revision?: number, targetId?: string): Promise<ScriptActionResult>;
}
