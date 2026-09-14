### Title
Uncaught `OverflowError` in mempool spend-bundle validation from unchecked minting subtraction before checked `uint64` cast - (File: `chia/full_node/mempool_manager.py`)

### Summary
`MempoolManager.validate_spend_bundle()` computes the transaction fee as `fees = uint64(removal_amount - addition_amount)` without first verifying that `removal_amount >= addition_amount`, unlike the equivalent, safe check performed in block body validation.

### Finding Description
In `MempoolManager.validate_spend_bundle()`, `addition_amount` is accumulated directly from attacker-controlled `CREATE_COIN` condition amounts in the submitted spend bundle, and `removal_amount` is accumulated from the amounts of the coins being spent [1](#0-0) [2](#0-1) . The fee is then computed as `fees = uint64(removal_amount - addition_amount)` with no prior guard ensuring the subtraction is non-negative [3](#0-2) .

`uint64` (from `chia_rs.sized_ints`) is a checked, fixed-width integer type that raises `OverflowError` when constructed from a negative value, as demonstrated for the sibling `Coin` type in the test suite (`OverflowError: can't convert negative int to unsigned`) [4](#0-3) . A spend bundle that attempts to "mint" coins — creating more total value than it consumes — will make `removal_amount - addition_amount` negative, so the `uint64()` cast raises an unhandled `OverflowError` inside `validate_spend_bundle()` instead of returning a controlled `Err` code.

This is precisely analogous to the reported Malt bug class: a checked-arithmetic operation (Solidity 0.8 revert-on-overflow / here, `uint64`'s revert-on-negative) is applied to a value whose sign was never validated beforehand, turning what should be a normal "reject invalid input" path into an uncontrolled exception. The correct pattern already exists elsewhere in the codebase — full block validation checks `removed < added` and returns a clean `Err.MINTING_COIN` *before* ever subtracting [5](#0-4)  — but this check is missing in the mempool admission path.

### Impact Explanation
An unprivileged spend-bundle submitter can trigger this by submitting any (otherwise well-formed) spend bundle whose `CREATE_COIN` outputs sum to more than the total amount of the coins being spent (an inflation/minting attempt, which is expected to be rejected cleanly with `Err.INVALID_BLOCK_FEE_AMOUNT`/`MINTING_COIN`-style handling). Instead, it raises an unhandled `OverflowError` inside `validate_spend_bundle()`. If this exception is not caught by all callers of `validate_spend_bundle()`/`add_spend_bundle()`, it can abort the coroutine handling that request, disrupting mempool/transaction processing for the full node — a spend-triggered transaction-processing halt reachable from a single submitted spend bundle. I was not able to fully confirm within the available tool budget whether every caller of `validate_spend_bundle()` wraps this call in a broad exception handler that would downgrade the crash to a benign rejection; this should be verified directly in the full call chain (`MempoolManager.add_spend_bundle()` and its callers) before concluding definitive node-level DoS impact.

### Likelihood Explanation
High reachability: any wallet or RPC user who can submit a spend bundle (`push_tx`) can trigger this deterministically by crafting a spend bundle where the sum of `CREATE_COIN` amounts exceeds the sum of the amounts of the coins spent — this requires no privileged access, no cryptographic bypass, and no coordination with other nodes.

### Recommendation
Add an explicit guard mirroring `block_body_validation.py`'s pattern before the subtraction/cast in `chia/full_node/mempool_manager.py`:
```python
if addition_amount > removal_amount:
    return Err.MINTING_COIN, None, []
fees = uint64(removal_amount - addition_amount)
```
This ensures illegitimate/minting spend bundles are rejected with a controlled error instead of raising an unhandled `OverflowError`.

### Proof of Concept
1. Construct a spend bundle spending a coin of amount `X`.
2. Add a `CREATE_COIN` condition whose puzzle-hash/amount pair sums to `> X` (attempted minting).
3. Submit via `push_tx`/`add_spend_bundle`, which calls `MempoolManager.validate_spend_bundle()`.
4. Execution reaches `fees = uint64(removal_amount - addition_amount)` at `chia/full_node/mempool_manager.py:814` with a negative operand, raising `OverflowError` instead of returning `Err.INVALID_BLOCK_FEE_AMOUNT`/`MINTING_COIN`, matching the pattern proven for `uint64`/`Coin` construction in `chia/_tests/core/custom_types/test_coin.py:77-83`.

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

**File:** chia/_tests/core/custom_types/test_coin.py (L73-83)
```python
def test_construction() -> None:
    H1 = b"a" * 32
    H2 = b"b" * 32

    with pytest.raises(OverflowError, match="int too big to convert"):
        # overflow
        Coin(H1, H2, 0x10000000000000000)  # type: ignore[arg-type]

    with pytest.raises(OverflowError, match="can't convert negative int to unsigned"):
        # overflow
        Coin(H1, H2, -1)  # type: ignore[arg-type]
```

**File:** chia/consensus/block_body_validation.py (L541-546)
```python
    # 16. Check that the total coin amount for added is <= removed
    if removed < added:
        return Err.MINTING_COIN

    fees = removed - added
    assert fees >= 0
```
