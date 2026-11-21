"""Web browsing tool using Tavily API."""
from .env import _run, get_tavily_key
import json
import urllib.request
import urllib.parse


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
    """Fetch a URL and return its text content.

    If recursive is True, also fetch linked pages up to the given depth.
    """
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
                    absolute = urljoin(current_url, link)
                    if absolute.startswith(("http://", "https://")):
                        fetch(absolute, current_depth + 1)

    fetch(url, 1)
    return "\n\n".join(parts) or "ok"


def _tavily_request(endpoint, payload):
    """Make a request to Tavily API."""
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
    """Search the web using Tavily API and return structured results.

    Args:
        query: Search query string
        max_results: Maximum number of results (default 5)
        search_depth: "basic" or "advanced" (default "basic")
        include_domains: Optional list of domains to include
        exclude_domains: Optional list of domains to exclude

    Returns JSON with: query, results[title, url, content, score]
    """
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
    """Extract content from URLs using Tavily API.

    Args:
        urls: Single URL string or list of URLs
        include_images: Whether to include images in extraction

    Returns JSON with extracted content for each URL.
    """
    if isinstance(urls, str):
        urls = [urls]

    payload = {
        "urls": urls,
        "include_images": include_images,
    }

    result = _tavily_request("/extract", payload)
    return json.dumps(result)


def tavily_search_and_extract(self, query, max_results=5, search_depth="basic"):
    """Search and extract content in one call using Tavily API.

    This is a convenience function that searches and then extracts
    the top results automatically.

    Returns JSON with search results including full extracted content.
    """
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
