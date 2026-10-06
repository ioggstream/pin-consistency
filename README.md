# Pre-commit hook to ensure pin consistency.

This hook ensures that container images,
pre-commit hooks and github actions are consistently pinned to
the same version across the repository.

## Adoption in each repository

1. Add to `.pre-commit-config.yaml`:

   ```yaml
   - repo: https://github.com/ioggstream/pin-consistency.git
     rev: 76b40a5be31fdc46403166dfd977699dc96f3290 # 0.1.0
     hooks:
     - id: pin-consistency
     - id: pin-consistency
       name: pin-consistency (verify)
       args: [--verify]
       stages: [pre-push]
   ```

## Contributing

Please, see [CONTRIBUTING.md](CONTRIBUTING.md) for more details on:

- using [pre-commit](CONTRIBUTING.md#pre-commit);
- following the git flow and making good [pull requests](CONTRIBUTING.md#making-a-pr).

## Using this repository

You can create new projects starting from this repository,
so you can use a consistent CI and checks for different projects.

Besides all the explanations in the [CONTRIBUTING.md](CONTRIBUTING.md) file,
containerized tests are based on [act](https://github.com/nektos/act).

Update the [.actrc](.actrc) file with your specific constraints,
e.g. maximum resource usage, user and group ids, ..;
then run:

```bash
act
```
