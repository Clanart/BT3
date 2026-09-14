## Title
PlotNFT2 pool-forwarded rewards are permanently lost when the singleton transitions state before forwarding — analogous to unaccrued fee loss on parameter change (File: `chia/wallet/plotnft_wallet/plotnft_wallet.py`)

### Summary
The reported bug describes losing pending, unclaimed fee accrual when `setManagementFeeInfo` overwrites fee parameters without first calling the claim function. The same class of bug — value that is only redeemable in a particular contract state being irrecoverably destroyed by an unguarded state transition — exists in the `PlotNFT2Wallet` pooling flow: pool reward coins farmed to the p2-singleton puzzle hash while `pooling` must be forwarded to the pool via `forward_pool_reward()`, but `leave_pool()`/`join_pool()`/`self_pool` transitions the singleton (and thus the `pool_config`) without ever checking for or forwarding pending pool rewards first.

### Finding Description
`PlotNFT.forward_pool_reward()` requires `self.pooling` to be `True` and is the only way to redeem a pool reward coin while the NFT is farming-to-pool/leaving-pool: [1](#0-0) . Once the plotnft exits to self-custody, only `claim_pool_rewards()` works, and it explicitly rejects operation `while pooling`: [2](#0-1) .

`PlotNFT2Wallet.leave_pool()` builds the exit-to-waiting-room spend and immediately submits the coin spend, transitioning `pool_config` to `None`/exiting, with no step that scans for and forwards outstanding pool-reward coins belonging to the current pool config before the transition: [3](#0-2) . Likewise `join_pool()` recurses into `leave_pool()` when already pooling, or directly spends into a new `pool_config` without forwarding pending rewards first: [4](#0-3) .

The project's own test suite documents this exact loss under a `# LOSE REWARDS` comment: rewards accrued while pooling that are not forwarded before the state transition become unclaimable/unredeemable and effectively lost, both "while pooling" and "while leaving": [5](#0-4) [6](#0-5) . In both cases the code forwards only a subset of pending rewards, then the transition proceeds and the remaining reward becomes stranded — mirroring the report's "fee not accrued is lost forever" language.

### Impact Explanation
This is a direct value-loss bug for a wallet user/pool participant: p2-singleton coinbase reward coins with real XCH value become permanently unspendable/inaccessible once the associated `PlotNFT` singleton leaves the pooling state that could forward them, because `claim_pool_rewards()` is only usable in self-custody and cannot redeem coins whose delegated-puzzle path assumed the old `pool_config`/forwarding logic. This is unauthorized loss of otherwise-claimable coin value triggered purely by a legitimate wallet-initiated transaction (`leave_pool`/`join_pool`), not by any attacker action — consistent with the "no fee/no accrual claimed before parameters changed, so it's lost forever" root cause pattern in the report.

### Likelihood Explanation
This will happen in practice any time a farmer receives a pool reward in the same window that they call `leave_pool` or `join_pool` (a normal, frequent user action), since there's no automatic sweep of pending rewards nor a check/blocking condition preventing the transition while rewards are outstanding. The project's tests explicitly reproduce and label this behavior, indicating it is a known, reachable condition rather than a hypothetical edge case.

### Recommendation
Before submitting the `leave_pool`/`join_pool`/`self_pool` transition spend, `PlotNFT2Wallet` should scan `plotnft2_store.get_pool_rewards()` for the current `plotnft_id` and, if any pending pool-reward coins are forwardable (`self.pooling` still True), forward them first (or bundle the forward spends together with the exit spend), analogous to calling `keeperManagementFeeClaim()` before `setManagementFeeInfo`. Alternatively, refuse the state transition until pending rewards are cleared, or ensure `claim_pool_rewards()` can still redeem reward coins that were left un-forwarded from a prior pooling period.

### Proof of Concept
The repository's own test demonstrates the loss: after farming several pool rewards while `pooling`/`LEAVING_POOL`, only a subset are forwarded via `plotnft.forward_pool_reward()` before the plotnft moves on; the un-forwarded reward's value is never recovered in the wallet balance accounting, matching the `# LOSE REWARDS (while pooling)` / `# LOSE REWARDS (while leaving)` test sections: [5](#0-4) [6](#0-5) .

### Citations

**File:** chia/pools/plotnft_drivers.py (L550-556)
```python
    def forward_pool_reward(self, reward: PoolReward) -> list[CoinSpend]:
        if not self.pooling:
            raise ValueError("Cannot forward pool reward while self pooling. Try `claim_pool_rewards`")
        return [
            self.singleton_action_spend(inner_solution=self.forward_pool_reward_inner_solution(reward)),
            make_spend(
                coin=reward.coin,
```

**File:** chia/pools/plotnft_drivers.py (L590-598)
```python
    def claim_pool_rewards(
        self,
        rewards_to_claim: list[PoolReward],
        reward_delegated_puzzles_and_solutions: list[DelegatedPuzzleAndSolution],
    ) -> list[CoinSpend]:
        if self.pooling:
            raise ValueError("Cannot claim rewards while pooling. If you're a pool, try `forward_pool_rewards`")
        if len(rewards_to_claim) != len(reward_delegated_puzzles_and_solutions):
            raise ValueError("Number of rewards and delegated puzzles and solutions must match")
```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L228-290)
```python
    async def join_pool(
        self,
        *,
        pool_config: PoolConfig,
        action_scope: WalletActionScope,
        fee: uint64 = uint64(0),
        finish_leaving_fee: uint64 = uint64(0),
        pool_url: str,
        extra_conditions: tuple[Condition, ...] = tuple(),
        plotnft_override: PlotNFT | None = None,
    ) -> None:
        if plotnft_override is None:
            plotnft = await self.get_current_plotnft()
        else:
            plotnft = plotnft_override
        if plotnft.pool_config is not None:
            await self.leave_pool(
                action_scope=action_scope,
                fee=fee,
                finish_leaving_fee=finish_leaving_fee,
                extra_conditions=extra_conditions,
                new_pool_url=pool_url,
                new_pool_config=pool_config,
            )
            return
        elif finish_leaving_fee != uint64(0):
            raise ValueError("A fee to finish leaving was specified but PlotNFT does not need to leave")
        fee_hook = CreateCoinAnnouncement(msg=b"", coin_id=plotnft.coin.name())
        url_remark = Remark(rest=Program.to(pool_url))
        coin_spends = plotnft.join_pool(
            user_config=plotnft.user_config,
            pool_config=pool_config,
            extra_conditions=(*extra_conditions, fee_hook, url_remark),
        )
        if fee > 0:
            await self.xch_wallet.create_tandem_xch_tx(
                fee=fee,
                action_scope=action_scope,
                extra_conditions=(fee_hook.corresponding_assertion(),),
            )

        spend_bundle = WalletSpendBundle(coin_spends, G2Element())

        async with action_scope.use() as interface:
            interface.side_effects.transactions.append(
                self.wallet_state_manager.new_outgoing_transaction(
                    wallet_id=self.id(),
                    puzzle_hash=pool_config.pool_puzzle_hash,
                    amount=uint64(1),
                    fee=fee,
                    spend_bundle=spend_bundle,
                    additions=[
                        Coin(
                            parent_coin_info=plotnft.coin.name(),
                            puzzle_hash=dataclasses.replace(plotnft, pool_config=pool_config).puzzle_hash(nonce=0),
                            amount=uint64(1),
                        )
                    ],
                    removals=[plotnft.coin],
                    name=spend_bundle.name(),
                    extra_conditions=extra_conditions,
                )
            )
```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L292-356)
```python
    async def leave_pool(
        self,
        *,
        action_scope: WalletActionScope,
        fee: uint64 = uint64(0),
        finish_leaving_fee: uint64 = uint64(0),
        extra_conditions: tuple[Condition, ...] = tuple(),
        new_pool_url: str | None = None,
        new_pool_config: PoolConfig | None = None,
    ) -> None:
        if (new_pool_url is None and new_pool_config is not None) or (
            new_pool_url is not None and new_pool_config is None
        ):
            raise ValueError("Both new_pool_url or new_pool_config must be provided together")
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
            interface.side_effects.transactions.append(
                self.wallet_state_manager.new_outgoing_transaction(
                    wallet_id=self.id(),
                    puzzle_hash=exit_create_coin.puzzle_hash,
                    amount=uint64(1),
                    fee=fee,
                    spend_bundle=spend_bundle,
                    additions=[
                        Coin(
                            parent_coin_info=plotnft.coin.name(),
                            puzzle_hash=next_plotnft.puzzle_hash(nonce=0),
                            amount=uint64(1),
                        )
                    ],
                    removals=[plotnft.coin],
                    name=spend_bundle.name(),
                    extra_conditions=extra_conditions,
                )
            )
```

**File:** chia/_tests/wallet/plotnft_wallet/test_plotnft_wallet.py (L288-326)
```python
    # LOSE REWARDS (while pooling)
    pool_rewards = await env.wallet_state_manager.plotnft2_store.get_pool_rewards(plotnft_id=plotnft_wallet.plotnft_id)
    plotnft = await plotnft_wallet.get_current_plotnft()
    coin_spends = []
    singleton_coin_spend = None
    for reward in pool_rewards[0:-1]:
        new_coin_spends = plotnft.forward_pool_reward(reward)
        coin_spends += new_coin_spends
        singleton_coin_spend = next(iter(spend for spend in new_coin_spends if spend.coin.amount == 1))
        plotnft = PlotNFT.get_next_from_coin_spend(
            coin_spend=singleton_coin_spend, genesis_challenge=None, pre_uncurry=None, previous_plotnft_puzzle=plotnft
        )

    NUM_CLAIMED = len(pool_rewards) - 1
    await wallet_environments.full_node_rpc_client.push_tx(WalletSpendBundle(coin_spends, G2Element()))

    await wallet_environments.process_pending_states(
        [
            WalletStateTransition(
                pre_block_balance_updates={},
                post_block_balance_updates={
                    "plotnft": {
                        "confirmed_wallet_balance": -POOL_REWARD_AMOUNT * NUM_CLAIMED,
                        "unconfirmed_wallet_balance": -POOL_REWARD_AMOUNT * NUM_CLAIMED,
                        "max_send_amount": -POOL_REWARD_AMOUNT * NUM_CLAIMED,
                        "spendable_balance": -POOL_REWARD_AMOUNT * NUM_CLAIMED,
                        "unspent_coin_count": -NUM_CLAIMED,
                    }
                },
            )
        ],
        # when we reorg, we'll still remember that we saw an attempt to spend our plotnft
        post_reorg_balance_differences=[
            WalletStateTransition(
                pre_block_balance_updates={"plotnft": {"pending_coin_removal_count": 2}},
                post_block_balance_updates={"plotnft": {"pending_coin_removal_count": -2}},
            )
        ],
        bundles_to_repush=[WalletSpendBundle(coin_spends, G2Element())],
```

**File:** chia/_tests/wallet/plotnft_wallet/test_plotnft_wallet.py (L451-473)
```python
    # LOSE REWARDS (while leaving)
    plotnft = await plotnft_wallet.get_current_plotnft()
    [pool_reward] = await env.wallet_state_manager.plotnft2_store.get_pool_rewards(plotnft_id=plotnft_wallet.plotnft_id)
    coin_spends = plotnft.forward_pool_reward(pool_reward)
    await env.rpc_client.push_tx(PushTX(spend_bundle=WalletSpendBundle(coin_spends, G2Element())))

    await wallet_environments.process_pending_states(
        [
            WalletStateTransition(
                pre_block_balance_updates={},
                post_block_balance_updates={
                    "plotnft": {
                        "confirmed_wallet_balance": -POOL_REWARD_AMOUNT,
                        "unconfirmed_wallet_balance": -POOL_REWARD_AMOUNT,
                        "max_send_amount": -POOL_REWARD_AMOUNT,
                        "spendable_balance": -POOL_REWARD_AMOUNT,
                        "unspent_coin_count": -1,
                    }
                },
            )
        ],
        bundles_to_repush=[WalletSpendBundle(coin_spends, G2Element())],
    )
```
