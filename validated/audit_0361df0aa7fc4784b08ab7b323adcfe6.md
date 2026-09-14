### Title
Unchecked coin-value subtraction in mempool bundle admission can raise an uncaught exception, halting transaction processing - (File: chia/full_node/mempool_manager.py)

### Summary
`MempoolManager`'s spend-bundle admission logic computes the fee for an incoming spend bundle as `fees = uint64(removal_amount - addition_amount)` without first verifying that `removal_amount >= addition_amount`, unlike the equivalent consensus check performed in block body validation. If a submitted spend bundle creates more total coin value than it spends (i.e. attempts to mint value), the `uint64()` cast on a negative Python int raises a `ValueError` instead of returning a structured `Err`, mirroring the reported `circulating_supply()` pattern where an unguarded subtraction between two independently-changing values reverts a periodic/critical accounting step.

### Finding Description
In `chia/consensus/block_body_validation.py`, value conservation is explicitly guarded: [1](#0-0) 
Here, `removed < added` is checked and `Err.MINTING_COIN` is returned *before* the raw subtraction `fees = removed - added` is computed, so the subsequent `assert fees >= 0` can never fail on attacker-controlled input.

By contrast, the mempool admission path in `MempoolManager.validate_spend_bundle()` builds `addition_amount` from CLVM `CREATE_COIN` conditions and `removal_amount` from the coins being spent, then computes fees directly: [2](#0-1) 

There is no `if removal_amount < addition_amount: return Err.MINTING_COIN, None, []` guard analogous to the one in `block_body_validation.py` anywhere in the visible admission pipeline before line 814. `uint64(removal_amount - addition_amount)` on a negative Python integer raises `ValueError: ... does not fit into uint64`, which is a raw Python exception rather than one of the `Err` codes this function is designed to return. This is structurally the same class of bug as the Velocimeter `circulating_supply()` issue: an unguarded subtraction of two values that can diverge under adversary-influenced input, executed inside a function whose contract is "return a status code, don't raise," on a path that is part of routine, spend-triggered processing (every submitted spend bundle goes through `validate_spend_bundle()`).

### Impact Explanation
`validate_spend_bundle()` is invoked for every spend bundle submitted to the node (via RPC `push_tx`, peer-relayed transactions, and re-validation during mempool `new_peak()` processing). An unhandled `ValueError` raised mid-way through this function, if not caught by every one of its callers, can abort processing of that call context. Depending on which caller path lacks a surrounding `try/except` for generic exceptions (this could not be fully confirmed within the available tool budget — see below), this could manifest as: (a) a clean per-transaction rejection (low impact, if some outer layer catches broad exceptions), or (b) a crash/abort of the enclosing coroutine handling mempool admission or peak-processing, which would qualify as a spend-triggered transaction-processing halt (denial of service) consistent with the accepted impact categories for this analog scan.

### Likelihood Explanation
Triggering the divergence itself is trivial and fully attacker-controlled: any unprivileged submitter can build a spend bundle whose spent coins' `CREATE_COIN` conditions declare more total output value than the coins being spent supply value in mojos (this bundle would fail block inclusion due to consensus's `MINTING_COIN` check, but that check happens later, in block body validation — not in the mempool admission function shown here). The exact severity depends on exception-handling robustness in the mempool manager's callers, which I could not fully trace before the tool budget was exhausted.

### Recommendation
Add an explicit guard mirroring `block_body_validation.py`'s pattern before computing fees in `mempool_manager.py`:
```python
if removal_amount < addition_amount:
    return Err.MINTING_COIN, None, []
fees = uint64(removal_amount - addition_amount)
```
This makes the mempool admission path return a structured `Err` for an attempted-mint spend bundle instead of raising an uncaught `ValueError`, consistent with the function's documented `Optional[Err]` return contract.

### Proof of Concept
Conceptual PoC (not fully executed due to tool-call budget):
1. Construct a spend bundle that spends one coin of amount `1000` mojo with a solution whose CLVM output includes a `CREATE_COIN` condition for `2000` mojo to some destination puzzle hash (violating value conservation).
2. Submit this spend bundle to `MempoolManager.validate_spend_bundle()` (e.g. via `push_tx` RPC or directly in a unit test calling `pre_validate_spend_bundle`/`validate_spend_bundle`).
3. `addition_amount` (2000) exceeds `removal_amount` (1000); `fees = uint64(removal_amount - addition_amount)` at [3](#0-2)  evaluates `uint64(-1000)`, which raises `ValueError` rather than returning `Err.MINTING_COIN`.

**Note on verification limits:** I was not able to fully trace every caller of `validate_spend_bundle()` (e.g. `add_spend_bundle`, `new_peak()` pending-item re-validation, RPC `push_tx` handler) to confirm whether a broad `except Exception` wraps all call sites, which determines whether this manifests only as a single-transaction rejection or as a broader mempool-processing disruption. This should be verified with direct access to the full call chain, and a Devin session with terminal/test-execution access could confirm the exact exception propagation behavior and its practical severity.

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

**File:** chia/full_node/mempool_manager.py (L748-814)
```python
            spend_additions = []
            for puzzle_hash, amount, _ in spend_conds.create_coin:
                child_coin = Coin(coin_id, puzzle_hash, uint64(amount))
                spend_additions.append(child_coin)
                additions_dict[child_coin.name()] = child_coin
                addition_amount += amount

            bundle_coin_spends[coin_id] = BundleCoinSpend(
                coin_spend=coin_spend,
                eligible_for_dedup=bool(spend_conds.flags & ELIGIBLE_FOR_DEDUP),
                additions=spend_additions,
                cost=uint64(spend_conds.condition_cost + spend_conds.execution_cost),
                latest_singleton_lineage=lineage_info,
                atom_count=spend_conds.atom_count,
                pair_count=spend_conds.pair_count,
            )

        non_ff_spend_ids = set()
        effective_spend_ids = set()
        for coin_id, spend_data in bundle_coin_spends.items():
            if spend_data.latest_singleton_lineage is None:
                non_ff_spend_ids.add(coin_id)
                effective_spend_id = coin_id
            else:
                effective_spend_id = spend_data.latest_singleton_lineage.coin_id
            # Fast forward spends are only allowed to be spent once in a spend bundle
            if effective_spend_id in effective_spend_ids:
                return Err.INVALID_SPEND_BUNDLE, None, []
            effective_spend_ids.add(effective_spend_id)
        # Fast forward spends are only allowed when bundled with other, non-FF
        # spends in order to evict an FF spend, it must be associated with a
        # normal spend that can be included in a block or invalidated some
        # other way.
        if len(non_ff_spend_ids) == 0:
            return Err.INVALID_SPEND_BUNDLE, None, []

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
