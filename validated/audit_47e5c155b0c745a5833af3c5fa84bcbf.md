### Title
Unfiltered, No-Minimum-Amount Clawback Coins Allow Grieving/DoS Against a Victim Wallet - (File: `chia/wallet/clawback_manager.py`)

### Summary
Any wallet user can send an attacker-chosen address an unlimited number of clawback-decorated coins of arbitrarily small value (e.g. 1 mojo, short timelock). Because clawback merkle coins do not match the "standard wallet" puzzle-hash check used by the wallet's dust filter, they bypass `filter_spam` entirely and are unconditionally recorded into the victim's `coin_store` as `CoinType.CLAWBACK` records with no minimum-amount enforcement and no access control on who may send them. This is structurally the same bug class as the Radiant Capital finding: an unbounded, attacker-fillable per-victim list with no floor on claim size and no permissioning, which can be grown arbitrarily to degrade or halt normal wallet processing for the victim (auto-claim, coin selection, RPC enumeration of clawback coins).

### Finding Description
The wallet's dust/spam filter is implemented in `WalletStateManager.filter_spam` [1](#0-0) . It only suppresses small-value coin states when `is_standard_wallet_tx(cs)` (or a previously-seen standard puzzle hash) is true; any coin state that is not a standard-wallet payment — including clawback merkle coins, which use a distinctive clawback puzzle wrapper — falls into the `else: filtered_cs.append(cs)` branch and is **always** passed through for processing, regardless of amount: [2](#0-1) 

Once a coin state reaches `determine_coin_type`, if it matches a clawback puzzle it is handed unconditionally to `ClawbackManager.identify`: [3](#0-2) 

`ClawbackManager.identify` performs no minimum-amount check and no sender allow-listing before persisting the coin as a permanent `CoinType.CLAWBACK` record and a corresponding transaction record for the recipient: [4](#0-3) 

The recipient is also force-subscribed to the coin (`add_interested_coin_ids`) as soon as it is recognized as the recipient, independent of value: [5](#0-4) 

Any wallet holder can create such coins for an arbitrary target address using the existing `send` / `SendTransaction` clawback-decorator flow, with no restriction on the number of separate low-value clawback sends they may issue over time: [6](#0-5) 

This is directly analogous to the Radiant Capital bug: bounty claims (there, RDNT rewards placed into a 90-day vesting array) had no minimum claimable amount and no access control, allowing an attacker to flood a victim's `userEarnings` array with tiny entries until interactions became unusable. Here, clawback merkle coins (a per-recipient, per-coin vesting-like structure with its own timelock) can similarly be flooded into a victim's `coin_store`/`CoinType.CLAWBACK` set with no minimum amount and no sender permissioning, because the dust filter structurally excludes non-standard-wallet coin types from suppression.

### Impact Explanation
An attacker with no special privileges can repeatedly send dust-value clawback transactions to a target puzzle hash. Because these bypass `filter_spam`, the victim's wallet will accumulate an unbounded number of `CoinType.CLAWBACK` records. This directly degrades or halts legitimate wallet operations that iterate or query over this coin type for that user:
- `ClawbackManager.auto_claim_coins`, which loops over `get_coin_records(coin_type=CoinType.CLAWBACK, ...)` for every unspent clawback coin belonging to the victim on every relevant sync tick [7](#0-6) .
- The `spend_clawback_coins` RPC, which fetches and batches over all matching clawback coin records for manual claim/clawback operations [8](#0-7) .

A large enough flood of dust clawback coins can make these queries and batched claim/spend operations prohibitively slow or resource-exhausting for the victim's wallet, effectively bricking their ability to interact with (or clean up) their own clawback-coin state — a spend-triggered transaction-processing halt targeting a specific, attacker-chosen victim, achievable at negligible cost to the attacker (dust-value coins, minimal fee).

### Likelihood Explanation
High. The attack requires only a standard wallet and the already-exposed `send`/clawback-decorator transaction flow; no special network position, no leaked keys, and no cooperation from the victim are needed. The dust filter's exclusion of non-standard-wallet coin types (including clawback) from suppression is a direct code-level gap, not a theoretical one, and the `identify()` path performs no amount or sender checks whatsoever before persisting the coin.

### Recommendation
- Enforce a configurable minimum amount for recorded clawback coins, similar to `xch_spam_amount`, or extend `filter_spam`'s "small coin" suppression logic to cover clawback (and other non-standard) coin types rather than exempting them via the `is_standard_wallet_tx` check.
- Consider capping the number of unspent `CoinType.CLAWBACK` records tracked per wallet/puzzle hash, or coalescing/rate-limiting incoming clawback coins from the same sender/puzzle-hash pattern within a short time window (mirroring the "1-day epoch merge" remediation used in the referenced Radiant fix).
- Ensure `auto_claim_coins` and `spend_clawback_coins` bound their per-call work (e.g., via pagination/limits) so that even if a large backlog exists, single calls cannot be forced into unbounded cost.

### Proof of Concept
1. Attacker wallet A repeatedly issues `send` transactions with a `ClawbackPuzzleDecoratorOverride` to victim puzzle hash `V`, each transferring 1 mojo with a short `clawback_timelock` (e.g., 5 seconds), as supported by the existing CLI/RPC flow [6](#0-5) .
2. Each such coin, upon confirmation and sync, is recognized via `match_clawback_puzzle` and unconditionally recorded by `ClawbackManager.identify` into victim V's `coin_store` as a `CoinType.CLAWBACK` record, since `filter_spam` does not suppress non-standard-wallet coin types [2](#0-1) [4](#0-3) .
3. Repeating this thousands of times (at negligible aggregate cost, since amounts are 1 mojo plus minimal fee) grows victim V's set of unspent `CoinType.CLAWBACK` coins without bound.
4. Subsequent calls to `auto_claim_coins` (triggered automatically if the victim enables auto-claim) or to the `spend_clawback_coins` RPC now must iterate/query over this attacker-inflated set, degrading or stalling the victim's wallet's clawback-related operations [7](#0-6) [8](#0-7) .

### Citations

**File:** chia/wallet/wallet_state_manager.py (L1000-1006)
```python
        # Check if the coin is clawback
        clawback_coin_data = match_clawback_puzzle(uncurried, coin_spend.puzzle_reveal, coin_spend.solution)
        if clawback_coin_data is not None:
            return (
                await self.clawback_manager.identify(clawback_coin_data, coin_state, coin_spend, peer),
                clawback_coin_data,
            )
```

**File:** chia/wallet/wallet_state_manager.py (L1040-1068)
```python
    async def filter_spam(self, new_coin_state: list[CoinState]) -> list[CoinState]:
        xch_spam_amount = self.config.get("xch_spam_amount", 1000000)

        # No need to filter anything if the filter is set to 1 or 0 mojos
        if xch_spam_amount <= 1:
            return new_coin_state

        spam_filter_after_n_txs = self.config.get("spam_filter_after_n_txs", 200)
        small_unspent_count = await self.coin_store.count_small_unspent(xch_spam_amount)

        # if small_unspent_count > spam_filter_after_n_txs:
        filtered_cs: list[CoinState] = []
        is_standard_wallet_phs: set[bytes32] = set()

        for cs in new_coin_state:
            # Only apply filter to new coins being sent to our wallet, that are very small
            if (
                cs.created_height is not None
                and cs.spent_height is None
                and cs.coin.amount < xch_spam_amount
                and (cs.coin.puzzle_hash in is_standard_wallet_phs or await self.is_standard_wallet_tx(cs))
            ):
                is_standard_wallet_phs.add(cs.coin.puzzle_hash)
                if small_unspent_count < spam_filter_after_n_txs:
                    filtered_cs.append(cs)
                small_unspent_count += 1
            else:
                filtered_cs.append(cs)
        return filtered_cs
```

**File:** chia/wallet/clawback_manager.py (L88-92)
```python
        elif recipient_derivation_record is not None:
            self.log.info("Found Clawback merkle coin %s as the recipient.", coin_state.coin.name().hex())
            is_recipient = True
            # For the recipient we need to manually subscribe the merkle coin
            await self.add_interested_coin_ids([coin_state.coin.name()])
```

**File:** chia/wallet/clawback_manager.py (L93-145)
```python
        if is_recipient is not None:
            spend_bundle = WalletSpendBundle([coin_spend], G2Element())
            memos = compute_memos(spend_bundle)
            spent_height: uint32 = uint32(0)
            if coin_state.spent_height is not None:
                self.log.debug("Resync clawback coin: %s", coin_state.coin.name().hex())
                # Resync case
                spent_height = uint32(coin_state.spent_height)
                # Create Clawback outgoing transaction
                created_timestamp = await self.timestamp_for_height(uint32(coin_state.spent_height))
                clawback_coin_spend: CoinSpend = await fetch_coin_spend_for_coin_state(coin_state, peer)
                clawback_spend_bundle = WalletSpendBundle([clawback_coin_spend], G2Element())
                if await self.puzzle_store.puzzle_hash_exists(clawback_spend_bundle.additions()[0].puzzle_hash):
                    to_ph = (
                        metadata.sender_puzzle_hash
                        if clawback_spend_bundle.additions()[0].puzzle_hash == metadata.sender_puzzle_hash
                        else metadata.recipient_puzzle_hash
                    )
                    tx_record = TransactionRecord(
                        confirmed_at_height=uint32(coin_state.spent_height),
                        created_at_time=created_timestamp,
                        to_puzzle_hash=to_ph,
                        to_address=self.puzzle_hash_encoder(to_ph),
                        amount=uint64(coin_state.coin.amount),
                        fee_amount=uint64(0),
                        confirmed=True,
                        sent=uint32(0),
                        spend_bundle=clawback_spend_bundle,
                        additions=clawback_spend_bundle.additions(),
                        removals=clawback_spend_bundle.removals(),
                        wallet_id=uint32(1),
                        sent_to=[],
                        trade_id=None,
                        type=uint32(TransactionType.OUTGOING_CLAWBACK),
                        name=clawback_spend_bundle.name(),
                        memos=compute_memos(clawback_spend_bundle),
                        valid_times=ConditionValidTimes(),
                    )
                    await self.transaction_store.add_transaction_record(tx_record)
            coin_record = WalletCoinRecord(
                coin_state.coin,
                uint32(coin_state.created_height),
                spent_height,
                spent_height != 0,
                False,
                WalletType.STANDARD_WALLET,
                1,
                CoinType.CLAWBACK,
                VersionedBlob(ClawbackVersion.V1.value, bytes(metadata)),
            )
            # Add merkle coin
            await self.coin_store.add_coin_record(coin_record)
            # Add tx record
```

**File:** chia/wallet/clawback_manager.py (L177-205)
```python
    async def auto_claim_coins(self, action_scope: WalletActionScope) -> None:
        # Get unspent clawback coin
        current_timestamp = self.blockchain.get_latest_timestamp()
        clawback_coins: dict[Coin, ClawbackMetadata] = {}
        unspent_coins = await self.coin_store.get_coin_records(
            coin_type=CoinType.CLAWBACK,
            wallet_type=WalletType.STANDARD_WALLET,
            spent_range=UInt32Range(stop=uint32(0)),
            amount_range=UInt64Range(
                start=action_scope.config.tx_config.coin_selection_config.min_coin_amount,
                stop=action_scope.config.tx_config.coin_selection_config.max_coin_amount,
            ),
        )

        for coin in unspent_coins.records:
            try:
                metadata = coin.parsed_metadata()
                assert isinstance(metadata, ClawbackMetadata)
                if await metadata.is_recipient(self.puzzle_store):
                    coin_timestamp = await self.timestamp_for_height(coin.confirmed_block_height)
                    if current_timestamp - coin_timestamp >= metadata.time_lock:
                        clawback_coins[coin.coin] = metadata
                        if len(clawback_coins) >= self.auto_claim_batch_size:
                            await self.spend_clawback_coins(clawback_coins, self.auto_claim_tx_fee, action_scope)
                            clawback_coins = {}
            except Exception as e:
                self.log.error(f"Failed to claim clawback coin {coin.coin.name().hex()}: %s", e)
        if len(clawback_coins) > 0:
            await self.spend_clawback_coins(clawback_coins, self.auto_claim_tx_fee, action_scope)
```

**File:** chia/cmds/wallet_funcs.py (L384-407)
```python
    if typ == WalletType.STANDARD_WALLET:
        print("Submitting transaction...")
        res: CATSpendResponse | SendTransactionResponse = await wallet_client.send_transaction(
            SendTransaction(
                wallet_id=uint32(wallet_id),
                amount=final_amount,
                address=address.original_address,
                fee=fee,
                memos=memos,
                push=push,
                puzzle_decorator=(
                    [
                        ClawbackPuzzleDecoratorOverride(
                            decorator=PuzzleDecoratorType.CLAWBACK.name,
                            clawback_timelock=uint64(clawback_time_lock),
                        )
                    ]
                    if clawback_time_lock > 0
                    else None
                ),
            ),
            tx_config=tx_config_loader.load_tx_config(mojo_per_unit, wallet_info.config, fingerprint),
            timelock_info=condition_valid_times,
        )
```

**File:** chia/wallet/wallet_rpc_api.py (L1492-1520)
```python
        coin_records = await self.service.wallet_state_manager.coin_store.get_coin_records(
            coin_id_filter=HashFilter.include(request.coin_ids),
            coin_type=CoinType.CLAWBACK,
            wallet_type=WalletType.STANDARD_WALLET,
            spent_range=UInt32Range(stop=uint32(0)),
        )

        batch_size = (
            request.batch_size
            if request.batch_size is not None
            else self.service.wallet_state_manager.clawback_manager.auto_claim_batch_size
        )
        records_list = list(coin_records.coin_id_to_record.values())
        for i in range(0, len(records_list), batch_size):
            try:
                coin_batch = {
                    coin_record.coin: coin_record.parsed_metadata() for coin_record in records_list[i : i + batch_size]
                }
            except WalletCoinRecordMetadataParsingError as e:
                log.error("Failed to spend clawback coin: %s", e)
                continue
            await self.service.wallet_state_manager.clawback_manager.spend_clawback_coins(
                # Semantically, we're guaranteed the right type here, but the typing isn't there
                coin_batch,  # type: ignore[arg-type]
                request.fee,
                action_scope,
                request.force,
                extra_conditions=extra_conditions,
            )
```
