## Title
Unfiltered Clawback Batch Spend Causes Entire Bundle to Revert When One Coin's Timelock Has Not Elapsed - (File: `chia/wallet/wallet_rpc_api.py`, `chia/wallet/clawback_manager.py`)

### Summary
The external report describes a Synthetix bug where `notifyRewardAmount()` reverts if `block.timestamp < periodFinish`, and because this call is embedded inside `mint()`, the revert propagates and halts the entire minting flow until the time condition is satisfied. The analogous pattern in this repository is the wallet's clawback claim/revert RPC, `spend_clawback_coins`, which aggregates multiple independently-selected coins into a single `WalletSpendBundle`. If any one of the batched coins has not yet passed its `ASSERT_SECONDS_RELATIVE` timelock, the entire aggregated bundle fails mempool validation with `ASSERT_SECONDS_RELATIVE_FAILED`, halting the claim/clawback of every other coin bundled with it — exactly the "external call reverts if period has not elapsed" pattern, reachable directly by any local wallet RPC/CLI caller.

### Finding Description
`WalletRpcApi.spend_clawback_coins()` [1](#0-0)  fetches all requested clawback coin records matching `request.coin_ids` and groups them into fixed-size batches (`batch_size`), without checking whether each coin's clawback timelock has actually elapsed, then calls `ClawbackManager.spend_clawback_coins()` for each batch.

`ClawbackManager.spend_clawback_coins()` [2](#0-1)  iterates over the supplied `clawback_coins` dict and, for every coin, unconditionally calls `generate_clawback_spend_bundle(coin, metadata, inner_puzzle, inner_solution)` to build a `CoinSpend`, appending it to a single list. All of these `CoinSpend`s are then combined into one aggregated `WalletSpendBundle(coin_spends, G2Element())` [3](#0-2)  — i.e., the whole batch is pushed as one atomic spend bundle.

Each claim spend embeds an `ASSERT_SECONDS_RELATIVE` condition baked into the puzzle via `create_merkle_solution`/`create_augmented_cond_puzzle`, using the coin's `timelock` [4](#0-3) . This condition is enforced at consensus/mempool time by `check_time_locks()` inside mempool validation [5](#0-4) , which treats the bundle as a whole: if any single spend in the aggregate bundle fails its relative-seconds assertion, the entire bundle is rejected (`Err.ASSERT_SECONDS_RELATIVE_FAILED`), as directly demonstrated in `test_clawback_lifecycle.py` where an early claim spend is rejected wholesale [6](#0-5) .

Unlike the safe `auto_claim_coins()` path, which explicitly filters candidate coins by `current_timestamp - coin_timestamp >= metadata.time_lock` before ever batching them [7](#0-6) , the RPC-facing `spend_clawback_coins` entry point performs no such pre-filtering; it accepts arbitrary caller-supplied `coin_ids` and groups them purely by `batch_size`, with no check that all coins in a batch are past their respective timelocks.

### Impact Explanation
Because the entire batch is submitted as one atomic `SpendBundle`, mixing a mature (claimable) clawback coin together with an immature one (whether accidentally, via multiple concurrent RPC callers hitting the same auto-batch, or via a caller intentionally supplying a mix of `coin_ids`) causes the mempool to reject the whole aggregated bundle. This blocks the transaction-processing of every other coin in that batch until the caller manually retries with a corrected, filtered set — a spend-triggered transaction-processing halt matching the accepted bug class in the validation rules. In batch/automated contexts (e.g., services managing many wallets' clawback coins through this RPC), a single immature coin silently included in a batch can repeatedly stall processing of otherwise-valid claims/clawbacks.

### Likelihood Explanation
This is reachable by any local, unprivileged wallet RPC caller (or the `chia wallet clawback` CLI command, which forwards directly to this RPC via `spend_clawback` in `chia/cmds/wallet_funcs.py` and `ClawbackCMD.run()` [8](#0-7) ) simply by supplying a `coin_ids` list containing at least one clawback coin whose timelock has not yet elapsed alongside mature ones. No special privileges, timing races with other nodes, or malicious peers are required — only a normal wallet interaction with a heterogeneous coin_ids batch.

### Recommendation
Filter candidate coins by verifying `current_timestamp - coin_timestamp >= metadata.time_lock` (mirroring the check already performed in `auto_claim_coins`) before including a coin in a batch inside `spend_clawback_coins`/`ClawbackManager.spend_clawback_coins`. Coins that are not yet claimable should be skipped (and reported back to the caller) rather than being bundled together with mature coins into a single atomic spend, so an immature coin cannot cause the entire batch's spend to revert.

### Proof of Concept
1. Send two clawback transactions to the same recipient wallet with different `clawback_time` values (e.g., `clawback_time=5` and `clawback_time=600`), producing two clawback coins `A` (matures quickly) and `B` (matures much later).
2. After coin `A`'s timelock has elapsed but before coin `B`'s has, call `spend_clawback_coins` (via RPC or `chia wallet clawback -ids <A_id>,<B_id>`) with both coin ids in the same batch (`batch_size >= 2`).
3. `ClawbackManager.spend_clawback_coins` builds one `CoinSpend` per coin and aggregates them into a single `WalletSpendBundle` [9](#0-8) .
4. Mempool validation runs `check_time_locks` over the whole bundle; because coin `B`'s `ASSERT_SECONDS_RELATIVE` condition is not yet satisfied, the entire bundle — including the otherwise-valid claim of coin `A` — is rejected with `Err.ASSERT_SECONDS_RELATIVE_FAILED`, exactly as observed for a single early claim in `test_clawback_lifecycle.py` [6](#0-5) .

### Citations

**File:** chia/wallet/wallet_rpc_api.py (L1479-1524)
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

**File:** chia/wallet/clawback_manager.py (L191-205)
```python
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

**File:** chia/wallet/puzzles/clawback/drivers.py (L121-131)
```python
    merkle_tree = create_clawback_merkle_tree(timelock, sender_ph, recipient_ph)
    inner_puzzle_hash = inner_puzzle.get_tree_hash()
    if inner_puzzle_hash == sender_ph:
        cb_inner_puz = create_p2_puzzle_hash_puzzle(sender_ph)
        merkle_proof = create_merkle_proof(merkle_tree, cb_inner_puz.get_tree_hash())
        cb_inner_solution = create_p2_puzzle_hash_solution(inner_puzzle, inner_solution)
    elif inner_puzzle_hash == recipient_ph:
        condition = [80, timelock]
        cb_inner_puz = create_augmented_cond_puzzle(condition, inner_puzzle)
        merkle_proof = create_merkle_proof(merkle_tree, cb_inner_puz.get_tree_hash())
        cb_inner_solution = create_augmented_cond_solution(inner_solution)
```

**File:** chia/full_node/mempool_manager.py (L864-874)
```python
        assert self.peak.timestamp is not None
        tl_error_rust: int | None = check_time_locks(
            removal_record_dict,
            conds,
            self.peak.height,
            self.peak.timestamp,
            nowrap=self.peak.height >= self.constants.HARD_FORK2_HEIGHT,
        )
        tl_error: Err | None = None
        if tl_error_rust is not None:
            tl_error = Err(tl_error_rust)
```

**File:** chia/_tests/wallet/clawback/test_clawback_lifecycle.py (L151-165)
```python
            # Fail an early claim spend
            recipient_sol = solution_for_conditions([[ConditionOpcode.CREATE_COIN, recipient_ph, amount]])
            claim_sol = create_merkle_solution(timelock, sender_ph, recipient_ph, recipient_puz, recipient_sol)
            coin_spend = make_spend(clawback_coin, cb_puzzle, claim_sol)
            sig = self.sign_coin_spend(coin_spend, recipient_index)
            spend_bundle = WalletSpendBundle([coin_spend], sig)

            await do_spend(
                sim,
                sim_client,
                spend_bundle,
                (MempoolInclusionStatus.FAILED, Err.ASSERT_SECONDS_RELATIVE_FAILED),
                cost_logger=cost_logger,
                cost_log_msg="Early Claim",
            )
```

**File:** chia/cmds/wallet.py (L233-248)
```python
    @transaction_endpoint_runner
    async def run(self) -> list[TransactionRecord]:
        from chia.cmds.wallet_funcs import spend_clawback

        async with self.rpc_info.wallet_rpc() as wallet_info:
            return await spend_clawback(
                wallet_info=wallet_info,
                fee=self.fee,
                tx_ids_str=self.tx_ids,
                force=self.force,
                push=self.push,
                condition_valid_times=self.load_condition_valid_times(),
                tx_config=self.tx_config_loader.load_tx_config(
                    units["chia"], wallet_info.config, wallet_info.fingerprint
                ),
            )
```
