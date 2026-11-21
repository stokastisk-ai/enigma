# Stokastisk integration

Pinned target: stokastisk 0.1.0 **build 5** (SHA256 `fe80d7c3…ae0b3e`), vLLM 0.25.1, Python 3.13, Kaggle 2xT4. Build 5 is CPU-audited only; Kaggle evidence is inherited from withdrawn build 3 (Qwen2.5-0.5B-Instruct, TP=1, 2048 ctx, HTTP server stage 21/21).

The connector uses only public SDK APIs: `ServerProcess` (started explicitly by the notebook), `serving.stream_chat`, and the server's `/metrics`. It never imports vLLM or torch and never modifies the SDK. `tests/connector/test_sdk_integration.py` imports the frozen SDK source read-only (no bytecode written) and drives the real `stream_chat` against a fake OpenAI-style server.

Notes: `stream_chat` yields content chunks, not token counts, so Enigma reports **chunks** and shows output tokens as "not reported". Closing the generator closes the SSE socket, which is how Stop reaches vLLM. Run the notebook at `docs/kaggle/enigma_connector_kaggle.ipynb` yourself (it ends with the GPU acceptance checklist). Only Qwen2.5-0.5B TP=1 is a tested HTTP configuration; TP=2/PP=2 serving and other models need separate labelled tests.


## Local GPU isolation (2026-11-21, owner rule)
The development laptop's NVIDIA GPU is never used. Enforced in three layers: `tests/conftest.py` blocks importing `pynvml`/`torch`/`vllm`/CUDA libraries and executing `nvidia-smi`; `ENIGMA_FORBID_LOCAL_GPU=1` is set for tests, the smoke script and the Playwright servers and makes `NvmlSampler()` and `--mode stokastisk` refuse before any NVML call; the Kaggle notebook refuses to run outside `/kaggle` and requires exactly 2 Tesla T4 (SM75). Real-GPU tests are marked remote-only (`ENIGMA_REMOTE_GPU_HOST=1`, set by the notebook). Covered by `tests/test_gpu_isolation.py`.
