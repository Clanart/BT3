### Title
`PlotNFT2Wallet.melt_plotnft` burns the pooling singleton without claiming pending pool rewards - (File: chia/wallet/plotnft_wallet/plotnft_wallet.py)

### Summary
`PlotNFT2Wallet.melt_plotnft` spends and permanently destroys the plot-NFT singleton coin without first sweeping or claiming any pending pool rewards recorded for that `launcher_id`. This mirrors the reported VotingEscrow class of bug: burning the identity-bearing NFT/singleton before harvesting rewards that are gated on that identity/ownership makes those rewards permanently unreachable afterward.

### Finding Description
`melt_plotnft` fetches the current `PlotNFT`, builds the melt spend, and destroys the singleton with no additional outputs: [1](#0-0) 

Compare this with the reward-claiming paths, `claim_rewards` and the "LOSE REWARDS" behavior documented in the pooling test, which explicitly require the *current* singleton coin to be spent together with the pool-reward coins (`removals=[reward.coin for reward in rewards_to_claim] + [plotnft.coin]`) in order to sweep p2-singleton reward coins into the wallet: [2](#0-1) 

Pending pool rewards are tracked per `plotnft_id`/`launcher_id` in `plotnft2_store` (`get_pool_rewards`), and both `claim_rewards` (self-pooling) and `forward_pool_reward` (pooling) rely on advancing the *same* singleton lineage to consume those reward coins: [3](#0-2) 

`melt_plotnft` does not call `claim_rewards`, does not check `plotnft2_store.get_pool_rewards`, and does not guard against melting while rewards are outstanding. Once the singleton is melted (spent to a terminal state with no singleton child), the on-chain identity that reward-claim/forward spends depend on (`plotnft.coin`, tracked lineage) is gone, so any reward coins sent to the p2-singleton puzzle hash before the melt can no longer be swept by any subsequent spend, exactly as in the reported VotingEscrow issue where burning the NFT removes the ownership required by `getReward()`.

### Impact Explanation
Any unclaimed pooling rewards (self-pooling or farming-to-pool) present at the time of `melt_plotnft` become permanently stranded XCH, unrecoverable by the user who legitimately earned them. This is a direct, spend-triggered loss of value reachable by a normal wallet user calling a documented wallet action (`PlotNFTMelt` RPC / `melt_plotnft`), not requiring any adversarial peer or privileged access. Impact is Medium: real funds can be lost by the owner's own action, but it requires a specific sequence (melt while rewards are pending) rather than enabling theft from others.

### Likelihood Explanation
Likelihood is Medium: the wallet lets a user farm-to-pool or self-pool (accumulating reward coins over multiple blocks before claiming) and independently allows melting the plot-NFT at any time via `melt_plotnft`/`PlotNFTMelt`. There is no cross-check in `melt_plotnft` against `plotnft2_store.get_pool_rewards`, so a user (or a UI/CLI flow) that calls melt without first claiming outstanding rewards will trigger this unintentionally.

### Recommendation
Before melting, `melt_plotnft` should query `wallet_state_manager.plotnft2_store.get_pool_rewards(plotnft_id=self.plotnft_id)` and either: (a) raise an error refusing to melt while unclaimed rewards exist (mirroring the existing `ValueError` guards used elsewhere, e.g. in `leave_pool`/`claim_rewards`), or (b) automatically fold a reward-claim/forward spend into the same transaction so outstanding p2-singleton reward coins are swept before/while the singleton is destroyed.

### Proof of Concept
1. Create a `PlotNFT2Wallet` (self-pooling or farming-to-pool) via `PlotNFT2Wallet.create_new`.
2. Farm one or more blocks to `plotnft_wallet.p2_singleton_puzzle_hash` so that pool reward coins accrue and are recorded via `coin_added` → `plotnft2_store.add_pool_reward` (see `chia/wallet/plotnft_wallet/plotnft_wallet.py` lines 651-657).
3. Without calling `claim_rewards` (self-pooling) or `forward_pool_reward` (pooling) to sweep those rewards, call `plotnft_wallet.melt_plotnft(action_scope=...)`.
4. Observe the melt spend (`chia/wallet/plotnft_wallet/plotnft_wallet.py` lines 483-514) consumes only `plotnft.coin` and creates no singleton child (`additions=[]`).
5. Attempt to claim the previously accrued reward coins: `claim_rewards`/`forward_pool_reward` require `get_current_plotnft()`/singleton lineage that no longer exists post-melt, so the reward coins at the p2-singleton puzzle hash can never be spent into the user's wallet again — funds are permanently stranded.

### Citations

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L149-226)
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

        spend_bundle = WalletSpendBundle(coin_spends, G2Element())

        async with action_scope.use() as interface:
            interface.side_effects.transactions.append(
                self.wallet_state_manager.new_outgoing_transaction(
                    wallet_id=self.id(),
                    puzzle_hash=self.rewards_claim_puzhash,
                    amount=total_reward_amount,
                    fee=fee,
                    spend_bundle=spend_bundle,
                    additions=[
                        Coin(
                            parent_coin_info=rewards_to_claim[0].coin.name(),
                            puzzle_hash=self.rewards_claim_puzhash,
                            amount=uint64(total_reward_amount - fee),
                        ),
                        Coin(
                            parent_coin_info=plotnft.coin.name(),
                            puzzle_hash=plotnft.puzzle_hash(nonce=uint64(0)),
                            amount=uint64(1),
                        ),
                    ],
                    removals=[reward.coin for reward in rewards_to_claim] + [plotnft.coin],
                    name=spend_bundle.name(),
                    extra_conditions=extra_conditions,
                )
            )
```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L483-514)
```python
    async def melt_plotnft(
        self,
        *,
        action_scope: WalletActionScope,
        fee: uint64 = uint64(0),
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> None:
        plotnft = await self.get_current_plotnft()
        fee_hook = CreateCoinAnnouncement(msg=b"", coin_id=plotnft.coin.name())
        coin_spends = plotnft.melt(extra_conditions=(fee_hook, *extra_conditions))
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
                    puzzle_hash=bytes32.zeros,
                    amount=uint64(1),
                    fee=fee,
                    spend_bundle=spend_bundle,
                    additions=[],
                    removals=[plotnft.coin],
                    name=spend_bundle.name(),
                    extra_conditions=extra_conditions,
                )
            )
```
