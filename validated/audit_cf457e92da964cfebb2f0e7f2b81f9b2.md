### Title
`PoolWallet.target_state` is set as a pending-intent lock before `generate_travel_transactions()` but is never rolled back on failure, permanently blocking `join_pool()`/`self_pool()` - ([File: chia/pools/pool_wallet.py])

### Summary
`PoolWallet.join_pool()` and `PoolWallet.self_pool()` set the in-memory field `self.target_state` *before* calling `generate_travel_transactions()`, which performs several `assert` checks and constructs the outgoing travel spend. If `generate_travel_transactions()` raises partway through (assertion failure, puzzle-reconstruction error, or any other exception before the transaction is durably staged), `self.target_state` remains permanently set to a non-`None` value with no corresponding on-chain transition ever being created. This is directly analogous to the reported `frontrunLock` issue: a state variable is optimistically set to a "locked/pending" value in anticipation of an asynchronous/external confirmation (VRF fulfillment in the original report; on-chain singleton state transition here), but nothing guarantees the unlock path runs if the intervening operation fails.

### Finding Description
In `chia/pools/pool_wallet.py`:

```python
self.target_state = target_state
self.next_transaction_fee = fee
self.next_tx_config = action_scope.config.tx_config
await self.generate_travel_transactions(fee, action_scope)
``` [1](#0-0) 

and similarly in `self_pool()`:
```python
self.target_state = create_pool_state(
    SELF_POOLING, owner_puzzlehash, owner_pubkey, pool_url=None, relative_lock_height=uint32(0)
)
self.next_transaction_fee = fee
self.next_tx_config = action_scope.config.tx_config
await self.generate_travel_transactions(fee, action_scope)
``` [2](#0-1) 

`generate_travel_transactions()` performs multiple operations that can raise before it finishes staging the transaction into the action scope, including an `assert new_inner_puzzle != inner_puzzle` and a `raise RuntimeError("Invalid state")` for an unrecognized inner puzzle shape:
```python
assert new_inner_puzzle != inner_puzzle
if is_pool_member_inner_puzzle(inner_puzzle):
    ...
elif is_pool_waitingroom_inner_puzzle(inner_puzzle):
    ...
else:
    raise RuntimeError("Invalid state")
``` [3](#0-2) 

`self.target_state` is only ever cleared back to `None` in `apply_state_transition()`, which requires that a matching on-chain singleton spend is later observed:
```python
for _, added_spend in reversed(
    await self.wallet_state_manager.pool_store.get_spends_for_wallet(self.wallet_id)
):
    latest_state: PoolState | None = solution_to_pool_state(added_spend)
    if latest_state is not None:
        if self.target_state == latest_state:
            self.target_state = None
            ...
        break
``` [4](#0-3) 

If the travel-spend generation never succeeds (so no spend bundle is ever broadcast/confirmed), `apply_state_transition()` never observes a matching state, so this "unlock" path never fires — exactly the same structural flaw as `frontrunLock` only being unlocked inside `fulfillRandomWords()`.

Because `self.target_state` is a plain Python attribute mutated directly (not routed through the `WalletActionScope` side-effect/rollback mechanism), an exception raised after the assignment does not revert the assignment; only the action-scope side effects (staged transactions) are discarded on error, not this field. Once `target_state is not None`, both entry points refuse to run again:
```python
if self.target_state is not None:
    raise ValueError(f"Cannot join a pool while waiting for target state: {self.target_state}")
``` [5](#0-4) 
```python
if self.target_state is not None:
    raise ValueError(f"Cannot self pool when already having target state: {self.target_state}")
``` [6](#0-5) 

### Impact Explanation
A user's plot-NFT/pool wallet can become permanently unable to change pool state (join a pool, leave a pool, or self-pool) after any transient failure during travel-spend construction, because the pending-intent flag is never cleared automatically. This is a spend-triggered transaction-processing halt for that wallet's pool-state machine: normal user-initiated calls (`join_pool`/`self_pool`) are the trigger, and the failure path leaves the wallet wedged with no automatic recovery. Note: the RPC endpoint `delete_unconfirmed_transactions` happens to also reset `wallet.target_state = None` as a side effect when the wallet is of type `POOLING_WALLET`:
```python
wallet = self.service.wallet_state_manager.wallets[request.wallet_id]
if wallet.type() == WalletType.POOLING_WALLET.value:
    assert isinstance(wallet, PoolWallet)
    wallet.target_state = None
``` [7](#0-6) 
This provides an incidental, undocumented manual workaround (unrelated to its stated purpose of deleting unconfirmed transactions), which reduces this from an unrecoverable permanent lock to a "stuck until the user finds and calls an unrelated RPC" state — still a functional/availability defect for the affected wallet.

### Likelihood Explanation
Requires a transient failure between setting `self.target_state` and successfully completing/persisting `generate_travel_transactions()` (e.g., an unexpected `RuntimeError("Invalid state")`, an `assert` failure from stale/racing on-chain state, a DB/network error while building the spend, or an unhandled exception in the surrounding `new_action_scope`). This is not attacker-controlled in the classic sense but is reachable through ordinary wallet operation under adverse conditions (concurrent state changes, crashes, unexpected puzzle state), making it a plausible, user-triggerable Medium-likelihood defect rather than a rare edge case.

### Recommendation
- Only set `self.target_state` (and `next_transaction_fee`/`next_tx_config`) after `generate_travel_transactions()` has successfully staged the transaction, or wrap the assignment/rollback in a `try/except` that resets `self.target_state = None` on any exception.
- Alternatively, route `target_state` through the same `WalletActionScope` side-effect/rollback boundary used for transactions, so a failed scope automatically reverts this field along with the rest of the pending mutation.
- Expose an explicit, documented recovery method (e.g., "clear pending pool target state") instead of relying on the incidental side effect inside `delete_unconfirmed_transactions`.

### Proof of Concept
1. Call `join_pool()` (or `self_pool()`) on a `PoolWallet` whose current on-chain state, due to a race with recent sync/rollback or unexpected puzzle contents, causes `generate_travel_transactions()` to hit `assert new_inner_puzzle != inner_puzzle` or the `raise RuntimeError("Invalid state")` branch after `self.target_state` has already been assigned. [1](#0-0) [3](#0-2) 
2. The exception propagates out of `join_pool()`/`self_pool()`; no spend bundle is created or broadcast, so no on-chain transition will ever occur that could satisfy `apply_state_transition()`'s clearing condition. [4](#0-3) 
3. `self.target_state` remains non-`None` indefinitely. Any subsequent call to `join_pool()` or `self_pool()` immediately raises `ValueError("Cannot join a pool while waiting for target state...")` / `"Cannot self pool when already having target state..."`, permanently blocking legitimate pool-state changes for that wallet until the user discovers and calls the unrelated `delete_unconfirmed_transactions` RPC. [5](#0-4) [6](#0-5)

### Citations

**File:** chia/pools/pool_wallet.py (L291-301)
```python
        # If we have reached the target state, resets it to None. Loops back to get current state
        for _, added_spend in reversed(
            await self.wallet_state_manager.pool_store.get_spends_for_wallet(self.wallet_id)
        ):
            latest_state: PoolState | None = solution_to_pool_state(added_spend)
            if latest_state is not None:
                if self.target_state == latest_state:
                    self.target_state = None
                    self.next_transaction_fee = uint64(0)
                    self.next_tx_config = DEFAULT_TX_CONFIG
                break
```

**File:** chia/pools/pool_wallet.py (L505-523)
```python
        assert new_inner_puzzle != inner_puzzle
        if is_pool_member_inner_puzzle(inner_puzzle):
            (
                _inner_f,
                _target_puzzle_hash,
                _p2_singleton_hash,
                _pubkey_as_program,
                _pool_reward_prefix,
                _escape_puzzle_hash,
            ) = uncurry_pool_member_inner_puzzle(inner_puzzle)
        elif is_pool_waitingroom_inner_puzzle(inner_puzzle):
            (
                _target_puzzle_hash,  # payout_puzzle_hash
                _relative_lock_height,
                _pubkey_as_program,
                _p2_singleton_hash,
            ) = uncurry_pool_waitingroom_inner_puzzle(inner_puzzle)
        else:
            raise RuntimeError("Invalid state")
```

**File:** chia/pools/pool_wallet.py (L633-634)
```python
        if self.target_state is not None:
            raise ValueError(f"Cannot join a pool while waiting for target state: {self.target_state}")
```

**File:** chia/pools/pool_wallet.py (L669-672)
```python
        self.target_state = target_state
        self.next_transaction_fee = fee
        self.next_tx_config = action_scope.config.tx_config
        await self.generate_travel_transactions(fee, action_scope)
```

**File:** chia/pools/pool_wallet.py (L684-685)
```python
        if self.target_state is not None:
            raise ValueError(f"Cannot self pool when already having target state: {self.target_state}")
```

**File:** chia/pools/pool_wallet.py (L705-710)
```python
        self.target_state = create_pool_state(
            SELF_POOLING, owner_puzzlehash, owner_pubkey, pool_url=None, relative_lock_height=uint32(0)
        )
        self.next_transaction_fee = fee
        self.next_tx_config = action_scope.config.tx_config
        await self.generate_travel_transactions(fee, action_scope)
```

**File:** chia/wallet/wallet_rpc_api.py (L1533-1536)
```python
            wallet = self.service.wallet_state_manager.wallets[request.wallet_id]
            if wallet.type() == WalletType.POOLING_WALLET.value:
                assert isinstance(wallet, PoolWallet)
                wallet.target_state = None
```
