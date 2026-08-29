from src.providers.catalog_fallback import CatalogFallbackProvider


class CromaProvider(CatalogFallbackProvider):
    def __init__(self):
        super().__init__("croma", "Croma")
