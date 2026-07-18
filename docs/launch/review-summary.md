# Local Launch Review

Nothing in this package has been committed, tagged, released, or pushed.

## Review first

1. Open `README.md` and confirm the positioning, screenshot, live-demo link, and contribution call.
2. Open `docs/assets/social-preview.png` and approve the proposed GitHub social image.
3. Review `benchmarks/baseline.md` and its explicit limitation: structural integrity, not return prediction.
4. Review `ROADMAP.md`, `docs/releases/v0.1.0.md`, and `docs/launch/launch-kit.md`.
5. Review the issue forms, pull-request template, Code of Conduct, support policy, and citation file.

## Product changes

- Story-first startup automatically loads the sanitized AAPL Deep Initiation demo.
- Research controls start collapsed and remain available from the sidebar control.
- A GitHub link appears in the header and contributor drawer.
- Responsive CSS contains horizontal overflow and stacks research cards on narrow layouts.

## Credibility changes

- Added a no-network research-integrity benchmark across AAPL, NVDA, BABA, TSLA, and GS.
- Added 25 cases and 125 checks for evidence, counter-thesis, monitoring/payoff, and promotion integrity.
- Added architecture, methodology, provider, development, and release documentation.

## Contributor changes

- Added `pyproject.toml`, Dockerfile, devcontainer, Dependabot, multi-Python CI, and Streamlit smoke CI.
- Added structured issue forms for bugs, sources, sector playbooks, ADR profiles, and features.
- Added a pull-request checklist that enforces citation, period, unit/currency, licensing, and no-lookahead rules.

## Verification

- Python compile checks: passed.
- Critical Ruff checks: passed.
- Full test suite: **377 passed in 196.96 seconds**.
- Targeted launch/UI/benchmark tests after final CSS change: **4 passed**.
- Research-integrity benchmark: **25/25 cases; 125/125 checks passed**.
- Local Streamlit health and desktop visual check: passed.
- Credential scan: no matching raw key files found outside excluded local/ignored data paths.
- Docker image build: not run because the local Docker CLI did not respond.

## Actions intentionally deferred

- Commit and push.
- GitHub description, homepage, topics, and social-preview upload.
- Enable Discussions and private vulnerability reporting.
- Create labels and seed issues.
- Create the `v0.1.0` tag/release.
- Publish launch posts.

The proposed settings and copy are in `docs/launch/github-settings.md` and `docs/launch/launch-kit.md`.
