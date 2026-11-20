"""The user's browser, through spur-browser's tools (Agent Quick Browse), called as another spur's tools.

A spur declares them in its reaches (`reaches={"calls": ["browser_browser_*"]}`) and makes one `Browser(app)`.
`op(args)` takes the ops a Switchboard macOS appendage's `browser` action took, so code written for that
runs as it was: navigate {url}, js {code}, text, look, click {element | match}, type {text, element?,
submit?}, key {keys}, scroll {direction?}, back, reload, wait {text, gone?, timeout?} | {seconds},
upload {element, paths}, value {element}, tabs, attach {tab}, release, screenshot, steps [ops].
`show`, `minimize` and `hold` change nothing here (the tab stays in the background). What only a
person may do (sign_in, fill_secret, autofill, save_account) isn't offered: it raises BrowserError.
"""
import json
import time


class BrowserError(Exception):
    pass


UNOFFERED = {"sign_in", "fill_secret", "autofill", "save_account", "accounts", "dialog", "network", "console",
             "drag", "set", "find"}
QUIET = {"show", "minimize", "hold"}

CLICK_BY_TEXT = """(() => {
  const want = %s, nth = %d;
  const re = want.startsWith('/') && want.lastIndexOf('/') > 0
    ? new RegExp(want.slice(1, want.lastIndexOf('/')), want.slice(want.lastIndexOf('/') + 1)) : null;
  const hit = (t) => re ? re.test(t) : t.toLowerCase().includes(want.toLowerCase());
  const all = [...document.querySelectorAll('a,button,[role=button],[role=link],[role=tab],[role=menuitem],input[type=submit],input[type=button],label,summary,[onclick]')]
    .filter((e) => e.offsetParent !== null && hit((e.innerText || e.value || e.getAttribute('aria-label') || '').trim()));
  const el = all[nth];
  if (!el) return {clicked: false, found: all.length};
  el.scrollIntoView({block: 'center'}); el.click();
  return {clicked: true, text: (el.innerText || el.value || '').trim().slice(0, 120), url: location.href};
})()"""

FOUND = """(() => { const t = document.body ? document.body.innerText : ''; const w = %s;
  return w.startsWith('/') && w.lastIndexOf('/') > 0
    ? new RegExp(w.slice(1, w.lastIndexOf('/')), w.slice(w.lastIndexOf('/') + 1)).test(t) : t.includes(w); })()"""


class Browser:
    def __init__(self, app, session=None):
        """`app` is the spur's sbmcp.Server; `session` names its tab (default: the spur's name)."""
        self.app = app
        self.session = session or app.name

    def tool(self, name, timeout=120, **arguments):
        """One spur-browser tool's answer: its structured content, else its text (parsed when it's JSON)."""
        arguments.setdefault("session", self.session)
        try:
            # As the agent sees it: spur-browser's own tool, under its spur's name.
            result = self.app.call(f"browser_{name}", {k: v for k, v in arguments.items() if v is not None}, timeout=timeout)
        except RuntimeError as e:
            raise BrowserError(f"{name}: {e}") from None
        words = "\n".join(b.get("text", "") for b in result.get("content", []) if b.get("type") == "text")
        if result.get("isError"):
            raise BrowserError(f"{name}: {words[:300] or 'it failed'}")
        if result.get("structuredContent") is not None:
            return result["structuredContent"]
        try:
            return json.loads(words)
        except ValueError:
            return words

    def js(self, code, timeout=60):
        return self.tool("browser_js", timeout=timeout + 15, code=code)

    def op(self, args, timeout=60):
        """One op as the macOS appendage's browser action took it; answers its output."""
        args = dict(args)
        op = args.pop("op", None)
        if args.get("session"):
            self.session = args.pop("session")
        if op in UNOFFERED:
            raise BrowserError(f"{op} isn't offered: it's for the user to do in their browser")
        if op in QUIET:
            return {}
        if op == "steps":
            out = None
            for step in args.get("steps") or []:
                out = self.op(step, timeout)
            return out
        if op == "navigate":
            self.tool("browser_navigate", timeout=timeout, url=args["url"])
            return self.js("({url: location.href, title: document.title})")
        if op == "js":
            return self.js(args["code"], timeout=float(args.get("timeout") or timeout))
        if op == "text":
            return self.tool("browser_page_text", timeout=timeout)
        if op in ("look", "screenshot"):
            return self.tool("browser_look", timeout=timeout)
        if op == "click":
            if args.get("element") is not None:
                return self.tool("browser_click", timeout=timeout, element=int(args["element"]))
            if args.get("match"):
                got = self.js(CLICK_BY_TEXT % (json.dumps(args["match"]), int(args.get("index") or 0)))
                if not (isinstance(got, dict) and got.get("clicked")):
                    raise BrowserError(f"click: nothing matches {args['match']!r}")
                return got
            raise BrowserError("click: an element or a match")
        if op == "type":
            if args.get("element") is None:
                raise BrowserError("type: an element (from look)")
            return self.tool("browser_type", timeout=timeout, element=int(args["element"]), text=args.get("text", ""),
                             submit=bool(args.get("submit")))
        if op == "key":
            out = None
            for key in str(args.get("keys") or args.get("key") or "").split():
                out = self.tool("browser_key", timeout=timeout, key=key)
            return out
        if op == "scroll":
            return self.tool("browser_scroll", timeout=timeout, direction=args.get("direction") or "down")
        if op == "back":
            return self.tool("browser_back", timeout=timeout)
        if op == "reload":
            self.js("(location.reload(), true)")
            time.sleep(2)
            return {}
        if op == "wait":
            if args.get("seconds") and not args.get("text"):
                time.sleep(float(args["seconds"]))
                return {}
            want, gone = args.get("text") or "", bool(args.get("gone"))
            end = time.monotonic() + float(args.get("timeout") or 10)
            while True:
                found = bool(self.js(FOUND % json.dumps(want)))
                if found != gone:
                    return {"found": found}
                if time.monotonic() > end:
                    raise BrowserError(f"wait: {want!r} {'still there' if gone else 'not found'}")
                time.sleep(0.5)
        if op == "upload":
            return self.tool("browser_upload", timeout=timeout, element=int(args["element"]), paths=list(args["paths"]))
        if op == "value":
            return self.tool("browser_value", timeout=timeout, element=int(args["element"]))
        if op == "tabs":
            return self.tool("browser_tabs", timeout=timeout)
        if op == "attach":
            return self.tool("browser_attach", timeout=timeout, tab_id=int(args["tab"]))
        if op == "release":
            return self.tool("browser_release", timeout=timeout, keep_open=bool(args.get("keep")))
        raise BrowserError(f"{op}: no such op")
