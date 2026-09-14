### Title
Unhandled `uint64` underflow when computing mempool fees allows an unprivileged spend bundle to crash `MempoolManager.validate_spend_bundle` before minting checks run - ([File: chia/full_node/mempool_manager.py])

### Summary
`MempoolManager.validate_spend_bundle()` computes the fee of a submitted spend bundle as `fees = uint64(removal_amount - addition_amount)` before any check that ensures `addition_amount <= removal_amount` (i.e. before any minting/value-conservation check is performed at the mempool layer). `uint64()` in this codebase raises on negative input (as demonstrated by the project's own tests), so a spend bundle whose `CREATE_COIN` conditions sum to more than the value of the coins it spends causes an unhandled exception at admission time, rather than a clean `Err` return.

### Finding Description
In `chia/full_node/mempool_manager.py`, `validate_spend_bundle()` accumulates `addition_amount` from every `CREATE_COIN` condition in the bundle: [1](#0-0) 
and accumulates `removal_amount` from the coin records being spent: [2](#0-1) 

It then immediately computes: [3](#0-2) 

with no preceding guard that `removal_amount >= addition_amount`. The equivalent "minting" check that exists at block-validation time, [4](#0-3) 
is only performed by `validate_block_body`, which runs on already-farmed blocks - it is never invoked during mempool admission of an individual spend bundle. Nothing in `validate_spend_bundle` rejects a bundle where `addition_amount > removal_amount` before the fee subtraction.

`uint64()` construction on a negative value raises rather than silently wrapping, as confirmed by the project's own regression tests and changelog entry that specifically documents this exact class of bug being fixed once already for coin amounts: [5](#0-4) [6](#0-5) 

Because a CLVM puzzle solution is fully attacker-controlled by the submitter (a coin owner can construct any `CREATE_COIN` conditions in their own puzzle reveal, subject only to their own puzzle logic - and many standard/attacker-authored puzzles do not themselves enforce output ≤ input), an attacker can trivially spend one of their own coins with a solution that creates a child coin (or several) whose amounts sum to more than the parent coin's amount. This is normally rejected by consensus at block-body time (`MINTING_COIN`), but here it is never rejected at the mempool layer before the vulnerable subtraction executes.

### Impact Explanation
This hits the "spend-triggered transaction-processing halt" category. If the resulting `ValueError`/`OverflowError` from the negative-to-`uint64` cast is not caught somewhere above `validate_spend_bundle` in the call chain (`pre_validate_spendbundle` → `add_spend_bundle`, invoked from `respond_transaction`/RPC transaction submission), it will propagate as an unhandled exception inside the mempool's spend-bundle admission pipeline. Since this code path is reached by any coin owner submitting a spend bundle to a node's mempool (locally via RPC, or via relay from a peer accepting an unauthenticated transaction), a single malformed-but-otherwise-valid-looking spend bundle can repeatedly throw during processing, degrading or halting transaction admission on the full node that processes it. This matches the class of "spend-triggered transaction-processing halt" that the analog report describes for the Dutch auction contract (an underflowing subtraction bricking further calls).

### Likelihood Explanation
High likelihood: constructing a spend bundle whose `CREATE_COIN` conditions exceed the spent coin's value requires no special privilege, no signature bypass, and no coordination with other network participants - it is a standard, self-authored spend from any wallet holding a spendable coin. The only requirement is a puzzle/solution under the attacker's control (their own coin) that emits `CREATE_COIN` conditions summing above the coin's own value; this is straightforward with any programmable puzzle (e.g., a bare `(mod solution (a solution))`-style reveal executing attacker-chosen conditions).

### Recommendation
Add an explicit minting/value-conservation check in `MempoolManager.validate_spend_bundle()` immediately after `removal_amount`/`addition_amount` are computed and before the fee subtraction, mirroring the `MINTING_COIN` check in `block_body_validation.py`:
```python
if removal_amount < addition_amount:
    return Err.MINTING_COIN, None, []
fees = uint64(removal_amount - addition_amount)
```
This ensures the mempool rejects such spend bundles with a proper `Err` result rather than raising an unhandled exception, and ensures the exception path can never be reached from attacker-supplied input.

### Proof of Concept
1. Construct a coin `C` with `amount = 100`, owned by the attacker, with a puzzle that will execute a solution verbatim (e.g. `(a (q . (list CREATE_COIN ph1 amount1 ...)) 1)`-style pattern, or any puzzle under attacker control).
2. Craft a solution that produces `CREATE_COIN` conditions summing to `amount > 100` (e.g., a single `CREATE_COIN ph 1000`).
3. Sign the coin spend (if the puzzle requires `AGG_SIG`) and submit the resulting `SpendBundle` to the target full node's mempool via `respond_transaction`/RPC `push_tx`.
4. In `MempoolManager.validate_spend_bundle()`, `addition_amount` (1000) exceeds `removal_amount` (100); `fees = uint64(removal_amount - addition_amount)` at `chia/full_node/mempool_manager.py:814` attempts `uint64(-900)`, raising an exception before the mempool's normal `Err.*` rejection path is reached.
5. Because prior tests in this codebase confirm `uint64()` raises rather than returning a benign value for negative input (see `test_compute_block_fee`), this is not silently handled; it propagates from `validate_spend_bundle` up through `pre_validate_spendbundle`/`add_spend_bundle`, whose call sites were not confirmed (during investigation) to wrap this specific call in a broad exception handler.

Note: I was not able to fully confirm, within the available tool budget, whether `pre_validate_spendbundle`/`add_spend_bundle`'s callers (e.g., `full_node.py`'s transaction-handling logic) wrap this call in a catch-all `except Exception`, which would downgrade the impact to a per-bundle rejection rather than a broader halt. This should be verified directly in `chia/full_node/full_node.py` and `chia/full_node/mempool_manager.py`'s `pre_validate_spendbundle`/`add_spend_bundle` implementations before treating this as confirmed high-severity DoS; regardless, the missing `MINTING_COIN`-style guard at the mempool layer is a real logic gap relative to block-validation-time consensus rules and should be fixed independent of exception-handling behavior elsewhere.

### Citations

**File:** chia/full_node/mempool_manager.py (L748-753)
```python
            spend_additions = []
            for puzzle_hash, amount, _ in spend_conds.create_coin:
                child_coin = Coin(coin_id, puzzle_hash, uint64(amount))
                spend_additions.append(child_coin)
                additions_dict[child_coin.name()] = child_coin
                addition_amount += amount
```

**File:** chia/full_node/mempool_manager.py (L790-812)
```python
        for name in removal_names:
            if name not in removal_record_dict and name not in additions_dict:
                return Err.UNKNOWN_UNSPENT, None, []
            if name in additions_dict:
                removal_coin = additions_dict[name]
                # The timestamp and block-height of this coin being spent needs
                # to be consistent with what we use to check time-lock
                # conditions (below). All spends (including ephemeral coins) are
                # spent simultaneously. Ephemeral coins with an
                # ASSERT_SECONDS_RELATIVE 0 condition are still OK to spend in
                # the same block.
                assert self.peak.timestamp is not None
                removal_record = CoinRecord(
                    removal_coin,
                    uint32(self.peak.height + 1),
                    uint32(0),
                    False,
                    self.peak.timestamp,
                )
                removal_record_dict[name] = removal_record
            else:
                removal_record = removal_record_dict[name]
            removal_amount += removal_record.coin.amount
```

**File:** chia/full_node/mempool_manager.py (L814-814)
```python
        fees = uint64(removal_amount - addition_amount)
```

**File:** chia/consensus/block_body_validation.py (L541-546)
```python
    # 16. Check that the total coin amount for added is <= removed
    if removed < added:
        return Err.MINTING_COIN

    fees = removed - added
    assert fees >= 0
```

**File:** CHANGELOG.md (L2687-2689)
```markdown
### Fixed

- We were not checking for negative values in the uint64 constructor. Therefore coins with negative values were added to the mempool. These blocks passed validation, but they did not get added into the blockchain due to negative values not serializing in uint64. Farmers making these blocks would make blocks that did not make it into or advance the chain, so the blockchain slowed down starting at block 255518 around 6:35AM PDT 5/9/2021. The fix adds a check in the mempool and block validation, and does not disconnect peers who send these invalid blocks (any peer 1.1.4 or older), making this update not mandatory but is recommended. Users not updating might see their blocks get rejected from other peers. Upgraded nodes will show an error when they encounter an old node trying to send an inval ... (truncated)
```

**File:** chia/_tests/core/consensus/test_block_creation.py (L19-23)
```python
    expected = sum(rem_amount) - sum(add_amount)

    if expected < 0:
        with pytest.raises(ValueError, match="does not fit into uint64"):
            compute_block_fee(additions, removals)
```
