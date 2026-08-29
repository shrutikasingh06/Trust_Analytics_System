from src.providers.catalog_fallback import CatalogFallbackProvider


class NykaaProvider(CatalogFallbackProvider):
    def __init__(self):
        super().__init__("nykaa", "Nykaa")
