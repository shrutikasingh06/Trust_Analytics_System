from src.providers.catalog_fallback import CatalogFallbackProvider


class MeeshoProvider(CatalogFallbackProvider):
    def __init__(self):
        super().__init__("meesho", "Meesho")
