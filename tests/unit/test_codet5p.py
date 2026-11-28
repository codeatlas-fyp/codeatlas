"""CodeT5+ adapter (CA-02), with a fake backend: no model download, no network."""

import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from codeatlas.analyze.codet5p import CodeT5pEmbedder, EmbedderUnavailable, load_codet5p
from codeatlas.analyze.embedding_cache import EmbeddingCache
from codeatlas.analyze.model_registry import spec


class FakeBackend:
    """One token per character; a window's vector is [len(window), sum(ids) % 7 + 1]."""

    def __init__(self) -> None:
        self.calls: list[list[list[int]]] = []

    def token_ids(self, text: str) -> list[int]:
        return [ord(c) for c in text]

    def embed_ids(self, batch: list[list[int]]) -> np.ndarray[Any, Any]:
        self.calls.append(batch)
        return np.array([[len(w), sum(w) % 7 + 1] for w in batch], dtype=np.float32)


def _small_spec(max_tokens: int = 12):  # type: ignore[no-untyped-def]
    from dataclasses import replace

    return replace(spec("codet5p"), dim=2, max_tokens=max_tokens)


def test_vectors_are_normalised_and_in_input_order() -> None:
    embedder = CodeT5pEmbedder(backend=FakeBackend(), model_spec=_small_spec())
    vecs = embedder.embed(["ab", "abcd"])
    assert vecs.shape == (2, 2)
    assert np.allclose(np.linalg.norm(vecs, axis=1), 1.0)
    assert not np.allclose(vecs[0], vecs[1])


def test_long_text_is_split_into_windows_not_truncated() -> None:
    backend = FakeBackend()
    embedder = CodeT5pEmbedder(backend=backend, model_spec=_small_spec(max_tokens=12))
    embedder.embed(["x" * 25])  # window = 12 - 2 special tokens = 10 -> 10, 10, 5
    windows = [w for batch in backend.calls for w in batch]
    assert [len(w) for w in windows] == [10, 10, 5]
    assert sum(len(w) for w in windows) == 25
    assert (embedder.stats.split_texts, embedder.stats.windows) == (1, 3)


def test_cache_skips_the_model_on_the_second_run(tmp_path: Path) -> None:
    first = FakeBackend()
    cache = EmbeddingCache(tmp_path, spec("codet5p").model_id)
    one = CodeT5pEmbedder(backend=first, model_spec=_small_spec(), cache=cache).embed(["abc"])
    second = FakeBackend()
    cache2 = EmbeddingCache(tmp_path, spec("codet5p").model_id)
    two = CodeT5pEmbedder(backend=second, model_spec=_small_spec(), cache=cache2).embed(["abc"])
    assert second.calls == []
    assert np.allclose(one, two)


def test_empty_input_gives_an_empty_matrix() -> None:
    embedder = CodeT5pEmbedder(backend=FakeBackend(), model_spec=_small_spec())
    assert embedder.embed([]).shape == (0, 2)


def test_model_id_is_the_pinned_revision() -> None:
    embedder = CodeT5pEmbedder(backend=FakeBackend())
    assert embedder.model_id == f"Salesforce/codet5p-110m-embedding@{spec('codet5p').revision}"


def test_missing_transformers_is_unavailable_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "transformers", None)
    monkeypatch.setitem(sys.modules, "torch", None)
    with pytest.raises(EmbedderUnavailable, match="missing package"):
        load_codet5p(local_files_only=True)


def test_model_not_in_local_cache_is_unavailable(tmp_path: Path) -> None:
    with pytest.raises(EmbedderUnavailable):
        load_codet5p(cache_dir=tmp_path, local_files_only=True)


# ---- the real backend, with fake tokenizer / model / torch ----------------------------------


class FakeTensor:
    def __init__(self, data: Any) -> None:
        self.data = np.asarray(data, dtype=np.float32)

    def detach(self) -> "FakeTensor":
        return self

    def cpu(self) -> "FakeTensor":
        return self

    def numpy(self) -> Any:
        return self.data


class FakeTorch:
    @staticmethod
    def tensor(data: Any) -> Any:
        return np.asarray(data)

    @staticmethod
    def no_grad() -> Any:
        import contextlib

        return contextlib.nullcontext()


class FakeTokenizer:
    pad_token_id = 0

    def encode(self, text: str, add_special_tokens: bool = True) -> list[int]:
        ids = [ord(c) for c in text]
        return [1, *ids, 2] if add_special_tokens else ids


class FakeModel:
    def __init__(self) -> None:
        self.seen: list[tuple[Any, Any]] = []

    def __call__(self, input_ids: Any, attention_mask: Any) -> FakeTensor:
        self.seen.append((input_ids, attention_mask))
        return FakeTensor([[float(m.sum()), 1.0] for m in attention_mask])

    def eval(self) -> None:
        pass


def test_hf_backend_adds_special_tokens_pads_and_masks() -> None:
    from codeatlas.analyze.codet5p import _HfBackend

    model = FakeModel()
    backend = _HfBackend(FakeTokenizer(), model, FakeTorch())
    assert backend.token_ids("ab") == [97, 98]
    vecs = backend.embed_ids([[97], [97, 98, 99]])
    input_ids, mask = model.seen[0]
    assert input_ids.tolist() == [[1, 97, 2, 0, 0], [1, 97, 98, 99, 2]]
    assert mask.tolist() == [[1, 1, 1, 0, 0], [1, 1, 1, 1, 1]]
    assert vecs[:, 0].tolist() == [3.0, 5.0]


def _fake_hub(tmp_path: Path, calls: dict[str, Any]) -> Any:
    import types

    def snapshot_download(repo_id: str, **kwargs: Any) -> str:
        calls["repo_id"], calls["revision"] = repo_id, kwargs["revision"]
        return str(tmp_path)

    hub = types.ModuleType("huggingface_hub")
    hub.snapshot_download = snapshot_download  # type: ignore[attr-defined]
    return hub


def _fake_transformers(model_loader: Any) -> Any:
    import types

    transformers = types.ModuleType("transformers")
    transformers.AutoTokenizer = types.SimpleNamespace(  # type: ignore[attr-defined]
        from_pretrained=lambda path, trust_remote_code: FakeTokenizer()
    )
    transformers.AutoConfig = types.SimpleNamespace(  # type: ignore[attr-defined]
        from_pretrained=lambda path, trust_remote_code: types.SimpleNamespace(d_model=4)
    )
    transformers.T5Config = lambda **kw: types.SimpleNamespace(is_decoder=False, d_ff=8, **kw)  # type: ignore[attr-defined]
    transformers.AutoModel = types.SimpleNamespace(from_pretrained=model_loader)  # type: ignore[attr-defined]
    return transformers


def test_hf_backend_with_a_suffix_only_tokenizer() -> None:
    from codeatlas.analyze.codet5p import _HfBackend

    class EosOnly(FakeTokenizer):
        def encode(self, text: str, add_special_tokens: bool = True) -> list[int]:
            ids = [ord(c) for c in text]
            return [*ids, 2] if add_special_tokens else ids

    model = FakeModel()
    _HfBackend(EosOnly(), model, FakeTorch()).embed_ids([[97]])
    assert model.seen[0][0].tolist() == [[97, 2]]


def test_load_uses_the_pinned_revision_and_hashes_weights(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "model.safetensors").write_bytes(b"weights")
    calls: dict[str, Any] = {}
    seen_config: list[Any] = []

    def load_model(path: Any, config: Any, trust_remote_code: bool) -> FakeModel:
        seen_config.append(config)
        return FakeModel()

    monkeypatch.setitem(sys.modules, "huggingface_hub", _fake_hub(tmp_path, calls))
    monkeypatch.setitem(sys.modules, "transformers", _fake_transformers(load_model))
    monkeypatch.setitem(sys.modules, "torch", FakeTorch())

    embedder = load_codet5p(embedding_cache_dir=tmp_path / "cache")

    assert calls == {"repo_id": spec("codet5p").repo_id, "revision": spec("codet5p").revision}
    assert embedder.weights is not None and embedder.weights[0] == "model.safetensors"
    assert (embedder.load_mode, embedder.load_note) == ("documented", None)
    # T5 defaults that transformers 5 no longer sets were added to the remote config.
    assert seen_config[0].is_decoder is False and seen_config[0].d_ff == 8
    assert seen_config[0].d_model == 4  # existing values are kept
    assert embedder.cache is not None
    assert embedder.embed(["abc"]).shape == (1, 2)


def test_broken_remote_code_falls_back_to_native_t5(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import codeatlas.analyze.codet5p as module

    def broken(path: Any, config: Any, trust_remote_code: bool) -> Any:
        raise TypeError("T5Stack.__init__() takes 2 positional arguments but 3 were given")

    monkeypatch.setitem(sys.modules, "huggingface_hub", _fake_hub(tmp_path, {}))
    monkeypatch.setitem(sys.modules, "transformers", _fake_transformers(broken))
    monkeypatch.setitem(sys.modules, "torch", FakeTorch())
    monkeypatch.setattr(module, "_load_native", lambda snapshot, torch, transformers: FakeModel())

    embedder = load_codet5p()

    assert embedder.load_mode == "native-t5"
    assert embedder.load_note is not None and "T5Stack" in embedder.load_note


def test_both_loaders_failing_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import codeatlas.analyze.codet5p as module

    def broken(path: Any, config: Any, trust_remote_code: bool) -> Any:
        raise TypeError("remote code broke")

    def no_native(snapshot: Any, torch: Any, transformers: Any) -> Any:
        raise RuntimeError("weights do not fit")

    monkeypatch.setitem(sys.modules, "huggingface_hub", _fake_hub(tmp_path, {}))
    monkeypatch.setitem(sys.modules, "transformers", _fake_transformers(broken))
    monkeypatch.setitem(sys.modules, "torch", FakeTorch())
    monkeypatch.setattr(module, "_load_native", no_native)

    with pytest.raises(EmbedderUnavailable, match="remote code broke.*weights do not fit"):
        load_codet5p()


def _tiny_codet5p_snapshot(folder: Path) -> tuple[Any, Any]:
    """A 2-layer T5 encoder + projection saved with the real model's key names."""
    import json

    torch = pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    safetensors_torch = pytest.importorskip("safetensors.torch")
    config = {
        "vocab_size": 50,
        "d_model": 16,
        "d_kv": 4,
        "d_ff": 32,
        "num_layers": 2,
        "num_heads": 4,
        "relative_attention_num_buckets": 8,
        "relative_attention_max_distance": 16,
        "dropout_rate": 0.0,
        "feed_forward_proj": "relu",
        "embed_dim": 8,
    }
    (folder / "config.json").write_text(json.dumps(config), encoding="utf-8")
    torch.manual_seed(0)
    t5 = transformers.T5EncoderModel(
        transformers.T5Config(**{k: v for k, v in config.items() if k != "embed_dim"})
    )
    proj = torch.nn.Linear(16, 8)
    state = {k: v.contiguous() for k, v in t5.state_dict().items()}
    state.pop("encoder.embed_tokens.weight")  # tied to shared.weight, as in the real file
    state["proj.weight"] = proj.weight.detach().contiguous()
    state["proj.bias"] = proj.bias.detach().contiguous()
    safetensors_torch.save_file(state, str(folder / "model.safetensors"))
    return t5, proj


def test_native_loader_matches_the_remote_code_forward_pass(tmp_path: Path) -> None:
    from codeatlas.analyze.codet5p import _load_native

    t5, proj = _tiny_codet5p_snapshot(tmp_path)
    import torch
    import transformers

    model = _load_native(tmp_path, torch, transformers)
    model.eval()
    t5.eval()
    ids = torch.tensor([[1, 5, 6, 2], [1, 9, 2, 0]])
    mask = torch.tensor([[1, 1, 1, 1], [1, 1, 1, 0]])
    with torch.no_grad():
        got = model(ids, attention_mask=mask)
        hidden = t5(input_ids=ids, attention_mask=mask).last_hidden_state[:, 0, :]
        want = torch.nn.functional.normalize(proj(hidden), dim=-1)
    assert got.shape == (2, 8)
    assert torch.allclose(got, want, atol=1e-6)


def test_native_loader_refuses_weights_of_another_shape(tmp_path: Path) -> None:
    from codeatlas.analyze.codet5p import _load_native

    _tiny_codet5p_snapshot(tmp_path)
    import json

    import torch
    import transformers

    config = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    config["num_layers"] = 3  # the file only holds 2 layers
    (tmp_path / "config.json").write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(RuntimeError, match="do not fit a T5 encoder"):
        _load_native(tmp_path, torch, transformers)
