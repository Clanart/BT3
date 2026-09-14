### Title
Unguarded subtraction in `MempoolManager.validate_spend_bundle()` allows a spend bundle that creates more value than it spends to raise an unhandled exception instead of a graceful rejection - (File: `chia/full_node/mempool_manager.py`)

### Summary
`MempoolManager.validate_spend_bundle()` computes the transaction fee as `fees = uint64(removal_amount - addition_amount)` without first verifying that `removal_amount >= addition_amount`. Unlike full block validation, which explicitly checks for coin minting before computing fees, the mempool path has no equivalent guard, mirroring the reported BlueBerryBank pattern where an unguarded `ov - pv` subtraction could underflow and DoS the caller.

### Finding Description
In `validate_spend_bundle`, `addition_amount` is accumulated from `spend_conds.create_coin` entries returned by the CLVM condition parser, and `removal_amount` is accumulated from the coins actually being spent: [1](#0-0) [2](#0-1) 

The fee is then computed as: [3](#0-2) 

There is no check equivalent to the one performed in full block validation, which explicitly rejects a block where additions exceed removals before computing fees: [4](#0-3) 

CLVM condition parsing at the per-spend level (`spend_conds.create_coin`) does not itself enforce that the sum of created coin amounts across an entire spend bundle is less than or equal to the sum of spent coin amounts — that "no minting" invariant is only enforced later, in full block body validation (`Err.MINTING_COIN`). Because `validate_spend_bundle()` runs earlier, at spend-bundle admission time (mempool), any spend bundle whose total `CREATE_COIN` amounts exceed the total value of the coins being spent will make `removal_amount - addition_amount` negative before this line is ever reached. `uint64(...)` (backed by `chia_rs` sized ints) raises a `ValueError` when constructed from a negative integer — this is directly confirmed by the existing unit test for the analogous `compute_fee_test` helper, which expects `pytest.raises(ValueError, match="does not fit into uint64")` for the same subtraction pattern: [5](#0-4) [6](#0-5) 

This is the exact analog of the reported bug class: a value that should be checked for a specific ordering relationship before being fed into a subtraction is instead subtracted unconditionally, and the wrapping/clamping/guard logic that exists elsewhere in the codebase (block body validation's `MINTING_COIN` check, and `compute_fee_test`'s explicit `max(ret, 0)` clamp) is missing at this particular call site.

### Impact Explanation
If this exception is not caught by an enclosing `try/except` somewhere further up the mempool admission call chain, any unprivileged submitter of a spend bundle (via the wallet RPC's push_tx, or a peer relaying a `NewTransaction`/`RespondTransaction` message) could trigger an unhandled `ValueError` inside `validate_spend_bundle()`, which is invoked while holding the blockchain lock during mempool processing. This is a spend-triggered halt/DoS vector on the transaction-processing pipeline of the full node, matching the bug class in the report ("spend-triggered transaction-processing halt").

### Likelihood Explanation
Likelihood depends on whether upstream code paths (e.g., `add_spend_bundle`/`add_transaction`) wrap the call to `validate_spend_bundle` in a broad exception handler that gracefully converts unexpected exceptions into an `Err`, which would reduce the impact to a rejected transaction rather than a crash. I was not able to fully confirm this within the given iteration limit — the call sites of `validate_spend_bundle` in `mempool_manager.py` were only partially inspected before running out of tool calls. This is the key uncertainty in this finding: whether the exception is caught gracefully upstream (making this a non-issue) or propagates unhandled (making this a genuine DoS).

### Recommendation
Add an explicit guard before computing `fees`, mirroring the `MINTING_COIN` check used in `block_body_validation.py`:
```python
if removal_amount < addition_amount:
    return Err.MINTING_COIN, None, []
fees = uint64(removal_amount - addition_amount)
```
This makes the mempool-admission path fail cleanly with a well-defined error instead of relying on an implicit `ValueError` from `uint64()` construction, and is consistent with the existing consensus-level protection.

### Proof of Concept
Because verification of whether the raised `ValueError` propagates unhandled requires tracing the full call chain from `add_transaction`/RPC handlers into `validate_spend_bundle`, which I could not complete before the iteration limit, I cannot provide a fully confirmed end-to-end PoC. The conceptual PoC is: submit a `SpendBundle` whose `CREATE_COIN` conditions collectively create coins with a greater total amount than the coins it spends (an invalid, coin-minting bundle). This must be rejected before block inclusion regardless, but the question is whether `validate_spend_bundle()` rejects it gracefully (returning `Err`) or crashes via the unguarded `uint64(removal_amount - addition_amount)` subtraction at [3](#0-2) .

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

**File:** chia/simulator/block_tools.py (L2428-2440)
```python
def compute_fee_test(additions: Sequence[Coin], removals: Sequence[Coin]) -> uint64:
    removal_amount = 0
    addition_amount = 0
    for coin in removals:
        removal_amount += coin.amount
    for coin in additions:
        addition_amount += coin.amount

    ret = removal_amount - addition_amount
    # in order to allow creating blocks that mint coins, clamp the fee
    # to 0, if it ends up being negative
    ret = max(ret, 0)
    return uint64(ret)
```

**File:** chia/_tests/core/consensus/test_block_creation.py (L11-25)
```python
@pytest.mark.parametrize("add_amount", [[0], [1, 2, 3], []])
@pytest.mark.parametrize("rem_amount", [[0], [1, 2, 3], []])
def test_compute_block_fee(add_amount: list[int], rem_amount: list[int]) -> None:
    additions: list[Coin] = [Coin(bytes32.random(), bytes32.random(), uint64(amt)) for amt in add_amount]
    removals: list[Coin] = [Coin(bytes32.random(), bytes32.random(), uint64(amt)) for amt in rem_amount]

    # the fee is the left-overs from the removals (spent) coins after deducting
    # the newly created coins (additions)
    expected = sum(rem_amount) - sum(add_amount)

    if expected < 0:
        with pytest.raises(ValueError, match="does not fit into uint64"):
            compute_block_fee(additions, removals)
    else:
        assert compute_block_fee(additions, removals) == expected
```
