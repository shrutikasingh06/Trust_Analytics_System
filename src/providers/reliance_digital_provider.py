from src.providers.catalog_fallback import CatalogFallbackProvider


class RelianceDigitalProvider(CatalogFallbackProvider):
    def __init__(self):
        super().__init__("reliance_digital", "Reliance Digital")
