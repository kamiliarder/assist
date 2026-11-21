"""Web browsing tool using Tavily API."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from .env import _run, get_tavily_key


def open(self, url):
    """Open a URL in Firefox."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return _run(["firefox", url]) or "ok"


def search(self, query):
    """Search the web in Firefox (opens browser)."""
    url = "https://www.google.com/search?q=" + query.replace(" ", "+")
    return _run(["firefox", url]) or "ok"


def read(self, url, recursive=False, depth=1):
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    visited = set()
    parts = []

    def fetch(current_url, current_depth):
        if current_url in visited or current_depth > depth:
            return
        visited.add(current_url)
        content = _run(["curl", "-sL", current_url])
        if content:
            parts.append(f"=== {current_url} (depth {current_depth}) ===\n{content}")
            if recursive and current_depth < depth:
                for link in re.findall(r'href=["\']([^"\']+)["\']', content):
                    absolute = urllib.parse.urljoin(current_url, link)
                    if absolute.startswith(("http://", "https://")):
                        fetch(absolute, current_depth + 1)

    fetch(url, 1)
    return "\n\n".join(parts) or "ok"


def _tavily_request(endpoint, payload):
    api_key = get_tavily_key()
    if not api_key:
        return {"error": "Tavily API key not configured. Add it to ~/.config/ordis/tavily_key"}

    url = f"https://api.tavily.com{endpoint}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": f"Tavily API error: {e.code} - {e.read().decode()}"}
    except Exception as e:
        return {"error": f"Request failed: {e}"}


def tavily_search(self, query, max_results=5, search_depth="basic", include_domains=None, exclude_domains=None):
    payload = {
        "query": query,
        "max_results": max_results,
        "search_depth": search_depth,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
    }
    if include_domains:
        payload["include_domains"] = include_domains
    if exclude_domains:
        payload["exclude_domains"] = exclude_domains

    result = _tavily_request("/search", payload)
    return json.dumps(result)


def tavily_extract(self, urls, include_images=False):
    if isinstance(urls, str):
        urls = [urls]

    payload = {
        "urls": urls,
        "include_images": include_images,
    }

    result = _tavily_request("/extract", payload)
    return json.dumps(result)


def tavily_search_and_extract(self, query, max_results=5, search_depth="basic"):
    payload = {
        "query": query,
        "max_results": max_results,
        "search_depth": search_depth,
        "include_answer": False,
        "include_raw_content": True,
        "include_images": False,
    }

    result = _tavily_request("/search", payload)
    return json.dumps(result)
