# Publishing agent-swarm to PyPI

This document is for maintainers. Skip if you're just using the package.

## Pre-publish checklist

- [ ] All tests pass: `pytest tests/ -v` (35/35 expected)
- [ ] CHANGELOG.md updated with new version section
- [ ] Version bumped in `pyproject.toml` AND `agent_swarm/__init__.py` (`__version__`)
- [ ] No uncommitted changes: `git status` clean
- [ ] Tag created: `git tag v<X.Y.Z>` and pushed: `git push --tags`

## Build distributions

```bash
# Clean prior builds
rm -rf dist/ build/ *.egg-info/

# Install build tool if not present
pip install build

# Build wheel + sdist
python -m build

# Verify dist contents
ls dist/
# expected: agent_swarm-X.Y.Z-py3-none-any.whl + agent_swarm-X.Y.Z.tar.gz
```

## Publish to TestPyPI first (recommended on first publish)

```bash
pip install twine

# Upload to test index
twine upload --repository testpypi dist/*

# Verify install from TestPyPI
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ agent-swarm

# Smoke test
agent-swarm --help
agent-swarm backends
```

## Publish to PyPI

Once TestPyPI verifies clean:

```bash
twine upload dist/*
# Authenticate via API token from https://pypi.org/manage/account/token/
# (or use trusted publishers via GitHub Actions OIDC — see PEP 740)
```

## After publish

- [ ] Verify on https://pypi.org/project/agent-swarm/
- [ ] Test install in fresh venv: `pip install agent-swarm`
- [ ] Update README install instructions if needed
- [ ] Announce / link from any consuming projects

## Trusted publisher (recommended for v0.3.1+)

Configure a Trusted Publisher on PyPI pointed at this GitHub Actions workflow:
- Owner: `xaxiuegg`
- Repository: `agent-swarm`
- Workflow: `release.yml` (TODO: add this when ready)
- Environment: `pypi` (optional, recommended for protection rules)

Once configured, no PyPI tokens needed in GitHub secrets — the workflow authenticates via OIDC.

## Cross-project use

After publishing, downstream projects can depend on `agent-swarm`:

```toml
# pyproject.toml of consuming project
dependencies = [
    "agent-swarm>=0.3.0",
]
```

Or for editable development:

```bash
pip install -e ../agent-swarm
```

The Maersk Warehouse Planner project is currently using a local copy at
`infrastructure/kimi-swarm/`. After publish + `v0.3.0` is on PyPI, that
project can switch to `pip install agent-swarm` and remove the embedded
copy. Until then, keep the embedded copy in sync via:

```bash
# From warehouse repo root:
cp -r ../agent-swarm/agent_swarm/* infrastructure/kimi-swarm/agent_swarm/
cp ../agent-swarm/pyproject.toml infrastructure/kimi-swarm/pyproject.toml
# Then test + commit warehouse-side
```
