### Title
Unhandled `AssertionError` on crafted spend bundle in `MempoolManager.validate_spend_bundle()` can halt mempool transaction processing - (File: chia/full_node/mempool_manager.py)

### Summary
CVE-2025-26265 describes a segfault DoS in an OAI 5G component caused by a crafted response that the receiver processes without adequate validation, crashing message processing. The closest reachable analog in this codebase is a bare Python `assert` in the mempool admission path that a single, unprivileged spend-bundle submitter can trigger with a specially crafted `SpendBundle`, converting what should be a handled protocol error (`Err`) into an unhandled `AssertionError`.

### Finding Description
`MempoolManager.validate_spend_bundle()` builds a `coin_id -> SpendConditions` map from the Rust-computed `conds.spends` and then asserts that it has exactly as many entries as `new_spend.coin_spends`: [1](#0-0) 

```
        # Map of coin ID to SpendConditions
        spend_conditions = {bytes32(spend.coin_id): spend for spend in conds.spends}

        # if this happens, the SpendBundle doesn't match the
        # SpendBundleConditions.
        assert len(new_spend.coin_spends) == len(spend_conditions)
```

`spend_conditions` is keyed by `coin_id`, so if a submitted `SpendBundle` contains the same coin (same parent id, puzzle hash, amount) spent twice — i.e., `new_spend.coin_spends` has duplicate entries for one coin id, but the Rust condition engine only produces a single `SpendConditions` for that coin id — `len(new_spend.coin_spends)` will exceed `len(spend_conditions)` and the `assert` fails. The next lines further assume a 1:1 mapping and call `spend_conditions.pop(coin_id)` per coin spend, which would raise `KeyError` on the second occurrence of the same coin if the assert did not already fail: [2](#0-1) 

Unlike the rest of this admission pipeline, which is deliberately structured to return `Err` values for malformed input (see `Err.INVALID_COIN_SOLUTION`, `Err.INVALID_SPEND_BUNDLE`, `Err.DOUBLE_SPEND`, etc., all through the same function), this specific invariant check is a bare `assert`/implicit `KeyError`, not a checked, typed error path. This mirrors the CVE's pattern: a message-shape assumption enforced by an unguarded runtime check rather than a validated condition, so a crafted input flips the assumption and raises an exception the surrounding code was not designed to convert into a normal rejection.

I was not able to fully trace (within the tool budget available) whether every caller of `validate_spend_bundle()` — `add_spend_bundle()` and `FullNode.add_transaction()` — wraps this call in a broad `try/except` that safely converts any exception (including `AssertionError`/`KeyError`) back into a `MempoolInclusionStatus.FAILED` response, or whether it can propagate further (e.g., leaving `blockchain.priority_mutex` in an inconsistent state, crashing the transaction processing task, or repeatedly disrupting the `TransactionQueue` worker that calls this path for every incoming spend bundle). This uncertainty should be resolved by a background agent before treating this as fully confirmed.

### Impact Explanation
If uncaught, an `AssertionError`/`KeyError` raised from inside `validate_spend_bundle()` — which executes while holding `blockchain.priority_mutex` — could disrupt the transaction-processing pipeline for a full node: a single malicious wallet or spend-bundle submitter could repeatedly submit crafted bundles designed to trip this invariant, causing repeated internal exceptions in the mempool admission worker/task. That matches the "spend-triggered transaction-processing halt" impact category. If it is in fact caught safely by an outer handler and only degrades to a `FAILED` status with no other consequence, then the real-world impact is much lower (denial-of-service is contained to that single rejected bundle), which would move it out of scope of a Medium/High finding.

### Likelihood Explanation
Likelihood of reaching the assert is straightforward: a `SpendBundle` with two `CoinSpend` entries referencing the exact same `Coin` (identical parent id, puzzle hash, amount) can be constructed by any external submitter without any special privilege, and is not filtered before `validate_clvm_and_signature`/`pre_validate_spendbundle` populates `conds.spends`. Confirming whether this reaches production-impacting behavior (vs. being silently caught) requires additional investigation of the Rust `SpendBundleConditions` dedup behavior for identical coin ids and of the exception handling in `add_spend_bundle()`/`FullNode.add_transaction()`, which I could not complete.

### Recommendation
- Replace the bare `assert len(new_spend.coin_spends) == len(spend_conditions)` (and the subsequent `spend_conditions.pop(coin_id)` which can `KeyError`) with an explicit check that returns a typed `Err` (e.g., a new or existing `Err.INVALID_SPEND_BUNDLE` variant) when a spend bundle contains duplicate coin spends for the same coin id, consistent with how every other malformed-input case in this function is handled.
- Audit `MempoolManager.add_spend_bundle()` and `FullNode.add_transaction()` to confirm all exceptions from `validate_spend_bundle()` are caught and converted into a `MempoolInclusionStatus.FAILED` response without leaving locks or worker state inconsistent.
- Add a regression test submitting a `SpendBundle` with two identical `CoinSpend` entries for the same coin and assert it is rejected with a typed `Err`, not an unhandled exception.

### Proof of Concept
1. Construct `coin_spend = CoinSpend(coin, puzzle_reveal, solution)` for some spendable coin.
2. Build `sb = SpendBundle([coin_spend, coin_spend], signature)` — the same `CoinSpend` object/coin included twice in one bundle.
3. Submit `sb` via `send_transaction` RPC or over the wallet protocol to a full node.
4. During mempool admission, `pre_validate_spendbundle` computes `conds.spends`, which (if the Rust engine dedupes/produces one `SpendConditions` per unique coin id) yields `len(spend_conditions) == 1` while `len(new_spend.coin_spends) == 2`.
5. `assert len(new_spend.coin_spends) == len(spend_conditions)` in `chia/full_node/mempool_manager.py:712` fails, raising `AssertionError` inside the mempool admission path instead of returning a handled `Err`.

Exact end-to-end confirmation of unhandled propagation (vs. safe catch by `add_spend_bundle`/`add_transaction`) requires running this PoC against a live/simulated node, which was not verifiable within this analysis.

### Citations

**File:** chia/full_node/mempool_manager.py (L707-712)
```python
        # Map of coin ID to SpendConditions
        spend_conditions = {bytes32(spend.coin_id): spend for spend in conds.spends}

        # if this happens, the SpendBundle doesn't match the
        # SpendBundleConditions.
        assert len(new_spend.coin_spends) == len(spend_conditions)
```

**File:** chia/full_node/mempool_manager.py (L714-722)
```python
        bundle_coin_spends: dict[bytes32, BundleCoinSpend] = {}
        for coin_spend in new_spend.coin_spends:
            coin_id = coin_spend.coin.name()
            removal_names.add(coin_id)

            # if this coin_id isn't found, the SpendBundle doesn't match the
            # SpendBundleConditions.
            spend_conds = spend_conditions.pop(coin_id)

```
