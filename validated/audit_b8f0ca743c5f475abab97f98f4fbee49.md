Confirmed: `PoolWallet.new_peak()` is invoked in an unguarded loop from `WalletStateManager.new_peak()`, and that call is itself made outside any try/except in `WalletNode.new_peak_wallet()`. An uncaught exception from one pool wallet's `new_peak()` therefore aborts the whole per-peak wallet update for every wallet processed after it in the loop, closely mirroring the PublicVault "expired lien not liquidated halts processEpoch()" pattern, where a single stuck/unhandled per-item condition blocks processing for everyone else in the batch.

### Title
Unhandled exception in `PoolWallet.new_peak()` halts wallet-wide new-peak processing for all wallets - ([File: chia/pools/pool_wallet.py])

### Summary
`WalletStateManager.new_peak()` iterates over every wallet and calls `PoolWallet.new_peak()`/`PlotNFT2Wallet.new_peak()` synchronously with no exception isolation. `PoolWallet.new_peak()` contains both an explicit `raise ValueError(...)` and several bare `assert` statements on the second-stage "leave pool" resubmission path. If any of these trigger on a given peak, the exception propagates out of the per-wallet loop and out of `WalletStateManager.new_peak()` uncaught, aborting processing for that peak for all wallets, similar to how an unliquidated expired lien in `PublicVault.processEpoch()` blocks the entire epoch-processing flow until the stuck condition is manually resolved.

### Finding Description
`WalletStateManager.new_peak()` does:
```python
async def new_peak(self, height: uint32) -> None:
    for wallet_id, wallet in self.wallets.items():
        if isinstance(wallet, (PoolWallet, PlotNFT2Wallet)):
            await wallet.new_peak(height)
    ...
``` [1](#0-0) 

There is no try/except around `wallet.new_peak(height)`, so any exception raised by a single pool wallet stops iteration of the `for` loop entirely — subsequent wallets in `self.wallets.items()` never get their `new_peak()` called for this height, and the trailing `tx_pending_changed()` resend logic is also skipped.

`PoolWallet.new_peak()` has an explicit unconditional-looking raise:
```python
if self.target_state == pool_wallet_info.current:
    self.target_state = None
    raise ValueError(f"Internal error. Pool wallet {self.wallet_id} state: {pool_wallet_info.current}")
``` [2](#0-1) 

and further down, on the two-stage "leave pool" resubmission path (reached once `peak_height > leave_height + 2`, a condition that remains true on every subsequent peak until the on-chain state actually transitions out of `LEAVING_POOL`):
```python
next_tip: Coin | None = get_most_recent_singleton_coin_from_coin_spend(tip_spend)
assert next_tip is not None
...
assert self.target_state.version == POOL_PROTOCOL_VERSION
assert pool_wallet_info.current.state == LEAVING_POOL.value
assert self.target_state.target_puzzle_hash is not None
``` [3](#0-2) 

The call site that invokes `WalletStateManager.new_peak()` is also unguarded:
```python
async with self.wallet_state_manager.lock:
    await self.wallet_state_manager.new_peak(new_peak.height)
    if self.config.get("auto_claim", {}).get("enabled", False):
        ...
``` [4](#0-3) 

While this call is itself wrapped by the outer `_process_new_subscriptions()` dispatcher's broad `except Exception` handler (which logs and closes the peer connection rather than crashing the wallet process) [5](#0-4) , the practical effect is: (1) the pool/plotnft leave-pool travel transaction that `new_peak()` was supposed to generate is skipped for that peak, and (2) every other pool/plotnft wallet later in the `self.wallets` dict iteration order also misses its `new_peak()` call for that peak, plus the peer that delivered the peak gets disconnected. Because the triggering condition (`peak_height > leave_height + 2` while still `LEAVING_POOL`) is persistent across peaks rather than a one-shot event, this can repeat on every subsequent peak until the underlying singleton state changes, closely paralleling the reported bug class where an unresolved per-item condition (expired lien / stuck pool-leave transition) blocks batch processing (`processEpoch()` / `new_peak()`) for everyone downstream until manual intervention.

### Impact Explanation
This is a wallet-side availability issue reachable purely from on-chain/self-triggered wallet-action state (no malicious peer needed): a wallet operator who calls `pw_join_pool`/`pw_self_pool` to leave a pool and then experiences a delayed/blocked second-stage travel transaction (e.g., due to `next_tip` momentarily being `None`, or a reorg edge case around the `target_state == current` check) can cause `new_peak()` processing to throw on every peak until resolved. This stalls the automatic second-stage leave-pool transaction (delaying pool departure/reward-claim availability) and, more broadly, causes each new peak's per-wallet update loop to abort early and disconnects the delivering peer, degrading wallet sync responsiveness for the whole node until the stuck pool wallet's state resolves itself.

### Likelihood Explanation
Medium likelihood: this path is reached automatically by any user going through the standard "leave pool" (`pw_self_pool`/`pw_join_pool` two-stage transition) flow, which is a normal, unprivileged wallet action, not an attacker-crafted input. The specific race (parent singleton lineage lookup returning `None`, or a target_state/current mismatch surfacing right at the reorg-buffer boundary) is timing-dependent rather than guaranteed, hence medium rather than high confidence of hitting it in practice, but it is not purely theoretical since the assertions/raise are explicitly reachable from the documented multi-block leave-pool state machine.

### Recommendation
Wrap each per-wallet `new_peak()` invocation in `WalletStateManager.new_peak()` in a scoped try/except so that one pool/plotnft wallet's failure cannot prevent other wallets from being updated on that peak, and convert the assertions/`raise ValueError` in `PoolWallet.new_peak()` into recoverable, logged-and-deferred outcomes (retry on a later peak) rather than propagating exceptions that abort the shared per-peak update loop.

### Proof of Concept
Not independently exploitable/reproducible from the given index alone (requires driving the wallet through the exact singleton-lineage race or a target-state/current-state collision at the reorg-buffer boundary in a live pool "leave" transition); the control-flow analysis above is based directly on the cited source. Due to indexing limits, deeper end-to-end reproduction (e.g., exact conditions under which `get_most_recent_singleton_coin_from_coin_spend` returns `None` for a tip spend) was not fully traced — a Devin session with full repo/test access would be needed to construct a concrete failing test scenario.

### Citations

**File:** chia/wallet/wallet_state_manager.py (L2120-2124)
```python
    async def new_peak(self, height: uint32) -> None:
        for wallet_id, wallet in self.wallets.items():
            if isinstance(wallet, (PoolWallet, PlotNFT2Wallet)):
                await wallet.new_peak(height)

```

**File:** chia/pools/pool_wallet.py (L816-818)
```python
        if self.target_state == pool_wallet_info.current:
            self.target_state = None
            raise ValueError(f"Internal error. Pool wallet {self.wallet_id} state: {pool_wallet_info.current}")
```

**File:** chia/pools/pool_wallet.py (L827-848)
```python
            if peak_height > leave_height + 2:
                unconfirmed: list[
                    TransactionRecord
                ] = await self.wallet_state_manager.tx_store.get_unconfirmed_for_wallet(self.wallet_id)
                next_tip: Coin | None = get_most_recent_singleton_coin_from_coin_spend(tip_spend)
                assert next_tip is not None

                if any([rem.name() == next_tip.name() for tx_rec in unconfirmed for rem in tx_rec.removals]):
                    self.log.info("Already submitted second transaction, will not resubmit.")
                    return

                self.log.info(f"Attempting to leave from\n{pool_wallet_info.current}\nto\n{self.target_state}")
                assert self.target_state.version == POOL_PROTOCOL_VERSION
                assert pool_wallet_info.current.state == LEAVING_POOL.value
                assert self.target_state.target_puzzle_hash is not None

                if self.target_state.state == SELF_POOLING.value:
                    assert self.target_state.relative_lock_height == 0
                    assert self.target_state.pool_url is None
                elif self.target_state.state == FARMING_TO_POOL.value:
                    assert self.target_state.relative_lock_height >= self.MINIMUM_RELATIVE_LOCK_HEIGHT
                    assert self.target_state.pool_url is not None
```

**File:** chia/wallet/wallet_node.py (L754-772)
```python
                elif item.item_type == NewPeakQueueTypes.NEW_PEAK_WALLET:
                    self.log.debug("Pulled from queue: %s %s", item.item_type.name, item.data[0])
                    # This can take a VERY long time, because it might trigger a long sync. It is OK if we miss some
                    # subscriptions or state updates, since all subscriptions and state updates will be handled by
                    # long_sync (up to the target height).
                    new_peak = item.data[0]
                    peer = item.data[1]
                    assert peer is not None
                    await self.new_peak_wallet(new_peak, peer)
                else:
                    self.log.debug("Pulled from queue: UNKNOWN %s", item.item_type)
                    assert False
            except asyncio.CancelledError:
                self.log.info("Queue task cancelled, exiting.")
                raise
            except Exception as e:
                self.log.error(f"Exception handling {item}, {e} {traceback.format_exc()}")
                if peer is not None:
                    await peer.close(9999)
```

**File:** chia/wallet/wallet_node.py (L1377-1385)
```python
        async with self.wallet_state_manager.lock:
            await self.wallet_state_manager.new_peak(new_peak.height)

            # Check if any coin needs auto spending
            if self.config.get("auto_claim", {}).get("enabled", False):
                async with self.wallet_state_manager.new_action_scope(
                    self.wallet_state_manager.tx_config, push=True
                ) as action_scope:
                    await self.wallet_state_manager.clawback_manager.auto_claim_coins(action_scope)
```
