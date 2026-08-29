from src.providers.catalog_fallback import CatalogFallbackProvider


class AjioProvider(CatalogFallbackProvider):
    def __init__(self):
        super().__init__("ajio", "Ajio")
