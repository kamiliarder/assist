"""Web browsing tool for Firefox."""
from .env import _run


def open(self, url):
    """Open a URL in Firefox."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return _run(["firefox", url]) or "ok"


def search(self, query):
    """Search the web in Firefox."""
    url = "https://www.google.com/search?q=" + query.replace(" ", "+")
    return _run(["firefox", url]) or "ok"


def read(self, url):
    """Fetch a URL and return its text content."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return _run(["curl", "-sL", url]) or "ok"
