## Summary

One or two sentences explaining what this PR does and why.

## Type of change

- [ ] Bug fix (fixes an issue without breaking existing functionality)
- [ ] New feature (adds functionality without breaking existing behavior)
- [ ] New detection rule (community tier)
- [ ] New detection rule (built-in tier — requires positive + negative tests)
- [ ] Breaking change (existing config, API, or schema changes incompatibly)
- [ ] Documentation only
- [ ] Performance improvement (include before/after numbers)
- [ ] Refactor (no behavior change)
- [ ] CI/build/tooling

## Related issues

Closes #
Relates to #

## What changed

Brief technical description of the changes:

- Module X: ...
- Module Y: ...
- New tests in ...

## Testing

- [ ] Existing tests still pass: `pytest`
- [ ] New tests added for new behavior
- [ ] `ruff check .` passes
- [ ] `ruff format --check .` passes
- [ ] `mypy --strict src/ tests/` passes
- [ ] If touching detector or policy: ran on real malicious + benign fixtures

For performance-sensitive changes:

- [ ] Ran `pytest tests/perf/ --benchmark`
- p50 before / after: 
- p95 before / after:

## False positive check (for new rules)

If this PR adds or modifies a detection rule:

- [ ] Tested against benign content that uses similar vocabulary
- [ ] Provided false_positive_examples in the rule YAML
- [ ] Confirmed no regressions in existing integration tests

## Backward compatibility

- [ ] No breaking changes to public CLI flags
- [ ] No breaking changes to config schema
- [ ] No breaking changes to audit log schema
- [ ] If yes to any of the above — migration path documented

## Documentation

- [ ] README updated if behavior visible to users changed
- [ ] CHANGELOG.md updated under [Unreleased]
- [ ] New ADR added if architectural decision (or referenced existing one)
- [ ] Inline docstrings updated for non-trivial changes

## Conventional commit format

This PR's commits follow the conventional commits spec. Examples:

- `feat(detector): add unicode normalization`
- `fix(policy): handle empty when clause`
- `docs: clarify rule promotion process`

The PR title will be used as the squash commit subject — make sure it follows the same format.

## Reviewer notes

Anything specific you want the reviewer to focus on, or known limitations of this PR.
_*_*_*_*_*
