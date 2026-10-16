"""Tiny explicit request-schema validation (no extra dependency).

    data = parse(request, {"email": Str(max=255, required=True), "remember": Bool()})
Unknown fields are ignored; wrong types, lengths or choices give a 400 with a clear message.
"""
import re

from flask import request as _request

from .errors import ApiError


class Field:
    def __init__(self, required=False, default=None, label=None):
        self.required, self.default, self.label = required, default, label

    def check(self, name, value):
        return value


class Str(Field):
    def __init__(self, min=0, max=1000, choices=None, pattern=None, strip=True, **kw):
        super().__init__(**kw)
        self.min, self.max, self.choices, self.strip = min, max, choices, strip
        self.pattern = re.compile(pattern) if pattern else None

    def check(self, name, value):
        if not isinstance(value, str):
            raise ApiError(400, f"{self.label or name} must be text.", "invalid_field")
        v = value.strip() if self.strip else value
        v = v.replace("\x00", "")
        if len(v) < self.min:
            raise ApiError(400, f"{self.label or name} is required." if self.min <= 1 else
                           f"{self.label or name} must be at least {self.min} characters.", "invalid_field")
        if len(v) > self.max:
            raise ApiError(400, f"{self.label or name} is too long (max {self.max} characters).", "invalid_field")
        if self.choices is not None and v not in self.choices:
            raise ApiError(400, f"{self.label or name} has an invalid value.", "invalid_field")
        if self.pattern and v and not self.pattern.fullmatch(v):
            raise ApiError(400, f"{self.label or name} has an invalid format.", "invalid_field")
        return v


class Bool(Field):
    def check(self, name, value):
        if not isinstance(value, bool):
            raise ApiError(400, f"{self.label or name} must be true or false.", "invalid_field")
        return value


class Int(Field):
    def __init__(self, min=None, max=None, choices=None, **kw):
        super().__init__(**kw)
        self.min, self.max, self.choices = min, max, choices

    def check(self, name, value):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ApiError(400, f"{self.label or name} must be a whole number.", "invalid_field")
        if (self.min is not None and value < self.min) or (self.max is not None and value > self.max):
            raise ApiError(400, f"{self.label or name} is out of range.", "invalid_field")
        if self.choices is not None and value not in self.choices:
            raise ApiError(400, f"{self.label or name} has an invalid value.", "invalid_field")
        return value


class StrList(Field):
    def __init__(self, max_items=50, item_max=100, **kw):
        super().__init__(**kw)
        self.max_items, self.item_max = max_items, item_max

    def check(self, name, value):
        if not isinstance(value, list) or len(value) > self.max_items:
            raise ApiError(400, f"{self.label or name} must be a list of up to {self.max_items} items.", "invalid_field")
        out = []
        for v in value:
            if not isinstance(v, str) or len(v) > self.item_max:
                raise ApiError(400, f"{self.label or name} contains an invalid item.", "invalid_field")
            out.append(v)
        return out


def parse(src, fields):
    """Validate a JSON object (or the current request body when src is a Flask request)."""
    data = src.get_json(silent=True) if src is _request or hasattr(src, "get_json") else src
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ApiError(400, "The request body must be a JSON object.", "bad_request")
    out = {}
    for name, f in fields.items():
        if name not in data or data[name] is None:
            if f.required:
                raise ApiError(400, f"{f.label or name} is required.", "missing_field")
            if f.default is not None:
                out[name] = f.default
            continue
        out[name] = f.check(name, data[name])
    return out


def page_args(default_limit=25, max_limit=100):
    """?page=&limit= query parameters with bounds."""
    try:
        limit = max(1, min(max_limit, int(_request.args.get("limit", default_limit))))
        page = max(1, int(_request.args.get("page", 1)))
    except ValueError:
        raise ApiError(400, "page and limit must be numbers.", "bad_request")
    return page, limit, (page - 1) * limit


def query_str(name, max_len=200):
    v = (_request.args.get(name) or "").strip()
    if len(v) > max_len:
        raise ApiError(400, f"{name} is too long.", "bad_request")
    return v
