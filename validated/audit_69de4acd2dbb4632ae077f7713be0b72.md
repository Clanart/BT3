### Title
Auto-retriggered PlotNFT2 second-stage leave-pool spend has no failure recovery, permanently stalling further pool state transitions - ([File: chia/wallet/plotnft_wallet/plotnft_wallet.py])

### Summary
`PlotNFT2Wallet` implements pool-switch as a two-step singleton transition, analogous to the SteadeFi report's two-step "repay then re-borrow" flow. Step 1 (`leave_pool`) moves the plotnft into the `exiting` state and records `exiting_fee`/next-pool intent. Step 2 (`_finish_leaving_pool`) is not driven by the user but is auto-triggered by `new_peak()` once the relative-lock height has passed. There is no mechanism to recover if this second, automatically-triggered spend fails to confirm.

### Finding Description
`leave_pool()` records intent and creates the first spend (`exit_to_waiting_room`), setting `exiting=True` and persisting a `PlotNFTTargetStateInfo` describing the next pool/self-pool target and `finish_leaving_fee`. [1](#0-0) 

The second, mandatory step is only invoked automatically from `new_peak()`, gated purely by height and by "no unconfirmed transactions for this wallet": [2](#0-1) 

`_finish_leaving_pool()` builds the exit-from-waiting-room spend, optionally attaches a fee via `create_tandem_xch_tx`, and — if a next pool was requested — chains directly into `join_pool()` in the same action scope: [3](#0-2) 

This mirrors the report's structural flaw: a state transition (`Withdraw_Failed` / `exiting=True`) that is only rolled forward by a second, dependent operation (`borrow` / `_finish_leaving_pool`+`join_pool`), where that second operation can fail for reasons outside the caller's control (a paused/restricted lending vault vs. here: insufficient fee balance in `create_tandem_xch_tx`, an already-spent/reorged plotnft coin, a rejected spend due to `ASSERT_HEIGHT_RELATIVE` timing at the mempool, or any other transient push failure). Because there is no explicit retry/backoff, error handling, or fallback path coded around this auto-trigger, and the only "already tried" guard is the presence of an unconfirmed transaction record, the plotnft can become stuck: if `_finish_leaving_pool`'s constructed spend never gets included (rejected by mempool or evicted), no new attempt is made because a stale unconfirmed transaction record continues to satisfy `get_unconfirmed_for_wallet(...) != []` in the next `new_peak()`, permanently short-circuiting retries. Conversely, if the call throws (e.g. an assertion inside `join_pool`/`exit_from_waiting_room_conditions` due to unexpected on-chain state), the exception propagates out of `new_peak()`, and there is no persisted "attempted and failed" bookkeeping to safely resume from — every subsequent `new_peak()` will retry the exact same failing path unconditionally once `finish_height` criteria are re-evaluated, or will be silently skipped forever if an unconfirmed-but-unconfirmable transaction lingers.

### Impact Explanation
A stuck PlotNFT can no longer transition between pools/self-pooling: it remains marked `exiting`, the wallet-visible pool state (`PoolSingletonState.LEAVING_POOL`, via `get_current_state()`) never resolves to the target, and the singleton itself cannot be spent to change pools until this stuck condition is manually cleared (deleting the unconfirmed transaction, similar to the workaround acknowledged for `PoolWallet.self_pool` — "If this is stuck, delete the unconfirmed transaction"). This halts pool-switching for that user's plotnft; funds/plots are not lost, but the wallet's automated pool-transition capability is denied of service. This is analogous to, but of lower severity than the original GMX report (which described a protocol-wide halt), because here the "halt" is scoped to a single plotnft/wallet, not to a shared multi-tenant vault.

### Likelihood Explanation
Moderate-to-low likelihood: it requires either (a) fee-coin unavailability at the exact retry moment, (b) a mempool/full-node rejection of the second-stage spend that the wallet doesn't detect and clear, or (c) a genuine assertion failure inside `_finish_leaving_pool`/`join_pool` due to a race with on-chain state. `PoolWallet.self_pool` has an explicit user-facing message acknowledging this exact class of stuck-unconfirmed-transaction problem exists in the codebase already: [4](#0-3) 
This corroborates that the "unconfirmed transaction never clears, blocking retries" failure mode is a known, reachable condition in this codebase family, not a purely theoretical one.

### Recommendation
- Add explicit failure detection/backoff in `PlotNFT2Wallet.new_peak()` around `_finish_leaving_pool`: catch exceptions, log, and do not silently re-attempt the identical spend indefinitely without invalidating/replacing the stale unconfirmed transaction record.
- Provide a user- or daemon-triggerable manual "retry finish leaving pool" RPC path (distinct from the automatic `new_peak` trigger) so a stuck plotnft can be recovered without requiring direct DB manipulation, mirroring but improving upon the existing `PoolWallet.self_pool` workaround message.
- Ensure `get_unconfirmed_for_wallet` reflects true on-chain rejection state promptly (e.g., proactively re-check mempool inclusion / expire long-unconfirmed transactions) so the guard in `new_peak()` cannot indefinitely suppress legitimate retries.

### Proof of Concept
Not independently reproducible from static analysis alone; the scenario requires simulating: (1) a plotnft entering `exiting` state via `leave_pool`, (2) reaching `finish_height`, (3) `new_peak()` invoking `_finish_leaving_pool` while the wallet lacks sufficient XCH to cover `exiting_info.exiting_fee` (or the resulting spend bundle is rejected by a simulated full node), and (4) observing that subsequent `new_peak()` calls perpetually see a non-empty unconfirmed-transaction list and skip retry, leaving `exiting=True` indefinitely. This would need to be validated with a Devin/test-harness session against `chia/_tests/wallet/plotnft_wallet/test_plotnft_wallet.py`-style scenarios; it was not run here due to tool/time constraints, so likelihood/impact rest on the code-path analysis above rather than an executed exploit.

### Citations

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L306-337)
```python
        plotnft = await self.get_current_plotnft()
        if not plotnft.pooling or plotnft.exiting:
            raise ValueError("`leave_pool` called on a non-pooling or exiting PlotNFT")
        next_plotnft = dataclasses.replace(plotnft, exiting=True)
        fee_hook = CreateCoinAnnouncement(msg=b"", coin_id=plotnft.coin.name())
        exit_create_coin = plotnft.exit_to_waiting_room_condition()
        exit_to_waiting_room_dpuz_and_sol = DelegatedPuzzleAndSolution(
            puzzle=self.xch_wallet.make_solution(
                primaries=[exit_create_coin],
                conditions=(*extra_conditions, fee_hook),
            ).at("rf"),  # strips away to just the delegated puzzle (bit of a hack)
            solution=Program.to(None),
        )
        coin_spends = plotnft.exit_to_waiting_room(exit_to_waiting_room_dpuz_and_sol)
        if fee > 0:
            await self.xch_wallet.create_tandem_xch_tx(
                fee=fee,
                action_scope=action_scope,
                extra_conditions=(fee_hook.corresponding_assertion(),),
            )

        spend_bundle = WalletSpendBundle(coin_spends, G2Element())

        async with action_scope.use() as interface:
            interface.side_effects.plotnft_exiting_info = PlotNFTTargetStateInfo(
                wallet_id=self.id(),
                exiting_fee=finish_leaving_fee,
                next_pool_url=new_pool_url,
                next_pool_puzzle_hash=new_pool_config.pool_puzzle_hash if new_pool_config is not None else None,
                next_heightlock=new_pool_config.heightlock if new_pool_config is not None else None,
                next_pool_memoization=new_pool_config.pool_memoization if new_pool_config is not None else None,
            )
```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L358-419)
```python
    async def _finish_leaving_pool(
        self,
        *,
        action_scope: WalletActionScope,
        exiting_info: PlotNFTTargetStateInfo,
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> None:
        plotnft = await self.get_current_plotnft()
        fee_hook = CreateCoinAnnouncement(msg=b"", coin_id=plotnft.coin.name())
        heightlock, exit_create_coin = plotnft.exit_from_waiting_room_conditions()
        exit_to_waiting_room_dpuz_and_sol = DelegatedPuzzleAndSolution(
            puzzle=self.xch_wallet.make_solution(
                primaries=[exit_create_coin],
                conditions=(fee_hook, heightlock, *extra_conditions),
            ).at("rf"),  # strips away to just the delegated puzzle (bit of a hack)
            solution=Program.to(None),
        )
        coin_spends = plotnft.exit_waiting_room(exit_to_waiting_room_dpuz_and_sol)
        next_plotnft = PlotNFT.get_next_from_coin_spend(
            coin_spend=coin_spends[0],
            genesis_challenge=self.wallet_state_manager.constants.GENESIS_CHALLENGE,
            previous_plotnft_puzzle=plotnft,
        )
        if exiting_info.exiting_fee > 0:
            await self.xch_wallet.create_tandem_xch_tx(
                fee=exiting_info.exiting_fee,
                action_scope=action_scope,
                extra_conditions=(fee_hook.corresponding_assertion(),),
            )

        spend_bundle = WalletSpendBundle(coin_spends, G2Element())

        async with action_scope.use() as interface:
            interface.side_effects.transactions.append(
                self.wallet_state_manager.new_outgoing_transaction(
                    wallet_id=self.id(),
                    puzzle_hash=exit_create_coin.puzzle_hash,
                    amount=uint64(1),
                    fee=exiting_info.exiting_fee,
                    spend_bundle=spend_bundle,
                    additions=[
                        Coin(
                            parent_coin_info=plotnft.coin.name(),
                            puzzle_hash=dataclasses.replace(plotnft, pool_config=None, exiting=False).puzzle_hash(
                                nonce=0
                            ),
                            amount=uint64(1),
                        )
                    ],
                    removals=[plotnft.coin],
                    name=spend_bundle.name(),
                    extra_conditions=(heightlock,),
                )
            )
        if exiting_info.pool_url_and_config is not None:
            pool_url, pool_config = exiting_info.pool_url_and_config
            await self.join_pool(
                action_scope=action_scope,
                pool_config=pool_config,
                pool_url=pool_url,
                plotnft_override=next_plotnft,
            )
```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L659-669)
```python
    async def new_peak(self, height: uint32) -> None:
        finish_height = await self.wallet_state_manager.plotnft2_store.get_exiting_height(wallet_id=self.id())
        if finish_height is not None and finish_height <= height - 2:  # 2 blocks for a little reorg safety
            if await self.wallet_state_manager.tx_store.get_unconfirmed_for_wallet(wallet_id=self.id()) != []:
                self.log.info(f"Not finishing plotnft from wallet {self.id()} due to unconfirmed transactions")
                return None
            finish_info = await self.wallet_state_manager.plotnft2_store.get_exiting_info(wallet_id=self.id())
            async with self.wallet_state_manager.new_action_scope(
                self.wallet_state_manager.tx_config, push=True, sign=True, merge_spends=True
            ) as action_scope:
                await self._finish_leaving_pool(action_scope=action_scope, exiting_info=finish_info)
```

**File:** chia/pools/pool_wallet.py (L675-679)
```python
    async def self_pool(self, fee: uint64, action_scope: WalletActionScope) -> uint64:
        if await self.have_unconfirmed_transaction():
            raise ValueError(
                "Cannot self pool due to unconfirmed transaction. If this is stuck, delete the unconfirmed transaction."
            )
```
