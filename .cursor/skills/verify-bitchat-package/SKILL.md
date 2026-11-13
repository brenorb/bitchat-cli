---
name: verify-bitchat-package
description: Verify the tagged BitChat build and the console entrypoints packaged in its Python wheel.
---

# Verify BitChat release packaging

At `06e5ff8` and the Homebrew-linked `v0.1.0`, `.github/workflows/release.yml` runs for `v*` tags, installs Python 3.13, and uses `uv build` before `uv publish`. The build produces a source distribution and a wheel. In `pyproject.toml`, Hatchling packages `src/bitchat_cli` and routes `bitchat` and `bchat` to the same CLI entrypoint.

```sh
dir=.cursor/skills/verify-bitchat-package
sh scripts/check-cli-state-machine.sh "$dir/Package.tla"
sh scripts/check-cli-state-machine.sh "$dir/Package.tla" "$dir/Package-mutant.cfg"
python3 "$dir/references/check-wheel.py"
```

The finite model allows either build stage to fail and checks that the workflow cannot attempt publication before the successful tagged build. The mutant allows a publish attempt after the source archive alone. The fixture inspects workflow order, builds the real distributions into temporary storage, and checks both console aliases and their target file in the wheel. Changing one alias target must fail the fixture. Registry behavior and installation of runtime dependencies are outside the checked properties.
