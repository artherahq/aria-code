# Examples

These examples are lightweight entrypoints for testing Aria Code workflows after
installing from source or npm.

## CLI

```bash
aria /doctor
aria "分析苹果股票和走势"
aria "/ta AAPL"
aria "/backtest momentum AAPL --period 1y"
```

## SDK Direction

The SDK/runtime migration should make workflows callable without importing
`aria_cli.py` directly:

```python
from packages.aria_sdk import AriaClient

client = AriaClient()
result = client.run("分析苹果股票和走势")
print(result.text)
```

The exact SDK surface is still evolving. Keep examples small and update them
when the stable client API changes.
