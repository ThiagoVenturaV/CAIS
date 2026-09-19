from __future__ import annotations

import math
import re
import time
import unicodedata
from collections import Counter
from typing import Any

import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import Normalizer

from ..database import Database


TOKEN_PATTERN = re.compile(r"[a-z0-9]{2,}")


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return " ".join(TOKEN_PATTERN.findall(text))


class HybridSearch:
    def __init__(self, database: Database, lexical_weight: float = 0.45):
        self.database = database
        self.lexical_weight = lexical_weight
        self.semantic_weight = 1 - lexical_weight

    def search(
        self, query: str, *, filters: dict[str, Any] | None = None, top_k: int = 5
    ) -> dict[str, Any]:
        started = time.perf_counter()
        documents = self._filter_documents(self.database.search_documents(), filters or {})
        if not documents:
            return {"results": [], "strategy": "bm25+lsa", "elapsed_ms": 0, "documents_considered": 0}

        corpus = [f"{doc['title']} {doc['content']}" for doc in documents]
        lexical = self._bm25(query, corpus)
        semantic = self._semantic(query, corpus)
        lexical = self._scale(lexical)
        semantic = self._scale(semantic)
        combined = self.lexical_weight * lexical + self.semantic_weight * semantic
        ranking = np.argsort(combined)[::-1][:top_k]

        results: list[dict[str, Any]] = []
        for index in ranking:
            score = float(combined[index])
            if score <= 0 and results:
                continue
            document = documents[int(index)]
            content = str(document["content"])
            results.append(
                {
                    "document_id": document["id"],
                    "title": document["title"],
                    "source": document["source"],
                    "score": round(score, 4),
                    "lexical_score": round(float(lexical[index]), 4),
                    "semantic_score": round(float(semantic[index]), 4),
                    "snippet": content[:520] + ("…" if len(content) > 520 else ""),
                    "metadata": document.get("metadata", {}),
                }
            )

        return {
            "results": results,
            "strategy": "bm25+lsa",
            "weights": {"lexical": self.lexical_weight, "semantic": self.semantic_weight},
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "documents_considered": len(documents),
        }

    @staticmethod
    def _filter_documents(
        documents: list[dict[str, Any]], filters: dict[str, Any]
    ) -> list[dict[str, Any]]:
        active = {key: value for key, value in filters.items() if value not in (None, "")}
        if not active:
            return documents
        filtered: list[dict[str, Any]] = []
        for document in documents:
            metadata = document.get("metadata", {})
            matches = True
            for key, value in active.items():
                current = str(metadata.get(key, "")).lower()
                expected = str(value).lower()
                if key == "local":
                    matches = expected in current
                else:
                    matches = current == expected
                if not matches:
                    break
            if matches:
                filtered.append(document)
        return filtered

    @staticmethod
    def _bm25(query: str, corpus: list[str], k1: float = 1.5, b: float = 0.75) -> np.ndarray:
        tokenized = [_normalize(text).split() for text in corpus]
        query_tokens = _normalize(query).split()
        if not query_tokens:
            return np.zeros(len(corpus))
        lengths = [len(tokens) for tokens in tokenized]
        average_length = sum(lengths) / max(len(lengths), 1)
        frequencies = [Counter(tokens) for tokens in tokenized]
        scores = np.zeros(len(corpus), dtype=float)
        for term in query_tokens:
            document_frequency = sum(1 for frequency in frequencies if frequency.get(term, 0) > 0)
            idf = math.log(1 + (len(corpus) - document_frequency + 0.5) / (document_frequency + 0.5))
            for index, frequency in enumerate(frequencies):
                tf = frequency.get(term, 0)
                if not tf:
                    continue
                denominator = tf + k1 * (1 - b + b * lengths[index] / max(average_length, 1))
                scores[index] += idf * (tf * (k1 + 1) / denominator)
        return scores

    @staticmethod
    def _semantic(query: str, corpus: list[str]) -> np.ndarray:
        vectorizer = TfidfVectorizer(
            strip_accents="unicode", lowercase=True, ngram_range=(1, 2),
            min_df=1, max_df=1.0, sublinear_tf=True,
        )
        matrix = vectorizer.fit_transform(corpus)
        query_vector = vectorizer.transform([query])
        components = min(64, matrix.shape[0] - 1, matrix.shape[1] - 1)
        if components >= 2:
            svd = TruncatedSVD(n_components=components, random_state=42)
            normalizer = Normalizer(copy=False)
            reduced = normalizer.fit_transform(svd.fit_transform(matrix))
            reduced_query = normalizer.transform(svd.transform(query_vector))
            return cosine_similarity(reduced_query, reduced).ravel()
        return cosine_similarity(query_vector, matrix).ravel()

    @staticmethod
    def _scale(values: np.ndarray) -> np.ndarray:
        if not len(values):
            return values
        minimum = float(values.min())
        maximum = float(values.max())
        if math.isclose(minimum, maximum):
            return np.ones_like(values) if maximum > 0 else np.zeros_like(values)
        return (values - minimum) / (maximum - minimum)
