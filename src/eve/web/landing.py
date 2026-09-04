"""The public front page: its copy, and the view-model that builds it.

Every other screen reports on a workspace. This one makes a claim to a stranger,
so it is held to the rule the engine is held to: say only what can be shown.

That rule is why the copy lives here rather than in the template. The layer list
is the pipeline's own order, the verdict blurbs are keyed on the four verdicts
:mod:`eve.verdict` can actually emit, the disposable-domain count is read from
the file the classifier loads, the palette comes from :mod:`eve.web.format`
(which is what the app itself renders from, so the swatch on the pitch cannot
drift from the product), and the founding-account offer counts real accounts.
A claim on this page cannot go stale without the number on the page moving too.
"""
from __future__ import annotations

from typing import Any

from eve.config import get_settings
from eve.layers.classify import list_sizes
from eve.web import format as F

# --- Palette --------------------------------------------------------------- #
#: The app's tokens, handed to the page as CSS custom properties. This page is
#: standalone and cannot inherit base.html's style block, and re-typing the hex
#: values is how a landing page ends up a shade off the product it sells.
PALETTE = {
    "blue": F.BLUE,
    "blue-dark": F.BLUE_DARK,
    "blue-wash": F.BLUE_WASH,
    "blue-line": F.BLUE_LINE,
    "ink": F.INK,
    "ink-2": F.INK_2,
    "ink-3": F.INK_3,
    "muted": F.MUTED,
    "muted-2": F.MUTED_2,
    "line": F.LINE,
    "line-2": F.LINE_2,
    "surface": F.SURFACE,
    # The ground the framed sheet sits on. Deepened and de-warmed when the
    # brand white became #FCFCFC: against the old #FAF9F7 the sheet and the
    # ground were a percent apart and the frame stopped reading as a sheet,
    # and a warm ground under a #0000FF brand reads as a different system.
    "canvas": "#F2F2F3",
    "white": F.WHITE,
}

# --- Copy ------------------------------------------------------------------ #
#: The seven layers, in the order the engine runs them. Numbered by position, so
#: a layer added to the pipeline is added here and every count on the page,
#: including the two headlines that spell it out, follows.
LAYERS = [
    (
        "Syntax",
        "RFC 5322 parsing, plus the length and character limits the RFC leaves to "
        "the implementation. A malformed address never costs a network call.",
    ),
    (
        "Normalise and dedupe",
        "Gmail dots and plus tags collapse to one key, so a person is counted once "
        "however their address was typed.",
    ),
    (
        "Typo correction",
        "A misspelled domain returns a suggestion rather than a deletion, and the "
        "row keeps its place in your file.",
    ),
    (
        "DNS and MX",
        "Resolved once per domain and cached for the run, so a million rows are not "
        "a million lookups.",
    ),
    (
        "Classification",
        "Role accounts, free providers and {disposable} disposable domains, from "
        "lists that ship inside the engine and can be read line by line.",
    ),
    (
        "SMTP mailbox probe",
        "EHLO, MAIL FROM, RCPT, QUIT, and never DATA. The receiving server answers "
        "for the exact address, and nothing is delivered to your list.",
    ),
    (
        "Catch-all detection",
        "One probe per domain. A domain that accepts every recipient has proven "
        "nothing, so its addresses come back Risky rather than Deliverable.",
    ),
]

#: The layer count in prose. Two headlines spell it out; both read it from here
#: rather than carrying a digit that an eighth layer would leave wrong.
COUNT_WORDS = {5: "Five", 6: "Six", 7: "Seven", 8: "Eight", 9: "Nine", 10: "Ten"}

#: What the depth of the pipeline buys, stated as the thing the reader gets.
#: The mechanism that produces it is the layer list above, which the page shows
#: directly below this. Icon paths follow the nav's stroke geometry.
BENEFITS = [
    (
        "M4 13.5l5 5 11-11",
        "Dead mailboxes found before your ESP finds them",
        "The probe opens a conversation with the receiving server and asks about "
        "the exact address. A mailbox that stopped existing is caught here rather "
        "than in a bounce report your provider keeps a copy of.",
    ),
    (
        "M12 3l7.5 3.5v5c0 4.4-3.1 7.9-7.5 9.5-4.4-1.6-7.5-5.1-7.5-9.5v-5z",
        "A sender reputation that survives the send",
        "The large providers accept almost any recipient at the door and bounce it "
        "later. Validrow knows which ones do this and refuses to report a "
        "confident Deliverable on an acceptance that means nothing.",
    ),
    (
        "M4 7h16M4 12h10M4 17h13M18.5 12l2.5 2.5-2.5 2.5",
        "Catch-all domains named as what they are",
        "One probe per domain settles whether the server accepts everything. If it "
        "does, its addresses come back Risky with the reason attached, so you can "
        "decide instead of guessing.",
    ),
    (
        "M12 20.5a8.5 8.5 0 100-17 8.5 8.5 0 000 17M9 12.2l2 2 4.2-4.4",
        "Real leads kept instead of quietly dropped",
        "A typo on a live domain returns a correction your team can act on. The row "
        "stays in the file with the suggestion beside it rather than disappearing "
        "into a removed pile.",
    ),
    (
        "M9 8a3 3 0 106 0 3 3 0 10-6 0M4 20a8 8 0 0116 0",
        "One person counted once",
        "Dots and plus tags are the same mailbox wearing different spellings. They "
        "collapse to one key, so a list is reported per person rather than per "
        "variation.",
    ),
    (
        "M6 3h9l4 4v14H6zM14 3v5h5M9 13h7M9 17h5",
        "Removals you can defend",
        "Every verdict carries the layer that produced it and the sub-reason under "
        "that. A row you took out can be explained to whoever asks why the list "
        "shrank.",
    ),
]

VERDICT_COPY = {
    "deliverable": "The mailbox was probed and the receiving server answered for it.",
    "risky": "Real, but not a clean send. A role account, or a catch-all domain "
             "whose acceptance settles nothing.",
    "unknown": "Greylisting, a timeout, or a provider that will not answer honestly. "
               "Retried on a schedule, and never rounded up to Deliverable.",
    "undeliverable": "Invalid syntax, no MX, a disposable domain, or a mailbox the "
                     "server rejected outright.",
}

OUTPUTS = [
    ("cleaned.csv", "Every row you uploaded, every column kept, with the verdict, "
                    "sub-reason, score and normalised address appended."),
    ("valid.csv", "The rows worth sending to, and nothing else."),
    ("removed.csv", "What came out, with the layer and the reason each row came out."),
]

#: Four addresses, one per verdict, each settled at the layer that would settle
#: it in a real run. ``settled`` indexes :data:`LAYERS` from 1.
SAMPLE = [
    ("jane.doe@acme.io", "deliverable", 6, "The receiving server answered for this mailbox."),
    ("sales@acme.io", "risky", 5, "A role account. Real, but nobody in particular."),
    ("john@gmial.com", "undeliverable", 4, "The domain has no MX. Suggested john@gmail.com."),
    ("hello@bigco.com", "unknown", 6, "The server deferred the probe. Queued for a retry."),
]

#: The mix behind the hero card. Illustrative, labelled as an example on the
#: page, and made to add up: the counts sum to the total and the percentages go
#: through the app's own rounding, so the one number a careful reader checks
#: holds.
SAMPLE_MIX = [
    ("deliverable", 2946),
    ("risky", 613),
    ("unknown", 722),
    ("undeliverable", 531),
]

#: The hero artifact: the uploaded file as it comes back. The left columns are
#: the visitor's own, carried through untouched; the three on the right are what
#: a run appends. Keyed on :data:`SAMPLE` so the page has one set of example
#: addresses rather than two that could come to disagree with each other.
YOUR_COLUMNS = ("company", "signed_up")
APPENDED_COLUMNS = ("status", "sub_status", "settled_at")
PREVIEW_ROWS = {
    "jane.doe@acme.io": (("Acme", "2026-01-14"), "mailbox_confirmed"),
    "sales@acme.io": (("Acme", "2026-02-02"), "role_account"),
    "john@gmial.com": (("Northwind", "2026-02-19"), "no_mx"),
    "hello@bigco.com": (("Bigco", "2026-03-05"), "greylisted"),
}



def _layers() -> list[dict[str, Any]]:
    counts = list_sizes()
    return [
        {"n": i, "name": name, "detail": detail.format(disposable=f"{counts['disposable']:,}")}
        for i, (name, detail) in enumerate(LAYERS, start=1)
    ]


def _sample() -> list[dict[str, Any]]:
    """The four example addresses, each with the rail that shows where it stopped.

    The rail is seven bars: the layers that ran, the layer that settled the
    verdict in that verdict's own colour, and the layers the address never
    reached. It is the one picture of the pipeline that carries information,
    which is why it is computed here rather than drawn in the template.
    """
    rows = []
    for email, verdict, settled, note in SAMPLE:
        style = F.VERDICT_STYLE[verdict]
        rows.append(
            {
                "email": email,
                **style,
                "note": note,
                "settled": settled,
                "settledLabel": f"Settled at layer {settled}, {LAYERS[settled - 1][0]}",
                "rail": [
                    style["dot"] if i == settled else (F.LINE_2 if i < settled else F.SURFACE)
                    for i in range(1, len(LAYERS) + 1)
                ],
            }
        )
    return rows


def _preview() -> dict[str, Any]:
    """The hero table: an uploaded file with the run's three columns appended.

    Split into the columns that arrived and the columns that were added, because
    that split is the promise the page is making. The engine only ever reads the
    address column, so everything on the left of the rule is passed through
    byte for byte, and everything on the right is what a run produces.
    """
    rows = []
    for email, verdict, settled, _note in SAMPLE:
        yours, sub = PREVIEW_ROWS[email]
        style = F.VERDICT_STYLE[verdict]
        rows.append(
            {
                "email": email,
                "yours": list(yours),
                "sub": sub,
                "settled": settled,
                "label": style["label"],
                "dot": style["dot"],
                "wash": style["wash"],
                "ink": style["ink"],
            }
        )
    return {
        "yours": ["email", *YOUR_COLUMNS],
        "added": list(APPENDED_COLUMNS),
        "rows": rows,
    }


def _proof(open_signup: bool) -> list[dict[str, str]]:
    """The three figures under the hero, each read from the engine itself.

    The third is the offer, and it is only made where the offer stands. A
    self-hosted engine with registration closed states what ships in the box
    instead, so the strip never advertises a place the signup route would then
    have to refuse.
    """
    counts = list_sizes()
    s = get_settings()
    strip = [
        {
            "figure": f"{len(LAYERS):,}",
            "unit": "verification layers",
            "detail": "Syntax through to an SMTP mailbox probe, run in that order.",
        },
        {
            "figure": f"{counts['disposable']:,}",
            "unit": "disposable domains",
            "detail": "Vendored into the engine, and readable line by line.",
        },
    ]
    strip.append(
        {
            "figure": f"{s.free_monthly_addresses:,}",
            "unit": "addresses a month",
            "detail": f"Free for the first {s.founding_accounts:,} accounts. No card.",
        }
        if open_signup
        else {
            "figure": f"{counts['roles'] + counts['free']:,}",
            "unit": "role and free-provider rules",
            "detail": "Classification runs offline, against lists you can audit.",
        }
    )
    return strip


def _mix() -> dict[str, Any]:
    """The hero card's stacked bar and legend.

    Built by ``format.mix``, which is what the dashboard's own verdict bar is
    built by. That is what makes the four percentages add to a hundred here for
    the same reason they add to a hundred in the app: one rounding rule, not a
    second one written for the pitch.
    """
    totals = dict(SAMPLE_MIX)
    return {
        "total": f"{sum(totals.values()):,}",
        "rows": [
            {**row, "share": row["pct_label"], "width": row["pct"]}
            for row in F.mix(totals)
        ],
    }


async def _offer() -> dict[str, Any]:
    """The founding-account offer, counted rather than asserted.

    Three states, because all three are reachable. A public engine with places
    left says how many are left; once they are gone it says that instead of
    counting down past zero; and a self-hosted engine with registration closed
    makes no offer at all, because on that install there is nothing to sign up
    for.

    The counter is the number of accounts, which the auth store knows exactly.
    It is not a usage meter: nothing measures addresses per account yet, so the
    monthly figure is stated as the offer it is and nothing on the page pretends
    to count against it.
    """
    from eve.auth import get_auth_store

    s = get_settings()
    places = s.founding_accounts
    taken = await get_auth_store().count_users()
    # The first account can always be created: that is the bootstrap for a fresh
    # install, and it is the rule the signup route itself applies.
    open_signup = s.open_signup or taken == 0
    left = max(0, places - taken)
    word = COUNT_WORDS.get(len(LAYERS), str(len(LAYERS)))

    if not open_signup:
        return {
            "badge": "Accounts on this engine are created by its owner",
            "headline": "This engine is not open for registration.",
            "body": "Ask whoever runs it for an account, then sign in.",
            "ctaLabel": "Sign in",
            "ctaHref": "/login",
            "open": False,
            "note": "",
            "counter": "",
        }
    if left == 0:
        return {
            "badge": f"The first {places:,} accounts are taken",
            "headline": f"The founding {places:,} places are claimed.",
            "body": "Validrow is still free while it is in beta. Create an account "
                    "and run a list today.",
            "ctaLabel": "Create an account",
            "ctaHref": "/signup",
            "open": True,
            "note": "No card.",
            "counter": "",
        }
    return {
        "badge": (
            f"Free for the first {places:,} accounts"
            if left == places
            else f"{left:,} of {places:,} free accounts left"
        ),
        "headline": f"Free for the first {places:,} accounts.",
        "body": f"{s.free_monthly_addresses:,} addresses a month, every month, through "
                f"all {word.lower()} layers. The same engine, the same probe, the same "
                f"file back.",
        "ctaLabel": "Claim a free account",
        "ctaHref": "/signup",
        "open": True,
        "note": "No card, and no clock counting down a trial.",
        # Nothing to count down until a place is taken, and "100 of 100" reads
        # as a counter that is broken rather than as one that has not moved.
        "counter": "" if left == places else f"{left:,} of {places:,} places open",
    }


async def context(engine_url: str) -> dict[str, Any]:
    """Everything ``landing.html`` renders."""
    s = get_settings()
    offer = await _offer()
    return {
        "palette": PALETTE,
        "offer": offer,
        "monthlyFree": f"{s.free_monthly_addresses:,}",
        "foundingAccounts": f"{s.founding_accounts:,}",
        "layerCount": len(LAYERS),
        "layerWord": COUNT_WORDS.get(len(LAYERS), str(len(LAYERS))),
        "layers": _layers(),
        "benefits": [{"icon": i, "title": t, "detail": d} for i, t, d in BENEFITS],
        "verdicts": [{**F.VERDICT_STYLE[k], "detail": VERDICT_COPY[k]} for k in F.ORDER],
        "outputs": [{"name": n, "detail": d} for n, d in OUTPUTS],
        "sample": _sample(),
        "mix": _mix(),
        "preview": _preview(),
        "proof": _proof(offer["open"]),
        "engineUrl": engine_url,
    }
