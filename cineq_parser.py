"""Parse the official ReserveScreenPlan fragment, never site navigation."""
import datetime
import re
from html.parser import HTMLParser


class Node:
    def __init__(self, tag="", attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def text(self, recursive=True):
        return "".join(child if isinstance(child, str) else child.text()
                       for child in self.children if recursive or isinstance(child, str)).strip()

    def has_class(self, name):
        return name in self.attrs.get("class", "").split()

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()


class Fragment(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in ("br", "hr", "img", "input", "meta", "link"):
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse_timetable(html, job, movie_title, url):
    if not html.strip():
        return {}
    title, screen, total, current = "", "", None, {}
    for node in Fragment(html).root.walk():
        if node.has_class("title"):
            title = node.text()
        elif node.has_class("theater-info"):
            screen = node.text(recursive=False)
            seats = next((child.text() for child in node.walk() if child.has_class("all-seats")), "")
            match = re.fullmatch(r"\((\d+)석\)", seats)
            total = int(match[1]) if match else None
        elif node.has_class("time"):
            if movie_title not in title or not screen or total is None:
                raise ValueError("CineQ movie/screen identity missing")
            if node.attrs.get("data-playdate") != job["date"] or not node.attrs.get("data-screenplanid", "").isdigit():
                raise ValueError("CineQ date or screening ID mismatch")
            start = next((child.text(recursive=False) for child in node.walk() if child.has_class("from")), "")
            status = next((child.text(recursive=False) for child in node.walk() if child.has_class("seats")), "")
            if not re.fullmatch(r"\d{2}:\d{2}", start):
                raise ValueError("CineQ start time missing")
            if status == "매진":
                left = 0
            elif re.fullmatch(r"\d+석", status):
                left = int(status[:-1])
            else:
                raise ValueError("Unknown CineQ availability: " + status[:40])
            if not 0 <= left <= total:
                raise ValueError("Invalid CineQ remaining seats")
            starts = datetime.datetime.strptime(job["date"] + start, "%Y%m%d%H:%M")
            current[job["scope"] + start + "|" + screen] = {
                "left": left, "total": total, "url": url,
                "opened": starts > datetime.datetime.now(), "screenplan_id": node.attrs["data-screenplanid"]}
    if not current:
        raise ValueError("Unrecognized CineQ timetable response")
    return current
