# Paptonis / AgentWatch — GSoC 2027 Eligibility and Project-History Audit

**Checked:** 2026-10-21 · **Status:** **NOT YET VERIFIED FOR STANDALONE GSoC 2027 APPLICATION** · **Owner:** OSS & GSoC lead  
**Scope:** Public repository evidence, connected GitHub metadata, and Google's published organization guidance. This file does **not** claim Google has assessed or accepted the project.

## Executive decision

The repository is public, active, and uses the OSI-approved **Apache License 2.0**, with Python distribution metadata and substantial public contributions. However, the **18-month established-project expectation**, **five genuinely active community members**, **committed administrators/mentors**, and **released-package history** have not yet been conclusively verified. This is an **at-risk / evidence-incomplete** assessment, **not** a categorical rejection.

Google's [2026 mentoring-organization announcement](https://opensource.googleblog.com/2026/01/mentor-org-applications-for-google-summer-of-code-2026-open-through-feb-3.html) states that an established project should have **at least 18 months of history**, OSI-licensed released software, an active robust community, project ideas and a high-quality application. The [GSoC mentor guide](https://google.github.io/gsocguides/mentor/org-application) separately says **at least five active people** and asks whether an organization/project is **18 months old**. These are meaningful **published 2026 selection expectations**, while the [FAQ](https://developers.google.com/open-source/gsoc/faq) and [organization eligibility page](https://summerofcode.withgoogle.com/get-started) summarize the more general eligibility conditions. Do not represent the 2026 details as confirmed final **2027** rules.

## Verification matrix

| Criterion | Current evidence | Conclusion | Next proof |
|---|---|---|---|
| Public/open-source repository | [Repo](https://github.com/Shinraya-Tech-Labs/AgentWatch) is accessible as a public GitHub repo | **Verified** | Keep code accessible |
| OSI-approved license | Root [LICENSE](https://github.com/Shinraya-Tech-Labs/AgentWatch/blob/main/LICENSE) begins with Apache License, Version 2.0; [pyproject.toml](https://github.com/Shinraya-Tech-Labs/AgentWatch/blob/main/pyproject.toml) declares Apache-2.0 | **Verified for declared repository license** | Check distributed wheels/sdists include licensing and required notices |
| Released software | `pyproject.toml` declares `panoptis` version `0.2.0`; README links PyPI | **Distribution configuration verified; public published release history not independently verified in this audit** | Archive direct PyPI version/upload dates and GitHub release/tag URLs |
| Recent activity | GitHub commit history includes [21 Oct roadmap commit](https://github.com/Shinraya-Tech-Labs/AgentWatch/commit/ce4925dd30b06d5ae614d22b50d794e9ab8af593); public PRs exist | **Verified active recently** | Quantify human activity over rolling 90 days |
| ≥18 months established history (2026 guidance) | Old [PR #7](https://github.com/Shinraya-Tech-Labs/AgentWatch/pull/7) mentions earlier `sreerevanth/AgentWatch` path, supporting naming/ownership continuity; earliest public creation/release dates not established | **At risk / unverified** | Extract repository `created_at`, earliest non-imported Git history, earliest public tag/release and archived predecessor URL; establish project continuity |
| ≥5 active humans (mentor guide) | PR history exists, but unique human participation and continuing reviewer capacity have not been counted | **Unverified** | Count active humans in the preceding 90 days, distinguish bots, core maintainers and outsiders |
| Administrators/technical mentors | No confirmed, named, consenting 2027 roster established by this audit | **Unverified** | Identify two eligible admins and primary/backup technical mentors; document real weekly time commitments |
| Project ideas | [Roadmap](https://github.com/Shinraya-Tech-Labs/AgentWatch/blob/main/docs/gsoc/2027/ROADMAP.md) merged; a bounded mentor-owned 2027 ideas page was not verified | **Preparation underway** | Publish detailed ideas with technical lead signoff and named mentors |
| Community onboarding | `CONTRIBUTING.md` exists; prior research identified extra gating/friction | **Partial** | Two outsiders complete independent quickstarts and receive substantive reviews |
| Organization/product identity | Current README says **Panopti**, package/repo says **AgentWatch**, user-selected next brand is **Paptonis** | **Not reconciled** | Publish naming/compatibility decision (ADR) |
| Geographic restriction | Google states organizations must not be based in US-embargoed countries | **No concern indicated by public info, organizational/legal details unverified** | Verify organization location and applicable current rules |

### Critical chronology

For a **January 2027** organization application, an 18-month continuous-history benchmark points approximately to **July 2025 or earlier**. The exact threshold must be calculated against the official 2027 application date when announced. **Do not backdate a project**, and do not claim unrelated repositories establish the age of AgentWatch/Paptonis.

The old `sreerevanth/AgentWatch` path shown by [PR #7](https://github.com/Shinraya-Tech-Labs/AgentWatch/pull/7) is evidence of repository-name continuity, **not proof of 18 months of activity**. A transfer preserves repository identity/history but cannot create older history than genuinely existed. Earliest imported Git commit and project origin can differ from the time the project was actually published.

## Required follow-up verification (owner: OSS & GSoC lead)

1. Obtain REST GitHub `created_at` and earliest reachable genuine Git commits for **this repo ID**, then inspect transfer history where available. Save both dates and explain any difference.
2. Check the first GitHub releases/tags and independent PyPI `panoptis` published versions/upload dates; capture durable links.
3. Export issues/PRs and reviews for the last 90 days. Count distinct **human** contributors and actual reviews; report how many are non-maintainers and how many returned for a second contribution. PR count and fork count are not human-count substitutes.
4. Request explicit commitments from administrators and project mentors (GitHub identity, qualified subsystem, hours/week, backup, time-zone, absences); don't publish private contact details without permission.
5. Confirm which public project name is final; migrate README/docs/distribution branding through a separate reviewed change, without breaking existing users.
6. Reassess official GSoC 2027 policies and the application timeline once released.

Suggested GitHub CLI commands for a maintainer with repository access:

```bash
gh api repos/Shinraya-Tech-Labs/AgentWatch --jq '{id,created_at,pushed_at,default_branch,license:.license.spdx_id}'
gh api repos/Shinraya-Tech-Labs/AgentWatch/releases --paginate --jq '.[] | [.tag_name,.published_at,.html_url] | @tsv'
gh api repos/Shinraya-Tech-Labs/AgentWatch/tags --paginate --jq '.[] | [.name,.commit.sha] | @tsv'
gh pr list --repo Shinraya-Tech-Labs/AgentWatch --state all --limit 500 --json number,author,createdAt,mergedAt,reviewDecision
gh api repos/Shinraya-Tech-Labs/AgentWatch/contributors --paginate --jq '.[] | [.login,.contributions] | @tsv'
```

The contributor endpoint includes historical and possibly automated contributors and is **not itself proof of currently active people**. For the last 90 days, correlate PR creation/merge, substantive review, issue discussions and maintainer actions. Verify the identity and nature of activity.

## Go / no-go

**GO, standalone application** only after evidence supports the current official project-history expectation, an active community with appropriate size, released OSI-licensed software, two capable admins, credible mentors and a vetted ideas list.

**PARTNER** with an established eligible GSoC mentoring/umbrella organization if the project is useful but standalone organizational maturity is insufficient (subject to that organization's approval).

**DEFER** standalone application if the age/community/mentorship expectations cannot honestly be met.

**Current recommendation:** Continue preparation, but **do not call Paptonis eligible yet**. Prioritize evidence of genuine inception date and independently active reviewers *before* significant additional feature development.

## Reference links

- [Google 2026 mentoring organization announcement](https://opensource.googleblog.com/2026/01/mentor-org-applications-for-google-summer-of-code-2026-open-through-feb-3.html)
- [Google GSoC mentor guide: organization application](https://google.github.io/gsocguides/mentor/org-application)
- [GSoC organization eligibility](https://summerofcode.withgoogle.com/get-started)
- [GSoC FAQ](https://developers.google.com/open-source/gsoc/faq)
- [GSoC timeline](https://developers.google.com/open-source/gsoc/timeline)
