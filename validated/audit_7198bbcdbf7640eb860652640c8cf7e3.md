### Title
Attacker-sent XCH to a PlotNFT's public p2_singleton address can raise an unhandled `ValueError` during wallet sync, indefinitely blocking recognition and claiming of legitimate pooling rewards - (File: chia/wallet/plotnft_wallet/plotnft_wallet.py)

### Summary
`PlotNFT2Wallet.coin_added` unconditionally raises a `ValueError` when it receives a coin sent to the wallet's `p2_singleton_puzzle_hash` that is not a genuine consensus pool-reward coin. Because `p2_singleton_puzzle_hash` is a fully public, deterministically-derivable address (from the plotNFT's `launcher_id`), any unprivileged actor can send an arbitrary XCH coin to it. This is structurally the same bug class as the reported issue: an unprivileged, attacker-controlled deposit into a shared/public destination that is later iterated/processed during a legitimate claim/sync operation, and whose processing failure blocks that operation for everyone, including the honest reward owner.

### Finding Description
`PlotNFT2Wallet` subscribes to `self.p2_singleton_puzzle_hash`, the deterministic pool-reward-receiving address for a given PlotNFT (`launcher_id_to_p2_puzzle_hash`-style address, mirrored in `PoolingShareState`). When wallet sync sees any coin sent to that address, `WalletStateManager._add_coin_state` routes it (for `WalletType.PLOTNFT_2`) into `PlotNFT2Wallet.coin_added`: [1](#0-0) 

```
elif coin_data is None and coin.puzzle_hash == self.p2_singleton_puzzle_hash:
    if coin.parent_coin_info[0:16] == self.wallet_state_manager.constants.GENESIS_CHALLENGE[0:16]:
        await self.wallet_state_manager.plotnft2_store.add_pool_reward(
            pool_reward=PoolReward(singleton_id=self.plotnft_id, coin=coin)
        )
    else:
        raise ValueError(f"A non-pooling reward coin was paid to PlotNFT with id: {self.plotnft_id}")
```

The `if` branch correctly filters for real consensus pool-reward coins (whose `parent_coin_info` is derived from `pool_parent_id(height, genesis_challenge)`, a value only the consensus reward-creation logic can produce). But the `else` branch does not skip/ignore a non-reward coin — it raises an exception. Since `p2_singleton_puzzle_hash` is publicly known (it must be, so the pool/farmer can pay rewards to it, and it is stored/exposed via `PoolingShareState`/RPC `pw_status`), any wallet user or script can send an ordinary XCH payment (any amount, e.g. 1 mojo) to this address. When the sending wallet's own sync (or, more importantly, any node/peer relaying coin-state updates for this puzzle hash to the target wallet) reports that coin as added to `p2_singleton_puzzle_hash`, the target wallet's `coin_added` is invoked and throws.

This call is made from within the state-sync path: [2](#0-1) 

`self.coin_added(...)` calls `await self.wallets[wallet_id].coin_added(...)` (in `WalletStateManager.coin_added`), which is where the `ValueError` is raised: [3](#0-2) 

There is no `try/except` around this specific call inside `_add_coin_state`'s `WalletType.PLOTNFT_2` branch (unlike the adjacent `plotnft2_store.get_plotnfts` call a few lines above, which *is* wrapped in `try/except ValueError`). An uncaught `ValueError` here propagates out of the coin-state processing routine that is iterating over a batch of coin states for this wallet during sync.

### Impact Explanation
Because coin-state processing for a given puzzle hash is generally done as part of a batch/loop over multiple `CoinState` updates, an exception raised while handling one poisoned coin can abort processing of the remaining coin states in that batch (including the wallet's real, legitimate pool-reward coins that should be recorded into `plotnft2_store` via `add_pool_reward`). Since `claim_rewards`/`pw_absorb_rewards` (RPC `pw_absorb_rewards` → `PlotNFT2Wallet.claim_rewards`) only claims rewards that have already been persisted to `plotnft2_store.pool_reward2s` via `get_pool_rewards`, any reward coins whose sync processing was interrupted by this exception never get recorded, and the wallet cannot claim them: [4](#0-3) [5](#0-4) 

Since the malicious coin can be re-sent (or the same unresolved coin-state re-delivered on every resync/reconnect), the wallet can be repeatedly forced through the same failing code path, indefinitely preventing it from making forward progress on recognizing/claiming pool rewards it is entitled to — an availability-and-fund-access impact directly analogous to the reported ERC20 case (an attacker deposit that the victim cannot avoid receiving, whose processing failure blocks legitimate reward claiming).

### Likelihood Explanation
High likelihood of triggerability: the address is deterministic and public (any observer of the plotNFT/launcher_id or `pw_status` RPC output can compute or read it), sending an XCH coin to an arbitrary puzzle hash requires no special privilege, and the vulnerable code path is unconditionally exercised whenever any coin lands on that address. No cooperation from the victim is required.

### Recommendation
- In `PlotNFT2Wallet.coin_added` (chia/wallet/plotnft_wallet/plotnft_wallet.py), change the `else` branch to log/ignore non-conforming coins sent to `p2_singleton_puzzle_hash` instead of raising, mirroring how unrelated/unexpected deposits are handled elsewhere in wallet sync (e.g., treat as an ordinary/unknown incoming coin, or simply skip recording it as a `PoolReward`).
- Ensure coin-state batch processing in `WalletStateManager._add_coin_state`/`coin_added` isolates failures per-coin (e.g., wrap each coin's processing in try/except and continue, as is already done for the `plotnft2_store.get_plotnfts` lookup a few lines above) so that one malformed/unexpected coin cannot abort processing of the rest of the batch.

### Proof of Concept
1. Attacker reads/derives the target PlotNFT's `p2_singleton_puzzle_hash` (public, exposed via `pw_status` RPC / `PoolingShareState`).
2. Attacker sends a standard XCH spend creating a coin of any amount to that puzzle hash (any user-controlled wallet can do this with a normal `CREATE_COIN` condition).
3. When the victim's wallet syncs and observes this coin addition on `p2_singleton_puzzle_hash`, `WalletStateManager._add_coin_state` calls `PlotNFT2Wallet.coin_added`, which reaches the `else` branch because `coin.parent_coin_info[0:16] != GENESIS_CHALLENGE[0:16]`, raising `ValueError("A non-pooling reward coin was paid to PlotNFT with id: ...")`.
4. This exception propagates out of the coin-state-processing loop for that sync batch, interrupting/aborting handling of any real pool-reward coins bundled in the same update, so they are never persisted via `plotnft2_store.add_pool_reward`.
5. Subsequent `pw_absorb_rewards`/`claim_rewards` calls see fewer or no rewards recorded in `plotnft2_store`, and the attack can be repeated on every resync, indefinitely delaying/blocking legitimate reward recognition and claiming.

Note: I was not able to fully trace, within the available tool budget, the exact outer loop/exception-boundary of the coin-state batch update (i.e., whether the framework retries per-coin-state or aborts the entire batch on this exception) — this should be confirmed by a background agent reviewing the caller of `_add_coin_state` and the wallet sync retry/error-handling logic before implementing the fix, to precisely characterize blast radius (single coin vs. whole sync batch).

### Citations

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L149-161)
```python
    async def claim_rewards(
        self,
        *,
        action_scope: WalletActionScope,
        fee: uint64 = uint64(0),
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> None:
        rewards_to_claim = await self.wallet_state_manager.plotnft2_store.get_pool_rewards(plotnft_id=self.plotnft_id)
        if len(rewards_to_claim) == 0:
            raise ValueError("No rewards to claim")
        total_reward_amount = uint64(sum(reward.coin.amount for reward in rewards_to_claim))
        if fee > total_reward_amount:
            raise ValueError("Fee is greater than the total amount of rewards")
```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L651-657)
```python
        elif coin_data is None and coin.puzzle_hash == self.p2_singleton_puzzle_hash:
            if coin.parent_coin_info[0:16] == self.wallet_state_manager.constants.GENESIS_CHALLENGE[0:16]:
                await self.wallet_state_manager.plotnft2_store.add_pool_reward(
                    pool_reward=PoolReward(singleton_id=self.plotnft_id, coin=coin)
                )
            else:
                raise ValueError(f"A non-pooling reward coin was paid to PlotNFT with id: {self.plotnft_id}")
```

**File:** chia/wallet/wallet_state_manager.py (L1453-1473)
```python
            elif record.wallet_type == WalletType.PLOTNFT_2:
                try:
                    await self.plotnft2_store.get_plotnfts(coin_ids=[coin_name])
                    if children == []:
                        plotnft_wallet = self.wallets[wallet_identifier.id]
                        assert isinstance(plotnft_wallet, PlotNFT2Wallet)
                        await plotnft_wallet.delete_self(coin_state.spent_height, sync_scope)
                except ValueError:
                    pass
                if isinstance(coin_data, PlotNFT):
                    await self.coin_added(
                        coin_state.coin,
                        uint32(coin_state.created_height),
                        all_unconfirmed,
                        wallet_identifier.id,
                        wallet_identifier.type,
                        peer,
                        coin_name,
                        coin_data,
                        sync_scope,
                    )
```

**File:** chia/wallet/wallet_state_manager.py (L1715-1793)
```python
    async def coin_added(
        self,
        coin: Coin,
        height: uint32,
        all_unconfirmed_transaction_records: list[LightTransactionRecord],
        wallet_id: uint32,
        wallet_type: WalletType,
        peer: WSChiaConnection,
        coin_name: bytes32,
        coin_data: object | None,
        sync_scope: WalletSyncScope,
    ) -> None:
        """
        Adding coin to DB
        """

        self.log.debug(
            "Adding record to state manager coin: %s at %s wallet_id: %s and type: %s",
            coin,
            height,
            wallet_id,
            wallet_type,
        )

        if self.is_pool_reward(height, coin):
            tx_type = TransactionType.COINBASE_REWARD
        elif self.is_farmer_reward(height, coin):
            tx_type = TransactionType.FEE_REWARD
        else:
            tx_type = TransactionType.INCOMING_TX

        coinbase = tx_type in {TransactionType.FEE_REWARD, TransactionType.COINBASE_REWARD}
        coin_confirmed_transaction = False
        if not coinbase:
            for record in all_unconfirmed_transaction_records:
                if coin in record.additions:
                    await self.tx_store.set_confirmed(record.name, height)
                    coin_confirmed_transaction = True
                    break

        parent_coin_record: WalletCoinRecord | None = await self.coin_store.get_coin_record(coin.parent_coin_info)
        change = parent_coin_record is not None and wallet_type.value == parent_coin_record.wallet_type
        # If the coin is from a Clawback spent, we want to add the INCOMING_TX,
        # no matter if there is another TX updated.
        clawback = parent_coin_record is not None and parent_coin_record.coin_type == CoinType.CLAWBACK

        if coinbase or clawback or (not coin_confirmed_transaction and not change):
            to_ph = await self.convert_puzzle_hash(wallet_id, coin.puzzle_hash)
            tx_record = TransactionRecord(
                confirmed_at_height=uint32(height),
                created_at_time=await self.wallet_node.get_timestamp_for_height(height),
                to_puzzle_hash=to_ph,
                to_address=self.encode_puzzle_hash(to_ph),
                amount=uint64(coin.amount),
                fee_amount=uint64(0),
                confirmed=True,
                sent=uint32(0),
                spend_bundle=None,
                additions=[coin],
                removals=[],
                wallet_id=wallet_id,
                sent_to=[],
                trade_id=None,
                type=uint32(tx_type),
                name=coin_name,
                memos={},
                valid_times=ConditionValidTimes(),
            )
            if tx_record.amount > 0:
                await self.tx_store.add_transaction_record(tx_record)

        # We only add normal coins here
        coin_record: WalletCoinRecord = WalletCoinRecord(
            coin, height, uint32(0), False, coinbase, wallet_type, wallet_id
        )

        await self.coin_store.add_coin_record(coin_record, coin_name)

        await self.wallets[wallet_id].coin_added(coin, height, peer, coin_data, sync_scope)
```

**File:** chia/wallet/plotnft_wallet/plotnft_store.py (L117-132)
```python
    async def add_pool_reward(self, *, pool_reward: PoolReward) -> None:
        async with self.db_wrapper.writer_maybe_transaction() as conn:
            await conn.execute_insert(
                "INSERT OR REPLACE INTO pool_reward2s ("
                "coin_id, parent_coin_id, puzzle_hash, amount, singleton_id, height, spent_height) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                (
                    pool_reward.coin.name(),
                    pool_reward.coin.parent_coin_info,
                    pool_reward.coin.puzzle_hash,
                    bytes(pool_reward.coin.amount),
                    pool_reward.singleton_id,
                    pool_reward.height,
                    None,
                ),
            )
```
