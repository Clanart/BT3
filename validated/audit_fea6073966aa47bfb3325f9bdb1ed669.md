### Title
Unchecked subtraction-then-cast to `uint64` for spend bundle fees can throw an unhandled `OverflowError` on a minting spend bundle - (File: chia/full_node/mempool_manager.py)

### Summary
`MempoolManager.validate_spend_bundle` computes the fee of an incoming spend bundle as `fees = uint64(removal_amount - addition_amount)` without first verifying that `removal_amount >= addition_amount`. `chia_rs`'s `uint64` constructor raises `OverflowError: can't convert negative int to unsigned` when given a negative Python int, exactly mirroring the Solidity bug class in the referenced report where a subtraction is performed in the unsigned domain and only afterward cast, causing a revert/exception instead of a graceful signed comparison.

### Finding Description
In `validate_spend_bundle`, `addition_amount` is accumulated from the `CREATE_COIN` conditions of every coin spend in the bundle, and `removal_amount` is accumulated from the coin records being spent: [1](#0-0) [2](#0-1) 

Immediately afterward, the fee is computed by subtracting in plain Python ints and then casting the result to `uint64`: [3](#0-2) 

There is no preceding check anywhere in this function (or its callers, as far as could be confirmed) that `removal_amount >= addition_amount` before this cast — that "conservation of value" / minting check (`Err.MINTING_COIN`) is only performed later, at full block-body validation time: [4](#0-3) 

`chia_rs.sized_ints.uint64` does not silently wrap or saturate on negative input — it raises `OverflowError` (confirmed by existing test expectations): [5](#0-4) [6](#0-5) 

This is the same root-cause pattern as the Sherlock M-41 report: an operation that can legitimately go negative (`removed - created`) is performed and only then coerced into an unsigned fixed-width type, instead of first checking the sign / clamping / branching on the comparison. The project's own `CHANGELOG.md` documents that exactly this class of bug (uncaught negative-to-`uint64` conversions surfacing in mempool/consensus paths) previously caused blocks to be rejected network-wide and required an explicit mitigating check to be added: [7](#0-6) 

### Impact Explanation
Any unprivileged party can submit a spend bundle to the mempool. If that spend bundle spends coins and issues `CREATE_COIN` conditions whose total amount exceeds the sum of the input coins' amounts (i.e., an attempted minting/inflation spend, which is otherwise legitimately rejected via `Err.MINTING_COIN` at block-validation time), `validate_spend_bundle` will hit `uint64(removal_amount - addition_amount)` with a negative operand and raise an unhandled `OverflowError` inside the mempool's spend-bundle acceptance path, rather than returning a controlled `Err` result. Because this occurs before the intended `Err.MINTING_COIN` consensus check is ever reached, a single crafted spend bundle can trigger an unhandled exception in transaction processing instead of a clean rejection — a spend-triggered transaction-processing halt/DoS vector reachable by any mempool submitter, rather than an actual asset-supply inflation (the eventual block-level `MINTING_COIN` check would still block real inflation if a block were assembled).

### Likelihood Explanation
High reachability: constructing a spend bundle whose declared `CREATE_COIN` outputs exceed its input coin amounts requires no special privileges, no cooperating peer, and no valid signature beyond what is needed to get past basic well-formedness checks earlier in `validate_spend_bundle` (puzzle-hash/canonical-encoding checks, `AGG_SIG` handling, etc., which occur before the fee computation at line 814). This makes the trigger straightforward for any spend-bundle submitter.

### Recommendation
Compute the fee with a safe order of operations, mirroring the fix recommended in the referenced report: perform the comparison/subtraction in the signed domain first, and explicitly reject the bundle with a defined `Err` (e.g., `Err.MINTING_COIN`) if `addition_amount > removal_amount`, before ever casting to `uint64`:
```python
if addition_amount > removal_amount:
    return Err.MINTING_COIN, None, []
fees = uint64(removal_amount - addition_amount)
```
This ensures the mempool manager returns a controlled validation error rather than raising an unhandled `OverflowError`.

### Proof of Concept
1. Create two arbitrary spendable coins totaling `N` mojos, controlled by the attacker/submitter's own puzzle (e.g., standard wallet coins).
2. Build a `SpendBundle` spending those coins with `CREATE_COIN` conditions whose combined output amount is `N + 1` (or any amount greater than the total input), with a validly aggregated signature for the coins actually owned by the submitter (only the *output amount* is being inflated; the spend authorization for the inputs is legitimate).
3. Submit the bundle via the standard `push_tx`/mempool ingestion RPC path so it reaches `MempoolManager.add_spend_bundle` → `validate_spend_bundle`.
4. Because `addition_amount > removal_amount`, `removal_amount - addition_amount` is negative, and `uint64(...)` at `chia/full_node/mempool_manager.py:814` raises `OverflowError: can't convert negative int to unsigned` instead of the function returning `Err.MINTING_COIN`, exercising the unhandled-exception path in transaction processing.

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

**File:** chia/full_node/mempool_manager.py (L784-812)
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
```

**File:** chia/full_node/mempool_manager.py (L814-814)
```python
        fees = uint64(removal_amount - addition_amount)
```

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

**File:** chia/_tests/core/custom_types/test_coin.py (L77-83)
```python
    with pytest.raises(OverflowError, match="int too big to convert"):
        # overflow
        Coin(H1, H2, 0x10000000000000000)  # type: ignore[arg-type]

    with pytest.raises(OverflowError, match="can't convert negative int to unsigned"):
        # overflow
        Coin(H1, H2, -1)  # type: ignore[arg-type]
```

**File:** chia/_tests/core/consensus/test_block_creation.py (L21-23)
```python
    if expected < 0:
        with pytest.raises(ValueError, match="does not fit into uint64"):
            compute_block_fee(additions, removals)
```

**File:** CHANGELOG.md (L2687-2690)
```markdown
### Fixed

- We were not checking for negative values in the uint64 constructor. Therefore coins with negative values were added to the mempool. These blocks passed validation, but they did not get added into the blockchain due to negative values not serializing in uint64. Farmers making these blocks would make blocks that did not make it into or advance the chain, so the blockchain slowed down starting at block 255518 around 6:35AM PDT 5/9/2021. The fix adds a check in the mempool and block validation, and does not disconnect peers who send these invalid blocks (any peer 1.1.4 or older), making this update not mandatory but is recommended. Users not updating might see their blocks get rejected from other peers. Upgraded nodes will show an error when they encounter an old node trying to send an inval ... (truncated)

```
