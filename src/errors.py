"""User-safe error types. Never leak raw exceptions to the UI."""


class TrustAnalyticsError(Exception):
    def __init__(self, code: str, user_message: str, log_detail: str | None = None):
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.log_detail = log_detail or user_message


class UnsupportedPlatformError(TrustAnalyticsError):
    def __init__(self, url: str):
        super().__init__(
            "UNSUPPORTED_PLATFORM",
            "This URL is not from a currently supported e-commerce platform.",
            log_detail=f"unsupported url={url!r}",
        )


class DataAccessUnavailable(TrustAnalyticsError):
    def __init__(self, platform: str, provider: str, reason: str | None = None):
        msg = (
            "Reviews could not be retrieved from this platform using the currently "
            "configured data provider."
        )
        super().__init__(
            "DATA_ACCESS_UNAVAILABLE",
            msg,
            log_detail=f"platform={platform} provider={provider} reason={reason}",
        )
        self.platform = platform
        self.provider = provider
        self.reason = reason
