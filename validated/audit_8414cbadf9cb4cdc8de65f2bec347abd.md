### Title
Unhandled `StopIteration` crash when computing coin additions from a malformed `CREATE_COIN` condition - ([File: chia/wallet/util/compute_additions.py])

### Summary
`compute_additions_with_cost()` re-executes a `CoinSpend`'s puzzle and manually walks each output condition's atom iterator without validating argument counts, unlike the Rust consensus condition parser which enforces exact `CREATE_COIN` arity. A puzzle reveal/solution pair that is not yet consensus-validated (e.g. supplied inside an offer file, or a coin spend built for fee estimation / lineage recomputation) can emit a truncated `CREATE_COIN` condition, causing `next(atoms)` to raise an uncaught `StopIteration`, crashing the caller.

### Finding Description
`compute_additions_with_cost()` runs the puzzle with `run_with_cost()` and iterates each output condition: [1](#0-0) 

For every condition, it does `atoms = cond.as_iter()` and `op = next(atoms).atom`. When `op == ConditionOpcode.CREATE_COIN.value`, it unconditionally calls `next(atoms).as_atom()` for the puzzle hash and `next(atoms).as_int()` for the amount, with no length/argument check and no `try/except`. This differs from consensus-level condition parsing (`chia/consensus/condition_tools.py`'s `parse_sexp_to_condition`), which defensively breaks out of the loop when an atom is absent, and from the Rust `SpendBundleConditions` extraction, which enforces exact argument counts before a spend is ever admitted to mempool or block. Because this Python helper re-runs the CLVM program itself rather than trusting already-validated `SpendConditions.create_coin` data, any caller that feeds it a puzzle reveal/solution pair that has *not* gone through full consensus validation (offers, fee estimation, singleton/CAT/DID/NFT lineage helpers that call `compute_additions`/`compute_additions_with_cost`) can be driven to produce a condition list like `(51)` (opcode only, no puzzle-hash/amount args), causing `next(atoms)` to raise `StopIteration` with no surrounding exception handling in this function.

This is the closest reachable analog to CVE‑2022‑3116's "crash on malformed/short input causing a null/invalid dereference in security-critical parsing code" pattern: a counterparty- or attacker-controlled CLVM output that is short one or two expected fields crashes the parsing routine instead of being rejected gracefully.

### Impact Explanation
An offer counterparty, or any spend-bundle-supplying caller whose coin spend is passed to `compute_additions`/`compute_additions_with_cost` before consensus admission (e.g. wallet-side offer inspection, fee estimation, or lineage-proof recomputation in CAT/DID/NFT/singleton/pool code paths that import this helper) can trigger an unhandled `StopIteration`, halting that transaction-processing call. This matches the "spend-triggered transaction-processing halt" impact category — a local denial of service against the wallet operation being performed, without requiring any privileged access.

### Likelihood Explanation
Crafting a CLVM puzzle that emits a truncated `(51)` (`CREATE_COIN`) condition is trivial and entirely within reach of anyone constructing an offer file, a spend bundle passed for fee estimation, or a coin spend inspected by lineage-recomputation helpers — no signature or consensus validation is required to exercise this code path, since it runs before/independently of mempool admission.

### Recommendation
Harden `compute_additions_with_cost()` to defensively check the number of remaining condition arguments before calling `next()`, mirroring the tolerant parsing in `chia/consensus/condition_tools.py::parse_sexp_to_condition` (break/skip on missing atoms rather than raising), or wrap the per-condition parsing in a `try/except (StopIteration, ValueError)` that skips malformed conditions instead of propagating an unhandled exception to callers.

### Proof of Concept
1. Construct a `CoinSpend` whose puzzle, when run, returns `((51))` — i.e. a `CREATE_COIN` opcode atom with no puzzle-hash/amount arguments (trivially achievable with a `puzzle_reveal` of `(q . ((51)))` or similar constant-output CLVM program).
2. Pass this `CoinSpend` to `compute_additions_with_cost()` (directly, or via any wallet code path that calls it on an unconfirmed/untrusted spend, such as offer inspection or fee estimation).
3. Observe that `next(atoms).as_atom()` at line 50 raises `StopIteration` because the condition's argument iterator is already exhausted after consuming the opcode, propagating an unhandled exception out of the helper instead of a graceful validation error. [2](#0-1)

### Citations

**File:** chia/wallet/util/compute_additions.py (L29-52)
```python
    cost, r = run_with_cost(cs.puzzle_reveal, max_cost, cs.solution)
    for cond in Program.to(r).as_iter():
        if cost > max_cost:
            raise ValidationError(Err.BLOCK_COST_EXCEEDS_MAX, "compute_additions() for CoinSpend")
        atoms = cond.as_iter()
        op = next(atoms).atom
        if op in {
            ConditionOpcode.AGG_SIG_PARENT,
            ConditionOpcode.AGG_SIG_PUZZLE,
            ConditionOpcode.AGG_SIG_AMOUNT,
            ConditionOpcode.AGG_SIG_PUZZLE_AMOUNT,
            ConditionOpcode.AGG_SIG_PARENT_AMOUNT,
            ConditionOpcode.AGG_SIG_PARENT_PUZZLE,
            ConditionOpcode.AGG_SIG_UNSAFE,
            ConditionOpcode.AGG_SIG_ME,
        }:
            cost += ConditionCost.AGG_SIG.value
            continue
        if op != ConditionOpcode.CREATE_COIN.value:
            continue
        cost += ConditionCost.CREATE_COIN.value
        puzzle_hash = next(atoms).as_atom()
        amount = uint64(next(atoms).as_int())
        ret.append(Coin(parent_id, puzzle_hash, uint64(amount)))
```
