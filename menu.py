import os, sys, subprocess
from termios import ECHO, ICANON, TCSAFLUSH, tcgetattr as tget, tcsetattr as tset

# todo: type annotate this at some point idk

try:
    fd = sys.stdin.fileno()
except:
    pass
w = sys.stdout.write


# "#rrggbb" -> r, g, b
def hex2rgb(hex):
    return int(hex[1:3], 16), int(hex[2:4], 16), int(hex[5:7], 16)


class Term:
    class Code(str):
        def __call__(self, n=1):
            w(self * n)

    def dims(self):
        sz = os.get_terminal_size()
        return sz.columns, sz.lines

    def flush(self):
        sys.stdout.flush()

    def explicit(self, code):
        w(code)

    def fg(self, hex):
        r, g, b = hex2rgb(hex)
        w(f"\033[38;2;{r};{g};{b}m")

    def bg(self, hex):
        r, g, b = hex2rgb(hex)
        w(f"\033[48;2;{r};{g};{b}m")

    def rst(self):
        w("\033[0m")

    # honestly i still dont know whats going on here
    def __getattr__(self, capname):
        setattr(
            self,
            capname,
            Term.Code(
                subprocess.check_output(["tput", capname], universal_newlines=True)
            ),
        )
        return getattr(self, capname)


t = Term()


def clamp(x, constraint, inclusive=False):
    l, r = constraint
    assert l is None or r is None or inclusive and l <= r or not inclusive and l < r

    if l is not None and x < l:
        return l

    if r is not None and (inclusive and x > r or not inclusive and x >= r):
        return r if inclusive else r - 1

    return x


class View:
    def __init__(self, n, constraint=(None, None)):
        self.n = n
        self._constraint = constraint

        l, r = self._constraint
        assert l is None or r is None or self.n <= r - l

        self.s = 0
        self.e = self.n

    def anchor(self, anchor):
        if anchor < self.s:
            self.anchor_s(anchor)

        elif anchor >= self.e:
            self.anchor_e(anchor)

    def anchor_s(self, anchor):
        self.s = clamp(anchor, self._constraint)
        self.e = self.s + self.n

    def anchor_e(self, anchor):
        self.e = clamp(anchor, self._constraint) + 1
        self.s = self.e - self.n

    def resize(self, n, anchor=None):
        self.n = n
        self.e = self.s + self.n
        if anchor is not None and anchor >= self.e:
            self.e = anchor + 1
            self.s = self.e - self.n


def limit(s, max_l, ellipsis=True, extend=False):
    assert max_l >= 0

    ret = s

    if ellipsis:
        if max_l <= 3:
            ret = s[:max_l]

        elif max_l <= 10:
            ret = f"{ret[:max_l - 2]}.." if len(ret) > max_l else ret

        else:
            ret = f"{ret[:max_l - 3]}..." if len(ret) > max_l else ret

    else:
        ret = ret[:max_l]

    if extend:
        ret += " " * (max_l - len(ret))

    return ret


def argmax(xs, f):
    return max(map(lambda x: (f(x), x), xs))[1]


class Columns:
    class Column:
        def __init__(self, default_width=None, min_width=3, ellipsis=True):
            assert default_width is None or default_width >= min_width
            self.default_width = default_width
            self.min_width = min_width
            self.ellipsis = ellipsis

        def format(self, s, width):
            return limit(s, width, ellipsis=self.ellipsis, extend=True)

    def __init__(self, fill=True):
        self._cols = []
        self._fill = fill

    # surely there is a better way...
    def column(self, default_width=None, min_width=3, ellipsis=True):
        self._cols.append(
            Columns.Column(
                default_width=default_width, min_width=min_width, ellipsis=ellipsis
            )
        )

    def get_widths(self, rows, width):
        total_width = 0
        widths = []
        for idx, col in enumerate(self._cols):
            col_width = col.default_width
            # no default_width means adaptive
            if col_width is None:
                col_width = col.min_width
                for row in rows:
                    col_width = max(col_width, len(row[idx]))
            widths.append(col_width)
            total_width += col_width

        # very inefficient... but dont really care? unless this becomes a real issue
        while total_width > width:
            # sort columns by
            #   1. column is not at min width?
            #   2. allocated column width
            #
            # decrement width of the the "largest" column by 1
            # this will make sure columns at min width will not be narrowed
            # before all other columns are at min width
            sacrificial_idx = argmax(
                list(range(len(self._cols))),
                lambda idx: (widths[idx] >= self._cols[idx].min_width, widths[idx]),
            )
            widths[sacrificial_idx] -= 1
            total_width -= 1

        if self._fill:
            widths[-1] += width - total_width

        return widths

    def format_rows(self, rows, width):
        widths = self.get_widths(rows, width)
        output_rows = []
        for row in rows:
            assert len(row) == len(self._cols)
            output_rows.append(
                list(
                    col.format(component, width)
                    for component, col, width in zip(row, self._cols, widths)
                )
            )
        return output_rows

    def format(self, rows, width):
        return "\n".join(
            list(map(lambda row: "".join(row), self.format_rows(rows, width)))
        )


# todo: event-driven? or smth
def key():
    return os.read(fd, 80).decode("ascii")


class Menu:
    SINGLE = object()
    MULTI = object()
    ORDERED = object()

    def __init__(self, entries, mode=SINGLE, prompt=None, use_descriptions=False):
        self._entries = list(entries)
        if use_descriptions:
            assert isinstance(
                entries, dict
            ), "entries must be a dictionary when using descriptions"
            self._displayed_entries = [entries[entry] for entry in self._entries]
        else:
            self._displayed_entries = list(self._entries)
        self._mode = mode
        self._prompt = prompt
        self._n = len(self._entries)
        self._constraint = (0, self._n)
        self._save = None

    def _setup(self):
        self._save = tget(fd)
        temp = tget(fd)
        temp[3] &= ~(ICANON | ECHO)
        tset(fd, TCSAFLUSH, temp)
        t.smkx()
        t.civis()

    def _teardown(self, n):
        assert self._save is not None
        t.dl1(n)
        t.cnorm()
        t.rmkx()
        tset(fd, TCSAFLUSH, self._save)

    def select(self):
        self._setup()
        cols = Columns()
        # arrow
        cols.column(default_width=2, min_width=2, ellipsis=False)
        # checkbox
        if self._mode is not Menu.SINGLE:
            if self._mode is Menu.ORDERED:
                max_idx_width = len(str(len(self._entries)))
                cols.column(
                    default_width=3 + max_idx_width,
                    min_width=3 + max_idx_width,
                    ellipsis=False,
                )
            else:
                cols.column(default_width=4, min_width=4, ellipsis=False)
        # item
        cols.column(min_width=10)
        # rpad
        cols.column(default_width=1, min_width=1, ellipsis=False)

        at = 0
        selection = []
        v_h = t.dims()[1] - 1
        if self._prompt is not None:
            v_h = max(v_h - 1, 0)
        v = View(min(self._n, v_h), self._constraint)

        try:
            while True:
                # very nice responsiveness :)
                # todo: just need to put this on a timer instead of blocked by read()
                t_w, t_h = t.dims()
                v_h = t_h - 1
                if self._prompt is not None:
                    v_h = max(v_h - 1, 0)
                v.resize(min(self._n, v_h), anchor=at)
                rows = []
                for idx, (entry, displayed_entry) in enumerate(
                    zip(self._entries, self._displayed_entries)
                ):
                    row = []
                    row.append("\u258c " if idx == at else "  ")
                    if self._mode is not Menu.SINGLE:
                        if self._mode is Menu.ORDERED:
                            max_idx_width = len(str(len(self._entries)))
                            row.append(
                                f"[{selection.index(entry) + 1:>{max_idx_width}}] "
                                if entry in selection
                                else f"[{' ' * max_idx_width}] "
                            )
                        else:
                            row.append("[x] " if entry in selection else "[ ] ")
                    row.append(displayed_entry)
                    row.append("")
                    rows.append(row)

                if self._prompt is not None:
                    w(f"{self._prompt}\n")

                formatted_rows = cols.format_rows(rows, t_w)
                for off, row in enumerate(formatted_rows[v.s : v.e]):
                    if off:
                        w("\n")

                    idx = v.s + off
                    if self._mode is Menu.SINGLE:
                        arrow, entry, rpad = row
                        checkbox = ""
                    else:
                        arrow, checkbox, entry, rpad = row

                    if idx == at:
                        t.bold()
                        t.fg("#7acb7f")
                        t.bg("#444444")
                        w(arrow)
                        t.rst()

                        if self._mode is not Menu.SINGLE:
                            if self._mode is Menu.ORDERED:
                                max_idx_width = len(str(len(self._entries)))
                                if len(checkbox) > 0:
                                    t.bold()
                                    t.bg("#444444")
                                    w(checkbox[0])
                                    t.rst()

                                if len(checkbox) > 1:
                                    t.bold()
                                    t.fg("#7acb7f")
                                    t.bg("#444444")
                                    w(checkbox[1 : 1 + max_idx_width])
                                    t.rst()

                                if len(checkbox) > 1 + max_idx_width:
                                    t.bold()
                                    t.bg("#444444")
                                    w(checkbox[1 + max_idx_width :])
                                    t.rst()

                            else:
                                if len(checkbox) > 0:
                                    t.bold()
                                    t.bg("#444444")
                                    w(checkbox[0])
                                    t.rst()

                                if len(checkbox) > 1:
                                    t.bold()
                                    t.fg("#7acb7f")
                                    t.bg("#444444")
                                    w(checkbox[1])
                                    t.rst()

                                t.bold()
                                t.bg("#444444")
                                w(checkbox[2:])
                                t.rst()

                        t.bold()
                        t.bg("#444444")
                        w(entry)
                        w(rpad)
                        t.rst()

                    else:
                        w(arrow)
                        if self._mode is not Menu.SINGLE:
                            if self._mode is Menu.ORDERED:
                                max_idx_width = len(str(len(self._entries)))
                                if len(checkbox) > 0:
                                    w(checkbox[0])

                                if len(checkbox) > 1:
                                    t.fg("#7acb7f")
                                    w(checkbox[1 : 1 + max_idx_width])
                                    t.rst()

                                if len(checkbox) > 1 + max_idx_width:
                                    w(checkbox[1 + max_idx_width :])

                            else:
                                if len(checkbox) > 0:
                                    w(checkbox[0])

                                if len(checkbox) > 1:
                                    t.fg("#7acb7f")
                                    w(checkbox[1])
                                    t.rst()

                                w(checkbox[2:])

                        w(entry)
                        w(rpad)

                w("\r")
                for _ in range(v.n - 1):
                    t.cuu1()

                if self._prompt is not None:
                    t.cuu1()

                sys.stdout.flush()

                for k in key():
                    match k:
                        case "j":
                            at = clamp(at + 1, self._constraint)
                            v.anchor(at)

                        case "k":
                            at = clamp(at - 1, self._constraint)
                            v.anchor(at)

                        case "G":
                            at = clamp(len(self._entries) - 1, self._constraint)

                        # c-d
                        case "\x04":
                            at = clamp(at + (v.n + 1) // 2, self._constraint)
                            v.anchor(at)

                        # c-u
                        case "\x15":
                            at = clamp(at - (v.n + 1) // 2, self._constraint)
                            v.anchor(at)

                        case " ":
                            if self._mode is not Menu.SINGLE:
                                if self._entries[at] in selection:
                                    selection = list(
                                        filter(
                                            lambda entry: entry != self._entries[at],
                                            selection,
                                        )
                                    )
                                else:
                                    selection.append(self._entries[at])

                        case "\n":
                            self._teardown(v.n + (self._prompt is not None))
                            if self._mode is Menu.SINGLE:
                                return self._entries[at]
                            else:
                                return selection

        except KeyboardInterrupt:
            self._teardown(v.n + (self._prompt is not None))
            return None


def select(entries, mode=Menu.SINGLE, prompt=None, use_descriptions=False):
    return Menu(
        entries, mode=mode, prompt=prompt, use_descriptions=use_descriptions
    ).select()


def main():
    entries = [
        c * (30 if idx % 11 == 5 else 1)
        for idx, c in enumerate("abcdefghijklmnopqrstuvwxyz")
    ]
    print(select(entries, mode=Menu.ORDERED, prompt="choose:"))

    dict_entries = {entry: f"{entry[0]} * {len(entry)}" for entry in entries}
    print(select(dict_entries, mode=Menu.MULTI, use_descriptions=True))


if __name__ == "__main__":
    main()
