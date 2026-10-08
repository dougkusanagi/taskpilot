"""Protótipo de snapshot DOM das fixtures; não substitui leitura DOM no Chrome."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from html.parser import HTMLParser

from evals.brain.contract import Element, Observation
from evals.brain.fixtures import CATALOG, page_html


@dataclass
class Node:
    tag: str
    attrs: dict
    parent: Node | None = None
    parts: list = field(default_factory=list)
    index: int = 0

    def text(self):
        return " ".join(
            " ".join(p.text() if isinstance(p, Node) else p for p in self.parts).split()
        )

    def ancestor(self, predicate):
        node = self
        while node:
            if predicate(node):
                return node
            node = node.parent
        return None


class DOM(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.root = Node("root", {})
        self.stack, self.nodes = [self.root], []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs), self.stack[-1], index=len(self.nodes))
        self.stack[-1].parts.append(node)
        self.nodes.append(node)
        if tag not in {"input", "meta", "br", "img", "hr", "link"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                self.stack = self.stack[:index]
                break

    def handle_data(self, text):
        self.stack[-1].parts.append(text)


def snapshots(
    page: str, tool: str, *, prune: bool, paginate: bool, page_size: int = 3
) -> list[str]:
    """Serializa resposta completa (envelope incluído); snapshot nunca expõe senha."""
    catalog = json.loads(CATALOG.read_text())
    dom = DOM(page_html(page, catalog))
    cards = [n for n in dom.nodes if n.tag == "article"]
    starts = range(0, max(1, len(cards)), page_size) if paginate else [0]
    output = []
    for start in starts:
        chosen = cards[start : start + page_size] if paginate else cards
        oid = f"snapshot-{page}-{start}"
        elements, texts = [], []
        for node in dom.nodes:
            if node.ancestor(
                lambda n: (
                    n.tag in {"script", "style"}
                    or "hidden" in n.attrs
                    or n.attrs.get("aria-hidden") == "true"
                )
            ):
                continue
            if prune and node.ancestor(
                lambda n: n.tag in {"nav", "footer"} or n.attrs.get("data-noise") == "true"
            ):
                continue
            card = node.ancestor(lambda n: n.tag == "article")
            if card and card not in chosen:
                continue
            role = node.attrs.get(
                "role",
                {"a": "link", "button": "button", "input": "textbox", "textarea": "textbox"}.get(
                    node.tag, node.tag
                ),
            )
            if tool == "read_page" and (not prune or role in {"link", "button", "textbox"}):
                if node.tag not in {"html", "body", "root", "meta"}:
                    sensitive = node.attrs.get("type") == "password"
                    name = node.attrs.get("aria-label", node.text())
                    elements.append(
                        Element(
                            ref=f"{oid}:e{node.index}",
                            role=role,
                            name=name,
                            value="" if sensitive else node.attrs.get("value", ""),
                            sensitive=sensitive,
                        )
                    )
            if node.tag in {"h1", "h2", "p", "pre", "output", "label"} and node.text():
                texts.append(node.text())
        next_cursor = (
            f"{page}:{start + page_size}" if paginate and start + page_size < len(cards) else ""
        )
        obs = Observation(
            id=oid,
            url=f"http://localhost:8765/{page}",
            elements=elements,
            text="\n".join(texts),
            coverage="fixture HTML prototype; untrusted",
            next=next_cursor,
        )
        output.append(obs.render())
    return output
