"""The API reference at "/", built from this app's own OpenAPI document.

FastAPI's Swagger UI was doing this job. Two things were wrong with it. It is
fetched from a CDN, so the one page that documents an engine you can run with no
network access was the one page that needed the network; and it looks like
Swagger rather than like the product, which is the whole reason this page is
being rebuilt.

What replaces it is not a second copy of the API written in prose. Every
endpoint, parameter, field, type and default on the page is read from
``app.openapi()`` at request time, which is generated from the route signatures
and the Pydantic models. A field renamed in :mod:`eve.api.schemas` is renamed
here; a route added to a router appears here; a description written on a model
is the description the reader sees. The only strings typed in this module are
the ones OpenAPI has nowhere to put: what the base URL is, how a key is sent,
and what the four verdicts mean.

The examples are generated from the schemas too, so a request body shown on the
page is a body the endpoint would accept.
"""
from __future__ import annotations

import html
import json
import re
from typing import Any, Optional

from markupsafe import Markup

from eve.config import get_settings
from eve.web import format as F

#: Tag order on the page. A caller reads it top to bottom: check one address,
#: then the file pipeline that checks a million, then the workspace those runs
#: fill, then the endpoint a load balancer polls. Anything the app grows a tag
#: for that is missing here is appended in the order OpenAPI reports it.
TAG_ORDER = ["verify", "files", "jobs", "workspace", "meta"]

TAG_COPY = {
    "verify": (
        "One address, checked now",
        "Runs the full pipeline on a single address and answers on the same "
        "request. Use it while a form is still open; use the file pipeline "
        "below for anything larger than a form.",
    ),
    "files": (
        "Uploading a list",
        "A bulk run is two calls: upload the CSV, then start a job against the "
        "file id you get back. The upload answers with the header row and a "
        "guess at which column holds the address, so a client can confirm the "
        "mapping instead of asking for it blind.",
    ),
    "jobs": (
        "Running and collecting",
        "A job is created, worked through in the background, and downloaded as "
        "three CSV segments. Poll the job or give it a webhook.",
    ),
    "workspace": (
        "The workspace read-model",
        "Every address this workspace has ever validated, de-duplicated across "
        "runs. These endpoints answer questions that span jobs, which is why "
        "they read the workspace rather than a run's output file.",
    ),
    "meta": (
        "Operations",
        "What a load balancer and a status page need. Reachable without "
        "credentials, always.",
    ),
}

METHOD_TONE = {
    "get": ("GET", F.BLUE_DARK, F.BLUE_WASH),
    "post": ("POST", F.GREEN_INK, "#ECFDF3"),
    "delete": ("DELETE", "#B42318", "#FEF3F2"),
    "put": ("PUT", "#B54708", "#FFFAEB"),
    "patch": ("PATCH", "#B54708", "#FFFAEB"),
}

_METHODS = ("get", "post", "put", "patch", "delete")


# --- Colouring the two code blocks ----------------------------------------- #
# The samples are generated, so they are highlighted here rather than by a
# script in the browser: a page that documents an engine which runs with no
# network access should not need to fetch a highlighter to be readable. Both
# passes are lexical and deliberately small, because the only two languages on
# this page are "JSON that json.dumps just produced" and "the curl line above
# it", and neither needs a parser.
# The samples land inside <pre>, where a quote needs no escaping, so they are
# escaped with quote=False and the patterns match a plain '"'. Escaping quotes
# too would leave every pattern here hunting for &quot; instead.
_JSON_TOKEN = re.compile(
    r'(?P<key>"[^"\n]*")(?P<colon>\s*:)'
    r'|(?P<str>"[^"\n]*")'
    r'|(?P<num>-?\b\d+(?:\.\d+)?\b)'
    r'|(?P<bool>\b(?:true|false|null)\b)'
)


def _highlight_json(text: str) -> Markup:
    """Colour a JSON sample. Escaped first, so the output is safe to emit."""
    escaped = html.escape(text, quote=False)

    def paint(m: re.Match[str]) -> str:
        if m.group("key"):
            return f'<span class="c-key">{m.group("key")}</span>{m.group("colon")}'
        if m.group("str"):
            return f'<span class="c-str">{m.group("str")}</span>'
        if m.group("num"):
            return f'<span class="c-num">{m.group("num")}</span>'
        return f'<span class="c-bool">{m.group("bool")}</span>'

    return Markup(_JSON_TOKEN.sub(paint, escaped))


# --- Reading the OpenAPI document ------------------------------------------ #
def _deref(spec: dict, node: Any) -> Any:
    """Follow a ``$ref`` into ``components``, once.

    OpenAPI describes a body as a reference to a named schema. The page renders
    fields, so every reference has to be resolved before it can be walked. Only
    one hop is followed here because that is all this API's schemas use; a
    deeper chain would come back as the reference's own name, which is still
    truthful, rather than as a wrong field list.
    """
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if not ref or not ref.startswith("#/components/schemas/"):
        return node
    return spec.get("components", {}).get("schemas", {}).get(ref.rsplit("/", 1)[1], {})


def _type_name(spec: dict, schema: Any) -> str:
    """A type as a caller would write it, not as OpenAPI spells it."""
    if not isinstance(schema, dict):
        return "any"
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[1]
    # Pydantic renders Optional[x] as anyOf[x, null]. The null is carried by the
    # "required" column, so it is dropped from the type rather than printed.
    for key in ("anyOf", "oneOf"):
        if key in schema:
            parts = [s for s in schema[key] if s.get("type") != "null"]
            return " or ".join(_type_name(spec, s) for s in parts) or "null"
    kind = schema.get("type")
    if kind == "array":
        return f"{_type_name(spec, schema.get('items', {}))}[]"
    if kind == "object":
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict):
            return f"object of {_type_name(spec, extra)}"
        return "object"
    if kind == "integer":
        return "integer"
    if kind == "number":
        return "number"
    if kind == "boolean":
        return "boolean"
    if kind == "string":
        return "file" if schema.get("format") == "binary" else "string"
    return kind or "any"


def _enum_of(spec: dict, schema: Any) -> list[str]:
    if not isinstance(schema, dict):
        return []
    if schema.get("enum"):
        return [str(v) for v in schema["enum"]]
    for key in ("anyOf", "oneOf"):
        for part in schema.get(key, []):
            if isinstance(part, dict) and part.get("enum"):
                return [str(v) for v in part["enum"]]
    return []


def _fields(spec: dict, schema: Any, *, prefix: str = "", depth: int = 0) -> list[dict[str, Any]]:
    """One row per property, in declaration order.

    A field whose type is another model is followed one level down and its
    properties come back as dotted rows under it. Printing ``ColumnMappingIn``
    and stopping would name a type this page never defines, which leaves the
    reader with a body they still cannot write.

    An array at the top level is unwrapped to its item, with the caller told it
    is a list. That is what ``GET /v1/jobs`` returns, and the alternative is a
    response section with no fields in it at all.
    """
    schema = _deref(spec, schema)
    if not isinstance(schema, dict):
        return []
    if schema.get("type") == "array" and depth == 0:
        return _fields(spec, schema.get("items"), prefix=prefix, depth=depth)

    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    rows = []
    for name, prop in props.items():
        prop = prop if isinstance(prop, dict) else {}
        default = prop.get("default")
        rows.append(
            {
                "name": prefix + name,
                "type": _type_name(spec, prop),
                "required": name in required,
                "description": prop.get("description") or "",
                "enum": _enum_of(spec, prop),
                # False and 0 are real defaults; only "absent" is no default.
                "default": "" if default is None else json.dumps(default),
                "depth": depth,
            }
        )
        nested = prop.get("$ref") and _deref(spec, prop)
        if nested and depth < 1 and isinstance(nested, dict) and nested.get("properties"):
            rows.extend(_fields(spec, nested, prefix=f"{prefix}{name}.", depth=depth + 1))
    return rows


def _example(spec: dict, schema: Any, *, depth: int = 0) -> Any:
    """A value this schema would accept, built from the schema itself.

    Preference order is the field's own ``examples``, then its default, then a
    placeholder for the type. That is what keeps the sample request on the page
    honest: it is assembled from the same document the endpoint validates
    against, so it cannot drift into showing a field that no longer exists.
    """
    schema = _deref(spec, schema)
    if not isinstance(schema, dict) or depth > 4:
        return None
    if schema.get("examples"):
        return _in_field_order(schema, schema["examples"][0])
    if "default" in schema and schema["default"] is not None:
        return schema["default"]
    if schema.get("enum"):
        return schema["enum"][0]
    for key in ("anyOf", "oneOf"):
        if key in schema:
            parts = [s for s in schema[key] if s.get("type") != "null"]
            return _example(spec, parts[0], depth=depth + 1) if parts else None

    kind = schema.get("type")
    if kind == "object" or schema.get("properties"):
        out = {}
        for name, prop in (schema.get("properties") or {}).items():
            out[name] = _example(spec, prop, depth=depth + 1)
        return out
    if kind == "array":
        item = _example(spec, schema.get("items", {}), depth=depth + 1)
        return [item] if item is not None else []
    if kind == "integer":
        return 0
    if kind == "number":
        return 0
    if kind == "boolean":
        return False
    if kind == "string":
        return "string"
    return None


def _in_field_order(schema: dict, value: Any) -> Any:
    """An authored example, re-keyed into the order the model declares.

    Pydantic sorts the keys of ``json_schema_extra`` on its way into the
    document, which would print a response with ``checks`` above ``email``. The
    model's own property order is the order a reader wants, and it is right
    there in the same schema.
    """
    props = schema.get("properties")
    if not isinstance(value, dict) or not isinstance(props, dict):
        return value
    known = [k for k in props if k in value]
    return {**{k: value[k] for k in known}, **{k: v for k, v in value.items() if k not in props}}


def _request_example(spec: dict, schema: Any) -> Any:
    """A body the endpoint would accept, and nothing more.

    An authored example wins. Failing that the body is built from the *required*
    fields only: an optional field filled with a type placeholder is not a
    neutral illustration, it is an instruction. `"check_dns": false` on the
    verify sample would tell a reader to turn DNS off.
    """
    resolved = _deref(spec, schema)
    if isinstance(resolved, dict) and resolved.get("examples"):
        return _in_field_order(resolved, resolved["examples"][0])
    if not isinstance(resolved, dict) or not resolved.get("properties"):
        return _example(spec, schema)
    required = set(resolved.get("required") or [])
    body = {}
    for name, prop in resolved["properties"].items():
        if name in required or (isinstance(prop, dict) and prop.get("examples")):
            body[name] = _example(spec, prop, depth=1)
    return body or _example(spec, schema)


def _body_schema(spec: dict, op: dict) -> tuple[Optional[dict], str]:
    """The request body's schema and its media type, if the operation takes one."""
    content = (op.get("requestBody") or {}).get("content") or {}
    for media in ("application/json", "multipart/form-data", "application/x-www-form-urlencoded"):
        if media in content:
            return content[media].get("schema"), media
    for media, node in content.items():
        return node.get("schema"), media
    return None, ""


def _ok_response(spec: dict, op: dict) -> tuple[str, str, Optional[dict]]:
    """The success response: its code, its description and its schema."""
    responses = op.get("responses") or {}
    for code in sorted(responses):
        if code.startswith("2"):
            node = responses[code]
            content = node.get("content") or {}
            schema = None
            for media in ("application/json",):
                if media in content:
                    schema = content[media].get("schema")
            return code, node.get("description") or "", schema
    return "200", "", None


def _title(op: dict, method: str, path: str) -> str:
    """A human title for an operation.

    FastAPI derives ``summary`` from the function name, so "Create Job" is
    already there. It is title-cased word by word, which reads as a label rather
    than as a sentence, so only the first word keeps its capital.
    """
    summary = (op.get("summary") or "").strip()
    if not summary:
        return f"{method.upper()} {path}"
    words = summary.split()
    return " ".join([words[0]] + [w if w.isupper() else w.lower() for w in words[1:]])


def _anchor(method: str, path: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")
    return f"{method}-{slug}"


def _curl(base: str, method: str, path: str, body: Any, media: str) -> Markup:
    """The call as a caller would make it, coloured as it is assembled.

    Built rather than pattern-matched: a regex over a shell line has no way to
    tell the port in a base URL from a number in a JSON body, and it painted
    ``127.0.0.1:8899`` as three numeric literals. Here every token is coloured
    by what it is known to be.
    """
    e = html.escape
    dim = '<span class="c-dim">%s</span>'
    lines = [f"curl {dim % ('-X ' + method.upper())} {e(base + path)}"]
    lines.append(
        f'  {dim % "-H"} <span class="c-str">"X-API-Key: '
        f'<span class="c-num">$VALIDROW_KEY</span>"</span>'
    )
    if media == "multipart/form-data":
        lines.append(f'  {dim % "-F"} <span class="c-str">"file=@list.csv"</span>')
    elif body:
        # The JSON carries its own newlines, so its continuation lines are
        # indented to sit under the -d rather than against the left margin.
        rendered = json.dumps(body, indent=2).replace("\n", "\n  ")
        lines.append(f'  {dim % "-H"} <span class="c-str">"Content-Type: application/json"</span>')
        lines.append(f"  {dim % '-d'} '{_highlight_json(rendered)}'")
    return Markup(" \\\n".join(lines))


# --- The view-model -------------------------------------------------------- #
def _operations(spec: dict, base: str) -> list[dict[str, Any]]:
    ops = []
    for path, item in (spec.get("paths") or {}).items():
        for method in _METHODS:
            op = item.get(method)
            if not op or op.get("deprecated"):
                continue
            body_schema, media = _body_schema(spec, op)
            code, ok_desc, ok_schema = _ok_response(spec, op)
            body_example = _request_example(spec, body_schema) if body_schema else None
            ok_example = _example(spec, ok_schema) if ok_schema else None

            params = []
            for p in (op.get("parameters") or []) + (item.get("parameters") or []):
                schema = p.get("schema") or {}
                default = schema.get("default")
                params.append(
                    {
                        "name": p.get("name", ""),
                        "location": p.get("in", ""),
                        "type": _type_name(spec, schema),
                        "required": bool(p.get("required")),
                        "description": p.get("description") or "",
                        "enum": _enum_of(spec, schema),
                        "default": "" if default is None else json.dumps(default),
                    }
                )

            label, ink, wash = METHOD_TONE.get(method, (method.upper(), F.INK_3, F.SURFACE))
            ops.append(
                {
                    "id": _anchor(method, path),
                    "method": method,
                    "methodLabel": label,
                    "methodInk": ink,
                    "methodWash": wash,
                    "path": path,
                    # The contents rail is 232px wide and every path starts
                    # "/v1", so carrying the prefix there costs four characters
                    # of the part that tells two routes apart.
                    "short": path[4:] if path.startswith("/v1/") else path.lstrip("/"),
                    "tag": (op.get("tags") or ["meta"])[0],
                    "title": _title(op, method, path),
                    # FastAPI takes this from the handler's docstring. Its first
                    # line is the summary line, which is already the title above.
                    "description": (op.get("description") or "").strip(),
                    "params": params,
                    "bodyFields": _fields(spec, body_schema) if body_schema else [],
                    "bodyMedia": media,
                    "okCode": code,
                    "okDescription": ok_desc,
                    "okFields": _fields(spec, ok_schema) if ok_schema else [],
                    "okIsList": bool(
                        isinstance(_deref(spec, ok_schema), dict)
                        and _deref(spec, ok_schema).get("type") == "array"
                    ),
                    "okIsStream": ok_schema is None,
                    "curl": _curl(base, method, path, body_example, media),
                    "responseExample": (
                        _highlight_json(json.dumps(ok_example, indent=2))
                        if ok_example is not None
                        else ""
                    ),
                }
            )
    return ops


def _groups(ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = list(dict.fromkeys(o["tag"] for o in ops))
    order = [t for t in TAG_ORDER if t in seen] + [t for t in seen if t not in TAG_ORDER]
    groups = []
    for tag in order:
        title, blurb = TAG_COPY.get(tag, (tag.title(), ""))
        groups.append(
            {
                "tag": tag,
                "id": f"tag-{tag}",
                "title": title,
                "blurb": blurb,
                "ops": [o for o in ops if o["tag"] == tag],
            }
        )
    return groups


def _essentials(base: str) -> list[dict[str, str]]:
    """The four things a caller needs before the first request.

    The two numbers here are settings, not copy: an engine with a different
    upload cap or a different rate limit documents its own.
    """
    s = get_settings()
    limit = (
        f"{s.rate_limit_per_minute} requests a minute per key on the endpoints that "
        "cost a network call: verify, upload and job creation. A request over the "
        "limit is refused with 429 rather than queued."
        if s.rate_limit_per_minute
        else "This engine has no rate limit configured, so nothing here is throttled. "
        "A public deployment sets one, and then the limit is per key rather than "
        "per IP, because a fleet of workers behind one address is one caller."
    )
    return [
        {
            "term": "Base URL",
            "detail": f"Every path below hangs off {base}. There is no other host: "
            "the engine that answers the API is the engine that serves this page.",
        },
        {
            "term": "Authentication",
            "detail": "Send your key as X-API-Key, or as Authorization: Bearer for "
            "clients that make that easier. Create one in Settings; it is shown "
            "once and stored hashed, so a lost key is replaced rather than "
            "recovered.",
        },
        {"term": "Rate limits", "detail": limit},
        {
            "term": "Upload size",
            "detail": f"A CSV up to {s.max_upload_bytes // (1024 * 1024)} MB. The cap "
            "is enforced while the body is being read, not from the "
            "Content-Length header, so a chunked upload cannot talk its way past it.",
        },
    ]


def _errors() -> list[dict[str, str]]:
    """The statuses this API actually returns, and what each one means here."""
    return [
        ("400", "The request was malformed."),
        ("401", "No credential, or one this engine does not recognise. Only "
                "returned when the engine requires auth."),
        ("404", "No such file, job or address in this workspace."),
        ("409", "The job exists but the segment you asked for is not written yet."),
        ("413", "The upload is over this engine's size cap."),
        ("422", "The body parsed but failed validation. The response names the "
                "field and what was wrong with it."),
        ("429", "Over the rate limit."),
    ]


def context(spec: dict, base: str) -> dict[str, Any]:
    """Everything ``docs.html`` renders."""
    from eve.web.landing import PALETTE

    ops = _operations(spec, base)
    info = spec.get("info") or {}
    return {
        "palette": PALETTE,
        "version": info.get("version", ""),
        "engineUrl": base,
        "groups": _groups(ops),
        "opCount": len(ops),
        "essentials": _essentials(base),
        "errors": [{"code": c, "detail": d} for c, d in _errors()],
        "verdicts": [
            {**F.VERDICT_STYLE[k], "key": k} for k in F.ORDER
        ],
    }
