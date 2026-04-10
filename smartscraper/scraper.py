import json
import hashlib
from html import unescape
from urllib.parse import urljoin, urlparse
from collections import defaultdict

import requests
from bs4 import BeautifulSoup

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

from smartscraper.utils import (
    FuzzyText, ResultItem, get_non_rec_text, normalize, text_match,
    unique_hashable, unique_stack_list,
)


class SmartScraper:
    request_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    }

    def __init__(self, stack_list=None):
        self.stack_list = stack_list or []

    @classmethod
    def _fetch_html(cls, url, request_args=None):
        request_args = request_args or {}
        headers = dict(cls.request_headers)
        if url:
            headers["Host"] = urlparse(url).netloc
        user_headers = request_args.pop("headers", {})
        headers.update(user_headers)
        res = requests.get(url, headers=headers, timeout=15, **request_args)
        if res.encoding == "ISO-8859-1" and "ISO-8859-1" not in res.headers.get("Content-Type", ""):
            res.encoding = res.apparent_encoding
        return res.text

    @classmethod
    def _fetch_html_js(cls, url, wait=3000):
        if not HAS_PLAYWRIGHT:
            raise RuntimeError("Playwright not installed. Run: pip install playwright && python -m playwright install chromium")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent=cls.request_headers["User-Agent"],
                viewport={"width": 1280, "height": 900},
            )
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(wait)
            # Scroll multiple times to trigger lazy loading
            for i in range(3):
                page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {(i+1)/3})")
                page.wait_for_timeout(1500)
            # Scroll back to top
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(500)
            html = page.content()
            browser.close()
        return html

    @classmethod
    def _get_soup(cls, url=None, html=None, request_args=None, use_js=False):
        if html:
            html = normalize(unescape(html))
            return BeautifulSoup(html, "lxml")
        if use_js:
            raw = cls._fetch_html_js(url)
        else:
            raw = cls._fetch_html(url, request_args)
        raw = normalize(unescape(raw))
        return BeautifulSoup(raw, "lxml")

    @staticmethod
    def _get_valid_attrs(item):
        key_attrs = {"class", "style"}
        attrs = {}
        for k, v in item.attrs.items():
            if k not in key_attrs:
                continue
            if v == []:
                v = ""
            if v:
                attrs[k] = v
        return attrs

    @staticmethod
    def _child_has_text(child, text, url, text_fuzz_ratio):
        child_text = child.getText().strip()
        if text_match(text, child_text, text_fuzz_ratio):
            parent_text = child.parent.getText().strip()
            if child_text == parent_text and child.parent.parent:
                return False
            child.wanted_attr = None
            return True

        if text_match(text, get_non_rec_text(child), text_fuzz_ratio):
            child.is_non_rec_text = True
            child.wanted_attr = None
            return True

        for key, value in child.attrs.items():
            if not isinstance(value, str):
                continue
            value = value.strip()
            if text_match(text, value, text_fuzz_ratio) or text in value:
                child.wanted_attr = key
                return True
            if key in {"href", "src"}:
                full_url = urljoin(url, value)
                if text_match(text, full_url, text_fuzz_ratio) or text in full_url:
                    child.wanted_attr = key
                    child.is_full_url = True
                    return True

        return False

    def _get_children(self, soup, text, url, text_fuzz_ratio):
        children = reversed(soup.findChildren())
        return [x for x in children if self._child_has_text(x, text, url, text_fuzz_ratio)]

    @classmethod
    def _build_stack(cls, child, url):
        content = [(child.name, cls._get_valid_attrs(child))]
        parent = child
        while True:
            grand_parent = parent.findParent()
            if not grand_parent:
                break
            children = grand_parent.findAll(
                parent.name, cls._get_valid_attrs(parent), recursive=False
            )
            for i, c in enumerate(children):
                if c == parent:
                    content.insert(0, (grand_parent.name, cls._get_valid_attrs(grand_parent), i))
                    break
            if not grand_parent.parent:
                break
            parent = grand_parent

        wanted_attr = getattr(child, "wanted_attr", None)
        is_full_url = getattr(child, "is_full_url", False)
        is_non_rec_text = getattr(child, "is_non_rec_text", False)
        stack = dict(
            content=content,
            wanted_attr=wanted_attr,
            is_full_url=is_full_url,
            is_non_rec_text=is_non_rec_text,
        )
        stack["url"] = url if is_full_url else ""
        stack["hash"] = hashlib.sha256(str(stack).encode("utf-8")).hexdigest()
        stack["stack_id"] = "rule_" + stack["hash"][:8]
        return stack

    def _get_result_for_child(self, child, soup, url):
        stack = self._build_stack(child, url)
        result = self._get_result_with_stack(stack, soup, url, 1.0)
        return result, stack

    @staticmethod
    def _fetch_result_from_child(child, wanted_attr, is_full_url, url, is_non_rec_text):
        if wanted_attr is None:
            if is_non_rec_text:
                return get_non_rec_text(child)
            return child.getText().strip()
        if wanted_attr not in child.attrs:
            return None
        if is_full_url:
            return urljoin(url, child.attrs[wanted_attr])
        return child.attrs[wanted_attr]

    @staticmethod
    def _get_fuzzy_attrs(attrs, attr_fuzz_ratio):
        attrs = dict(attrs)
        for key, val in attrs.items():
            if isinstance(val, str) and val:
                val = FuzzyText(val, attr_fuzz_ratio)
            elif isinstance(val, (list, tuple)):
                val = [FuzzyText(x, attr_fuzz_ratio) if x else x for x in val]
            attrs[key] = val
        return attrs

    def _get_result_with_stack(self, stack, soup, url, attr_fuzz_ratio, **kwargs):
        parents = [soup]
        stack_content = stack["content"]
        contain_sibling_leaves = kwargs.get("contain_sibling_leaves", False)
        for index, item in enumerate(stack_content):
            children = []
            if item[0] == "[document]":
                continue
            for parent in parents:
                attrs = item[1]
                if attr_fuzz_ratio < 1.0:
                    attrs = self._get_fuzzy_attrs(attrs, attr_fuzz_ratio)
                found = parent.findAll(item[0], attrs, recursive=False)
                if not found:
                    continue
                if not contain_sibling_leaves and index == len(stack_content) - 1:
                    idx = min(len(found) - 1, stack_content[index - 1][2])
                    found = [found[idx]]
                children += found
            parents = children

        wanted_attr = stack["wanted_attr"]
        is_full_url = stack["is_full_url"]
        is_non_rec_text = stack.get("is_non_rec_text", False)
        result = [
            ResultItem(
                self._fetch_result_from_child(i, wanted_attr, is_full_url, url, is_non_rec_text),
                getattr(i, "child_index", 0),
            )
            for i in parents
        ]
        result = [x for x in result if x.text]
        return result

    def _get_result_with_stack_index_based(self, stack, soup, url, attr_fuzz_ratio, **kwargs):
        p = soup.findChildren(recursive=False)[0]
        stack_content = stack["content"]
        for index, item in enumerate(stack_content[:-1]):
            if item[0] == "[document]":
                continue
            content = stack_content[index + 1]
            attrs = content[1]
            if attr_fuzz_ratio < 1.0:
                attrs = self._get_fuzzy_attrs(attrs, attr_fuzz_ratio)
            p = p.findAll(content[0], attrs, recursive=False)
            if not p:
                return []
            idx = min(len(p) - 1, item[2])
            p = p[idx]

        result = [
            ResultItem(
                self._fetch_result_from_child(
                    p, stack["wanted_attr"], stack["is_full_url"], url, stack.get("is_non_rec_text", False)
                ),
                getattr(p, "child_index", 0),
            )
        ]
        result = [x for x in result if x.text]
        return result

    def build(self, url=None, wanted_list=None, html=None, request_args=None,
              update=False, text_fuzz_ratio=1.0, use_js=False):
        if not wanted_list:
            raise ValueError("wanted_list is required")

        soup = self._get_soup(url=url, html=html, request_args=request_args, use_js=use_js)

        if not update:
            self.stack_list = []

        result_list = []
        wanted_list = [normalize(w) for w in wanted_list]

        for wanted in wanted_list:
            children = self._get_children(soup, wanted, url, text_fuzz_ratio)
            for child in children:
                result, stack = self._get_result_for_child(child, soup, url)
                result_list += result
                self.stack_list.append(stack)

        result_list = [item.text for item in result_list]
        result_list = unique_hashable(result_list)
        self.stack_list = unique_stack_list(self.stack_list)
        return result_list

    def _get_result_by_func(self, func, url, html, soup, request_args,
                            grouped, unique, attr_fuzz_ratio, **kwargs):
        if not soup:
            soup = self._get_soup(url=url, html=html, request_args=request_args)

        result_list = []
        grouped_result = defaultdict(list)
        for stack in self.stack_list:
            stack_url = url or stack.get("url", "")
            result = func(stack, soup, stack_url, attr_fuzz_ratio, **kwargs)
            if not grouped:
                result_list += result
            else:
                grouped_result[stack["stack_id"]] += result

        if grouped:
            return {k: unique_hashable([x.text for x in v]) for k, v in grouped_result.items()}

        result = [x.text for x in result_list]
        if unique is None or unique:
            result = unique_hashable(result)
        return result

    def get_result_similar(self, url=None, html=None, request_args=None,
                           grouped=False, unique=None, attr_fuzz_ratio=1.0, use_js=False):
        if not self.stack_list:
            raise RuntimeError("No rules learned. Call build() first.")
        soup = self._get_soup(url=url, html=html, request_args=request_args, use_js=use_js) if url or html else None
        return self._get_result_by_func(
            self._get_result_with_stack, url, html, soup, request_args,
            grouped, unique, attr_fuzz_ratio, contain_sibling_leaves=True,
        )

    def get_result_exact(self, url=None, html=None, request_args=None,
                         grouped=False, unique=None, attr_fuzz_ratio=1.0, use_js=False):
        if not self.stack_list:
            raise RuntimeError("No rules learned. Call build() first.")
        soup = self._get_soup(url=url, html=html, request_args=request_args, use_js=use_js) if url or html else None
        return self._get_result_by_func(
            self._get_result_with_stack_index_based, url, html, soup, request_args,
            grouped, unique, attr_fuzz_ratio,
        )

    def get_rules(self):
        return {s["stack_id"]: {"stack_id": s["stack_id"], "wanted_attr": s["wanted_attr"],
                "is_full_url": s["is_full_url"], "hash": s["hash"][:12]} for s in self.stack_list}

    def remove_rule(self, rule_id):
        self.stack_list = [s for s in self.stack_list if s["stack_id"] != rule_id]

    def keep_rules(self, rule_ids):
        self.stack_list = [s for s in self.stack_list if s["stack_id"] in rule_ids]

    def save(self, filepath):
        if not filepath.endswith(".json"):
            filepath += ".json"
        with open(filepath, "w") as f:
            json.dump({"stack_list": self.stack_list}, f, indent=2)

    def load(self, filepath):
        if not filepath.endswith(".json"):
            filepath += ".json"
        with open(filepath, "r") as f:
            data = json.load(f)
        if isinstance(data, list):
            self.stack_list = data
        else:
            self.stack_list = data.get("stack_list", [])

    def __repr__(self):
        return f"SmartScraper(rules={len(self.stack_list)})"
