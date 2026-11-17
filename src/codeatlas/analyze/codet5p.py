"""Local adapter for Salesforce/codet5p-110m-embedding (CA-02).

The model is loaded the way its model card documents, not through SentenceTransformer:

    tokenizer = AutoTokenizer.from_pretrained(checkpoint, trust_remote_code=True)
    model = AutoModel.from_pretrained(checkpoint, trust_remote_code=True)
    embedding = model(inputs)[0]          # 256-d, L2-normalised

If that remote code cannot run on the installed transformers, the same pinned weights are
loaded into transformers' own T5 encoder with the same projection (`load_mode="native-t5"`),
and the reason is kept in `load_note` so the run reports it as a fallback.

`trust_remote_code` runs Python shipped with the model, so the revision is always pinned to
the commit in `model_registry` and never follows the moving `main` branch.

No hidden truncation: a text longer than the model's 512-token limit is cut into consecutive
token windows, each window is embedded, and the text's vector is the normalised mean of its
windows. `stats.split_texts` counts how often that happened, so a run can report it.

Vectors are cached on disk (`embedding_cache`). Everything that can fail (no `transformers`,
no `torch`, no network on first use, a broken model file) raises `EmbedderUnavailable` with a
reason; the ranker then records the CodeT5+ ranker as unavailable instead of crashing.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from codeatlas.analyze.embedding_cache import EmbeddingCache, Vector
from codeatlas.analyze.model_registry import ModelSpec, spec, weights_sha256

SPECIAL_TOKENS = 2  # <s> and </s> around every window
BATCH_SIZE = 16


class EmbedderUnavailable(Exception):
    """The model cannot be used in this run; the message says why."""


class Backend(Protocol):
    """The two things the adapter needs from a tokenizer + model pair."""

    def token_ids(self, text: str) -> list[int]:
        """Token ids of `text`, without special tokens."""
        ...

    def embed_ids(self, batch: list[list[int]]) -> np.ndarray[Any, Any]:
        """One vector per window; the backend adds special tokens and padding."""
        ...


@dataclass
class EmbedStats:
    texts: int = 0
    split_texts: int = 0
    windows: int = 0


class CodeT5pEmbedder:
    def __init__(
        self,
        *,
        backend: Backend,
        model_spec: ModelSpec | None = None,
        cache: EmbeddingCache | None = None,
        weights: tuple[str, str] | None = None,
    ) -> None:
        self.spec = model_spec or spec("codet5p")
        self.backend = backend
        self.cache = cache
        self.weights = weights  # (file name, sha256) when loaded from a real snapshot
        self.stats = EmbedStats()
        self.load_mode = "injected"  # "documented" or "native-t5" when loaded by load_codet5p
        self.load_note: str | None = None

    @property
    def model_id(self) -> str:
        return self.spec.model_id

    def embed(self, texts: Sequence[str]) -> np.ndarray[Any, Any]:
        """L2-normalised vectors, one row per text, in input order."""
        out: list[Vector | None] = []
        todo: list[int] = []
        for i, text in enumerate(texts):
            cached = self.cache.get(text) if self.cache is not None else None
            out.append(cached)
            if cached is None:
                todo.append(i)
        self.stats.texts += len(texts)

        windows: list[list[int]] = []
        owner: list[int] = []
        window_len = self.spec.max_tokens - SPECIAL_TOKENS
        for i in todo:
            ids = self.backend.token_ids(texts[i]) or [0]
            parts = [ids[s : s + window_len] for s in range(0, len(ids), window_len)]
            if len(parts) > 1:
                self.stats.split_texts += 1
            windows.extend(parts)
            owner.extend([i] * len(parts))
        self.stats.windows += len(windows)

        sums: dict[int, Vector] = {}
        for start in range(0, len(windows), BATCH_SIZE):
            vecs = np.asarray(
                self.backend.embed_ids(windows[start : start + BATCH_SIZE]), dtype=np.float32
            )
            for row, i in zip(vecs, owner[start : start + BATCH_SIZE], strict=True):
                sums[i] = sums[i] + row if i in sums else row.copy()
        for i in todo:
            vec = _normalise(sums[i])
            out[i] = vec
            if self.cache is not None:
                self.cache.put(texts[i], vec)

        if not texts:
            return np.zeros((0, self.spec.dim), dtype=np.float32)
        return np.stack([v for v in out if v is not None])


def _normalise(vec: Vector) -> Vector:
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec


# ---- the real backend ------------------------------------------------------------------------


class _HfBackend:
    def __init__(self, tokenizer: Any, model: Any, torch: Any) -> None:
        self.tokenizer = tokenizer
        self.model = model
        self.torch = torch
        # The tokenizer's own special-token wrapping, read from an empty input: [<s>, </s>] for
        # CodeT5+. transformers 5 removed build_inputs_with_special_tokens, encode("") works in
        # both 4 and 5.
        wrap = list(tokenizer.encode("", add_special_tokens=True))
        self.prefix, self.suffix = (wrap[:1], wrap[1:]) if len(wrap) >= 2 else ([], wrap)

    def token_ids(self, text: str) -> list[int]:
        return list(self.tokenizer.encode(text, add_special_tokens=False))

    def embed_ids(self, batch: list[list[int]]) -> np.ndarray[Any, Any]:
        with_special = [[*self.prefix, *ids, *self.suffix] for ids in batch]
        width = max(len(ids) for ids in with_special)
        pad = self.tokenizer.pad_token_id or 0
        input_ids = [ids + [pad] * (width - len(ids)) for ids in with_special]
        mask = [[1] * len(ids) + [0] * (width - len(ids)) for ids in with_special]
        with self.torch.no_grad():
            vecs = self.model(self.torch.tensor(input_ids), attention_mask=self.torch.tensor(mask))
        return np.asarray(vecs.detach().cpu().numpy(), dtype=np.float32)


def load_codet5p(
    *,
    cache_dir: Path | None = None,
    embedding_cache_dir: Path | None = None,
    local_files_only: bool = False,
) -> CodeT5pEmbedder:
    """Load the pinned CodeT5+ revision; raise EmbedderUnavailable with a reason on failure.

    First the documented `AutoModel` + remote-code path. Its code was written for transformers
    4, so the config is given the T5 defaults that transformers 5 no longer adds by itself
    (for example `is_decoder`). If that path still fails, the same weights are loaded into
    transformers' own `T5EncoderModel` plus the 768->256 projection, which is the architecture
    the remote code defines; the embedder then reports `load_mode="native-t5"` and why.
    """
    model_spec = spec("codet5p")
    try:
        import torch
        import transformers
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise EmbedderUnavailable(f"missing package: {exc.name}") from exc
    try:
        snapshot = Path(
            snapshot_download(
                model_spec.repo_id,
                revision=model_spec.revision,
                cache_dir=cache_dir,
                local_files_only=local_files_only,
            )
        )
        tokenizer = transformers.AutoTokenizer.from_pretrained(snapshot, trust_remote_code=True)
    except Exception as exc:  # any load failure means "unavailable", and says why
        raise EmbedderUnavailable(f"{type(exc).__name__}: {exc}") from exc

    load_mode, load_note = "documented", None
    try:
        model: Any = _load_documented(snapshot, transformers)
    except Exception as documented_exc:  # remote code broke: try the native architecture
        load_note = f"documented loader failed ({type(documented_exc).__name__}: {documented_exc})"
        try:
            model = _load_native(snapshot, torch, transformers)
        except Exception as exc:
            raise EmbedderUnavailable(f"{load_note}; native loader failed ({exc})") from exc
        load_mode = "native-t5"
    model.eval()

    cache = (
        EmbeddingCache(embedding_cache_dir, model_spec.model_id)
        if embedding_cache_dir is not None
        else None
    )
    embedder = CodeT5pEmbedder(
        backend=_HfBackend(tokenizer, model, torch),
        model_spec=model_spec,
        cache=cache,
        weights=weights_sha256(snapshot),
    )
    embedder.load_mode, embedder.load_note = load_mode, load_note
    return embedder


def _load_documented(snapshot: Path, transformers: Any) -> Any:
    config = transformers.AutoConfig.from_pretrained(snapshot, trust_remote_code=True)
    for name, value in vars(transformers.T5Config()).items():
        if not name.startswith("_") and not hasattr(config, name):
            setattr(config, name, value)
    config.is_decoder = False
    config.use_cache = False
    return transformers.AutoModel.from_pretrained(snapshot, config=config, trust_remote_code=True)


# Keys of config.json that describe the T5 encoder; everything else (embed_dim, auto_map) is
# read separately or not needed.
_T5_KEYS = (
    "vocab_size",
    "d_model",
    "d_kv",
    "d_ff",
    "num_layers",
    "num_heads",
    "relative_attention_num_buckets",
    "relative_attention_max_distance",
    "dropout_rate",
    "layer_norm_epsilon",
    "feed_forward_proj",
    "pad_token_id",
    "eos_token_id",
)


def _load_native(snapshot: Path, torch: Any, transformers: Any) -> Any:
    """T5 encoder + linear projection + L2 norm on the first token, from the pinned weights."""
    raw = json.loads((snapshot / "config.json").read_text(encoding="utf-8"))
    config = transformers.T5Config(**{k: raw[k] for k in _T5_KEYS if k in raw})
    encoder = transformers.T5EncoderModel(config)
    state = _read_weights(snapshot, torch)
    proj_weight = state.pop("proj.weight")
    proj_bias = state.pop("proj.bias", None)
    result = encoder.load_state_dict(state, strict=False)
    missing = [k for k in result.missing_keys if k != "encoder.embed_tokens.weight"]
    if missing or result.unexpected_keys:
        raise RuntimeError(
            f"weights do not fit a T5 encoder: missing {missing[:3]}, "
            f"unexpected {list(result.unexpected_keys)[:3]}"
        )
    proj = torch.nn.Linear(proj_weight.shape[1], proj_weight.shape[0], bias=proj_bias is not None)
    with torch.no_grad():
        proj.weight.copy_(proj_weight)
        if proj_bias is not None:
            proj.bias.copy_(proj_bias)
    return _NativeCodeT5p(encoder, proj, torch)


def _read_weights(snapshot: Path, torch: Any) -> dict[str, Any]:
    if (snapshot / "model.safetensors").is_file():
        from safetensors.torch import load_file

        return dict(load_file(str(snapshot / "model.safetensors")))
    return dict(torch.load(snapshot / "pytorch_model.bin", map_location="cpu", weights_only=True))


class _NativeCodeT5p:
    """Same forward pass as the model's remote code: normalise(proj(h[:, 0, :]))."""

    def __init__(self, encoder: Any, proj: Any, torch: Any) -> None:
        self.encoder = encoder
        self.proj = proj
        self.torch = torch

    def eval(self) -> None:
        self.encoder.eval()
        self.proj.eval()

    def __call__(self, input_ids: Any, attention_mask: Any = None) -> Any:
        hidden = self.encoder(input_ids=input_ids, attention_mask=attention_mask)
        first = hidden.last_hidden_state[:, 0, :]
        return self.torch.nn.functional.normalize(self.proj(first), dim=-1)
