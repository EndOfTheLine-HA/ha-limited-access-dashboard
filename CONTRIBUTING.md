# Contributing

Bug reports, documentation fixes, tests, and narrowly scoped pull requests are
welcome. Security-sensitive changes should include abuse cases and tests.

```shell
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"  # Windows
.venv/Scripts/pytest -q
```

Do not include real webhook IDs, entity IDs, addresses, credentials, reservation
details, or exported Home Assistant configuration in issues or commits.

