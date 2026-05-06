# Publishing xaxiu-swarm to PyPI

This document is for maintainers. Skip if you're just using the package.

## Pre-publish checklist

- [ ] All tests pass: `pytest tests/ -v` (35/35 expected)
- [ ] CHANGELOG.md updated with new version section
- [ ] Version bumped in `pyproject.toml` AND `xaxiu_swarm/__init__.py` (`__version__`)
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
# expected: xaxiu_swarm-X.Y.Z-py3-none-any.whl + xaxiu_swarm-X.Y.Z.tar.gz
```

## Publish to TestPyPI first (recommended on first publish)

```bash
pip install twine

# Upload to test index
twine upload --repository testpypi dist/*

# Verify install from TestPyPI
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ xaxiu-swarm

# Smoke test
xaxiu-swarm --help
xaxiu-swarm backends
```

## Publish to PyPI

Once TestPyPI verifies clean:

```bash
twine upload dist/*
# Authenticate via API token from https://pypi.org/manage/account/token/
# (or use trusted publishers via GitHub Actions OIDC — see PEP 740)
```

## After publish

- [ ] Verify on https://pypi.org/project/xaxiu-swarm/
- [ ] Test install in fresh venv: `pip install xaxiu-swarm`
- [ ] Update README install instructions if needed
- [ ] Announce / link from any consuming projects

## Trusted publisher (recommended for v0.3.1+)

Configure a Trusted Publisher on PyPI pointed at this GitHub Actions workflow:
- Owner: `xaxiuegg`
- Repository: `xaxiu-swarm`
- Workflow: `release.yml` (TODO: add this when ready)
- Environment: `pypi` (optional, recommended for protection rules)

Once configured, no PyPI tokens needed in GitHub secrets — the workflow authenticates via OIDC.

## Cross-project use

After publishing, downstream projects can depend on `xaxiu-swarm`:

```toml
# pyproject.toml of consuming project
dependencies = [
    "xaxiu-swarm>=0.3.0",
]
```

Or for editable development:

```bash
pip install -e ../xaxiu-swarm
```

The Maersk Warehouse Planner project is currently using a local copy at
`infrastructure/kimi-swarm/`. After publish + `v0.3.0` is on PyPI, that
project can switch to `pip install xaxiu-swarm` and remove the embedded
copy. Until then, keep the embedded copy in sync via:

```bash
# From warehouse repo root:
cp -r ../xaxiu-swarm/xaxiu_swarm/* infrastructure/kimi-swarm/xaxiu_swarm/
cp ../xaxiu-swarm/pyproject.toml infrastructure/kimi-swarm/pyproject.toml
# Then test + commit warehouse-side
```
