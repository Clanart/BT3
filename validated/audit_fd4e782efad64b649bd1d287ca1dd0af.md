### Title
`PoolWallet.target_state` is mutated before the travel-transaction generation that can fail, permanently blocking further pool operations - ([File: chia/pools/pool_wallet.py])

### Summary
`PoolWallet.join_pool()` and `PoolWallet.self_pool()` set `self.target_state` (an in-memory intent flag) *before* calling `generate_travel_transactions()`, which can raise (e.g. from `generate_fee_transaction()` failing due to insufficient spendable balance, or from internal `assert`/`RuntimeError` guards). If that call fails, `target_state` is left set to the new value even though no transaction was ever generated or broadcast, permanently blocking the pool wallet from any further `join_pool`/`self_pool` calls until the wallet service restarts. This mirrors the reported bug class where `lastExecutionPrice` is persisted before the operation that can revert, instead of only after success.

### Finding Description
`join_pool()` sets `self.target_state = target_state` and then calls `await self.generate_travel_transactions(fee, action_scope)`: [1](#0-0) 

`self_pool()` follows the identical pattern: [2](#0-1) 

Both guard against re-entry via `if self.target_state is not None: raise ValueError(...)`: [3](#0-2) [4](#0-3) 

`generate_travel_transactions()` can fail after `target_state` has already been mutated. It builds the new inner puzzle, asserts invariants, and — if `fee > 0` — calls `generate_fee_transaction()`, which performs standard coin selection/signing and can raise (e.g. insufficient spendable balance): [5](#0-4) 

`generate_fee_transaction()` simply forwards to `standard_wallet.generate_signed_transaction()`, a call that raises on insufficient funds/coin-selection failure: [6](#0-5) 

Once any of these paths raises, the `await` in `join_pool`/`self_pool` propagates the exception back to the RPC caller, but `self.target_state` has already been assigned and is not rolled back anywhere in the exception path (there is no `try`/`except` around the mutation-and-call sequence). `target_state` is a plain dataclass field, not persisted to the DB: [7](#0-6) 

Because it is in-memory only, the corrupted state persists for the lifetime of the wallet-service process. Every subsequent `pw_join_pool`/`pw_self_pool` RPC call for that pool wallet is rejected due to the guard clauses above, even though no travel transaction was ever created or confirmed. The only path documented in the codebase to reset `target_state` to `None` is a matching on-chain state transition observed via `apply_state_transition()`, which will never occur here since nothing was ever spent: [8](#0-7) 

### Impact Explanation
An unprivileged wallet user can trigger this from the standard RPC surface simply by calling `pw_join_pool` or `pw_self_pool` with a `fee` that exceeds their currently spendable balance (or via any other failure path inside `generate_travel_transactions`, such as coin-selection races against pending/unconfirmed spends). The result is that the pool wallet becomes permanently unable to change pool state (join/leave/self-pool) until the wallet service is restarted — a denial-of-service on legitimate pooling/plotnft transitions for that wallet, with no on-chain trace explaining why, since no transaction was ever created. This is a Medium-severity availability/consistency bug directly analogous to the reported issue: transient/derived state is committed before the operation that can fail, instead of only on success.

### Likelihood Explanation
High likelihood of accidental triggering: any user who calls `join_pool`/`self_pool` with a fee slightly larger than their liquid balance, or while some coins are locked/pending/excluded by `TXConfig`, will hit this. No special privileges, timing races, or malicious peers are required — a normal wallet RPC call with an insufficiently funded fee is sufficient.

### Recommendation
Only mutate `self.target_state` (and `self.next_transaction_fee`/`self.next_tx_config`) after `generate_travel_transactions()` (and any nested fee-transaction generation) has completed successfully, or wrap the call in a `try/except` that resets `self.target_state = None` on failure before re-raising. Alternatively, perform all pre-flight validation (including fee affordability) before assigning `target_state`, so a failure cannot leave the wallet in an unrecoverable pending-target state.

### Proof of Concept
1. Create/select a `PoolWallet` in `SELF_POOLING` state with a small spendable balance (e.g. exactly `MINIMUM_INITIAL_BALANCE`).
2. Call `pw_join_pool` (or `pw_self_pool`) via wallet RPC with `fee` greater than the wallet's currently spendable XCH balance.
3. Observe: `PoolWallet.join_pool()` sets `self.target_state = new_target_state`, then calls `generate_travel_transactions()`, which calls `generate_fee_transaction()` → `standard_wallet.generate_signed_transaction()`, which raises due to insufficient funds; the RPC call fails with an error.
4. Call `pw_status` for the wallet: `target` is now non-`None` and reflects the requested (never-broadcast) target state.
5. Call `pw_join_pool`/`pw_self_pool` again with a valid, affordable fee: the call is rejected with `"Cannot join a pool while waiting for target state..."` / `"Cannot self pool when already having target state..."`, even though no transaction is pending or was ever broadcast. This condition persists until the wallet-service process restarts.

### Citations

**File:** chia/pools/pool_wallet.py (L79-81)
```python
    next_transaction_fee: uint64 = uint64(0)
    next_tx_config: TXConfig = DEFAULT_TX_CONFIG
    target_state: PoolState | None = None
```

**File:** chia/pools/pool_wallet.py (L291-304)
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

        await self.update_pool_config(action_scope)
        return True
```

**File:** chia/pools/pool_wallet.py (L446-460)
```python
    async def generate_fee_transaction(
        self,
        fee: uint64,
        action_scope: WalletActionScope,
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> None:
        await self.standard_wallet.generate_signed_transaction(
            [],
            [],
            action_scope,
            fee=fee,
            origin_id=None,
            coins=None,
            extra_conditions=extra_conditions,
        )
```

**File:** chia/pools/pool_wallet.py (L505-529)
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

        unsigned_spend_bundle = WalletSpendBundle([outgoing_coin_spend], G2Element())
        assert unsigned_spend_bundle.removals()[0].puzzle_hash == singleton.puzzle_hash
        assert unsigned_spend_bundle.removals()[0].name() == singleton.name()
        if fee > 0:
            await self.generate_fee_transaction(fee, action_scope)
```

**File:** chia/pools/pool_wallet.py (L633-638)
```python
        if self.target_state is not None:
            raise ValueError(f"Cannot join a pool while waiting for target state: {self.target_state}")
        if await self.have_unconfirmed_transaction():
            raise ValueError(
                "Cannot join pool due to unconfirmed transaction. If this is stuck, delete the unconfirmed transaction."
            )
```

**File:** chia/pools/pool_wallet.py (L669-673)
```python
        self.target_state = target_state
        self.next_transaction_fee = fee
        self.next_tx_config = action_scope.config.tx_config
        await self.generate_travel_transactions(fee, action_scope)
        return total_fee
```

**File:** chia/pools/pool_wallet.py (L684-685)
```python
        if self.target_state is not None:
            raise ValueError(f"Cannot self pool when already having target state: {self.target_state}")
```

**File:** chia/pools/pool_wallet.py (L705-711)
```python
        self.target_state = create_pool_state(
            SELF_POOLING, owner_puzzlehash, owner_pubkey, pool_url=None, relative_lock_height=uint32(0)
        )
        self.next_transaction_fee = fee
        self.next_tx_config = action_scope.config.tx_config
        await self.generate_travel_transactions(fee, action_scope)
        return total_fee
```
