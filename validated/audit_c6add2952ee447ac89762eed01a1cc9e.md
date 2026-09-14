### Title
Unclaimed PlotNFT pool rewards are permanently strandable when the owner melts the singleton while self-pooling with reward coins outstanding - (File: `chia/pools/plotnft_drivers.py`)

### Summary
`PlotNFT2Wallet.melt_plotnft()` / `PlotNFT.melt()` permanently destroys the plot-NFT singleton with no check for outstanding, unclaimed `PoolReward` coins sitting at the plot-NFT's `p2_singleton_puzzle_hash`. Those reward coins can only be swept by `claim_pool_rewards()`, which requires a `SendMessage` attestation signed by the still-existing singleton (`RewardPuzzle`/`SingletonMember` curried with the singleton id). Once the singleton is melted, no coin can ever produce that attestation again, so any reward coins the owner didn't claim before melting become permanently unspendable — the exact bug class in the reported Vault.vy finding (`liquidate()` destroying a position while claimable funds remain, with `_claimable_for_token()` subsequently returning 0 forever, and no rescue path).

### Finding Description
A self-pooling (or previously self-pooling) plot-NFT owner accumulates `PoolReward` coins at the `p2_singleton_puzzle_hash` derived from `RewardPuzzle(singleton_id=self.plotnft_id)` [1](#0-0) . These reward coins are recorded by `plotnft2_store.add_pool_reward()` when observed on-chain [2](#0-1) .

The only way to move a `PoolReward` coin to the owner's wallet is `claim_rewards()` → `PlotNFT.claim_pool_rewards()`, which builds a `SendMessage`/`AssertCoinAnnouncement`-linked spend where the currently-live plot-NFT singleton attests to (and co-spends alongside) each reward coin via the `RewardPuzzle.singleton_member` restriction [3](#0-2) . The reward puzzle is a `SingletonMember` curried on the singleton id, and its solve path requires a delegated puzzle/solution proven against that singleton's current inner puzzle hash [4](#0-3) .

`PlotNFT.melt()` permanently terminates the singleton lineage by creating a zero-value coin with the escape/melt condition, and its only precondition is `if self.pooling: raise ValueError(...)` — there is no check for outstanding unclaimed pool rewards [5](#0-4) . `PlotNFT2Wallet.melt_plotnft()` simply calls `plotnft.melt()` and submits the spend, again without any reward-outstanding check [6](#0-5) , and the RPC entrypoint `plotnft_melt` performs no additional validation either [7](#0-6) .

Once the singleton coin is melted (spent with the `-113`/melt escape value, consistent with the generic singleton melt semantics in `singleton_top_layer_v1_1.py` [8](#0-7) ), there is no longer any live coin whose puzzle hash matches the singleton lineage that `RewardPuzzle`'s `SingletonMember` requires. Any `PoolReward` coin not swept before the melt spend can never again produce a valid `claim_pool_rewards()` spend bundle, because the delegated-puzzle/attestation path fundamentally requires an active singleton coin to co-spend with. No rescue or fallback claim path exists elsewhere in `plotnft_drivers.py` or `plotnft_wallet.py`.

This mirrors the reported Vault.vy issue precisely: a user-initiated "liquidation"/"melt" action destroys the position record needed to prove entitlement to previously-earned funds, and once destroyed, the funds are permanently orphaned with no rescue logic.

### Impact Explanation
Any XCH pool reward coins paid to a self-pooling plot-NFT's `p2_singleton_puzzle_hash` that have not yet been claimed via `claim_rewards()`/`pw_absorb_rewards` become permanently unspendable the moment the owner (or anyone with signing authority, e.g., after a key compromise or accidental automation) submits a `melt_plotnft`/`plotnft_melt` transaction. This is a direct, irreversible loss of user funds with no path to recovery, matching the High-severity classification of the original report (permanent loss of rewards, no rescue logic implemented).

### Likelihood Explanation
This requires no adversary — it's triggerable by the legitimate owner's own normal wallet operation. The CLI/RPC flow (`MeltPlotNFTCMD` → `plotnft_melt` → `melt_plotnft` → `PlotNFT.melt()`) does not warn about or block melting when unclaimed rewards exist, and `test_plotnft_cli_melt` exercises a melt with no reward-outstanding guard [9](#0-8) . Existing project tests even explicitly label similar scenarios "LOSE REWARDS" when transitioning pool state without first claiming [10](#0-9) [11](#0-10) , confirming the codebase authors are aware unclaimed-reward loss is possible during state transitions, but the melt path in particular has no reward check at all (unlike `join_pool`, which just re-routes to `leave_pool`).

### Recommendation
Add a check in `PlotNFT.melt()` / `PlotNFT2Wallet.melt_plotnft()` that queries `plotnft2_store.get_pool_rewards(plotnft_id=...)` and refuses to melt (raising a clear `ValueError`) while any unclaimed `PoolReward` coins remain outstanding for that plot-NFT, forcing the owner to `claim_rewards()` first — analogous to the existing `if self.pooling: raise ValueError("Cannot melt a pooling PlotNFT")` guard. Alternatively, have `melt_plotnft()` automatically batch a `claim_pool_rewards()` spend into the same transaction before/alongside the melt spend so outstanding rewards are swept atomically.

### Proof of Concept
1. Create a self-pooling plot-NFT (`PlotNFT2Wallet.create_new` with no `pool_config`).
2. Farm several blocks with `farm_to=plotnft.p2_singleton_puzzle_hash` so multiple `PoolReward` coins accumulate at the plot-NFT's p2-singleton address (as in `test_plotnft_lifecycle`'s "RECEIVE REWARDS" step) [12](#0-11) .
3. Do not call `claim_rewards()`.
4. Call `plotnft_wallet.melt_plotnft(action_scope=...)` (or the `plotnft melt` CLI command) and confirm the resulting spend on-chain [6](#0-5) .
5. Attempt `claim_rewards()` afterward (or manually reconstruct `PlotNFT.claim_pool_rewards()`): it will fail because `get_current_plotnft()`/singleton lookup can no longer find a live singleton coin to build the required `SingletonMember`-proven delegated spend — the pending `PoolReward` coins are now permanently unspendable.

### Citations

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L87-89)
```python
    @property
    def p2_singleton_puzzle_hash(self) -> bytes32:
        return RewardPuzzle(singleton_id=self.plotnft_id).puzzle_hash()
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

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L651-655)
```python
        elif coin_data is None and coin.puzzle_hash == self.p2_singleton_puzzle_hash:
            if coin.parent_coin_info[0:16] == self.wallet_state_manager.constants.GENESIS_CHALLENGE[0:16]:
                await self.wallet_state_manager.plotnft2_store.add_pool_reward(
                    pool_reward=PoolReward(singleton_id=self.plotnft_id, coin=coin)
                )
```

**File:** chia/pools/plotnft_drivers.py (L590-641)
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
```

**File:** chia/pools/plotnft_drivers.py (L723-748)
```python
    def melt(self, extra_conditions: tuple[Condition, ...] = tuple()) -> list[CoinSpend]:
        if self.pooling:
            raise ValueError("Cannot melt a pooling PlotNFT")

        dpuz_and_solution = DelegatedPuzzleAndSolution(
            puzzle=Program.to(
                (
                    1,
                    [
                        UnknownCondition(opcode=Program.to(51), args=[Program.to(None), Program.to(-113)]).to_program(),
                        *(cond.to_program() for cond in extra_conditions),
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
            )
        ]
```

**File:** chia/pools/plotnft_drivers.py (L768-777)
```python
    def solve(
        self, singleton_inner_puzzle_hash: bytes32, delegated_puzzle_and_solution: DelegatedPuzzleAndSolution
    ) -> Program:
        return self.puzzle_with_restrictions().solve(
            [],
            [],
            self.singleton_member.solve(singleton_inner_puzzle_hash),
            delegated_puzzle_and_solution,
        )

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

**File:** chia/wallet/puzzles/singleton_top_layer_v1_1.py (L35-36)
```python
ESCAPE_VALUE = -113
MELT_CONDITION = [ConditionOpcode.CREATE_COIN, 0, ESCAPE_VALUE]
```

**File:** chia/_tests/pools/test_pool_cmdline.py (L906-933)
```python
@pytest.mark.parametrize(
    "wallet_environments",
    [{"num_environments": 1, "blocks_needed": [1]}],
    indirect=True,
)
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

**File:** chia/_tests/wallet/plotnft_wallet/test_plotnft_wallet.py (L451-455)
```python
    # LOSE REWARDS (while leaving)
    plotnft = await plotnft_wallet.get_current_plotnft()
    [pool_reward] = await env.wallet_state_manager.plotnft2_store.get_pool_rewards(plotnft_id=plotnft_wallet.plotnft_id)
    coin_spends = plotnft.forward_pool_reward(pool_reward)
    await env.rpc_client.push_tx(PushTX(spend_bundle=WalletSpendBundle(coin_spends, G2Element())))
```
