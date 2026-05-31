"""Sentiment engine - VADER tuned for finance/WSB slang, with a lexicon fallback.

score(text) -> (compound: float in [-1,1], label: 'positive'|'neutral'|'negative')
"""

# Finance / WSB slang boosters layered onto VADER's lexicon.
_FINANCE_LEXICON = {
    # bullish
    "moon": 3.0, "mooning": 3.0, "rocket": 2.5, "tendies": 2.5, "calls": 1.5,
    "bullish": 2.5, "buy": 1.5, "long": 1.2, "squeeze": 2.0, "breakout": 2.0,
    "rip": 1.5, "ripping": 2.0, "green": 1.5, "gains": 2.0, "printing": 2.0,
    "undervalued": 2.0, "diamond": 1.5, "hold": 1.0, "hodl": 1.5, "yolo": 0.5,
    "pump": 1.0, "rally": 2.0, "beat": 1.5, "upgrade": 2.0,
    # bearish
    "puts": -1.5, "bearish": -2.5, "sell": -1.5, "short": -1.2, "crash": -3.0,
    "dump": -2.0, "dumping": -2.5, "red": -1.5, "bagholder": -2.5, "bag": -1.5,
    "rug": -3.0, "rugpull": -3.0, "rugged": -3.0, "tank": -2.5, "tanking": -2.5,
    "drilling": -2.0, "drill": -1.5, "bleeding": -2.5, "overvalued": -2.0,
    "downgrade": -2.0, "miss": -1.5, "bankruptcy": -3.0, "dilution": -2.0,
    "scam": -3.0, "halt": -1.5, "bag holding": -2.5,
}

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _analyzer = SentimentIntensityAnalyzer()
    _analyzer.lexicon.update(_FINANCE_LEXICON)
    _HAVE_VADER = True
except Exception:  # pragma: no cover - fallback when dep missing
    _analyzer = None
    _HAVE_VADER = False


def _label(compound):
    if compound >= 0.05:
        return "positive"
    if compound <= -0.05:
        return "negative"
    return "neutral"


def score(text):
    """Return (compound, label) for a piece of text."""
    if not text:
        return 0.0, "neutral"
    if _HAVE_VADER:
        compound = _analyzer.polarity_scores(text)["compound"]
        return round(compound, 4), _label(compound)
    # Fallback: reuse the simple lexicon scorer.
    from analytics.sentiment import analyze_sentiment
    s, label = analyze_sentiment(text)
    return s, label
