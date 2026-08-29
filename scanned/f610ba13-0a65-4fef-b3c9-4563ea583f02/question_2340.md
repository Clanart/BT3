# Q2340: find-superset via borrow: normalize a real holding to zero USD while the paired debt

## Question
Does `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) let an unprivileged attacker who controls `receiver`, including a contract principal reach `find-superset` (mainnet/contracts/registry/v0-egroup.clar:262) in a state where it normalize a real holding to zero USD while the paired debt normalizes upward? Given that it returns the FIRST mask that is a superset, walking buckets in population order rather than finding the tightest, the invariant that a position that holds value can always be priced, and therefore always closed breaks and the result is temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/registry/v0-egroup.clar:262` -> `find-superset`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: `receiver`, including a contract principal
- Exploit idea: `find-superset` returns the FIRST mask that is a superset, walking buckets in population order rather than finding the tightest. Reach it through `borrow` and normalize a real holding to zero USD while the paired debt normalizes upward.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz `receiver`, including a contract principal across its boundary values through `borrow` in simnet and assert `find-superset` never returns a value that breaks the invariant.
