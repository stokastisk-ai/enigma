# Enigma × Kaggle notebooks: three, build 5 only

The six-notebook diagnostic set and the earlier isolation cells are **superseded** (they remain in git history). Build 6 was discarded; the immutable
`stokastisk-0.1.0-5-cp313-cp313-linux_x86_64.whl` is the only supported artifact.

Notebooks are generated into [`diagnostics/`](diagnostics/) by `scripts/build_kaggle_diagnostic_notebooks.py` and copied to the directory you run Kaggle
notebooks from with `scripts/sync_kaggle_notebooks.py --dest <dir>`. Every step ends with `VERDICT <step>: PASS | FAIL | INCONCLUSIVE`; anything but
PASS stops the run. Prerequisites are accepted only from the current session.

| # | Notebook | Accelerator | Purpose |
|---|---|---|---|
| 01 | `01_enigma_kaggle_platform_aws_diagnostics.ipynb` | None | standard library only: idle survival, public HTTPS, the independent AWS diagnostic endpoint, Railway health, each followed by a survival window |
| 02 | `02_stokastisk_build5_kaggle_t4_validation.ipynb` | T4 ×2 | build 5 verified by size + SHA-256, real local Qwen inference, native vLLM metrics, 10-min server observation; no Enigma |
| 03 | `03_enigma_stokastisk_build5_aws_integration.ipynb` | T4 ×2 | the full Enigma integration through the supervised connector, with one real Playground job (run after 01 and 02 pass) |

Evidence durability: markers are flushed to the cell output, fsynced locally, and (with the `ENIGMA_DIAG_KEY` Kaggle Secret) persisted to the independent
AWS endpoint, which keeps the last acknowledged marker if the Kaggle VM disappears. Missing files do not prove that no crash occurred.
