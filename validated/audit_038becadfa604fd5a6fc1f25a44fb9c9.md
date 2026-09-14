## Analog Found

Chia's Plot NFT v2 self-custody flow (`PlotNFT2Wallet`) has the same "claim-before-transition" hazard as the reported Vault bug: switching state can permanently redirect previously-earned, not-yet-claimed reward coins away from the owner.

### Title
Plot NFT owner can lose unclaimed self-pooling rewards by joining a pool before claiming them - (File: `chia/wallet/plotnft_wallet/plotnft_wallet.py`)

### Summary
A self-custody Plot NFT accumulates farming reward coins at its `p2_singleton_puzzle_hash` that are meant to be swept to the owner via `claim_rewards()`/`claim_pool_rewards()`. If the owner calls `join_pool()` before claiming, the singleton's mode flips to pooling and the only remaining sweep path for those same reward coins becomes `forward_pool_reward()`, which sends the coin's value to the pool's `pool_puzzle_hash` instead of the owner.

### Finding Description
`PlotNFT.claim_pool_rewards()` explicitly refuses to run while pooling: [1](#0-0) 

The only reward-sweep path while pooling is `forward_pool_reward()`, which pays the pool, not the owner: [2](#0-1) 

`PlotNFT2Wallet.claim_rewards()` (the wallet-level entry point) simply calls `plotnft.claim_pool_rewards()` and will raise once `self.pooling` is true — it performs no automatic claim-then-transition sequencing: [3](#0-2) 

`PlotNFT2Wallet.join_pool()` transitions the singleton straight to the pooling puzzle without first checking or sweeping any pending/unclaimed `p2_singleton_puzzle_hash` reward coins tracked for this `plotnft_id`: [4](#0-3) 

Reward coins are tracked in `plotnft2_store` keyed only by `plotnft_id`/singleton id, independent of the singleton's current pooling/self-custody state, at the time they're seen on chain: [5](#0-4) 

Once the state flips to pooling, those same still-unclaimed coins can only be moved with `forward_pool_reward()`, whose delegated puzzle sends the swept amount to the pool's puzzle hash rather than the owner's wallet — this is exactly the class of "reward redirection" caused by a state transition happening before the pending claim, mirroring the original Vault report where `liquidate()` could run before `claim()`.

The project's own test suite documents this exact loss scenario (self-titled "LOSE REWARDS"), confirming the mechanism is real and reachable purely by the owner's own RPC actions, with no admin/pool malice required: [6](#0-5) [7](#0-6) 

### Impact Explanation
Farming rewards that were earned and rightfully owed to the plot-NFT owner while self-pooling get redirected to the pool's payout address once the owner joins a pool without first claiming. This is an unintended transfer of value away from the coin's rightful owner triggered entirely by the owner's own normal wallet workflow (`join_pool`), not by any external attacker — matching the "reward redirection" acceptance criterion. The funds are not lost to a black hole (unlike the Vault case) but are misappropriated to the pool operator, which is a fund-safety issue for the plot-NFT owner.

### Likelihood Explanation
This requires no adversarial coordination: any self-custody Plot NFT owner who forgets to run `claim_rewards` before calling `join_pool` (a routine, commonly performed action, e.g. via `pw_join_pool` RPC / `chia plotnft join`) will trigger this. Because the reward coins can silently accumulate over many blocks before the owner decides to join a pool, the likelihood of an owner having unclaimed rewards at the moment of joining is realistically high.

### Recommendation
`PlotNFT2Wallet.join_pool()` (and `leave_pool()`/pool-switching flows) should first check `plotnft2_store.get_pool_rewards()` for the plotnft and automatically sweep/claim any pending self-custody reward coins via `claim_rewards()` before constructing the state-transition spend, or explicitly reject the join/leave request until pending rewards are claimed, analogous to the recommended fix of calling the claim path before allowing the state-changing action.

### Proof of Concept
1. Create a self-custody Plot NFT via `PlotNFT2Wallet.create_new()`.
2. Farm blocks so that one or more reward coins land at `p2_singleton_puzzle_hash` and get tracked by `plotnft2_store.add_pool_reward()` (`chia/wallet/plotnft_wallet/plotnft_wallet.py:651-656`).
3. Instead of calling `claim_rewards()`, call `join_pool()` (`chia/wallet/plotnft_wallet/plotnft_wallet.py:228`) to transition the singleton to `FARMING_TO_POOL`.
4. Attempt `claim_rewards()` again — it now raises `ValueError("Cannot claim rewards while pooling...")` (`chia/pools/plotnft_drivers.py:595-596`).
5. The only way to move the still-unswept reward coins is `forward_pool_reward()`, which pays the pool's `pool_puzzle_hash`, not the original owner — reproducing the behavior explicitly exercised in `test_plotnft_wallet.py`'s "LOSE REWARDS" test blocks.

### Citations

**File:** chia/pools/plotnft_drivers.py (L550-566)
```python
    def forward_pool_reward(self, reward: PoolReward) -> list[CoinSpend]:
        if not self.pooling:
            raise ValueError("Cannot forward pool reward while self pooling. Try `claim_pool_rewards`")
        return [
            self.singleton_action_spend(inner_solution=self.forward_pool_reward_inner_solution(reward)),
            make_spend(
                coin=reward.coin,
                puzzle_reveal=reward.puzzle(),
                solution=reward.solve(
                    self.inner_puzzle_hash(),
                    delegated_puzzle_and_solution=DelegatedPuzzleAndSolution(
                        puzzle=self.forward_pool_reward_dpuz(),
                        solution=Program.to([reward.coin.amount]),
                    ),
                ),
            ),
        ]
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

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L149-198)
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

        plotnft = await self.get_current_plotnft()
        coin_spends = plotnft.claim_pool_rewards(
            rewards_to_claim=rewards_to_claim,
            reward_delegated_puzzles_and_solutions=[
                DelegatedPuzzleAndSolution(
                    puzzle=self.xch_wallet.make_solution(
                        primaries=[
                            CreateCoin(
                                puzzle_hash=self.rewards_claim_puzhash,
                                amount=uint64(total_reward_amount - fee),
                            ),
                        ],
                        fee=fee,
                        conditions=(*extra_conditions, CreateCoinAnnouncement(b""))
                        if len(rewards_to_claim) > 1
                        else extra_conditions,
                    ).at("rf"),  # strips away to just the delegated puzzle (bit of a hack)
                    solution=Program.to(None),
                )
                if i == 0
                else DelegatedPuzzleAndSolution(
                    puzzle=Program.to(
                        (
                            1,
                            [
                                AssertCoinAnnouncement(
                                    asserted_id=rewards_to_claim[0].coin.name(), asserted_msg=b""
                                ).to_program()
                            ],
                        )
                    ),
                    solution=Program.to(None),
                )
                for i, reward in enumerate(rewards_to_claim)
            ],
        )
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

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L651-656)
```python
        elif coin_data is None and coin.puzzle_hash == self.p2_singleton_puzzle_hash:
            if coin.parent_coin_info[0:16] == self.wallet_state_manager.constants.GENESIS_CHALLENGE[0:16]:
                await self.wallet_state_manager.plotnft2_store.add_pool_reward(
                    pool_reward=PoolReward(singleton_id=self.plotnft_id, coin=coin)
                )
            else:
```

**File:** chia/_tests/wallet/plotnft_wallet/test_plotnft_wallet.py (L285-327)
```python
        ):
            await plotnft_wallet.claim_rewards(action_scope=action_scope)

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
    )
```

**File:** chia/_tests/wallet/plotnft_wallet/test_plotnft_wallet.py (L444-474)
```python
    async with env.wallet_state_manager.new_action_scope(wallet_environments.tx_config, push=True) as action_scope:
        with pytest.raises(
            ValueError,
            match=re.escape("Cannot claim rewards while pooling. If you're a pool, try `forward_pool_rewards`"),
        ):
            await plotnft_wallet.claim_rewards(action_scope=action_scope)

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
