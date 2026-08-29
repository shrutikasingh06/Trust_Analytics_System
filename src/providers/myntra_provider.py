from src.providers.catalog_fallback import CatalogFallbackProvider


class MyntraProvider(CatalogFallbackProvider):
    def __init__(self):
        super().__init__("myntra", "Myntra")
