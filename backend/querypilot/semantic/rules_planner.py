"""Deterministic semantic parser: natural language -> Draft plan.

It is cheap (no model, ~1 ms), fully explainable, and handles the bulk of routine finance
questions. It also *measures its own coverage*: every content word must be explained by a
metric, dimension, value, time expression or known modifier. Words it cannot explain lower
the confidence, and the hybrid router hands those questions to the LLM planner (or asks
the user to clarify when no model is configured).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from querypilot.core.models import Filter, QueryPlan
from querypilot.semantic import timeparse as tp
from querypilot.semantic.catalog import Catalog
from querypilot.semantic.plan_builder import Draft, draft_from_plan

STOPWORDS = set("""
a an the of in on for to by with and or what which who whom how much many was were is are be been being did do does
had has have our we us me my mine show give list tell get display find please can could would should you i total
overall all each per across between from at as vs versus than it that this those them these there their its so far
about any some only just also now then sum entire whole company business let know want need see view look like
numbers number data report table chart graph plot me split broken break breakdown down out into up value values amount
level levels figure figures stats statistics metric metrics kpi kpis performance perform performed doing compare
compared comparison relative did happen happened during against when where versus vs. along plus over rank ranked
ranking sorted sort order ordered biggest largest highest lowest smallest most least top bottom best worst max min
maximum minimum greatest per-region rate whats what's how's whats' respectively period periods time been go went
quick quickly currently current here every same again too well both either thing things result results
""".split())

FOLLOWUP_START = re.compile(
    r"^(?:and|what about|how about|now|also|then|ok|okay|same|instead|only|just|break (?:it|that|this|them) down|"
    r"drill|split (?:it|that|this)|show (?:it|that|this|them)|compare (?:it|that|this)|and for|for|but|"
    r"what if|same thing|same for|can you also|could you also|now show|now break|filter|exclude|remove|drop|add|"
    r"switch|change (?:it|that|the)|make it|limit|sort|order)\b")
PRONOUN_REF = re.compile(r"\b(?:it|that|this|those|them|same|these)\b")

CMP_YOY = re.compile(
    r"\b(?:year[- ]over[- ]year|yoy|y/y|year[- ]on[- ]year|(?:vs\.?|versus|compared (?:with|to)|relative to|against)\s+"
    r"(?:the\s+)?same (?:period|quarter|month|time|half) (?:last|a|the previous|the prior|previous|prior) year|"
    r"(?:vs\.?|versus|compared (?:with|to)|relative to|against|than)\s+(?:a|one) year (?:ago|earlier|before)|"
    r"(?:vs\.?|versus|compared (?:with|to)|relative to|against)\s+(?:the\s+)?(?:prior|previous|last) year'?s? same \w+)\b")
CMP_PREV = re.compile(
    r"\b(?:(?:compared|relative|in comparison) (?:with|to) (?:the\s+)?(?:previous|prior|preceding|last|earlier) "
    r"(?:quarter|month|period|year|half)|(?:vs\.?|versus|than|over|from|against|with) (?:the\s+)?(?:previous|prior|preceding) "
    r"(?:quarter|month|period|year|half)|sequential(?:ly)?|period[- ]over[- ]period|pop)\b")
CMP_QOQ = re.compile(r"\b(?:quarter[- ]over[- ]quarter|quarter[- ]on[- ]quarter|qoq|q/q)\b")
CMP_MOM = re.compile(r"\b(?:month[- ]over[- ]month|month[- ]on[- ]month|mom|m/m)\b")
CHANGE_WORDS = re.compile(r"\b(?:change|changed|changes|growth|grow|grew|growing|increase|increased|increasing|"
                          r"decrease|decreased|decreasing|decline|declined|declining|drop|dropped|dropping|fell|fall|"
                          r"falling|fallen|shrank|shrink|shrinking|rose|rise|rising|risen|improve|improved|improving|"
                          r"improvement|worsen|worsened|worsening|deteriorate|deteriorated|deteriorating|delta|"
                          r"variance|movement|moved|swing|trended|shift|shifted)\b")
DECLINE = re.compile(r"\b(?:decline|declined|declining|declines|decrease|decreased|decreasing|drop|dropped|dropping|"
                     r"fell|fall|falling|fallen|shrank|shrink|shrinking|worsen|worsened|worsening|deteriorat\w*|"
                     r"went down|gone down|go down|lower than|contract(?:ed|ing))\b")
INCREASE = re.compile(r"\b(?:increased|increasing|grew|growing|rose|rising|risen|improved|improving|went up|gone up|"
                      r"higher than|expanded|expanding)\b")
DRIVERS = re.compile(r"\b(?:what changed|what has changed|why|drivers?|driving|drove|what caused|cause[sd]?|root cause|"
                     r"reasons?|behind|explain|explanation|attribut\w*|decompos\w*|what happened|contributed|contributors?)\b")
UNSUPPORTED = re.compile(r"\b(?:forecast\w*|predict\w*|projection|project(?:ed)? to|will be|going to be|next (?:quarter|month|year)|"
                         r"budget|target|plan(?:ned)? vs|quota|nps|net promoter|satisfaction|churn|weather|stock price|headcount plan)\b")
SQL_START = re.compile(r"^\s*(?:select|with|insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|merge|"
                       r"call|do|explain|vacuum|analy[sz]e|set|reset|begin|commit|rollback|lock|listen|notify)\b", re.I)
TOPN = re.compile(r"\b(top|bottom|best|worst|highest|lowest|largest|smallest|biggest|first|last)\s+(\d{1,3}|"
                  + "|".join(tp.WORDNUM) + r")\b")
SUPER_DESC = re.compile(r"\b(?:highest|most|best|largest|biggest|greatest|maximum|max|top|strongest|leading|leader)\b")
SUPER_ASC = re.compile(r"\b(?:lowest|least|worst|smallest|minimum|min|bottom|weakest|lagging|laggard)\b")
BIGGEST_DROP = re.compile(r"\b(?:biggest|largest|steepest|sharpest|most|greatest|worst)\s+(?:declines?|drops?|decreases?|falls?|deterioration)|"
                          r"(?:declined|dropped|fell|decreased|deteriorated) (?:the )?most\b")
BIGGEST_RISE = re.compile(r"\b(?:biggest|largest|strongest|most|greatest|fastest)\s+(?:growth|increases?|improvements?|gains?|rises?)|"
                          r"(?:grew|increased|improved|rose) (?:the )?most|fastest[- ]growing\b")
EXCLUDE = re.compile(r"\b(?:excluding|exclude|except|other than|outside(?: of)?|without|not in|apart from|besides|ex)\s+$")
CLEAR_FILTERS = re.compile(r"\b(?:all regions|all categories|all channels|all segments|remove (?:the )?filters?|"
                           r"without (?:the )?filters?|clear (?:the )?filters?|everything|everywhere|company[- ]wide|overall)\b")
CLEAR_DIMS = re.compile(r"\b(?:in total|as a total|overall total|no breakdown|remove (?:the )?breakdown|total only|just the total)\b")
GROUP_TRIGGER = re.compile(r"(?:\bby|\bper|\beach|\bevery|\bacross|\bwhich|\bwhat|\bbreak(?:down)?|\bsplit|\bfor each|"
                           r"\bfor every|\bbroken down by|\bgrouped by|\bwise|\bvarious|\bdifferent|\bcompare)\s+(?:\w+\s+){0,2}$")

# value aliases: dimension -> {alias: canonical}
VALUE_ALIASES = {
    "region": {"asia pacific": "APAC", "asia-pacific": "APAC", "asia": "APAC", "europe middle east and africa": "EMEA",
               "europe": "EMEA", "western": "West", "eastern": "East", "northern": "North", "southern": "South",
               "latin america": "LATAM", "latam": "LATAM"},
    "category": {"food and beverage": "Food & Beverage", "food & beverages": "Food & Beverage", "f&b": "Food & Beverage",
                 "food": "Food & Beverage", "beverages": "Food & Beverage", "home and garden": "Home & Garden",
                 "home": "Home & Garden", "garden": "Home & Garden", "apparel": "Clothing", "clothes": "Clothing",
                 "fashion": "Clothing", "electronic": "Electronics", "consumer electronics": "Electronics",
                 "sporting goods": "Sports", "sport": "Sports"},
    "channel": {"e-commerce": "Online", "ecommerce": "Online", "web": "Online", "website": "Online", "digital": "Online",
                "in-store": "Retail", "stores": "Retail", "store": "Retail", "brick and mortar": "Retail",
                "partners": "Partner", "partner channel": "Partner", "resellers": "Partner", "reseller": "Partner",
                "wholesale": "Partner"},
    "segment": {"small business": "SMB", "small businesses": "SMB", "small and medium": "SMB", "smbs": "SMB",
                "enterprises": "Enterprise", "enterprise customers": "Enterprise", "large accounts": "Enterprise",
                "consumers": "Consumer", "retail customers": "Consumer", "b2c": "Consumer", "individuals": "Consumer"},
}
GENERIC_VALUE_WORDS = {"region", "regions", "category", "categories", "channel", "channels", "segment", "segments", "the"}


@dataclass
class RuleParse:
    draft: Draft
    route: str = "semantic"  # semantic | sql | unsupported | unknown
    is_followup: bool = False
    coverage: float = 0.0
    confidence: float = 0.0
    unknown_terms: list[str] = field(default_factory=list)
    reason: str = ""
    matched: dict[str, list[str]] = field(default_factory=dict)


def normalize(q: str) -> str:
    s = q.lower().replace("’", "'").replace("–", "-").replace("—", "-")
    s = re.sub(r"[?!,;:\"()\[\]]", " ", s)
    s = re.sub(r"(?<=\w)'s\b", "", s)
    s = re.sub(r"\.(?=\s|$)", " ", s)
    return re.sub(r"\s+", " ", s).strip()


class Masker:
    """Tracks which character spans of the question have been explained."""

    def __init__(self, text: str):
        self.text = text
        self.mask = [False] * len(text)

    def free(self, a: int, b: int) -> bool:
        return not any(self.mask[a:b])

    def take(self, a: int, b: int):
        for i in range(a, b):
            self.mask[i] = True

    def find_free(self, pattern: re.Pattern | str):
        rx = re.compile(pattern) if isinstance(pattern, str) else pattern
        for m in rx.finditer(self.text):
            if m.end() > m.start() and self.free(m.start(), m.end()):
                yield m

    def remaining_tokens(self) -> list[str]:
        out, cur = [], []
        for ch, used in zip(self.text, self.mask):
            if not used and (ch.isalnum() or ch in "&%-"):
                cur.append(ch)
            else:
                if cur:
                    out.append("".join(cur))
                cur = []
        if cur:
            out.append("".join(cur))
        return [t.strip("-") for t in out if t.strip("-")]


def _phrase_rx(phrase: str) -> re.Pattern:
    p = re.escape(phrase.lower()).replace(r"\ ", r"\s+")
    return re.compile(r"(?<![\w&])" + p + r"(?![\w&])")


class RulePlanner:
    def __init__(self, catalog: Catalog):
        self.cat = catalog
        self.metric_phr = sorted(((s.lower(), k) for k, m in catalog.metrics.items() for s in m.synonyms + [k.replace("_", " ")]),
                                 key=lambda x: -len(x[0]))
        self.dim_phr = sorted(((s.lower(), k) for k, d in catalog.dimensions.items() for s in d.synonyms),
                              key=lambda x: -len(x[0]))

    # ------------------------------------------------------------------ helpers
    def _values(self, mk: Masker, values: dict[str, list[str]]) -> list[tuple[str, str, int, int]]:
        cands: list[tuple[str, str, str]] = []  # (phrase, dim, canonical)
        for dim, vals in values.items():
            for v in vals:
                cands.append((v.lower(), dim, v))
                if "&" in v:
                    cands.append((v.lower().replace("&", "and"), dim, v))
            for alias, canon in VALUE_ALIASES.get(dim, {}).items():
                if canon in vals:
                    cands.append((alias, dim, canon))
        cands.sort(key=lambda c: -len(c[0]))
        found = []
        for phrase, dim, canon in cands:
            if phrase in GENERIC_VALUE_WORDS:
                continue
            for m in mk.find_free(_phrase_rx(phrase)):
                mk.take(m.start(), m.end())
                found.append((dim, canon, m.start(), m.end()))
        return sorted(found, key=lambda x: x[2])

    # ------------------------------------------------------------------ main
    def parse(self, question: str, today: date, values: dict[str, list[str]], previous: QueryPlan | None = None) -> RuleParse:
        raw = question.strip()
        if SQL_START.match(raw) or re.search(r";\s*(?:drop|delete|update|insert|alter|truncate|grant)\b", raw, re.I):
            return RuleParse(draft=Draft(), route="sql", confidence=1.0, coverage=1.0, reason="user-supplied SQL")
        text = normalize(raw)
        mk = Masker(text)
        d = Draft()
        matched: dict[str, list[str]] = {k: [] for k in ["metrics", "dimensions", "values", "time", "modifiers"]}

        if UNSUPPORTED.search(text):
            m = UNSUPPORTED.search(text)
            return RuleParse(draft=d, route="unsupported", reason=m.group(0), confidence=0.9)

        # 1) comparison phrases (masked first so "previous quarter" isn't read as the main period)
        cmp_kind = None
        for rx, kind in [(CMP_YOY, "yoy"), (CMP_PREV, "previous_period"), (CMP_QOQ, "qoq"), (CMP_MOM, "mom")]:
            for m in mk.find_free(rx):
                mk.take(m.start(), m.end())
                cmp_kind = cmp_kind or kind
                matched["modifiers"].append(m.group(0))

        # 2) time expressions
        times: list[tp.TimeMatch] = []
        for tm in tp.resolve_all(text, today):
            if mk.free(*tm.span):
                mk.take(*tm.span)
                times.append(tm)
                matched["time"].append(text[tm.span[0]:tm.span[1]])
        if times:
            primary = times[0]
            d.time = primary.spec
            if primary.assumption:
                d.assumptions.append(primary.assumption)
            if len(times) >= 2:
                a, b = times[0].spec, times[1].spec
                cur, other = (a, b) if a.start >= b.start else (b, a)
                d.time = cur
                if other.start == tp.previous_period(cur).start and other.end == cur.start:
                    cmp_kind = cmp_kind or "previous_period"
                elif other.start == tp.same_period_last_year(cur).start:
                    cmp_kind = cmp_kind or "yoy"
                else:
                    cmp_kind = cmp_kind or "custom"
                    d.compare_to = other
                if a.start == b.start and a.end == b.end:
                    cmp_kind = None
        if cmp_kind in ("qoq", "mom"):
            unit = "quarter" if cmp_kind == "qoq" else "month"
            if d.time is not None and d.time.grain == unit:
                cmp_kind = "previous_period"
            elif d.time is None:
                cmp_kind = "previous_period"
                if unit == "month":
                    cur_m = date(today.year, today.month, 1)
                    s = tp.add_months(cur_m, -1)
                    d.time = tp.month(s.year, s.month)
                    d.assumptions.append(f"Month-over-month read as {d.time.label} vs the month before.")
            else:
                d.dimensions.append(unit)
                cmp_kind = None
        if cmp_kind:
            d.comparison = cmp_kind

        # 3) filter values (before dimension words: "West region" -> filter, not group-by)
        for dim, canon, a, b in self._values(mk, values):
            prefix = text[max(0, a - 22):a]
            op = "not_in" if EXCLUDE.search(prefix) else "in"
            if op == "not_in":
                em = EXCLUDE.search(prefix)
                mk.take(a - (len(prefix) - em.start()), a)
            existing = next((f for f in d.filters if f.dimension == dim and f.op == op), None)
            if existing:
                if canon not in existing.values:
                    existing.values.append(canon)
            else:
                d.filters.append(Filter(dimension=dim, op=op, values=[canon]))
            matched["values"].append(canon)
            # absorb a trailing generic word: "the West region", "Electronics category", "SMB customers"
            nouns = "region|category|channel|segment|segments|regions|categories|channels|products|product|items"
            if dim == "segment":
                nouns += "|customers|customer|clients|accounts|buyers"
            for m in mk.find_free(re.compile(r"(?<=\s)(?:" + nouns + r")\b")):
                if 0 <= m.start() - b <= 2:
                    mk.take(m.start(), m.end())

        # "all categories", "remove the filter" are filter resets, never group-bys
        cleared: list[str] = []
        for m in mk.find_free(CLEAR_FILTERS):
            mk.take(m.start(), m.end())
            cleared.append(m.group(0))

        # 4) metrics. "customers"/"products" are both metric- and dimension-ish: decide by context.
        for phrase, key in self.metric_phr:
            for m in mk.find_free(_phrase_rx(phrase)):
                before = text[max(0, m.start() - 30):m.start()]
                if key == "customers" and phrase in ("customers", "customer") and re.search(
                        r"(?:\btop|\bbottom|\bwhich|\bbest|\bworst|\blargest|\bbiggest|\bby|\bper|\beach|\blist|\bour|\bhighest|"
                        r"\blowest|\bmost|\bleast)\s+(?:\w+\s+)?$", before) and not re.search(r"(?:how many|number of|count of)\s+(?:\w+\s+)?$", before):
                    continue  # it's the customer dimension
                mk.take(m.start(), m.end())
                if key not in d.metrics:
                    d.metrics.append(key)
                matched["metrics"].append(phrase)

        # 5) dimensions (group-by) - need a grouping trigger, a plural, or a time-grain word
        for phrase, key in self.dim_phr:
            dim = self.cat.dimensions[key]
            for m in mk.find_free(_phrase_rx(phrase)):
                before = text[:m.start()]
                is_plural = phrase.endswith("s") and not phrase.endswith("ss") and phrase not in ("sales channel",)
                triggered = bool(GROUP_TRIGGER.search(before)) or bool(TOPN.search(before[-25:]))
                grain_word = dim.time_grain and phrase not in ("month", "quarter", "year", "months", "quarters", "years")
                after = text[m.end():m.end() + 12]
                if dim.time_grain and phrase in ("month", "quarter", "year") and not triggered:
                    continue
                if is_plural or triggered or grain_word or re.match(r"\s*(?:wise|level|breakdown|split|mix)\b", after):
                    mk.take(m.start(), m.end())
                    if key not in d.dimensions:
                        d.dimensions.append(key)
                    matched["dimensions"].append(phrase)
                    am = re.match(r"\s*(?:wise|level|breakdown|split|mix)\b", after)
                    if am:
                        mk.take(m.end(), m.end() + am.end())

        # 6) ranking
        ranked = False
        for m in mk.find_free(TOPN):
            ranked = True
            word, n = m.group(1), m.group(2)
            d.limit = tp._n(n)
            d.order_dir = "asc" if word in ("bottom", "worst", "lowest", "smallest") else "desc"
            mk.take(m.start(), m.end())
            matched["modifiers"].append(m.group(0))
        drop_m, rise_m = BIGGEST_DROP.search(text), BIGGEST_RISE.search(text)
        if drop_m or rise_m:
            d.order_by = "__change__"
            d.order_dir = "asc" if drop_m else "desc"
            for mm in (drop_m, rise_m):
                if mm:
                    mk.take(mm.start(), mm.end())
            if d.comparison == "none":
                d.comparison = "previous_period"
            sw = re.search(r"\b(?:which|what)\s+(\w+)", text)
            if d.limit is None and sw and not sw.group(1).endswith("s"):
                d.limit = 1
            ranked = True
        elif d.limit is None:
            desc, asc = SUPER_DESC.search(text), SUPER_ASC.search(text)
            if desc or asc:
                ranked = True
                d.order_dir = "asc" if (asc and (not desc or asc.start() < desc.start())) else "desc"
                singular_which = re.search(r"\b(?:which|what)\s+(\w+)", text)
                if singular_which and d.dimensions and not singular_which.group(1).endswith("s"):
                    d.limit = 1
                elif re.search(r"\b(?:the|our)\s+(?:single\s+)?(?:highest|lowest|best|worst|top|most|least|largest|smallest|biggest)\b", text) and d.dimensions and not re.search(r"\b(?:which|what)\s+\w+s\b", text):
                    d.limit = 1
                for mm in (desc, asc):
                    if mm:
                        mk.take(mm.start(), mm.end())
        if d.limit is None and re.search(r"\btop\b", text) and d.dimensions:
            d.limit = 5
            d.assumptions.append("'Top' without a number read as top 5.")

        # 7) change direction / drivers
        if DECLINE.search(text) and (d.dimensions or d.comparison != "none" or re.search(r"\b(?:which|what|where|list|show|any)\b", text)):
            d.change_filter = "declining" if d.dimensions else "none"
            if d.comparison == "none":
                d.comparison = "previous_period"
        elif INCREASE.search(text) and d.dimensions and re.search(r"\b(?:which|what|where|list|show|any)\b", text):
            d.change_filter = "increasing"
            if d.comparison == "none":
                d.comparison = "previous_period"
        if CHANGE_WORDS.search(text) and d.comparison == "none" and not any(self.cat.dimensions[x].time_grain for x in d.dimensions):
            d.comparison = "previous_period"
            d.assumptions.append("'Change/growth' measured against the previous period.")
        if DRIVERS.search(text):
            d.drivers = True
        for rx in (DECLINE, INCREASE, CHANGE_WORDS, DRIVERS):
            for m in mk.find_free(rx):
                mk.take(m.start(), m.end())
        if d.order_by == "__change__":
            d.order_by = f"{d.metrics[0]}_change" if d.metrics else None
        elif ranked and d.order_by is None and d.metrics:
            d.order_by = d.metrics[0]

        # 8) follow-up handling
        is_followup = False
        if previous is not None and (not d.metrics or FOLLOWUP_START.match(text) or
                                     (PRONOUN_REF.search(text) and not d.metrics)):
            is_followup = True
            d = self._merge(previous, d, text, cleared)
            for m in mk.find_free(FOLLOWUP_START):
                mk.take(m.start(), m.end())
            for m in mk.find_free(CLEAR_FILTERS):
                mk.take(m.start(), m.end())
            for m in mk.find_free(CLEAR_DIMS):
                mk.take(m.start(), m.end())

        # 9) coverage + confidence
        leftovers = [t for t in mk.remaining_tokens() if t not in STOPWORDS and not t.isdigit() and len(t) > 1]
        content = len(leftovers) + sum(len(v) for v in matched.values())
        coverage = 1.0 if content == 0 else 1 - len(leftovers) / max(content, 1)
        route = "semantic" if d.metrics else "unknown"
        confidence = 0.0 if not d.metrics else round(min(1.0, 0.3 + 0.7 * coverage), 3)
        if d.order_by and d.order_by not in d.metrics + d.dimensions and not d.order_by.endswith("_change"):
            d.order_by = None
        return RuleParse(draft=d, route=route, is_followup=is_followup, coverage=round(coverage, 3),
                         confidence=confidence, unknown_terms=leftovers, matched=matched)

    # ------------------------------------------------------------------ follow-ups
    def _merge(self, prev: QueryPlan, new: Draft, text: str, cleared: list[str] | None = None) -> Draft:
        out = draft_from_plan(prev)
        out.assumptions = list(new.assumptions)
        additive = bool(re.search(r"\b(?:also|as well|add|plus|along with|together with|and also)\b", text))
        replace_dims = bool(re.search(r"\b(?:instead|rather)\b", text))
        if new.metrics:
            out.metrics = list(dict.fromkeys(out.metrics + new.metrics)) if additive else new.metrics
        if CLEAR_DIMS.search(text):
            out.dimensions = []
        if new.dimensions:
            if replace_dims:
                out.dimensions = new.dimensions
            else:
                for nd in new.dimensions:
                    if self.cat.dimensions[nd].time_grain:
                        out.dimensions = [x for x in out.dimensions if not self.cat.dimensions[x].time_grain]
                    if nd not in out.dimensions:
                        out.dimensions.append(nd)
        for phrase in cleared or []:
            dim = next((k for w, k in (("region", "region"), ("categor", "category"), ("channel", "channel"),
                                       ("segment", "segment")) if w in phrase), None)
            out.filters = [f for f in out.filters if dim is not None and f.dimension != dim]
        for f in new.filters:
            out.filters = [g for g in out.filters if g.dimension != f.dimension] + [f]
            if f.op == "in" and len(f.values) == 1 and f.dimension in out.dimensions and not new.dimensions:
                pass
        if new.time is not None:
            out.time = new.time
            out.compare_to = new.compare_to
        if new.comparison != "none":
            out.comparison = new.comparison
            out.compare_to = new.compare_to
        elif re.search(r"\b(?:without (?:the )?comparison|no comparison|just (?:the )?current|only (?:the )?current)\b", text):
            out.comparison, out.change_filter = "none", "none"
        if new.change_filter != "none":
            out.change_filter = new.change_filter
        if new.limit is not None:
            out.limit, out.order_dir = new.limit, new.order_dir
        if new.order_by:
            out.order_by, out.order_dir = new.order_by, new.order_dir
        out.drivers = new.drivers
        if out.order_by and out.order_by not in out.metrics + out.dimensions and not out.order_by.endswith("_change"):
            out.order_by = None
        if new.metrics and not additive and out.order_by in prev.metrics:
            out.order_by = None
        return out
