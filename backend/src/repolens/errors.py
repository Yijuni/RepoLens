class RepoLensError(Exception):
    """An intentionally safe, user-facing error without upstream payloads."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status
