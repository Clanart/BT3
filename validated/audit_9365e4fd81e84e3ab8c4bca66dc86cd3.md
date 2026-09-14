### Title
Clawback/claim coins can become permanently stuck when `spend_clawback_coins` marks the incoming transaction as "sent" before the spend bundle is actually created and pushed - (File: `chia/wallet/clawback_manager.py`)

### Summary
`ClawbackManager.spend_clawback_coins` marks the per-coin `incoming_tx` record as `MempoolInclusionStatus.PENDING` via `increment_sent` *before* the overall spend bundle (including the fee-paying tandem transaction) is guaranteed to be successfully built and queued. If bundle construction fails after this point (e.g., the fee tandem transaction fails, or any other exception is raised in the unguarded code that follows), the coin's underlying transaction record is left marked as sent/pending with no corresponding spend bundle ever produced. A subsequent, non-`force` claim/clawback attempt for that same coin will then be silently skipped because the code treats it as "already in a pending spend bundle," leaving the user unable to claim or claw back that coin through normal means.

### Finding Description
In `spend_clawback_coins`, for each clawback coin the code:
1. Looks up `incoming_tx` and skips (with `continue`) if `incoming_tx.sent > 0 and not force` [1](#0-0) 
2. Builds the coin's individual spend, appends it to `coin_spends`, and then immediately calls `await self.transaction_store.increment_sent(incoming_tx.name, "", MempoolInclusionStatus.PENDING, None)` to "prevent double spend and mark it is pending" [2](#0-1) 

This `increment_sent` call happens **inside the per-coin try/except block**, so failures while generating that specific coin's own spend are caught. However, the aggregation, fee-tandem-transaction construction, and final `tx_record` assembly that happen **after** the loop are **not** wrapped in the same try/except: [3](#0-2) 

If an exception is raised anywhere in that unguarded block — for example `create_tandem_xch_tx` failing due to insufficient XCH to pay the fee, or any other error in constructing/aggregating the final `spend_bundle` — the exception propagates out of `spend_clawback_coins` and the final combined `tx_record` (containing the actual clawback/claim spend bundle) is **never appended to `action_scope`'s side effects**, meaning it is never pushed to the mempool. But the individual `incoming_tx` records for every coin already looped over have already had `increment_sent` called with `PENDING` status, so `incoming_tx.sent` is now `> 0`.

On the next call to `spend_clawback_coins` (e.g. via `spend_clawback_coins` RPC in `chia/wallet/wallet_rpc_api.py` [4](#0-3)  or via `auto_claim_coins` [5](#0-4) ), the check `if incoming_tx.sent > 0 and not force: ... continue` causes the coin to be silently skipped, since no real spend bundle was ever created or broadcast to reset/consume the coin's state. Without discovering and manually setting `force=True` on `SpendClawbackCoins`, the coin can never be claimed/clawed back through the normal RPC/UI flow again.

This closely mirrors the reported Vader bug class: an irreversible, one-shot state marker (there: the merkle proof being consumed; here: `incoming_tx.sent`/`is_valid()` transaction bookkeeping) is committed before the actual value-transferring action is guaranteed to succeed, permanently locking the user out of retrying the action through the standard path.

### Impact Explanation
A user's clawback (self-sent) or claim (received) coin can become permanently unreachable through the standard, non-`force` RPC/UI flow if the underlying spend bundle build/push fails after the per-coin `increment_sent` call but before the final transaction record is queued (e.g., insufficient balance to cover the requested fee, or any other exception in the post-loop code). The value isn't stolen by an attacker, but the legitimate owner effectively loses practical access to their own funds unless they know to pass `force=True`, which is not exposed prominently in the normal wallet UI flow. This matches a Medium-severity "user locks themselves out of their own funds" bug class, analogous to the VETH/VADER conversion issue.

### Likelihood Explanation
This requires a normal user action (attempting a claim or clawback, particularly with a fee under `spend_clawback_coins`) combined with a failure condition after the per-coin loop (most plausibly, insufficient XCH balance to cover the requested fee via `create_tandem_xch_tx`, or a transient error during bundle aggregation). This is a realistic, unprivileged usage scenario (any wallet owner attempting to claim/claw back a coin with a fee they can't quite afford, or hitting any exception in the unguarded post-loop code) rather than requiring any adversarial third party.

### Recommendation
Do not call `increment_sent`/mark the per-coin `incoming_tx` as pending until the complete spend bundle (including any fee tandem transaction) has been successfully constructed and is guaranteed to be queued in `action_scope`'s side effects. Concretely:
- Move the `increment_sent` calls to after the fee-tandem transaction and final `tx_record` are successfully built (i.e., right before/at the same time the final `tx_record` is appended to `action_scope`'s side effects at [6](#0-5) ), or
- Wrap the fee/tandem construction and final aggregation code (lines 264-298) in a try/except that, on failure, reverts (or never applies) the `increment_sent` calls made for the coins in that batch, so a legitimate retry without `force` is not silently swallowed.

### Proof of Concept
1. User has an unspent clawback/claim coin with a `ClawbackMetadata` entry and calls the `spend_clawback_coins` RPC with `fee > 0` but insufficient additional XCH balance to cover that fee.
2. In `spend_clawback_coins` [7](#0-6) , the per-coin loop successfully builds `coin_spend`, appends it to `coin_spends`, and calls `increment_sent(..., MempoolInclusionStatus.PENDING, None)`, setting `incoming_tx.sent > 0`.
3. Execution proceeds to the `if fee > 0:` block [8](#0-7) ; `create_tandem_xch_tx` raises (e.g. insufficient funds) because there isn't enough XCH to cover `fee`. This is not caught by any surrounding try/except at this point in the function.
4. The final `tx_record` (lines 300-321) is never constructed/appended, so no spend bundle for this attempt ever reaches the mempool.
5. User retries `spend_clawback_coins` (without `force`) for the same coin; the check `if incoming_tx.sent > 0 and not force: continue` at line 226 causes the coin to be silently skipped every time, and the RPC returns an empty `transaction_ids` list, leaving the coin stuck.

### Citations

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

**File:** chia/wallet/clawback_manager.py (L207-263)
```python
    async def spend_clawback_coins(
        self,
        clawback_coins: dict[Coin, ClawbackMetadata],
        fee: uint64,
        action_scope: WalletActionScope,
        force: bool = False,
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> None:
        assert len(clawback_coins) > 0
        coin_spends: list[CoinSpend] = []
        message = std_hash(b"".join([c.name() for c in clawback_coins.keys()]))
        derivation_record = None
        amount = uint64(0)
        for coin, metadata in clawback_coins.items():
            try:
                self.log.info(f"Claiming clawback coin {coin.name().hex()}")
                # Get incoming tx
                incoming_tx = await self.transaction_store.get_transaction_record(coin.name())
                assert incoming_tx is not None, f"Cannot find incoming tx for clawback coin {coin.name().hex()}"
                if incoming_tx.sent > 0 and not force:
                    self.log.error(
                        f"Clawback coin {coin.name().hex()} is already in a pending spend bundle. {incoming_tx}"
                    )
                    continue

                recipient_puzhash = metadata.recipient_puzzle_hash
                sender_puzhash = metadata.sender_puzzle_hash
                is_recipient: bool = await metadata.is_recipient(self.puzzle_store)
                if is_recipient:
                    derivation_record = await self.puzzle_store.get_derivation_record_for_puzzle_hash(recipient_puzhash)
                else:
                    derivation_record = await self.puzzle_store.get_derivation_record_for_puzzle_hash(sender_puzhash)
                assert derivation_record is not None
                amount = uint64(amount + coin.amount)
                # Remove the clawback hint since it is unnecessary for the XCH coin
                memos: list[bytes] = [] if len(incoming_tx.memos) == 0 else next(iter(incoming_tx.memos.items()))[1][1:]
                inner_puzzle = self.xch_wallet.puzzle_for_pk(derivation_record.pubkey)
                inner_solution = self.xch_wallet.make_solution(
                    primaries=[
                        CreateCoin(
                            derivation_record.puzzle_hash,
                            uint64(coin.amount),
                            memos,  # Forward memo of the first coin
                        )
                    ],
                    conditions=(
                        extra_conditions
                        if len(coin_spends) > 0 or fee == 0
                        else (*extra_conditions, CreateCoinAnnouncement(message))
                    ),
                )
                coin_spend: CoinSpend = generate_clawback_spend_bundle(coin, metadata, inner_puzzle, inner_solution)
                coin_spends.append(coin_spend)
                # Update incoming tx to prevent double spend and mark it is pending
                await self.transaction_store.increment_sent(incoming_tx.name, "", MempoolInclusionStatus.PENDING, None)
            except Exception as e:
                self.log.error(f"Failed to create clawback spend bundle for {coin.name().hex()}: {e}")
```

**File:** chia/wallet/clawback_manager.py (L264-321)
```python
        if len(coin_spends) == 0:
            return
        spend_bundle = WalletSpendBundle(coin_spends, G2Element())
        if fee > 0:
            async with self.action_scope_sandbox(action_scope.config.tx_config, False) as inner_action_scope:
                async with action_scope.use() as interface:
                    async with inner_action_scope.use() as inner_interface:
                        inner_interface.side_effects.selected_coins = interface.side_effects.selected_coins
                    await self.xch_wallet.create_tandem_xch_tx(
                        fee,
                        inner_action_scope,
                        extra_conditions=(
                            AssertCoinAnnouncement(asserted_id=coin_spends[0].coin.name(), asserted_msg=message),
                        ),
                    )
                    async with inner_action_scope.use() as inner_interface:
                        # This should not be looked to for best practice.
                        # Ideally, the two spend bundles can exist separately on each tx record until they are pushed.
                        # This is not very supported behavior at the moment
                        # so to avoid any potential backwards compatibility issues,
                        # we're moving the spend bundle from this TX to the main
                        interface.side_effects.transactions.extend(
                            [replace(tx, spend_bundle=None) for tx in inner_interface.side_effects.transactions]
                        )
                        interface.side_effects.selected_coins.extend(inner_interface.side_effects.selected_coins)
            spend_bundle = WalletSpendBundle.aggregate(
                [
                    spend_bundle,
                    *(
                        tx.spend_bundle
                        for tx in inner_action_scope.side_effects.transactions
                        if tx.spend_bundle is not None
                    ),
                ]
            )
        assert derivation_record is not None
        tx_record = TransactionRecord(
            confirmed_at_height=uint32(0),
            created_at_time=uint64(time.time()),
            to_puzzle_hash=derivation_record.puzzle_hash,
            to_address=self.puzzle_hash_encoder(derivation_record.puzzle_hash),
            amount=amount,
            fee_amount=uint64(fee),
            confirmed=False,
            sent=uint32(0),
            spend_bundle=spend_bundle,
            additions=spend_bundle.additions(),
            removals=spend_bundle.removals(),
            wallet_id=uint32(1),
            sent_to=[],
            trade_id=None,
            type=uint32(TransactionType.OUTGOING_CLAWBACK),
            name=spend_bundle.name(),
            memos=compute_memos(spend_bundle),
            valid_times=parse_timelock_info(extra_conditions),
        )
        async with action_scope.use() as interface:
            interface.side_effects.transactions.append(tx_record)
```

**File:** chia/wallet/wallet_rpc_api.py (L1479-1523)
```python
    async def spend_clawback_coins(
        self,
        request: SpendClawbackCoins,
        action_scope: WalletActionScope,
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> SpendClawbackCoinsResponse:
        """Spend clawback coins that were sent (to claw them back) or received (to claim them).

        :param coin_ids: list of coin ids to be spent
        :param batch_size: number of coins to spend per bundle
        :param fee: transaction fee in mojos
        :return:
        """
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

        # tx_endpoint will fill in the default values here
        return SpendClawbackCoinsResponse(unsigned_transactions=[], transactions=[], transaction_ids=[])
```
