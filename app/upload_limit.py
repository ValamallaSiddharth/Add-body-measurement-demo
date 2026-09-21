from starlette.responses import JSONResponse


class UploadLimitMiddleware:
    """Bound multipart bodies before the parser writes temporary files."""
    def __init__(self, app, max_bytes=31_000_000):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = self.max_bytes + 1
        body = bytearray()
        while length <= self.max_bytes:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.max_bytes:
                break
            if not message.get("more_body", False):
                sent = False
                async def replay():
                    nonlocal sent
                    if not sent:
                        sent = True
                        return {"type": "http.request", "body": bytes(body), "more_body": False}
                    return await receive()
                return await self.app(scope, replay, send)
        response = JSONResponse({"success": False, "error": "Upload too large. Each photo must be at most 15 MB."}, status_code=413)
        await response(scope, receive, send)
