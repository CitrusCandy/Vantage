from abc import ABC, abstractmethod
from typing import List

from app.ingestion.schemas import IngestedItem


class BaseIngestor(ABC):
    """Abstract base class defining interface for all data source ingestors."""

    source_name: str

    @abstractmethod
    def fetch_items(
        self,
        query: str,
        limit: int = 25,
        timeout_seconds: float = 10.0,
    ) -> List[IngestedItem]:
        """Fetch and normalize discourse items from the underlying data source."""
        pass
