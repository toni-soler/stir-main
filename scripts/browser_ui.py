"""Small helpers shared by the browser scripts, so they follow the UI's wording instead of its exact markup.

A STIR field's accessible name is its label plus whatever the label wraps: a required marker ("Título *")
or a hint under the input ("Nombre visible" + "Lo que verán las demás personas…"). Matching the exact text
breaks every time a hint or marker is added; matching the start of the name does not.
"""
import re


def field(text):
    """The accessible name of a field whose label starts with `text`."""
    # The hint follows the label with no space ("Nombre visibleEste es el nombre..."), so only a lower-case
    # continuation of the same word ("Título" vs "Títulos") counts as a different label.
    return re.compile('^' + re.escape(text) + '(?![a-záéíóúüñ])')


def test_images(browser, folder):
    """Writes a small PNG avatar and JPEG photo into `folder` (once), rendered by the browser itself, so a
    script that uploads images needs no file prepared by hand on the machine that runs it."""
    folder.mkdir(parents=True, exist_ok=True)
    targets = {'test-avatar.png': ('png', '#2f7d6d'), 'test-photo.jpg': ('jpeg', '#c9874a')}
    if all((folder / name).exists() for name in targets):
        return
    page = browser.new_page(viewport={'width': 320, 'height': 240})
    try:
        for name, (kind, colour) in targets.items():
            page.set_content(f'<body style="margin:0"><div style="width:320px;height:240px;background:{colour}"></div></body>')
            page.screenshot(path=str(folder / name), type=kind, clip={'x': 0, 'y': 0, 'width': 320, 'height': 240})
    finally:
        page.close()


def explain(**pages):
    """Prints where each page was and what it showed: call it from an `except` before re-raising, so a failed
    run says what the person would have seen instead of only which locator timed out."""
    for name, page in pages.items():
        try:
            area = page.locator('.stir') if page.locator('.stir').count() else page.locator('body')
            alerts = [text for text in page.locator('[role=alert]').all_inner_texts() if text.strip()]
            print(f'--- {name} at {page.url}\nalerts: {alerts}\n{area.first.inner_text()[:2500]}')
        except Exception as failure:  # noqa: BLE001 - best effort while already failing
            print(f'--- {name}: could not read the page ({failure})')
