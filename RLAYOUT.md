# Experimental rlayout backend branch

This branch adapts kfactory to the standalone Rust-backed `rlayout` package.
Base: upstream `main` at `2b6a057c571be4aba44ecb2cf3999a667f3af15b`
(kfactory 3.2.0). The license and upstream history are preserved.

Development currently happens as `vendor/kfactory` inside the rlayout repository.
Use that repository's `.venv` and `just kfactory-collect` / `just kfactory-test`.
Backend imports use `rlayout.db`, `rlayout.lay`, and `rlayout.rdb`; engine version
checks use `rlayout.__klayout_version__`, not the rlayout package version.
Tests retain their algorithms and assertions; five tests have backend import
changes so their objects come from the same engine as kfactory's objects.

This is an incomplete port. Upstream dependency metadata and `uv.lock` remain
the baseline, including the upstream KLayout dependency: do not install this
branch through that metadata yet. The parent repository runs these sources
directly and rejects imports of the existing `klayout` Python extension.
Kfnetlist >=0.3 must also be adapted before end-to-end extraction can pass.
Package metadata/lockfile changes belong with that dependency adaptation.

Commit consumer changes on `rlayout-backend` and push to `flaport/kfactory`.
Then update and commit the submodule pointer in rlayout. Merge upstream changes
deliberately; a submodule update must not silently move the acceptance baseline.
