from src.providers.catalog_fallback import CatalogFallbackProvider


class TataCliqProvider(CatalogFallbackProvider):
    def __init__(self):
        super().__init__("tata_cliq", "Tata CLiQ")
