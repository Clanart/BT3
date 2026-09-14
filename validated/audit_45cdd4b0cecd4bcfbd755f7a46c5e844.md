## Analog Found

### Title
Melting or transferring a PlotNFT permanently strands unclaimed pool rewards - (File: `chia/wallet/plotnft_wallet/plotnft_wallet.py`)

### Summary
`PlotNFT2Wallet.melt_plotnft()` and `PlotNFT2Wallet.transfer_plotnft()` spend the current PlotNFT singleton coin without first checking for or claiming any outstanding pool rewards recorded in `pool_reward2s`. This mirrors the Fair Funding `Vault.vy` bug: once the position (singleton) is "liquidated" (melted) or handed off (transferred) without a preceding claim, the previously accrued but unclaimed rewards become permanently unclaimable.

### Finding Description
`PlotNFT2Wallet.claim_rewards()` requires spending the *current* PlotNFT singleton coin together with the pending `PoolReward` coins in a single bundle: the reward coins are only spendable when co-spent with the live singleton, because `PlotNFT.claim_pool_rewards()` couples the claim via a `SendMessage`/`AssertCoinAnnouncement` binding to `self.puzzle_hash(nonce=0)` of the currently active singleton instance. [1](#0-0) [2](#0-1) 

`melt_plotnft()` spends the plotnft coin via `plotnft.melt(...)` and creates **no additions**, permanently destroying the singleton lineage, with no check that `plotnft2_store.get_pool_rewards()` is empty before doing so: [3](#0-2) 

`transfer_plotnft()` similarly spends the current plotnft coin to re-key it to a different owner (`new_user_config`) without any check for or claim of pending rewards: [4](#0-3) 

Once the singleton coin backing a given `plotnft_id`/launcher id is spent this way, the rewards still recorded in `pool_reward2s` (keyed by `singleton_id`) can never be co-spent again by the original signing key — for `melt_plotnft`, the singleton no longer exists at all; for `transfer_plotnft`, the signing key changes, and the previous owner's wallet is deleted (`WalletType.PLOTNFT_2` handling calls `plotnft_wallet.delete_self` when there are no children coins) so it no longer even tracks these balances: [5](#0-4) 

The wallet code itself acknowledges an analogous "lose rewards" scenario for `leave_pool`/`forward_pool_reward` flows in its own tests (explicitly labeled "LOSE REWARDS"), confirming this is a known, real class of bug in this subsystem, though those specific instances are treated as intended trade-offs of forwarding rewards to a pool. The melt/transfer case is different in kind: it is the plotnft owner's own action (analogous to "liquidating" the position) irreversibly cutting off access to rewards that were already accrued and claimable, exactly matching the reported bug class ("user will lose funds if they don't claim before liquidating"). [6](#0-5) 

### Impact Explanation
Any unclaimed farmed pool rewards sitting in `pool_reward2s` for a given plotnft at the time of melt or transfer become permanently stranded: for `melt_plotnft`, the singleton required to co-spend the reward coin is gone forever; for `transfer_plotnft`, the local wallet that tracked the pending rewards is deleted from local state (`delete_self`), and there is no guarantee the new owner's wallet independently rediscovers and can claim rewards accrued before the transfer, especially since the reward-claim delegated puzzle path is signed by the *current* owner's key at claim time. This is a direct loss of user funds analogous to the reported Vault issue.

### Likelihood Explanation
This requires no attacker — it is triggered purely by the plotnft owner's own routine wallet actions (`plotnft melt`, `plotnft transfer`) via the standard RPC/CLI (`plotnft_melt`, `plotnft_transfer` in `wallet_rpc_api.py`), if performed while pool rewards are pending and unclaimed. Because there is no explicit warning or automatic claim-before-action step, and normal pool-reward accrual (farming to a self-pooled plot) happens continuously, this is a fairly likely occurrence for any user who melts or transfers a plotnft without manually calling `claim` first.

### Recommendation
Before executing `melt_plotnft()` or `transfer_plotnft()`, check `plotnft2_store.get_pool_rewards(plotnft_id=...)` for any unclaimed rewards and either automatically fold a `claim_pool_rewards` spend into the same transaction (co-spending both the melt/transfer and the outstanding rewards), or refuse to proceed and require the caller to claim first, surfacing a clear error to the user.

### Proof of Concept
1. Create a self-pooling PlotNFT v2 and farm blocks to accrue `PoolReward` coins to its `pool_puzzle_hash` (as in `test_plotnft_lifecycle`), without calling `claim_rewards()`.
2. Call `plotnft_transfer` or `plotnft_melt` (via `MeltPlotNFTCMD`/`TransferPlotNFTCMD` or the RPC endpoints) while rewards remain unclaimed in `pool_reward2s`.
3. Observe that the plotnft singleton coin is spent/destroyed (melt) or re-keyed (transfer) with the local wallet subsequently deleted (`delete_self`), while the previously accrued `PoolReward` coins are never included in the spend.
4. Attempt to call `claim_rewards()`/`pw_absorb_rewards` afterward: for melt, there is no live plotnft singleton left to co-spend with the reward coins, so the claim can never succeed; for transfer, the original wallet — and its `WalletActionScope`/state that tracked these particular pending rewards — has been removed, with no guaranteed re-claim path for those specific rewards by the new owner.

**Note on completeness:** I could not fully trace, due to iteration limits, whether wallet resync after a `transfer_plotnft` (from the new owner's fresh sync) would rediscover and successfully re-populate `pool_reward2s` with the pre-transfer unclaimed reward coins as claimable by the new key, or whether the `melt` puzzle's underlying CLVM (`PlotNFT.melt()` in `chia/pools/plotnft_drivers.py`) truly forbids reward co-spending post-melt. This should be verified with an end-to-end wallet-sync test (pending-reward → melt/transfer → resync → attempt claim) to confirm exploitability with full certainty before treating this as conclusively proven.

### Citations

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

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L421-481)
```python
    async def transfer_plotnft(
        self,
        *,
        action_scope: WalletActionScope,
        target_wallet_fingerprint: int,
        fee: uint64 = uint64(0),
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> None:
        """
        Because the wallet doesn't have broad support for MIPS-style custody, transferring using addresses is
        a bit complicated because transferring to the wrong kind of address could leave the wallet unable to
        sync/spend the PlotNFT. As a guard, we implement this endpoint as a transfer between local keys to make
        sure that we generate the correct kind of inner puzzle.

        This is not necessarily a permanent restriction, but solves the primary use case of transferring between
        a user's own wallets and keeps the implementation relatively simple for now.
        """
        plotnft = await self.get_current_plotnft()
        fee_hook = CreateCoinAnnouncement(msg=b"", coin_id=plotnft.coin.name())
        root_pubkey = await self.wallet_state_manager.wallet_node.keychain_proxy.get_key_for_fingerprint(
            fingerprint=target_wallet_fingerprint, private=False
        )
        if root_pubkey is None:
            raise RuntimeError(f"Error retrieving key for fingerprint {target_wallet_fingerprint}")
        wallet_pubkey = master_pk_to_wallet_pk_unhardened(root_pubkey, index=uint32(0))
        synthetic_pubkey = self.xch_wallet.convert_public_key_to_synthetic(wallet_pubkey)
        hint = self.xch_wallet.puzzle_hash_for_pk(wallet_pubkey)
        new_user_config = UserConfig(synthetic_pubkey=synthetic_pubkey)
        coin_spends = plotnft.new_user_config(
            user_config=new_user_config,
            hint=hint,
            extra_conditions=(fee_hook, *extra_conditions),
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
                    puzzle_hash=hint,
                    amount=uint64(1),
                    fee=fee,
                    spend_bundle=spend_bundle,
                    additions=[
                        Coin(
                            parent_coin_info=plotnft.coin.name(),
                            puzzle_hash=dataclasses.replace(plotnft, user_config=new_user_config).puzzle_hash(nonce=0),
                            amount=uint64(1),
                        )
                    ],
                    removals=[plotnft.coin],
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

**File:** chia/pools/plotnft_drivers.py (L590-642)
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
        dpuz_and_solution = DelegatedPuzzleAndSolution(
            puzzle=Program.to(
                (
                    1,
                    [
                        CreateCoin(
                            puzzle_hash=self.inner_puzzle_hash(),
                            amount=self.coin.amount,
                            memos=[self.singleton_struct.struct_hash()],
                        ).to_program(),
                        *(
                            SendMessage(
                                msg=dpuz_and_sol.puzzle.get_tree_hash(),
                                sender=MessageParticipant(puzzle_hash_committed=self.puzzle_hash(nonce=0)),
                                receiver=MessageParticipant(coin_id_committed=reward.coin.name()),
                            ).to_program()
                            for reward, dpuz_and_sol in zip(rewards_to_claim, reward_delegated_puzzles_and_solutions)
                        ),
                    ],
                )
            ),
            solution=Program.to([]),
        )
        return [
            self.singleton_action_spend(
                inner_solution=self.puzzle_with_restrictions().solve(
                    member_validator_solutions=[],
                    dpuz_validator_solutions=[],
                    member_solution=self.bls_member.solve(),
                    delegated_puzzle_and_solution=dpuz_and_solution,
                )
            ),
            *(
                make_spend(
                    coin=reward.coin,
                    puzzle_reveal=reward.puzzle(),
                    solution=reward.solve(
                        self.inner_puzzle_hash(),
                        delegated_puzzle_and_solution=dpuz_and_sol,
                    ),
                )
                for reward, dpuz_and_sol in zip(rewards_to_claim, reward_delegated_puzzles_and_solutions)
            ),
        ]
```

**File:** chia/wallet/wallet_state_manager.py (L1453-1461)
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
```

**File:** chia/_tests/wallet/plotnft_wallet/test_plotnft_wallet.py (L288-327)
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
    )
```
