### Title
Melting or transferring a PlotNFT2 with unclaimed self-pooled reward coins permanently strands those funds - (File: chia/wallet/plotnft_wallet/plotnft_wallet.py)

### Summary
The reported Comptroller bug class is "an admin/state-changing action destroys the record that authorizes payout, causing already-accrued-but-unclaimed rewards to be permanently lost." In this codebase the closest reachable analog is in the `PlotNFT2Wallet` pooling flow: reward coins farmed to a plot NFT's `p2_singleton_puzzle_hash` are only claimable by spending them together with the live plot-NFT singleton via `claim_rewards()`/`claim_pool_rewards()`. `melt_plotnft()` and, to a lesser extent, `transfer_plotnft()` let the wallet owner destroy or mutate the singleton without first checking whether any of these `PoolReward` records are still outstanding.

### Finding Description
`PlotNFT2Wallet.claim_rewards()` requires the current live singleton (`plotnft = await self.get_current_plotnft()`) and builds a `claim_pool_rewards` spend that consumes both the plot NFT singleton coin and every outstanding `PoolReward` coin recorded in `plotnft2_store` (`get_pool_rewards`): [1](#0-0) 

`melt_plotnft()` spends and permanently destroys the plot-NFT singleton (`plotnft.melt(...)`, `additions=[]`), with no check for any pending/unclaimed `PoolReward` entries for that `plotnft_id`: [2](#0-1) 

Because `claim_pool_rewards()` (the CLVM-level driver) requires the singleton to be present and non-pooling in order to build the delegated claim spend (`self.singleton_action_spend(...)`), once the singleton coin is melted there is no longer any live singleton coin whose spend can authorize consuming the leftover `p2_singleton_puzzle_hash` reward coins: [3](#0-2) 

`transfer_plotnft()` similarly re-curries the singleton with a new `user_config`/hint and does not check or migrate outstanding rewards before the transfer, and `claim_rewards()` itself explicitly forbids claiming while pooling (rewards accrued while `FARMING_TO_POOL`/`LEAVING_POOL` can only be forwarded to the pool operator via `forward_pool_reward`, not claimed by the owner) — the test suite documents this design gap literally as "LOSE REWARDS (while pooling)" / "LOSE REWARDS (while leaving)": [4](#0-3) [5](#0-4) 

None of the wallet RPC entry points (`plotnft_melt`, `plotnft_transfer`) query `plotnft2_store.get_pool_rewards()` before allowing the mutating/destructive action: [6](#0-5) 

### Impact Explanation
If a self-pooling PlotNFT owner farms blocks (rewards land as `PoolReward` records tied to `plotnft_id`) and then calls `plotnft_melt` (or joins a pool / leaves and re-transfers) before calling `claim_rewards`, the accrued, unspent reward coins remain on-chain at the `p2_singleton_puzzle_hash` but the only spend path that can move them (via the now-destroyed or re-curried singleton) is gone. This is a direct, unauthorized loss of the user's own farmed XCH — funds that were rightfully theirs become permanently unclaimable, matching the "accrued rewards lost on state replacement" bug class from the report, just realized against the plot-NFT/pool reward-claim design instead of an EVM Comptroller. This is a wallet-user-triggered fund-loss bug, not an admin-trust issue, since any owner of a plot NFT can hit it inadvertently through the exposed `plotnft_melt`/leave-pool RPCs.

### Likelihood Explanation
Likelihood is moderate: it requires a user to have unclaimed self-pooling rewards recorded and then invoke `plotnft_melt`, or transition through leave/join-pool cycles, without first running `claim_rewards`. This is a very plausible sequence for any solo/self-pooling farmer who decides to retire or restructure a plot NFT, since nothing in the CLI/RPC path warns about or blocks this, and the existing test suite even demonstrates and accepts loss of rewards as expected behavior during pooling/leaving state transitions.

### Recommendation
- Before allowing `melt_plotnft()` (and ideally before `join_pool`/`leave_pool`/`transfer_plotnft` transitions that change ownership or destroy the singleton), check `plotnft2_store.get_pool_rewards(plotnft_id=..., include_spent=False)` and reject the action (or force an automatic `claim_rewards()`/sweep) if any unclaimed reward coins exist.
- Alternatively, expose a "sweep all outstanding pool rewards" step as a mandatory precondition in `plotnft_melt`/`transfer_plotnft` RPC handlers (`chia/wallet/wallet_rpc_api.py`), analogous to the mitigation recommended in the original report (claim before replacing the singleton/distributor).
- Add explicit CLI/RPC warnings and a hard failure (`ValueError`) rather than silent loss, so a user cannot destructively melt or transfer a plot NFT while `PoolReward` records remain unclaimed.

### Proof of Concept
1. Create a `PlotNFT2Wallet` in `SELF_POOLING` mode via `create_new_plotnft`.
2. Farm one or more blocks to `plotnft_wallet.p2_singleton_puzzle_hash` so that `plotnft2_store.get_pool_rewards()` returns non-empty `PoolReward` entries (see the "RECEIVE REWARDS" section of the lifecycle test): [7](#0-6) 
3. Instead of calling `claim_rewards()`, call `plotnft_melt` (`MeltPlotNFTCMD`/`wallet.melt_plotnft`) to destroy the singleton, as exercised in: [8](#0-7) 
4. After the melt confirms, the `PoolReward` coins sitting at `p2_singleton_puzzle_hash` can no longer be spent because `claim_pool_rewards()`/`singleton_action_spend()` requires the live (non-melted) singleton coin — the funds are stranded on-chain with no code path to reclaim them.

### Citations

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L149-164)
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

**File:** chia/_tests/wallet/plotnft_wallet/test_plotnft_wallet.py (L258-279)
```python
    # RECEIVE REWARDS (while pooling)
    EXTRA_POOLING_REWARDS = 2
    await wallet_environments.full_node.farm_blocks_to_puzzlehash(
        count=NUM_REWARDS_FARMED + EXTRA_POOLING_REWARDS,
        farm_to=plotnft_wallet.p2_singleton_puzzle_hash,
        guarantee_transaction_blocks=True,
    )
    await wallet_environments.full_node.farm_blocks_to_puzzlehash(count=1)

    await wallet_environments.full_node.wait_for_wallet_synced(env.node)
    await env.change_balances(
        {
            "plotnft": {
                "confirmed_wallet_balance": REWARDS_GAINED + POOL_REWARD_AMOUNT * EXTRA_POOLING_REWARDS,
                "unconfirmed_wallet_balance": REWARDS_GAINED + POOL_REWARD_AMOUNT * EXTRA_POOLING_REWARDS,
                "max_send_amount": REWARDS_GAINED + POOL_REWARD_AMOUNT * EXTRA_POOLING_REWARDS,
                "spendable_balance": REWARDS_GAINED + POOL_REWARD_AMOUNT * EXTRA_POOLING_REWARDS,
                "unspent_coin_count": NUM_REWARDS_FARMED + EXTRA_POOLING_REWARDS,
            }
        }
    )
    await env.check_balances()
```

**File:** chia/_tests/wallet/plotnft_wallet/test_plotnft_wallet.py (L288-301)
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
```

**File:** chia/_tests/wallet/plotnft_wallet/test_plotnft_wallet.py (L444-455)
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
```

**File:** chia/wallet/wallet_rpc_api.py (L3089-3101)
```python
    async def plotnft_melt(
        self,
        request: PlotNFTMelt,
        action_scope: WalletActionScope,
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> PlotNFTMeltResponse:
        wallet = self.service.wallet_state_manager.wallets[request.wallet_id]

        if not isinstance(wallet, PlotNFT2Wallet):
            raise ValueError("`plotnft_melt` called on a non-pooling v2 wallet")

        await wallet.melt_plotnft(action_scope=action_scope, fee=request.fee, extra_conditions=extra_conditions)

```

**File:** chia/_tests/pools/test_pool_cmdline.py (L911-933)
```python
@pytest.mark.anyio
async def test_plotnft_cli_melt(wallet_environments: WalletTestFramework, self_hostname: str) -> None:
    env = wallet_environments.environments[0]
    env.wallet_aliases = {
        "xch": 1,
        "plotnft": 2,
    }
    env.wallet_state_manager.config["reuse_public_key_for_change"][
        str(env.wallet_state_manager.root_pubkey.get_fingerprint())
    ] = wallet_environments.tx_config.reuse_puzhash

    client_info = WalletClientInfo(
        env.rpc_client,
        env.wallet_state_manager.root_pubkey.get_fingerprint(),
        env.wallet_state_manager.config,
    )
    wallet_id = await create_new_plotnft(wallet_environments, version=2, self_pool=True)
    FEE_AMOUNT = 1_800_000_000_000
    await MeltPlotNFTCMD(
        id=uint32(wallet_id),
        fee=uint64(FEE_AMOUNT),
        rpc_info=NeedsWalletRPC(client_info=client_info),
    ).run()
```
