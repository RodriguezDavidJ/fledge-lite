"""Expression filter: add a datapoint computed from the others.

Python expression syntax, limited to arithmetic, comparisons, `a if c else b`,
datapoint names and the functions in the math module (plus abs/min/max/round).
Example: name=power_kw, expression=flow * pressure / 36

Expressions arrive in shareable links, so they are checked against that
whitelist instead of being handed to eval() as-is.
"""
import ast
import math

_DEFAULT_CONFIG = {
    "plugin": {"description": "Add a computed datapoint (Python expression, in-browser)", "type": "string",
               "default": "expression", "readonly": "true"},
    "enable": {"description": "Enable filter", "type": "boolean", "default": "false", "displayName": "Enabled"},
    "name": {"description": "Name of the new datapoint", "type": "string", "default": "calculated",
             "displayName": "Datapoint name", "order": "1"},
    "expression": {"description": "Python expression using datapoint names and math functions", "type": "string",
                   "default": "0", "displayName": "Expression", "order": "2"},
}
_SAFE = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
_SAFE.update({"abs": abs, "min": min, "max": max, "round": round})


_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp, ast.Call,
          ast.Name, ast.Load, ast.Constant, ast.operator, ast.unaryop, ast.boolop, ast.cmpop)


def _compile(expr):
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _NODES):
            raise ValueError("not allowed in an expression: " + type(node).__name__)
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            raise ValueError("names may not start with _")
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in _SAFE):
            raise ValueError("only math functions may be called")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float, bool)):
            raise ValueError("only numeric constants are allowed")
    return compile(tree, "<expression>", "eval")


def plugin_info():
    return {"name": "expression", "version": "0.1.0", "mode": "none", "type": "filter",
            "interface": "1.0", "config": _DEFAULT_CONFIG}


def plugin_init(config, ingest_ref, callback):
    try:
        code = _compile(config["expression"]["value"])
    except (SyntaxError, ValueError):
        code = None  # invalid expression: readings pass through unchanged
    return {"config": config, "ref": ingest_ref, "cb": callback, "code": code}


def plugin_ingest(handle, data):
    c = handle["config"]
    if str(c["enable"]["value"]).lower() != "true" or handle["code"] is None:
        handle["cb"](handle["ref"], data)
        return
    out = []
    for r in data:
        rd = dict(r["readings"])
        try:
            rd[c["name"]["value"]] = eval(handle["code"], {"__builtins__": {}}, dict(_SAFE, **rd))
        except Exception:
            pass  # missing datapoint etc.: pass the reading through unchanged
        out.append(dict(r, readings=rd))
    handle["cb"](handle["ref"], out)


def plugin_reconfigure(handle, new_config):
    return plugin_init(new_config, handle["ref"], handle["cb"])


def plugin_shutdown(handle):
    pass
