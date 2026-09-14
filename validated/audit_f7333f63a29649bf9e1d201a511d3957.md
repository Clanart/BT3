### Title
Unhandled `OverflowError` in mempool fee computation allows a spend-triggered transaction-processing halt — ([File: chia/full_node/mempool_manager.py])

### Summary
`MempoolManager.validate_spend_bundle()` computes the transaction fee as `uint64(removal_amount - addition_amount)` without first verifying that `removal_amount >= addition_amount`. Because `uint64()` raises an `OverflowError` for negative inputs rather than saturating or wrapping, a spend bundle that creates more value (`CREATE_COIN` totals) than it consumes triggers an unhandled exception deep inside the mempool admission path, mirroring the report's "unmitigated overflow on user-provided values" class of bug.

### Finding Description
In `validate_spend_bundle`, `addition_amount` is accumulated from every `CREATE_COIN` condition across the bundle's spends, and `removal_amount` is accumulated from the spent coins' recorded amounts: [1](#0-0) [2](#0-1) 

The fee is then derived directly: [3](#0-2) 

There is no prior check ensuring `removal_amount >= addition_amount`. Chia's `uint64` (from `chia_rs.sized_ints`) does not wrap on negative construction — it raises `OverflowError`, as demonstrated by the project's own tests: [4](#0-3) 

The immediate caller, `FullNode.add_transaction`, only catches `ValueError` and `ValidationError` around the call into the mempool manager; an `OverflowError` is not one of those types and will propagate uncaught: [5](#0-4) 

This is functionally the same defect class flagged in the external report: an arithmetic operation over user/attacker-controlled amounts (`removal_amount`, `addition_amount`, both derived from attacker-supplied `CoinSpend` conditions) is performed without validating the operands can't underflow/overflow the target integer type, and the surrounding code does not defensively catch the resulting exception.

### Impact Explanation
A spend bundle whose declared `CREATE_COIN` outputs sum to more than the amounts of the coins it spends (value inflation attempt) causes `uint64(removal_amount - addition_amount)` to raise `OverflowError` instead of being rejected with a proper `Err` code (e.g. an "invalid fee"/"invalid spend" rejection). Because this exception type isn't handled by the `except` clauses in `add_transaction`, it propagates out of the transaction-processing coroutine. This can disrupt the full node's transaction-processing pipeline (unhandled exception in the request-handling path) for a single unprivileged, low-cost spend bundle submission — a spend-triggered transaction-processing halt, without requiring any privileged access, valid signatures for the crashing bundle's true effect, or cooperation from other network participants.

### Likelihood Explanation
The attack requires only crafting a `SpendBundle` whose puzzle(s) emit `CREATE_COIN` conditions whose amounts sum to more than the coin(s) being spent — no valid AGG_SIG proof of ownership of additional funds is required to trigger the crash path, since the fee computation occurs prior to/independent of the amount-conservation checks that exist at block-body validation. Any unprivileged party able to submit a spend bundle to a full node's mempool (via RPC or protocol message) can attempt this. Likelihood is limited only by whether some other Rust-side or Python-side validation in the current codebase already filters out such non-conserving bundles before `validate_spend_bundle` is reached, which could not be conclusively confirmed within the available static analysis — this is the primary source of remaining uncertainty.

### Recommendation
Add an explicit conservation check before constructing the `uint64` fee value, e.g.:
```python
if addition_amount > removal_amount:
    return Err.INVALID_BLOCK_FEE_AMOUNT, None, []
fees = uint64(removal_amount - addition_amount)
```
Additionally, broaden exception handling in `FullNode.add_transaction` (or at the boundary of `pre_validate_spendbundle`) to catch `OverflowError`/`ArithmeticError` and convert it into a proper `MempoolInclusionStatus.FAILED` response rather than letting it propagate. As the original report recommends, document every arithmetic operation over attacker-controlled values in the fee-computation path and state explicitly where conservation/overflow is enforced.

### Proof of Concept
1. Construct a `SpendBundle` spending a coin of amount `X`.
2. Craft the puzzle/solution so its output conditions include one or more `CREATE_COIN` conditions whose amounts sum to `Y > X`.
3. Submit the bundle to a full node via the standard transaction-submission RPC/protocol path (`add_transaction`).
4. `MempoolManager.validate_spend_bundle` computes `addition_amount = Y`, `removal_amount = X`, then executes `uint64(X - Y)`, which is negative and raises `OverflowError`.
5. This exception is not caught by the `except ValueError`/`except ValidationError` handlers in `full_node.py`'s `add_transaction`, propagating up and disrupting normal transaction-processing flow for that request.

Note: Full confirmation that no earlier Rust-side consensus check rejects value-inflating bundles before reaching this line could not be completed with the tools available; a Devin session with full repository/test execution access would be needed to dynamically verify reachability of this exact code path from an unauthenticated RPC-submitted spend bundle.

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

**File:** chia/full_node/mempool_manager.py (L784-813)
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

**File:** chia/_tests/core/custom_types/test_coin.py (L77-83)
```python
    with pytest.raises(OverflowError, match="int too big to convert"):
        # overflow
        Coin(H1, H2, 0x10000000000000000)  # type: ignore[arg-type]

    with pytest.raises(OverflowError, match="can't convert negative int to unsigned"):
        # overflow
        Coin(H1, H2, -1)  # type: ignore[arg-type]
```

**File:** chia/full_node/full_node.py (L3063-3079)
```python
        try:
            cost_result = await self.mempool_manager.pre_validate_spendbundle(
                transaction, spend_name, self._bls_cache, fee_per_cost=fee_per_cost
            )
        except ValueError as e:
            # ValueError is used to indicate a soft failure. We don't want to
            # ban the peer. Timeouts are logged by MempoolManager when
            # log_mempool is "timeout".
            self.log.info(f"Rejecting transaction {spend_name}: {e}")
            return MempoolInclusionStatus.FAILED, Err.INVALID_SPEND_BUNDLE
        except ValidationError as e:
            # Keep known-invalid bundles in seen-cache to prevent re-validation.
            self.mempool_manager.add_and_maybe_pop_seen(spend_name)
            self.log.info(f"Rejecting transaction {spend_name}: {e}")
            return MempoolInclusionStatus.FAILED, e.code
        finally:
            self.mempool_manager.remove_in_flight(spend_name)
```
