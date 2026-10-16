class ApiError(Exception):
    """An error with a user-facing message, an HTTP status and an optional machine-readable code."""

    def __init__(self, status, message, code=None):
        super().__init__(message)
        self.status, self.message, self.code = status, message, code
