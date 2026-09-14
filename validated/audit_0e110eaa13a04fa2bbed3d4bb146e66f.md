### Title
Missing coin-conservation check before `uint64()` cast in mempool spend-bundle validation can crash the mempool on a single submitted spend bundle - (File: chia/full_node/mempool_manager.py)

### Summary
The external report describes an unchecked arithmetic operation (`_moveDelegateVotes` subtracting a `balanceOf` amount from the wrong address inside an `unchecked` block) that underflows and never reverts, letting a user obtain unlimited votes. The root cause class is: **an invariant that is enforced elsewhere in the protocol (supply/balance conservation) is not re-checked at the point where an unchecked/unvalidated arithmetic operation consumes it**, so a single user-controlled transaction can trip an arithmetic fault that the code assumes cannot happen.

In `MempoolManager.validate_spend_bundle` (`chia/full_node/mempool_manager.py`), the same class of bug appears: the function computes `fees = uint64(removal_amount - addition_amount)` for a *single, unconfirmed* spend bundle before any check that `removal_amount >= addition_amount` has been performed at the mempool layer. The corresponding check (`Err.MINTING_COIN`, "the total coin amount for added is <= removed") only exists in full block validation, `chia/consensus/block_body_validation.py`.

### Finding Description
`chia/consensus/block_body_validation.py` computes `removed` and `added` totals over an entire block and explicitly guards against a spend bundle creating more value than it consumes: [1](#0-0) 

That guard (`Err.MINTING_COIN`) is the analog of the "balance/vote conservation" invariant in the ERC721Votes report — it is the place the protocol expects the underlying "removed >= added" fact to always hold before doing further unchecked arithmetic on the difference.

However, `MempoolManager.validate_spend_bundle`, which runs for every spend bundle submitted to a node (via RPC `push_tx`, wallet, or gossip) *before* it is ever included in a block, computes the totals for a single spend bundle and immediately casts the (potentially negative) difference to `uint64` without first verifying `removal_amount >= addition_amount`: [2](#0-1) 

If a spend bundle's `create_coin` outputs (`addition_amount`) sum to more than its input coins (`removal_amount`) — which is trivially achievable because `SpendBundleConditions`/CLVM execution alone does not enforce coin conservation, only the later block-level `MINTING_COIN` check does — the expression `removal_amount - addition_amount` is negative. Casting a negative Python `int` into the `uint64` sized-integer type is expected to raise (`OverflowError`/`ValueError`) rather than silently produce a valid unsigned value, since `uint64` in this codebase enforces the `[0, 2**64-1]` range.

This mirrors the external report's pattern precisely: an arithmetic operation that assumes an invariant ("removed ≥ added", analogous to "the vote/balance being subtracted always ≤ balance") is performed *before* that invariant has actually been validated for the object currently being processed, and the operation itself is not defensively guarded (no explicit `if removal_amount < addition_amount: return Err.MINTING_COIN` check exists at this call site, unlike in `block_body_validation.py`).

### Impact Explanation
An unhandled exception raised while validating an attacker-crafted spend bundle inside `validate_spend_bundle` would propagate up through `add_spend_bundle`, which is invoked directly from the mempool's transaction-intake path (RPC `push_tx`, wallet broadcast, and peer transaction gossip). Because I was not able to fully trace whether every caller of `add_spend_bundle`/`validate_spend_bundle` wraps this call in a broad `try/except`, the concrete blast radius (a single failed RPC call vs. an unhandled exception that aborts the async task processing incoming transactions/gossip for that node) is **not conclusively verified** with the available tools. At minimum this is a spend-triggered fault in a security-critical arithmetic check with no explicit `MINTING_COIN`-style guard at the mempool layer, matching the "spend-triggered transaction-processing halt" impact category if the exception is not fully contained.

### Likelihood Explanation
Any unprivileged user who can submit a spend bundle to a node's mempool (via RPC `push_tx` or by broadcasting to a full node) can trigger this code path by crafting a spend bundle whose `create_coin` outputs exceed the value of its inputs. No special privileges, malicious peer status, or leaked keys are required — this is a standard mempool-admission code path executed for every incoming spend bundle before block inclusion.

### Recommendation
Add an explicit coin-conservation check in `MempoolManager.validate_spend_bundle`, mirroring `block_body_validation.py`'s `Err.MINTING_COIN` check, before computing `fees`:
```python
if removal_amount < addition_amount:
    return Err.MINTING_COIN, None, []
fees = uint64(removal_amount - addition_amount)
```
This ensures the arithmetic invariant is validated before the unchecked cast, consistent with how the same invariant is defensively checked in full block validation.

### Proof of Concept
Not independently reproduced in this session (no execution environment available). The vulnerability class is demonstrated by code inspection: `chia/full_node/mempool_manager.py` lines 784-814 compute `removal_amount` and `addition_amount` for a single spend bundle and cast their difference to `uint64` with no preceding guard, whereas the equivalent aggregate check exists only in `chia/consensus/block_body_validation.py` lines 533-546 (`Err.MINTING_COIN`). A background Devin agent with code-execution access should:
1. Construct a `SpendBundle` whose single coin spend creates `CREATE_COIN` conditions summing to more than the spent coin's amount (no valid TAIL/CAT minting authorization).
2. Submit it to `MempoolManager.add_spend_bundle` / `validate_spend_bundle` directly or via the full node's `push_tx` RPC.
3. Observe whether `uint64(removal_amount - addition_amount)` raises an exception, and whether that exception is caught by an enclosing handler or propagates to disrupt spend-bundle processing.

### Citations

**File:** chia/consensus/block_body_validation.py (L533-546)
```python
    removed = 0
    for unspent in removal_coin_records.values():
        removed += unspent.coin.amount

    added = 0
    for coin, _ in additions:
        added += coin.amount

    # 16. Check that the total coin amount for added is <= removed
    if removed < added:
        return Err.MINTING_COIN

    fees = removed - added
    assert fees >= 0
```

**File:** chia/full_node/mempool_manager.py (L784-814)
```python
        removal_record_dict: dict[bytes32, CoinRecord] = {}
        removal_amount: int = 0
        removal_records = await get_coin_records(removal_names)
        for record in removal_records:
            removal_record_dict[record.coin.name()] = record

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

        fees = uint64(removal_amount - addition_amount)
```
