# Xingjing Bilingual README Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the inherited ArcReel-centered Chinese and English root READMEs with a detailed, truthful Xingjing Drama Studio product and developer overview.

**Architecture:** Both README files use the same section order and factual source hierarchy. Product vision, target v9.2 architecture, current repository implementation, and verified delivery status are explicitly separated so planned scope is never presented as completed functionality.

**Tech Stack:** Markdown, Mermaid, PowerShell link and parity checks, Git

---

### Task 1: Rewrite the Chinese README

**Files:**
- Modify: `README.md`
- Reference: `docs/superpowers/specs/2026-08-01-xingjing-readme-rewrite-design.md`
- Reference: `docs/product/v9.2/01-product/scope-and-priority.md`
- Reference: `docs/product/v9.2/04-architecture/system-architecture.md`
- Reference: `docs/project/requirements/01-需求总览与完成度.md`

- [ ] **Step 1: Replace the inherited project header**

Use “星镜剧创” as the product name and “Xingjing Drama Studio” as the English identifier. Link the language selector to `README.md` and `README.en.md`. Remove all ArcReel repository badges, old clone URLs, old community QR code, and ArcReel product slogans.

- [ ] **Step 2: Add the product and status overview**

State that Xingjing is a Web production operating system for Chinese AI short-drama and motion-comic teams. Describe the 124-page creator application, 18-page team console, 50-page platform console, and 5-page client review experience. Add a prominent status note that v9.2 is under development and that the requirements ledger, not the feature list, determines completion.

- [ ] **Step 3: Add workflow, product surfaces, and capability domains**

Document the lifecycle from source material and script analysis through reusable assets, shot/storyboard production, multi-model generation, audio/subtitles, editing, compliance, client review, and formal export. Summarize the four product surfaces and group capabilities by production, collaboration, governance, billing, and ecosystem concerns.

- [ ] **Step 4: Add delivery phases and current-state boundaries**

Describe P0 as the 56-page production closure, P1 as the 121-page studio-scale collaboration and delivery stage, and P2 as the 20-page ecosystem and enterprise stage. State that all 19 requirement modules are in development and that “implemented” does not mean “verified” until the matching joint acceptance package passes.

- [ ] **Step 5: Add architecture and developer sections**

Include one Mermaid target-architecture diagram covering Web clients, Spring/Java platform services, Python AI/runtime services, RabbitMQ, PostgreSQL, Redis, object storage, and FFmpeg workers. Explain that the repository currently combines inherited Python/React generation capabilities with new Xingjing Java/Python domain modules. Add the current technology table, repository map, prerequisites, backend/frontend/Java development commands, migration command, and quality gates.

- [ ] **Step 6: Add documentation and legal navigation**

Link the v9.2 product baseline, requirements ledger, architecture, API conventions, getting-started guide, deployment notes, contribution guide, license, and notice. Use the Xingjing repository clone URL. Preserve the exact required upstream attribution `Powered by ArcReel — https://github.com/ArcReel/ArcReel` in the license section without making ArcReel the README subject.

### Task 2: Rewrite the English README with factual parity

**Files:**
- Modify: `README.en.md`
- Reference: `README.md`

- [ ] **Step 1: Mirror the Chinese information architecture**

Use the same top-level section order, tables, Mermaid workflow, target-architecture diagram, commands, relative links, phase counts, and status qualifiers as `README.md`.

- [ ] **Step 2: Translate for an English-speaking product and engineering audience**

Translate meaning rather than Chinese word order. Keep product terms stable: workspace, project, episode, shot, reusable subject asset, storyboard, generation task, compute credits, client review link, and formal export.

- [ ] **Step 3: Preserve commands, paths, and legal text**

Keep shell commands, environment variable names, file paths, repository URL, AGPL-3.0 reference, and the required upstream attribution byte-for-byte equivalent where applicable.

### Task 3: Validate the bilingual documentation

**Files:**
- Test: `README.md`
- Test: `README.en.md`

- [ ] **Step 1: Check forbidden inherited marketing content**

Run:

```powershell
rg -n "ArcReel Logo|github.com/ArcReel/ArcReel.git|feishu-qr|support@arc-reel.com|ArcReel 工作台|ArcReel Workspace" README.md README.en.md
```

Expected: no matches. The legal upstream link without `.git` remains allowed.

- [ ] **Step 2: Check required facts and attribution**

Run:

```powershell
rg -n "星镜剧创|Xingjing Drama Studio|197|124|18|50|P0|P1|P2|Powered by ArcReel" README.md README.en.md
```

Expected: both files contain the product identity, scope counts, delivery phases, and required attribution.

- [ ] **Step 3: Check local Markdown links**

Extract Markdown link targets from both files, ignore `http`, `https`, `mailto`, and anchor-only targets, strip optional anchors, then verify every remaining target exists relative to the repository root.

Expected: zero missing local targets.

- [ ] **Step 4: Check heading parity and whitespace**

Compare the ordered count of level-two headings in both files and run:

```powershell
git diff --check
```

Expected: equal level-two heading counts and no whitespace errors.

- [ ] **Step 5: Review the rendered-information flow**

Read each file from top to bottom and confirm a new reader can answer: what Xingjing is, who it serves, what the target scope is, what is currently verified, how the system is shaped, how to run it locally, where detailed requirements live, and what upstream attribution applies.

- [ ] **Step 6: Commit the README rewrite**

Run:

```powershell
git add README.md README.en.md docs/superpowers/plans/2026-08-01-xingjing-readme-rewrite.md
git commit -m "docs(readme): introduce Xingjing product and developer guide"
```

Expected: one documentation commit containing the bilingual README rewrite and its execution plan.
