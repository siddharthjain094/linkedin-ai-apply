"""Easy Apply modal scrolling and footer click helpers."""

from agent.browser import easy_apply as ea


class _FakeLocator:
  def __init__(self, count: int = 0, *, disabled: bool = False, click_ok: bool = True):
    self._count = count
    self._disabled = disabled
    self._click_ok = click_ok
    self.scroll_calls = 0
    self.clicked = False
    self.evaluated = False
    self.first = self

  def count(self):
    return self._count

  def locator(self, sel):
    return _FakeLocator(0)

  def scroll_into_view_if_needed(self, **_kw):
    self.scroll_calls += 1

  def wait_for(self, **_kw):
    return None

  def is_disabled(self):
    return self._disabled

  def click(self, **_kw):
    if not self._click_ok:
      raise RuntimeError("click failed")
    self.clicked = True

  def evaluate(self, _script):
    self.evaluated = True


class _FakePage:
  def __init__(self, modal_count: int = 1, content_count: int = 1):
    self._modal_count = modal_count
    self._content_count = content_count
    self._footer_btn = _FakeLocator(1)
    self.content_scrolled = False

  def locator(self, sel):
    if "jobs-easy-apply-modal" in sel or "data-test-modal" in sel:
      return _ModalLocator(self._modal_count, self._content_count, self._footer_btn, self)
    return _FakeLocator(0)


class _ModalLocator:
  def __init__(self, modal_count, content_count, footer_btn, page=None):
    self._modal_count = modal_count
    self._content_count = content_count
    self._footer_btn = footer_btn
    self._page = page
    self.first = self

  def count(self):
    return self._modal_count

  def locator(self, sel):
    if "jobs-easy-apply-content" in sel or "modal__content" in sel:
      loc = _FakeLocator(self._content_count)
      orig_eval = loc.evaluate

      def evaluate(script):
        if self._page is not None:
          self._page.content_scrolled = True
        orig_eval(script)

      loc.evaluate = evaluate
      return loc
    if "Submit" in sel or "Next" in sel or "Review" in sel:
      return self._footer_btn
    return _FakeLocator(0)

  def evaluate(self, _script):
    pass


def test_footer_has_scoped_to_modal():
  page = _FakePage(modal_count=1)
  page._footer_btn._count = 1
  assert ea._footer_has(page, "Submit application") is True

  page2 = _FakePage(modal_count=0)
  assert ea._footer_has(page2, "Submit application") is False


def test_scroll_modal_content_evaluates_inner_container():
  page = _FakePage(modal_count=1, content_count=1)
  ea._scroll_modal_content(page)
  assert page.content_scrolled is True


def test_click_modal_footer_scrolls_and_clicks():
  page = _FakePage(modal_count=1, content_count=1)
  assert ea._click_modal_footer(page, "button:has-text('Next')") is True
  assert page._footer_btn.clicked is True
  assert page._footer_btn.scroll_calls >= 1


def test_click_modal_footer_skips_disabled():
  page = _FakePage(modal_count=1, content_count=1)
  page._footer_btn._disabled = True
  assert ea._click_modal_footer(page, "button:has-text('Next')") is False
