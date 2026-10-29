"""Fake hand: place and close orders by driving the MT5 desktop window.

A fallback executor for when `order_send` is blocked or failing. It posts
Win32 messages to the terminal's own windows, so it needs no focus and never
moves the physical mouse. Positions are only READ through the MT5 API (the
trade list in the window is a virtual list with no readable text).

Setup: keep one chart open per symbol you want to trade (the order dialog opens
for the active chart's symbol).

    py -3 mt5/hand.py list  --login 35188708
    py -3 mt5/hand.py place --login 35188708 --symbol SPX500 --side buy --volume 0.01 --yes
    py -3 mt5/hand.py close --login 35188708 --ticket 123456 --yes

Without --yes, place/close only report what they would do.
Needs: pip install pywinauto pywin32 MetaTrader5
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import time

import MetaTrader5 as mt5
import win32api
import win32con
import win32gui
from pywinauto import Desktop

# Control ids are fixed by the MT5 build; verified on FundedNext MT5 (2026-10).
ID_VOLUME = 10333
ID_STOP_LOSS = 10334
ID_TAKE_PROFIT = 10336
ID_COMMENT = 1001
ID_SELL = 10409
ID_BUY = 10408
ID_CLOSE = 10410
ID_TRADE_LIST = 10328

TERMINAL_CLASS = "MetaQuotes::MetaTrader::5.00"
DIALOG_TIMEOUT = 5.0
FILL_TIMEOUT = 20.0


class HandError(RuntimeError):
    pass


def _close_quietly(handle: int) -> None:
    """WM_CLOSE to a window that may already be gone: a stale dialog or a chart
    found a moment ago can be destroyed before the post lands, and PostMessage
    then raises (1400, 'Invalid window handle') -- 13 FundedNext closes failed
    on it 2026-10-21..23, each costing a 60s retry."""
    try:
        win32gui.PostMessage(handle, win32con.WM_CLOSE, 0, 0)
    except win32gui.error:
        pass


class Hand:
    def __init__(self, login: int, positions_fn=None):
        # positions_fn lets a host that already holds an MT5 session (the
        # bridge) supply positions, since attaching here would shut it down.
        self.login = login
        self._positions_fn = positions_fn
        self.window = self._find_terminal(login)
        self.hwnd = self.window.handle
        self.pid = self.window.process_id()

    @staticmethod
    def _find_terminal(login: int):
        prefix = f"{login} - "
        for w in Desktop(backend="win32").windows(class_name=TERMINAL_CLASS):
            if w.window_text().startswith(prefix):
                return w
        raise HandError(f"no MT5 window logged into {login}")

    # --- reading (MT5 API, read-only) -------------------------------------

    def _attach(self) -> None:
        if not mt5.initialize(_exe_path(self.pid)):
            raise HandError(f"MT5 attach failed: {mt5.last_error()}")
        info = mt5.account_info()
        if info is None or int(info.login) != self.login:
            raise HandError(f"terminal is not logged into {self.login}")

    def positions(self) -> list:
        if self._positions_fn is not None:
            return list(self._positions_fn())
        self._attach()
        try:
            return list(mt5.positions_get() or [])
        finally:
            mt5.shutdown()

    # --- window plumbing ---------------------------------------------------

    def _tap(self, vk: int) -> None:
        win32gui.PostMessage(self.hwnd, win32con.WM_KEYDOWN, vk, 0)
        win32gui.PostMessage(self.hwnd, win32con.WM_KEYUP, vk, 0)

    def _dialogs(self, prefix: str) -> list:
        return [
            d
            for d in Desktop(backend="win32").windows(process=self.pid, class_name="#32770")
            if d.window_text().startswith(prefix)
        ]

    def _dialog(self, prefix: str, timeout: float = DIALOG_TIMEOUT):
        deadline = time.time() + timeout
        while time.time() < deadline:
            found = self._dialogs(prefix)
            if found:
                return found[0]
            time.sleep(0.05)
        raise HandError(f"dialog '{prefix}' did not appear")

    @staticmethod
    def _child(dialog, control_id: int):
        for c in dialog.children():
            if c.control_id() == control_id and c.is_visible():
                return c
        raise HandError(f"control {control_id} not found in '{dialog.window_text()}'")

    @staticmethod
    def _type(control, text: str) -> None:
        # WM_SETTEXT leaves Buy/Sell disabled; keystrokes fire the validation.
        win32gui.SendMessage(control.handle, win32con.EM_SETSEL, 0, -1)
        for ch in text:
            win32gui.SendMessage(control.handle, win32con.WM_CHAR, ord(ch), 0)

    @staticmethod
    def _press(control) -> None:
        win32gui.PostMessage(control.handle, win32con.BM_CLICK, 0, 0)

    def _charts(self) -> tuple[int, dict[str, int]]:
        """(MDI client handle, {symbol: chart handle}), tolerant of charts that
        are being destroyed while we look."""
        mdi = win32gui.FindWindowEx(self.hwnd, 0, "MDIClient", None)
        if not mdi:
            raise HandError("terminal has no chart area")
        found: dict[str, int] = {}
        child = 0
        while True:
            child = win32gui.FindWindowEx(mdi, child, None, None)
            if not child:
                break
            title = win32gui.GetWindowText(child)
            if "," in title:
                found.setdefault(title.split(",")[0].casefold(), child)
        return mdi, found

    def _activate_chart(self, symbol: str) -> bool:
        """Bring the symbol's chart forward, opening it if needed. True if opened here."""
        key = symbol.casefold()
        mdi, charts = self._charts()
        opened = key not in charts
        if opened:
            win32gui.PostMessage(self.hwnd, win32con.WM_COMMAND, self._new_chart_command(symbol), 0)
            deadline = time.time() + DIALOG_TIMEOUT
            while key not in charts and time.time() < deadline:
                time.sleep(0.1)
                mdi, charts = self._charts()
            if key not in charts:
                raise HandError(f"could not open a {symbol} chart")
        win32gui.SendMessage(mdi, win32con.WM_MDIACTIVATE, charts[key], 0)
        time.sleep(0.3)
        return opened

    def _close_chart(self, symbol: str) -> None:
        _, charts = self._charts()
        handle = charts.get(symbol.casefold())
        if handle:
            _close_quietly(handle)

    def _menu_command(self, title: str) -> int:
        """Command id of the first top-level-menu item whose text starts with title."""
        user32 = ctypes.windll.user32

        def walk(menu):
            for i in range(user32.GetMenuItemCount(menu)):
                sub = user32.GetSubMenu(menu, i)
                if sub:
                    found = walk(sub)
                    if found:
                        return found
                    continue
                text = ctypes.create_unicode_buffer(128)
                user32.GetMenuStringW(menu, i, text, 128, win32con.MF_BYPOSITION)
                if text.value.replace("&", "").split("	")[0].casefold() == title.casefold():
                    return int(user32.GetMenuItemID(menu, i))
            return 0

        command = walk(win32gui.GetMenu(self.hwnd))
        if not command:
            raise HandError(f"menu item '{title}' not found")
        return command

    def _new_chart_command(self, symbol: str) -> int:
        """Command id of File > New Chart > <group> > symbol."""
        user32 = ctypes.windll.user32
        new_chart = user32.GetSubMenu(user32.GetSubMenu(win32gui.GetMenu(self.hwnd), 0), 0)

        def walk(menu):
            for i in range(user32.GetMenuItemCount(menu)):
                sub = user32.GetSubMenu(menu, i)
                if sub:
                    found = walk(sub)
                    if found:
                        return found
                    continue
                text = ctypes.create_unicode_buffer(128)
                user32.GetMenuStringW(menu, i, text, 128, win32con.MF_BYPOSITION)
                if text.value.casefold() == symbol.casefold():
                    return int(user32.GetMenuItemID(menu, i))
            return 0

        command = walk(new_chart)
        if not command:
            raise HandError(f"{symbol} is not in File > New Chart")
        return command

    # --- actions -----------------------------------------------------------

    def place(self, symbol: str, side: str, volume: float, comment: str = "",
              sl: float = 0.0, tp: float = 0.0):
        """Open a position. A chart the hand had to open is closed again after;
        a chart that was already open is left alone."""
        if side not in ("buy", "sell"):
            raise HandError(f"side must be buy or sell, got {side}")
        opened = self._activate_chart(symbol)
        try:
            return self._place_on_active_chart(symbol, side, volume, comment, sl, tp)
        finally:
            if opened:
                self._close_chart(symbol)

    def _place_on_active_chart(self, symbol, side, volume, comment, sl, tp):
        before = {p.ticket for p in self.positions()}
        for stale in self._dialogs("Order:"):
            _close_quietly(stale.handle)
        # A posted F9 is occasionally dropped right after a chart switch.
        for attempt in range(3):
            # The Tools > New Order command works while the terminal is
            # minimized; the F9 accelerator does not.
            win32gui.PostMessage(self.hwnd, win32con.WM_COMMAND, self._menu_command("New Order"), 0)
            try:
                dlg = self._dialog(f"Order: {symbol} -", timeout=2.0)
                break
            except HandError:
                if attempt == 2:
                    raise
                self._activate_chart(symbol)
        button = ID_BUY if side == "buy" else ID_SELL
        try:
            vol = self._child(dlg, ID_VOLUME)
            for _ in range(10):
                time.sleep(0.3)
                self._type(vol, f"{volume:.2f}")
                time.sleep(0.2)
                if self._child(dlg, button).is_enabled():
                    break
            else:
                raise HandError("order dialog never armed its buy/sell buttons")
            if sl:
                self._type(self._child(dlg, ID_STOP_LOSS), f"{sl}")
            if tp:
                self._type(self._child(dlg, ID_TAKE_PROFIT), f"{tp}")
            if comment:
                self._type(self._child(dlg, ID_COMMENT), comment)
            self._press(self._child(dlg, button))
        except Exception:
            _close_quietly(dlg.handle)
            raise
        try:
            deadline = time.time() + FILL_TIMEOUT
            while time.time() < deadline:
                # By the order's own comment first: it names THIS order even when
                # another position on the symbol opened meanwhile.
                new = [
                    p for p in self.positions()
                    if (comment and str(getattr(p, "comment", "")) == comment)
                    or (p.ticket not in before and p.symbol.casefold() == symbol.casefold())
                ]
                if new:
                    return new[0]
                time.sleep(0.2)
            raise HandError(f"no new {symbol} position appeared after the order")
        finally:
            # The dialog stays on its "Done" page until dismissed.
            _close_quietly(dlg.handle)

    def close(self, ticket: int) -> None:
        open_now = self.positions()
        if all(p.ticket != ticket for p in open_now):
            raise HandError(f"position {ticket} is not open")
        lv = next(
            (c for c in self.window.children()
             if c.control_id() == ID_TRADE_LIST and c.class_name() == "SysListView32"),
            None,
        )
        if lv is None:
            raise HandError("Trade list not found")
        # Select the "Trade" tab so the position list is the one showing.
        for c in self.window.children():
            if c.class_name() == "SysTabControl32":
                names = [c.get_tab_text(i) for i in range(c.tab_count())]
                if "Trade" in names:
                    c.select(names.index("Trade"))
        # Rows are 20px tall under a 24px header; try each row until the
        # dialog that opens is the wanted ticket's.
        for row in range(len(open_now)):
            for stale in self._dialogs("Position:") + self._dialogs("Order:"):
                _close_quietly(stale.handle)
            lp = win32api.MAKELONG(300, 34 + 20 * row)
            for msg, wp in (
                (win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON),
                (win32con.WM_LBUTTONUP, 0),
                (win32con.WM_LBUTTONDBLCLK, win32con.MK_LBUTTON),
                (win32con.WM_LBUTTONUP, 0),
            ):
                win32gui.PostMessage(lv.handle, msg, wp, lp)
                time.sleep(0.05)
            try:
                dlg = self._dialog("Position:")
            except HandError:
                continue
            if f"#{ticket} " in dlg.window_text():
                try:
                    self._press(self._child(dlg, ID_CLOSE))
                    deadline = time.time() + FILL_TIMEOUT
                    while time.time() < deadline:
                        if all(p.ticket != ticket for p in self.positions()):
                            return
                        time.sleep(0.2)
                    raise HandError(f"position {ticket} still open after close")
                finally:
                    _close_quietly(dlg.handle)
            _close_quietly(dlg.handle)
        raise HandError(f"could not reach position {ticket} in the trade list")


def _exe_path(pid: int) -> str:
    import win32process

    handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    try:
        return win32process.GetModuleFileNameEx(handle, 0)
    finally:
        win32api.CloseHandle(handle)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["list", "place", "close"])
    ap.add_argument("--login", type=int, required=True)
    ap.add_argument("--symbol")
    ap.add_argument("--side", choices=["buy", "sell"])
    ap.add_argument("--volume", type=float)
    ap.add_argument("--ticket", type=int)
    ap.add_argument("--comment", default="")
    ap.add_argument("--yes", action="store_true", help="actually send the order")
    a = ap.parse_args()
    hand = Hand(a.login)
    if a.action == "list":
        for p in hand.positions():
            print(p.ticket, p.symbol, p.volume, p.profit)
        return 0
    if a.action == "place":
        if not (a.symbol and a.side and a.volume):
            ap.error("place needs --symbol --side --volume")
        print(f"{'PLACE' if a.yes else 'DRY RUN'}: {a.side} {a.volume} {a.symbol} on {a.login}")
        if a.yes:
            p = hand.place(a.symbol, a.side, a.volume, a.comment)
            print("opened", p.ticket, p.symbol, p.volume, p.price_open)
        return 0
    if not a.ticket:
        ap.error("close needs --ticket")
    print(f"{'CLOSE' if a.yes else 'DRY RUN'}: ticket {a.ticket} on {a.login}")
    if a.yes:
        hand.close(a.ticket)
        print("closed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except HandError as e:
        print(f"hand: {e}", file=sys.stderr)
        sys.exit(1)
