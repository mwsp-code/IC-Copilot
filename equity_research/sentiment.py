from __future__ import annotations

import re
from dataclasses import dataclass, field

from .analysis import normalize_text


POSITIVE_TERMS = {
    "accelerate", "accelerated", "acceleration", "beat", "better than expected",
    "cloud growth", "cost discipline", "demand improved", "expansion",
    "free cash flow", "gain share", "gross margin expanded", "growth",
    "improved", "improvement", "margin expansion", "operating leverage",
    "outperform", "profitability improved", "raise", "raised", "resilient",
    "strong", "upside",
}

NEGATIVE_TERMS = {
    "competition", "competitive pressure", "decline", "deceleration", "delay",
    "demand softened", "downside", "headwind", "impairment", "litigation",
    "margin pressure", "miss", "pressure", "regulatory", "risk", "slowdown",
    "soft", "uncertain macro", "weaker",
}

UNCERTAINTY_TERMS = {
    "cannot predict", "challenging", "depends on", "difficult to forecast",
    "early stage", "macro uncertainty", "may", "might", "not clear",
    "too early", "uncertain", "uncertainty", "volatile", "we expect volatility",
}

EVASION_TERMS = {
    "cannot comment", "cannot disclose", "do not disclose", "don't disclose",
    "not going to comment", "not prepared to", "not providing guidance",
    "too early to quantify", "we will not comment",
}

PROMOTIONAL_TERMS = {
    "best in class", "game changing", "huge opportunity", "massive opportunity",
    "transformational", "unprecedented", "world class",
}

SPECIFICITY_PATTERNS = (
    r"\b\d+(?:\.\d+)?\s*%",
    r"\$?\b\d+(?:\.\d+)?\s*(?:billion|million|bn|mn)\b",
    r"\b(?:q[1-4]|fy|fiscal year|quarter|next year|202\d)\b",
    r"\b(?:revenue|eps|margin|free cash flow|capex|buyback|dividend|cloud|orders)\b",
)


@dataclass
class SentimentResult:
    label: str
    score: float
    confidence: str
    positive_terms: list[str] = field(default_factory=list)
    negative_terms: list[str] = field(default_factory=list)
    uncertainty_terms: list[str] = field(default_factory=list)
    evasion_terms: list[str] = field(default_factory=list)
    promotional_terms: list[str] = field(default_factory=list)
    specificity_score: float = 0.0
    source: str = "rules_based"


def score_text(text: str) -> SentimentResult:
    normalized = normalize_text(text).lower()
    positive = _hits(normalized, POSITIVE_TERMS)
    negative = _hits(normalized, NEGATIVE_TERMS)
    uncertainty = _hits(normalized, UNCERTAINTY_TERMS)
    evasion = _hits(normalized, EVASION_TERMS)
    promotional = _hits(normalized, PROMOTIONAL_TERMS)
    specificity = _specificity_score(normalized)
    token_count = max(1, len(re.findall(r"\b\w+\b", normalized)))

    raw_score = (
        1.6 * len(positive)
        - 1.8 * len(negative)
        - 1.0 * len(uncertainty)
        - 2.5 * len(evasion)
        - 0.8 * len(promotional)
        + 0.4 * min(specificity, 4.0)
    )
    score = max(-1.0, min(1.0, raw_score / max(4.0, token_count / 45)))
    label = _label(score, negative, uncertainty, evasion, promotional)
    confidence = _confidence(positive, negative, uncertainty, evasion, promotional, specificity, token_count)
    return SentimentResult(
        label=label,
        score=round(score, 3),
        confidence=confidence,
        positive_terms=positive,
        negative_terms=negative,
        uncertainty_terms=uncertainty,
        evasion_terms=evasion,
        promotional_terms=promotional,
        specificity_score=round(specificity, 2),
    )


def aggregate_scores(results: list[SentimentResult]) -> dict[str, float | None]:
    if not results:
        return {
            "sentiment_score": None,
            "uncertainty_score": None,
            "evasion_score": None,
            "specificity_score": None,
        }
    return {
        "sentiment_score": round(sum(item.score for item in results) / len(results), 3),
        "uncertainty_score": round(sum(len(item.uncertainty_terms) for item in results) / len(results), 3),
        "evasion_score": round(sum(len(item.evasion_terms) for item in results) / len(results), 3),
        "specificity_score": round(sum(item.specificity_score for item in results) / len(results), 3),
    }


def tone_shift_summary(sentiment_shift: float | None, uncertainty_shift: float | None, evasion_shift: float | None, specificity_shift: float | None) -> str:
    if sentiment_shift is None:
        return "No prior transcript was available for tone comparison."
    parts: list[str] = []
    if abs(sentiment_shift) >= 0.2:
        parts.append("tone became more constructive" if sentiment_shift > 0 else "tone became more cautious")
    if uncertainty_shift is not None and abs(uncertainty_shift) >= 0.5:
        parts.append("uncertainty language increased" if uncertainty_shift > 0 else "uncertainty language declined")
    if evasion_shift is not None and abs(evasion_shift) >= 0.25:
        parts.append("evasive language increased" if evasion_shift > 0 else "evasive language declined")
    if specificity_shift is not None and abs(specificity_shift) >= 0.5:
        parts.append("commentary became more specific" if specificity_shift > 0 else "commentary became less specific")
    return "; ".join(parts) + "." if parts else "No material transcript tone shift detected."


def _hits(text: str, terms: set[str]) -> list[str]:
    found = []
    for term in sorted(terms):
        if _term_present(text, term):
            found.append(term)
    return found


def _term_present(text: str, term: str) -> bool:
    escaped = re.escape(term.lower())
    if " " in term:
        return bool(re.search(escaped, text))
    return bool(re.search(rf"\b{escaped}\b", text))


def _specificity_score(text: str) -> float:
    score = 0.0
    for pattern in SPECIFICITY_PATTERNS:
        score += min(3, len(re.findall(pattern, text, flags=re.I)))
    if any(term in text for term in ("between", "range", "from", "to", "at least", "approximately")):
        score += 1
    return min(10.0, score)


def _label(score: float, negative: list[str], uncertainty: list[str], evasion: list[str], promotional: list[str]) -> str:
    if evasion:
        return "Evasive"
    if promotional and not negative:
        return "Promotional"
    if uncertainty and not negative:
        return "Cautious"
    if score >= 0.25:
        return "Constructive"
    if score <= -0.25:
        return "Negative"
    if uncertainty or score < -0.05:
        return "Cautious"
    return "Neutral"


def _confidence(
    positive: list[str],
    negative: list[str],
    uncertainty: list[str],
    evasion: list[str],
    promotional: list[str],
    specificity: float,
    token_count: int,
) -> str:
    evidence = len(positive) + len(negative) + len(uncertainty) + len(evasion) + len(promotional)
    if token_count < 12:
        return "Low"
    if evidence >= 2 or specificity >= 3:
        return "High"
    if evidence == 1 or specificity >= 1:
        return "Medium"
    return "Low"
