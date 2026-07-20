# AAPL Demo Recording

## Purpose

Show one complete IC research journey in roughly 91 seconds:

`source event -> causal bridge -> peer operating evidence -> valuation -> counter-thesis -> IC verdict`

The recording uses the frozen AAPL demo. It must remain labelled as illustrative research, not current investment advice.

## Shot List

| Time | Asset | Action |
|---|---|---|
| Opening | `01-intro.png` | Establish the evidence-first verdict and High-Conviction demo stage. |
| Source event | `03-source-claim.png` | Highlight the exact revenue, gross-profit, and gross-margin change. |
| Citation | `04-evidence-drawer.png` | Show the issuer source, event period, and citation excerpt. |
| Causal bridge | `06-causal-graph-detail.png` | Follow the scored causal connections and weakest link. |
| Peer evidence | `09-peer-checks.png` | Highlight aligned MSFT and GOOGL operating metrics. |
| Valuation | `10-reverse-dcf.png` | Show market-implied expectations and editable assumptions. |
| IC judgment | `14-bull-bear-judge.png` | Show the bull case, counter-thesis, accepted evidence, and gaps. |
| Closing | `01-intro.png` | Return to the verdict and GitHub call to action. |

## Production

1. Record the eight narration paragraphs as separate `.m4a` clips using the timing labels in `docs/assets/demo/`.
2. Run `python scripts/build_demo_video.py` to apply conservative voice processing, synchronize scenes, and export the 1080p MP4 and SRT.
3. Run `python scripts/build_demo_gif.py` to regenerate the silent README/social preview.
4. Review transitions and complete word endings before publishing.
5. Keep captions within the lower safe area and preserve the source text on screen.

Raw `.m4a` recordings are gitignored. Only the finished MP4, SRT, GIF, thumbnail, source screenshots, and reproducible builders are published.

## Existing Asset

`ic-copilot-aapl-demo.gif` is a 31-second, silent, captioned cut for GitHub README and social posts.
