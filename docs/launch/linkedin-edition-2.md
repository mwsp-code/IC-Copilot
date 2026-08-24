# LinkedIn launch kit — IC Copilot Edition 2

This package is designed for manual publishing. It deliberately does not tag
Diagram, mention an active recruiting process, or automate any LinkedIn action.

## Pre-publication gate

- Confirm Edition 2 is deployed from `main` and the logged-out app opens on Decision.
- Check the AAPL, NVDA, BABA, and BLK demos on desktop and a narrow viewport.
- Confirm frozen demos require no provider credentials and show their data cutoff.
- Confirm the README, live gallery, post copy, and recording name the same four demos.
- Record a new 45–60 second native video after deployment; do not reuse a walkthrough
  that shows an older navigation or research workflow.

## Recommended post

Most AI research tools are optimized to produce an answer.

I became more interested in a harder question: can a research system recognize
when the evidence is not strong enough to act?

Feedback on the first version of IC Copilot made one weakness clear. It could
assemble a useful research pack, but the path from question → evidence →
challenge → decision was not yet clear or resilient enough.

I’ve now shipped Edition 2.

The new workflow:

• lets the research model build and adapt its investigation agenda;

• separates exploratory leads from evidence that can actually support a conclusion;

• uses independent adversarial review before research is treated as closed; and

• can conclude `No position / Watch` when the thesis or expected return is not
strong enough.

I also redesigned the product around user feedback: clearer decision-first
navigation, focused investigation journeys, saved research, resumable QA, and
more transparent progress when agents are working.

The broader building lesson for me was that, in uncertain 0→1 work, the useful
output is not simply a polished narrative. It is a testable set of assumptions,
an honest account of what remains unknown, and a clear next action.

The updated public demo includes AAPL, NVDA, BABA, and BLK.

I’d value feedback from investors, founders, and operators: which parts of this
workflow would be most useful when pressure-testing an early-stage market or
venture thesis?

https://ic-copilot.streamlit.app/

#VentureBuilding #InvestmentResearch #AI

## First comment

Build notes and source code:
https://github.com/mwsp-code/IC-Copilot

Research architecture and methodology:
https://github.com/mwsp-code/IC-Copilot/blob/main/docs/agent-led-research.md

Edition 2 passes 892 automated tests, including saved-result, evidence-integrity,
QA-recovery, and Python compatibility checks.

## Private follow-up template

Send this only to the principal interview contact after publishing; personalize
the opening sentence rather than sending identical messages to multiple people.

> Hi [Name] — thank you again for our conversation. One theme I kept thinking
> about was the ownership required to take an ambiguous problem from initial
> thesis to something testable.
>
> I’ve just shipped the second edition of IC Copilot, incorporating feedback
> from the first release. Although the demo focuses on investment research, the
> project captures how I approach 0→1 work: break uncertainty into testable
> questions, build a working product, challenge the conclusions, and iterate
> based on evidence.
>
> I thought the latest workflow might be relevant to our discussion:
> [LinkedIn post or demo]
>
> Thanks again — I enjoyed the conversation.

## Native video storyboard

Keep the recording between 45 and 60 seconds and use captions because viewers
may watch without sound.

| Time | Screen | Caption |
|---|---|---|
| 0–5s | Decision header | “Can AI research know when not to act?” |
| 5–15s | Valuation and market-implied expectations | “Make the assumptions embedded in price explicit.” |
| 15–25s | Historical Playbook | “Use analogs only when the mechanism and evidence transfer.” |
| 25–38s | Evidence and Independent QA | “Separate discovery from decision-grade evidence.” |
| 38–48s | Investigation journey / saved research | “Show what is running, preserve progress, and resume safely.” |
| 48–60s | Decision summary and URL | “IC Copilot Edition 2 — public demo and open source.” |

## Interview discussion prompts

Be ready to explain:

1. Which user complaint caused the largest product change.
2. How model autonomy is balanced against evidence integrity.
3. How a failure such as an interrupted QA stage became a reusable,
   checkpointed execution design rather than a ticker-specific patch.
