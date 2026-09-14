### Title
Mempool spend-bundle fee calculation performs an unchecked unsigned subtraction that can raise an unhandled exception - (File: `chia/full_node/mempool_manager.py`)

### Summary
`MempoolManager.validate_spend_bundle` computes the transaction fee as `fees = uint64(removal_amount - addition_amount)` [1](#0-0)  without first verifying that `removal_amount >= addition_amount`. If a submitted spend bundle creates more value than it spends, `removal_amount - addition_amount` is negative and the `uint64()` constructor raises an exception instead of the code returning a controlled `Err` result, mirroring the Derby report's pattern of an unguarded unsigned subtraction that reverts/crashes rather than being handled as a normal validation failure.

### Finding Description
`validate_spend_bundle` accumulates `addition_amount` from every `CREATE_COIN` condition seen in the bundle's spends [2](#0-1) , and separately accumulates `removal_amount` from the coin records of the spent coins [3](#0-2) . It then immediately computes:

```
fees = uint64(removal_amount - addition_amount)
``` [4](#0-3) 

There is no preceding check equivalent to the block-body-validation rule `if removed < added: return Err.MINTING_COIN` that exists in the block validation path [5](#0-4) . In that consensus path, the amount-conservation violation is explicitly caught and turned into a graceful `Err.MINTING_COIN` return before any unsigned-to-signed/limited-width cast occurs. The mempool acceptance path (`validate_spend_bundle`), which processes freshly submitted, unvalidated spend bundles from wallets/peers before block inclusion, has no analogous guard before the `uint64()` cast. `uint64` is a fixed-width unsigned integer type; constructing it from a negative Python `int` raises rather than saturating or wrapping, so a bundle whose declared `CREATE_COIN` outputs exceed its input coin amounts drives this line straight into an unhandled exception during mempool validation.

This exactly parallels the reported Derby bug class: an operation assumes a monotonic/non-negative unsigned difference (`currentPrice - lastPrice` in Derby vs. `removal_amount - addition_amount` here) without checking the sign first, so a legitimately-reachable, attacker-controlled input (a decreasing price update there; an over-minting spend bundle here) triggers a revert/exception in a core state-transition function instead of a clean error path.

### Impact Explanation
Any unprivileged party can submit a spend bundle to a full node (via RPC `push_tx` or over the wallet protocol) that spends coins totaling `removal_amount` mojos while creating `CREATE_COIN` outputs totaling more than `removal_amount`. This directly triggers the unguarded subtraction/cast at `mempool_manager.py:814` and raises an exception inside `validate_spend_bundle`, which is on the hot path used by every mempool-admission attempt (`add_spend_bundle` / `respond_transaction`). Depending on how far up the call chain the exception propagates uncaught, this can disrupt mempool processing for that request and represents a spend-triggered fault in transaction-processing logic rather than the intended graceful `MINTING_COIN`-style rejection that the equivalent block-validation code path already implements.

### Likelihood Explanation
Likelihood is high for triggering the code path: constructing a spend bundle with `CREATE_COIN` outputs summing to more than the spent coins' amounts requires no special privileges, keys beyond ordinary coin ownership, or network position — it is a standard, attacker-crafted spend bundle submitted through the normal transaction-submission interface. Whether this fully "halts" node transaction processing versus being caught by an outer exception handler in the RPC/protocol layer could not be conclusively verified with the available tools, since the exact exception-handling wrapper around `add_spend_bundle`/`validate_spend_bundle` in the full node's message-handling layer was not inspected in this pass.

### Recommendation
Add an explicit non-negative check before the cast, mirroring the block-body-validation logic:
```python
if removal_amount < addition_amount:
    return Err.MINTING_COIN, None, []
fees = uint64(removal_amount - addition_amount)
```
This makes mempool-time rejection of over-minting spend bundles consistent with the block-validation consensus rule and removes the reliance on an unchecked unsigned cast.

### Proof of Concept
1. Construct a `CoinSpend` for an owned coin `C` with amount `A`.
2. Craft its solution so the puzzle emits one or more `CREATE_COIN` conditions whose summed amount `B > A` (e.g., simply request an output larger than the input; no signature/ownership beyond spending `C` is required to reach this code path — the puzzle's own logic decides whether to actually permit it, but the mempool manager computes `addition_amount` directly from the emitted conditions before any global balance check).
3. Submit the resulting `SpendBundle` through the standard transaction-submission RPC/protocol.
4. `MempoolManager.validate_spend_bundle` reaches `fees = uint64(removal_amount - addition_amount)` at `chia/full_node/mempool_manager.py:814` with `addition_amount > removal_amount`, causing `uint64()` to raise instead of returning `Err.MINTING_COIN` as intended by the analogous consensus rule in `chia/consensus/block_body_validation.py:541-546`.

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

**File:** chia/consensus/block_body_validation.py (L541-546)
```python
    # 16. Check that the total coin amount for added is <= removed
    if removed < added:
        return Err.MINTING_COIN

    fees = removed - added
    assert fees >= 0
```
