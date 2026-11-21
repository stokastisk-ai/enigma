"""Write the OpenAPI schema to packages/contracts/openapi.json (source of the generated TypeScript client types)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from enigma_api.main import create_app          # noqa: E402
from enigma_api.settings import Settings        # noqa: E402

app = create_app(Settings(env="test"))
out = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "openapi.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")
print(f"wrote {out}")
