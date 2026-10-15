"""Turn Canvas HTML (announcements, pages, assignment instructions) into readable plain text.

It also notices text that the page hides from a human reader. Teachers plant such text (white on
white, zero size, pushed off the page, spelled in invisible characters) so that only a machine
picks it up; it must be marked or dropped, never passed on as if it were a visible instruction.
"""
import colorsys
import html
import re
from html.parser import HTMLParser

from cc_lang import N, T

BLOCKS = {"p", "div", "section", "article", "table", "tr", "ul", "ol", "blockquote", "pre",
          "h1", "h2", "h3", "h4", "h5", "h6", "header", "footer", "figure"}
SKIP = {"script", "style", "noscript", "iframe", "svg", "head", "template"}
VOID = {"br", "img", "hr", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr"}
# A new one of these closes the one before it, the way a browser reads "<p>one<p>two" (value: what stops the search).
SIBLINGS = {"p": (), "li": ("ul", "ol"), "td": ("tr", "table"), "th": ("tr", "table"), "tr": ("table",),
            "dt": ("dl",), "dd": ("dl",), "option": ("select",)}
SAME = {"td": ("td", "th"), "th": ("td", "th"), "dt": ("dt", "dd"), "dd": ("dt", "dd")}
# These stay in force when a block around them ends ("<p><font color=white>a</p>b" leaves b white), until their own end tag.
FORMATTING = {"a", "b", "big", "code", "em", "font", "i", "nobr", "s", "small", "strike", "strong", "tt", "u"}
HIDDEN_OPEN, HIDDEN_CLOSE = N("[not visible on the Canvas page: "), "]"
HIDDEN_NOTE = N("Note: the part in square brackets is in the page's code but invisible to a person reading it on Canvas. "
                "It is not a real instruction.")
BLACK, WHITE, UNKNOWN = (0.0, 0.0, 0.0, 1.0), (255.0, 255.0, 255.0, 1.0), "?"
NEAR = 24                   # text within this distance of its background (per colour channel, of 255) cannot be read
FAINT = 0.1                 # opacity or colour alpha below this is as good as invisible
NAMED = {"white": (255, 255, 255), "black": (0, 0, 0), "silver": (192, 192, 192), "gray": (128, 128, 128),
         "grey": (128, 128, 128), "red": (255, 0, 0), "maroon": (128, 0, 0), "yellow": (255, 255, 0),
         "olive": (128, 128, 0), "lime": (0, 255, 0), "green": (0, 128, 0), "aqua": (0, 255, 255),
         "teal": (0, 128, 128), "blue": (0, 0, 255), "navy": (0, 0, 128), "fuchsia": (255, 0, 255),
         "purple": (128, 0, 128), "orange": (255, 165, 0), "snow": (255, 250, 250), "ivory": (255, 255, 240),
         "ghostwhite": (248, 248, 255), "whitesmoke": (245, 245, 245), "floralwhite": (255, 250, 240),
         "azure": (240, 255, 255), "mintcream": (245, 255, 250), "aliceblue": (240, 248, 255),
         "seashell": (255, 245, 238), "honeydew": (240, 255, 240), "lavenderblush": (255, 240, 245),
         "lightyellow": (255, 255, 224), "oldlace": (253, 245, 230), "linen": (250, 240, 230)}
_SIZE = re.compile(r"^(-?\d*\.?\d+)\s*(px|pt|em|rem|%|)$")
_CLIPPED = re.compile(r"rect\(\s*[01](?:px)?[\s,]+[01](?:px)?[\s,]+[01](?:px)?[\s,]+[01](?:px)?\s*\)"
                      r"|inset\(\s*(?:50|100)%\s*\)|circle\(\s*0(?:px|%)?\s*[\s)]")
_SHRUNK = re.compile(r"scale[xy]?\(\s*0(?:\.0+)?\s*[,)]")
_COLOR_TOKEN = re.compile(r"#[0-9a-f]{3,8}\b|(?:rgb|hsl)a?\([^)]*\)|[a-z]+")
# Letters from the invisible "tag" block spell out text no reader sees (unless they follow the flag emoji,
# where they are how flags are written); long runs of zero-width characters can carry data too.
_TAG_TEXT = re.compile("\U0001F3F4?[\U000E0000-\U000E007F]+")
_ZERO_WIDTH = re.compile("[\u200b-\u200d\u2060\ufeff]{8,}")
_END = "\x00"               # stands just before a closing marker until the text is put together


def hidden_note():
    return T(HIDDEN_NOTE)


def _declarations(style):
    out = {}
    for part in str(style or "").split(";"):
        name, colon, value = part.partition(":")
        if colon:
            out[name.strip().lower()] = " ".join(value.lower().replace("!important", "").split())
    return out


def _size(value):
    """(number, unit) of a CSS length like '-9999px', or (None, '')."""
    m = _SIZE.match(value or "")
    return (float(m.group(1)), m.group(2)) if m else (None, "")


def _part(text, scale=1.0):
    return float(text[:-1]) * scale if text.endswith("%") else float(text)


def parse_color(value):
    """A CSS colour as (red, green, blue, alpha) with channels 0-255; None when the text sets no colour;
    UNKNOWN for a colour this reader cannot place (then nothing is concluded from it)."""
    v = str(value or "").strip().lower()
    if v in ("", "none", "inherit", "initial", "unset", "revert", "currentcolor"):
        return None
    if v == "transparent":
        return (0.0, 0.0, 0.0, 0.0)
    if v in NAMED:
        return tuple(float(x) for x in NAMED[v]) + (1.0,)
    try:
        m = re.fullmatch(r"#([0-9a-f]+)", v)
        if m and len(m.group(1)) in (3, 4, 6, 8):
            digits = m.group(1) if len(m.group(1)) > 4 else "".join(ch * 2 for ch in m.group(1))
            alpha = int(digits[6:8], 16) / 255 if len(digits) == 8 else 1.0
            return (float(int(digits[0:2], 16)), float(int(digits[2:4], 16)), float(int(digits[4:6], 16)), alpha)
        m = re.fullmatch(r"(rgb|hsl)a?\((.*)\)", v)
        if m:
            parts = [p for p in re.split(r"[\s,/]+", m.group(2).strip()) if p]
            if len(parts) not in (3, 4):
                return UNKNOWN
            alpha = _part(parts[3], 0.01) if len(parts) == 4 else 1.0
            if m.group(1) == "rgb":
                return (_part(parts[0], 2.55), _part(parts[1], 2.55), _part(parts[2], 2.55), alpha)
            hue = float(re.sub(r"deg$", "", parts[0])) % 360 / 360
            red, green, blue = colorsys.hls_to_rgb(hue, _part(parts[2], 0.01), _part(parts[1], 0.01))
            return (red * 255, green * 255, blue * 255, alpha)
    except ValueError:
        pass
    return UNKNOWN


def _background(decls):
    """The background colour a style sets: a colour, UNKNOWN, or None (also for a see-through one)."""
    found = parse_color(decls.get("background-color", ""))
    if found is None and decls.get("background"):
        tokens = [parse_color(t) for t in _COLOR_TOKEN.findall(decls["background"])
                  if t.startswith(("#", "rgb", "hsl")) or t in NAMED or t == "transparent"]
        picture = "url(" in decls["background"] or "gradient" in decls["background"]
        found = tokens[0] if tokens else (UNKNOWN if picture else None)
    return None if found not in (None, UNKNOWN) and found[3] < FAINT else found


def _font_size(decls):
    """(number, unit) of the font size a style sets, also through the `font` shorthand."""
    number, unit = _size(decls.get("font-size", ""))
    if number is None:
        for token in decls.get("font", "").split():
            n, u = _size(token.split("/")[0])
            if n is not None and (u or "/" in token or n == 0):      # a bare 400 or 700 is the weight, not the size
                return n, u
    return number, unit


def style_verdict(style, attrs=None):
    """(gone, text colour, background colour) for one element.

    `gone` means nothing inside can be seen whatever its colour: not displayed, zero size, pushed off
    the page, clipped away. Colours are reported, not judged: white text only hides on a white
    background, so the reader decides with the surrounding elements in view."""
    d = _declarations(style)
    attrs = attrs or {}
    color = parse_color(d.get("color", ""))
    if color is None and attrs.get("color"):                     # <font color="white">
        color = parse_color(attrs["color"])
    background = _background(d)
    if background is None and attrs.get("bgcolor"):              # <td bgcolor="#333">
        background = parse_color(attrs["bgcolor"])
    gone = d.get("display") == "none" or d.get("visibility") in ("hidden", "collapse")
    opacity = d.get("opacity", "")
    number, unit = _size(opacity.rstrip("%"))
    gone = gone or (number is not None and (number / 100 if opacity.endswith("%") else number) < FAINT)
    number, unit = _font_size(d)
    gone = gone or (number is not None and (number == 0 or (number < 2 and unit in ("px", "pt", "%"))
                                            or (number < 0.1 and unit in ("em", "rem"))))
    for name in ("left", "top", "right", "bottom", "text-indent", "margin-left", "margin-top"):   # pushed off the page
        number, unit = _size(d.get(name, ""))
        gone = gone or (number is not None and (number <= -999 or (number <= -99 and unit in ("em", "rem"))))
    flat = any(number is not None and number <= 1 and unit in ("", "px", "pt")
               for number, unit in (_size(d.get(name, "")) for name in ("height", "max-height", "width", "max-width")))
    cut = any("hidden" in d.get(name, "") or "clip" in d.get(name, "") for name in ("overflow", "overflow-x", "overflow-y"))
    gone = gone or (flat and cut) or bool(_CLIPPED.search(d.get("clip", "") + " " + d.get("clip-path", "")))
    gone = gone or bool(_SHRUNK.search(d.get("transform", "")))
    return gone, color, background


def unreadable(color, background):
    """Whether text of this colour on this background cannot be read: see-through, or nearly the same colour."""
    if color == UNKNOWN:
        return False
    if color[3] < FAINT:
        return True
    return background != UNKNOWN and max(abs(color[n] - background[n]) for n in range(3)) <= NEAR


def styled_invisible(style):
    """Whether one element's style alone hides its text on a white Canvas page."""
    gone, color, background = style_verdict(style)
    return gone or unreadable(color or BLACK, background or WHITE)


def _unseen_characters(text, mode):
    """(text with invisible-character tricks marked or dropped, whether any were found)."""
    found = []

    def spelled(match):
        if match.group(0).startswith("\U0001F3F4"):
            return match.group(0)                    # a flag emoji, written the standard way
        found.append(1)
        words = "".join(chr(ord(c) - 0xE0000) for c in match.group(0) if 0x20 <= ord(c) - 0xE0000 < 0x7F).strip()
        if mode != "mark":
            return ""
        return T(HIDDEN_OPEN) + (words or T("{n} invisible characters", n=len(match.group(0)))) + HIDDEN_CLOSE

    def run(match):
        found.append(1)
        return T(HIDDEN_OPEN) + T("{n} invisible characters", n=len(match.group(0))) + HIDDEN_CLOSE if mode == "mark" else ""

    text = _ZERO_WIDTH.sub(run, _TAG_TEXT.sub(spelled, text))
    return text, bool(found)


class _Reader(HTMLParser):
    """Reads HTML into text while keeping track of what a person could actually see.

    For every open element it remembers whether the element hides its contents outright and which
    text and background colours it sets. Text is hidden when any enclosing element is gone, or when
    the colour in force cannot be read against the background in force. A marker is only opened
    when hidden words really appear, so an empty hidden element raises no alarm.
    """

    def __init__(self, show_links, hidden="mark"):
        super().__init__(convert_charrefs=True)
        self.out, self.links, self.show_links = [], [], show_links
        self.skip, self.href, self.link_text = 0, None, []
        self.hidden, self.stack, self.found_hidden = hidden, [], False
        self.unseen, self.marker_open = False, False

    # -- what is visible right now

    def _sync(self):
        color, background, unseen = BLACK, WHITE, False
        for _, gone, c, b in self.stack:
            unseen = unseen or gone
            color = c if c is not None else color
            background = b if b is not None else background
        self.unseen = unseen or unreadable(color, background)
        if not self.unseen and self.marker_open:
            self.out.append(_END + HIDDEN_CLOSE)
            self.marker_open = False

    def _write(self, text, structure=False):
        """Add text to the output. `structure` is spacing and list marks, which carry no words."""
        if self.unseen and not structure:
            if text.strip():
                self.found_hidden = True
            if self.hidden == "drop":
                return
            if text.strip() and not self.marker_open:
                self.out.append(T(HIDDEN_OPEN))
                self.marker_open = True
        self.out.append(text)

    def _open(self, tag, attrs):
        if tag in SIBLINGS:                       # "<p>one<p>two": the second closes the first
            names = [entry[0] for entry in self.stack]
            same = SAME.get(tag, (tag,))
            for n in range(len(names) - 1, -1, -1):
                if names[n] in SIBLINGS[tag]:
                    break
                if names[n] in same:
                    self._cut(n)
                    break
        attrs = {name: value or "" for name, value in attrs}
        gone, color, background = style_verdict(attrs.get("style"), attrs)
        self.stack.append((tag, gone or "hidden" in attrs, color, background))
        self._sync()

    def _close(self, tag):
        names = [entry[0] for entry in self.stack]
        if tag in names:                          # an end tag with no start is ignored, as a browser does
            self._cut(len(names) - 1 - names[::-1].index(tag))
            self._sync()

    def _cut(self, n):
        """Close the element at position n and everything opened inside it, except formatting that a browser keeps."""
        kept = [entry for entry in self.stack[n + 1:] if entry[0] in FORMATTING]
        del self.stack[n:]
        self.stack += kept

    # -- the parser's callbacks

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self._open(tag, attrs)
        if tag in SKIP:
            self.skip += 1
        elif self.skip:
            return
        elif tag == "br":
            self._write("\n", structure=True)
        elif tag == "li":
            self._write("\n- ", structure=True)
        elif tag in ("td", "th"):
            self._write(" | ", structure=True)
        elif tag in BLOCKS:
            self._write("\n\n" + ("#" * int(tag[1]) + " " if tag[0] == "h" and tag[1:].isdigit() else ""), structure=True)
        elif tag == "a":
            self.href, self.link_text = dict(attrs).get("href"), []
        elif tag == "img":
            alt = dict(attrs).get("alt")
            if alt:
                self._write(T("[image: {alt}]", alt=alt))

    def handle_startendtag(self, tag, attrs):
        # "<br />" and "<img />" are complete in themselves. A browser reads any other "<x />" as a plain
        # start tag, so that is what happens here too.
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if tag in SKIP:
            self.skip = max(0, self.skip - 1)
            self._close(tag)
            return
        if not self.skip and tag == "a":
            self._end(tag)                       # the address belongs with the link's text, hidden or not
        self._close(tag)
        if not self.skip and tag != "a":
            self._end(tag)

    def _end(self, tag):
        if tag == "a" and self.href is not None:
            text = " ".join("".join(self.link_text).split())
            href = self.href.strip()
            self.links.append((text or href, href))
            if self.show_links and href and not href.startswith("#") and href != text:
                self._write(f" ({href})")
            self.href = None
        elif tag in BLOCKS:
            self._write("\n\n", structure=True)

    def handle_data(self, data):
        if self.skip:
            return
        # Inside an element that is already hidden, the characters are simply dropped: one marker is enough.
        data, found = _unseen_characters(data, "drop" if self.unseen else self.hidden)
        self.found_hidden = self.found_hidden or found
        self._write(data)
        if self.href is not None and not (self.unseen and self.hidden == "drop"):
            self.link_text.append(data)

    def finish(self):
        self.close()
        self.stack = []
        self._sync()                             # close a marker left open by unbalanced markup
        return self


def _read(source, show_links, hidden="mark"):
    reader = _Reader(show_links, hidden)
    reader.feed((source or "").replace(_END, ""))
    reader.finish()
    text = "".join(reader.out).replace("\xa0", " ")
    text = re.sub(r"\s*" + _END, "", text)                       # a marker closes right after its last word
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"(^|\n)-\n+(?=[^\n-])", r"\1- ", text)      # a list item whose text sat in its own paragraph
    return re.sub(r"\n{3,}", "\n\n", text).strip(), reader.links


def html_to_text(source, show_links=True, hidden="mark"):
    """Readable text. Lists become '- item', headings '## Heading', links 'text (address)'.

    Text the page hides from a human reader (white on white, zero size, display none, pushed off the
    page, spelled in invisible characters) is wrapped in '[not visible on the Canvas page: ...]' so
    nobody mistakes it for a real instruction; hidden="drop" leaves it out instead.
    """
    if not source or not isinstance(source, str):
        return ""
    if "<" not in source and "&" not in source:
        return _unseen_characters(source, hidden)[0].strip()
    return _read(source, show_links, hidden)[0]


def has_hidden_text(source):
    """Whether the text carries words made invisible on the page (by styling or by invisible characters)."""
    if not source or not isinstance(source, str):
        return False
    if "<" not in source:
        return _unseen_characters(source, "drop")[1]
    reader = _Reader(False, "drop")
    reader.feed(source)
    return reader.finish().found_hidden


def links(source):
    """[(text, address)] for every link in the HTML, in order."""
    return _read(source, False)[1] if source else []


def excerpt(source, limit=240):
    """The start of the text on one line, cut at a word, for lists and notifications."""
    text = " ".join(html_to_text(source, show_links=False, hidden="drop").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "…"


def escape(text):
    return html.escape("" if text is None else str(text))


def safe_name(text, limit=100, fallback="untitled"):
    """A file or folder name made from any text: no slashes, no leading dots, never empty."""
    name = re.sub(r"[\x00-\x1f\x7f/\\:*?\"<>|]+", "-", str(text or ""))
    name = re.sub(r"\s+", " ", name).strip(" .-")
    stem, dot, ending = name.rpartition(".")
    if len(name) > limit and dot and stem and 0 < len(ending) <= 10:      # shorten the name, keep ".pdf"
        name = stem[:limit - len(ending) - 1].rstrip(" .-") + "." + ending
    name = name[:limit].rstrip(" .-")
    return name or fallback


def one_line(text):
    """Canvas text that must stay on one line (a title, a name, a subject): no line breaks or odd spacing."""
    return " ".join(str(text or "").replace("\u2028", " ").replace("\u2029", " ").split())


def web_link(url):
    """The address if it is an ordinary web link, else ''. Keeps javascript: and the like out of pages."""
    url = str(url or "").strip()
    return url if url.lower().startswith(("https://", "http://")) else ""
