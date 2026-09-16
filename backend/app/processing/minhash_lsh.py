from collections import defaultdict
import hashlib
import math
import random
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# 61-bit Mersenne prime for universal hashing
MERSENNE_PRIME = (1 << 61) - 1
MAX_HASH = (1 << 32) - 1


class MinHash:
    """MinHash signature generator for estimating Jaccard similarity between document sets."""

    def __init__(self, num_perm: int = 128, seed: int = 42):
        self.num_perm = num_perm
        self.seed = seed
        self.permutations = self._generate_hash_params(num_perm, seed)
        self.digest = [MAX_HASH] * num_perm

    @staticmethod
    def _generate_hash_params(num_perm: int, seed: int) -> List[Tuple[int, int]]:
        """Generate deterministic (a, b) parameters for universal hashing."""
        gen = random.Random(seed)
        params = []
        for _ in range(num_perm):
            a = gen.randint(1, MERSENNE_PRIME - 1)
            b = gen.randint(0, MERSENNE_PRIME - 1)
            params.append((a, b))
        return params

    @staticmethod
    def _hash_shingle(shingle: str) -> int:
        """Convert shingle string to a 32-bit unsigned integer hash."""
        digest = hashlib.sha1(shingle.encode("utf-8")).digest()
        return int.from_bytes(digest[:4], byteorder="little")

    def update(self, shingles: Iterable[str]) -> "MinHash":
        """Update MinHash signature with shingles."""
        for shingle in shingles:
            hv = self._hash_shingle(shingle)
            for i in range(self.num_perm):
                a, b = self.permutations[i]
                permuted = (a * hv + b) % MERSENNE_PRIME
                hash_val = permuted & MAX_HASH
                if hash_val < self.digest[i]:
                    self.digest[i] = hash_val
        return self

    def jaccard(self, other: "MinHash") -> float:
        """Estimate Jaccard similarity between this signature and another."""
        if self.num_perm != other.num_perm:
            raise ValueError("Signatures must have the same number of permutations")
        equal_count = sum(1 for i in range(self.num_perm) if self.digest[i] == other.digest[i])
        return equal_count / self.num_perm

    def copy(self) -> "MinHash":
        m = MinHash(num_perm=self.num_perm, seed=self.seed)
        m.digest = list(self.digest)
        return m


class MinHashLSH:
    """Locality-Sensitive Hashing (LSH) index for fast approximate nearest-neighbor search."""

    def __init__(
        self,
        threshold: float = 0.75,
        num_perm: int = 128,
        weights: Tuple[float, float] = (0.5, 0.5),
    ):
        self.threshold = threshold
        self.num_perm = num_perm
        self.b, self.r = self._compute_bands_and_rows(threshold, num_perm, weights)
        # HashTable per band: band_idx -> hash_value -> list of keys
        self.hashtables: List[Dict[int, List[Any]]] = [defaultdict(list) for _ in range(self.b)]
        self.keys: Dict[Any, MinHash] = {}

    @staticmethod
    def _compute_bands_and_rows(
        threshold: float,
        num_perm: int,
        weights: Tuple[float, float],
    ) -> Tuple[int, int]:
        """Compute optimal band (b) and row (r) sizes to approximate the target threshold."""
        best_b, best_r = 1, num_perm
        min_error = float("inf")

        for b in range(1, num_perm + 1):
            if num_perm % b != 0:
                continue
            r = num_perm // b
            # S-curve approximation: (1/b)^(1/r)
            approx_thresh = math.pow(1.0 / b, 1.0 / r)
            error = abs(approx_thresh - threshold)
            if error < min_error:
                min_error = error
                best_b, best_r = b, r

        return best_b, best_r

    def insert(self, key: Any, minhash: MinHash):
        """Insert a key and its MinHash signature into the LSH index."""
        if minhash.num_perm != self.num_perm:
            raise ValueError(f"MinHash must have {self.num_perm} permutations")
        self.keys[key] = minhash

        for band_idx in range(self.b):
            start = band_idx * self.r
            end = start + self.r
            band_slice = tuple(minhash.digest[start:end])
            band_hash = hash(band_slice)
            self.hashtables[band_idx][band_hash].append(key)

    def query(self, minhash: MinHash) -> List[Any]:
        """Find keys of items whose estimated Jaccard similarity is >= threshold."""
        candidates: Set[Any] = set()
        for band_idx in range(self.b):
            start = band_idx * self.r
            end = start + self.r
            band_slice = tuple(minhash.digest[start:end])
            band_hash = hash(band_slice)
            bucket = self.hashtables[band_idx].get(band_hash, [])
            candidates.update(bucket)

        results = []
        for cand_key in candidates:
            cand_minhash = self.keys[cand_key]
            if minhash.jaccard(cand_minhash) >= self.threshold:
                results.append(cand_key)
        return results
