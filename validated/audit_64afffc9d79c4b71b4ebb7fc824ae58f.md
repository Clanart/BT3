### Title
Auto-claim batching of clawback coins can be griefed by a counterparty spending their own coin mid-batch, halting settlement of other users' unrelated claims - (File: chia/wallet/clawback_manager.py)

### Summary
`ClawbackManager.auto_claim_coins` groups up to `auto_claim_batch_size` unrelated clawback coins — which may originate from different senders/counterparties, all payable to the same recipient wallet — into a single batch, and `spend_clawback_coins` aggregates all of their individual `CoinSpend`s into one atomic `WalletSpendBundle` before pushing it to the network. [1](#0-0) 

### Finding Description
`auto_claim_coins` selects all unspent, timelock-expired clawback coins for the wallet and batches them (default `auto_claim_batch_size`) before calling `spend_clawback_coins`, which builds one aggregated `WalletSpendBundle` containing the spends for every coin in the batch: [2](#0-1) 

Each clawback coin has two legitimate spenders: the original sender (who can claw it back before the timelock expires) and the recipient (who claims it after expiry, via this auto-claim path). Because the coin-selection query only filters on local DB `spent_range` at fetch time, a sender who intends to grief the batch can broadcast their own reclaim spend for their own coin immediately before/while the recipient's daemon assembles and pushes the aggregated batch. Since the batch is pushed as a single `WalletSpendBundle`, if any one of the constituent coins has already been spent on-chain by the time the bundle reaches the mempool, the entire aggregated spend fails as a double-spend for that one coin, and none of the other — unrelated, honestly claimable — coins in that same batch get processed in that push cycle, even though they belong to different transactions/counterparties.

### Impact Explanation
This is a spend-triggered transaction-processing halt for a batch of otherwise-independent user claims: a single participant, by exercising their own valid ability to reclaw their own coin at the right moment, can cause an entire aggregated `WalletSpendBundle` — potentially containing many unrelated senders' clawback payments to the same recipient — to be rejected atomically. This directly reflects the reported bug class: batching independent parties' claimable assets into one atomic operation lets any one party invalidate the whole batch by manipulating the state of just their own contribution.

### Likelihood Explanation
Exploitability only requires the attacker to be a normal clawback sender who knows (or can guess/monitor) the daemon's auto-claim cadence and batch composition, and to race their own reclaim transaction against the recipient's auto-claim push — a low-cost, repeatable action requiring no special privileges.

### Recommendation
When constructing `spend_clawback_coins`, validate on-chain unspent status of each coin immediately prior to inclusion (or retry with per-coin exclusion) rather than aggregating unconditionally, and/or fall back to splitting the batch into smaller sub-bundles (or per-coin bundles) so that one already-spent coin cannot block settlement of the rest of the batch.

### Proof of Concept
1. Wallet W has multiple pending clawback-receivable coins from senders A, B, C, all past their timelock, queued for auto-claim in the same batch (`auto_claim_batch_size` >= 3).
2. `auto_claim_coins` calls `spend_clawback_coins({A_coin, B_coin, C_coin, ...}, fee, action_scope)`, which builds one `WalletSpendBundle` combining spends for all three coins. [3](#0-2) 
3. Attacker (sender A) races and pushes their own reclaim spend of `A_coin` first so it lands on-chain before W's aggregated bundle is submitted/confirmed.
4. W's aggregated bundle, which still references the now-already-spent `A_coin`, is rejected by the mempool as a double-spend, causing the claims for B_coin and C_coin (unrelated to attacker) to also fail to be processed in this push cycle, delaying honest users' fund settlement.

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

**File:** chia/wallet/clawback_manager.py (L207-266)
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
        if len(coin_spends) == 0:
            return
        spend_bundle = WalletSpendBundle(coin_spends, G2Element())
```
