"""The geometry behind the screenshot editor: fitting, cropping, shapes.

Kept apart from the drawing because this is the part that can be wrong
without looking wrong -- a region that drifts by the display's scale
factor, a crop that runs off the edge of the image, a rectangle dragged
right-to-left that comes out with a negative width and disappears. All
of that is arithmetic, and arithmetic can be tested without a screen.

Two coordinate spaces. *Image* pixels are what the screenshot has and
what gets saved; *view* pixels are what the pointer reports. Everything
stored here is in image space, so the result does not depend on the
window that happened to be drawing it.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Fit:
    """How an image sits inside a view: scaled to fit, centred."""

    scale: float
    ox: float
    oy: float

    def to_image(self, x: float, y: float) -> tuple[float, float]:
        return ((x - self.ox) / self.scale, (y - self.oy) / self.scale)

    def to_view(self, x: float, y: float) -> tuple[float, float]:
        return (x * self.scale + self.ox, y * self.scale + self.oy)


def fit(img_w: int, img_h: int, view_w: float, view_h: float) -> Fit:
    """Contain the image in the view: the whole screenshot is visible,
    letterboxed rather than cropped, because a region that has scrolled
    off the edge cannot be selected."""
    if img_w <= 0 or img_h <= 0 or view_w <= 0 or view_h <= 0:
        return Fit(1.0, 0.0, 0.0)
    scale = min(view_w / img_w, view_h / img_h)
    return Fit(scale, (view_w - img_w * scale) / 2,
               (view_h - img_h * scale) / 2)


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def empty(self) -> bool:
        # A click without a drag is not a region; it is a click.
        return self.w < 2 or self.h < 2

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x <= self.x + self.w and \
            self.y <= y <= self.y + self.h


def rect_from(x0: float, y0: float, x1: float, y1: float) -> Rect:
    """A rectangle from two corners, dragged in any direction."""
    return Rect(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))


def clamp(rect: Rect, img_w: int, img_h: int) -> Rect:
    """Pull a rectangle back inside the image.

    Selections are dragged past the edge constantly -- it is the natural
    way to catch a window flush against the side of the screen -- and a
    crop that starts at -4 either throws or silently shifts the picture.
    """
    x0 = max(0.0, min(rect.x, img_w))
    y0 = max(0.0, min(rect.y, img_h))
    x1 = max(0.0, min(rect.x + rect.w, img_w))
    y1 = max(0.0, min(rect.y + rect.h, img_h))
    return Rect(x0, y0, x1 - x0, y1 - y0)


# What each tool draws. "pixelate" is the redaction: a mosaic rather
# than a blur, because a blur of readable text is sometimes reversible
# and a mosaic at this block size is not.
TOOLS = ("select", "rect", "arrow", "pen", "pixelate")


@dataclass
class Shape:
    kind: str
    points: list[tuple[float, float]] = field(default_factory=list)

    @property
    def rect(self) -> Rect:
        """The bounding box, for the shapes that are one."""
        (x0, y0), (x1, y1) = self.points[0], self.points[-1]
        return rect_from(x0, y0, x1, y1)


@dataclass
class Markup:
    """Everything the user has done to one screenshot."""

    width: int
    height: int
    shapes: list[Shape] = field(default_factory=list)
    region: Rect | None = None

    @property
    def crop(self) -> Rect:
        """The area to save: the selection, or the whole screenshot.

        No selection means the user wanted the screen, not that they
        failed to choose -- pressing Enter straight away is the fast
        path, and it should give the picture that is on screen."""
        if self.region is None or self.region.empty:
            return Rect(0, 0, self.width, self.height)
        return clamp(self.region, self.width, self.height)

    def add(self, kind: str, points) -> Shape | None:
        """Record a stroke, or nothing if it was too small to mean it."""
        pts = [(float(x), float(y)) for x, y in points]
        if len(pts) < 2:
            return None
        if kind != "pen" and rect_from(*pts[0], *pts[-1]).empty:
            return None
        shape = Shape(kind, pts)
        self.shapes.append(shape)
        return shape

    def undo(self) -> None:
        """Take back the last mark, or the selection once they are gone.

        One key for both because "undo" means "the last thing I did",
        and having to know which kind of thing that was is the sort of
        detail an editor should keep to itself."""
        if self.shapes:
            self.shapes.pop()
        else:
            self.region = None
