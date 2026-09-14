### Title
Melting a PlotNFT singleton permanently freezes unclaimed pool rewards tied to that singleton - (File: `chia/pools/plotnft_drivers.py`)

### Summary
`PlotNFT.melt()` destroys the plot-NFT singleton by creating the terminal `CREATE_COIN 0 -113` (`ESCAPE_VALUE`/melt) condition, ending the singleton's lineage permanently. [1](#0-0)  Pool reward coins earned by that singleton are paid to a `RewardPuzzle` that is curried to the specific `singleton_id` and can only be claimed via a delegated `SingletonMember` proof produced by spending the live singleton (`claim_pool_rewards` / `forward_pool_reward`). [2](#0-1) [3](#0-2)  `melt()` only checks `self.pooling` and never checks whether unclaimed `PoolReward` coins for this `plotnft_id` still exist, so a user (or the wallet CLI/RPC path that calls `PlotNFT.melt()`) can burn the singleton while reward coins are still sitting unclaimed at the `p2_singleton_puzzle_hash`. [4](#0-3)  Once the singleton is melted there is no longer any live singleton coin that can produce the required `SingletonMember`/`SendMessage` proof, so those reward coins become permanently unspendable — the same "burn coin before claiming its rewards → funds frozen forever" pattern described in the Alchemix `VotingEscrow.merge()` report.

### Finding Description
- `PlotNFT2Wallet.claim_rewards()` is the only path that spends outstanding `PoolReward` coins for a plot NFT; it explicitly forbids claiming while pooling and requires `rewards_to_claim` to be non-empty, but nothing forces a claim before other terminal actions. [5](#0-4) 
- `claim_pool_rewards()` proves the claim by having the singleton itself emit `SendMessage` conditions bound to each reward coin's id, solved through the `SingletonMember` puzzle-with-restrictions member solution — i.e., only a live singleton coin belonging to that `launcher_id` can authorize spending the reward coins. [6](#0-5) 
- `melt()` builds a delegated puzzle whose only condition is the singleton melt sentinel `CREATE_COIN 0 -113`, which is the canonical way to permanently exit the singleton top layer (the coin it creates is no longer a singleton and cannot be treated as one again). [1](#0-0) 
- `melt()`'s only guard is `if self.pooling: raise ValueError(...)`; it performs no check against `WalletStateManager.plotnft2_store` / `get_pool_rewards()` for outstanding, unclaimed reward coins tied to `self.singleton_struct.launcher_id`. [4](#0-3) 
- Because the reward-claim path structurally depends on the singleton continuing to exist (to emit the `SendMessage`/`SingletonMember` proof), destroying the singleton via `melt()` before calling `claim_rewards()` leaves any already-farmed-but-unclaimed `PoolReward` coins with no possible future spend path — they are frozen exactly as the ALCX rewards were frozen by burning the `from` veALCX token before claiming in the reported Alchemix bug.

### Impact Explanation
This matches the "Permanent freezing of unclaimed yield" impact class explicitly in scope: a plot-NFT owner who accumulates self-custody pool rewards and then melts (exits) the plot NFT without first calling `claim_rewards()` permanently loses those funds, with no recovery path since the singleton lineage required to authorize the claim no longer exists. This is a direct, unprivileged, wallet-user-triggered loss of funds (their own farmed rewards), not a network or peer-level issue.

### Likelihood Explanation
Likelihood is moderate: it requires (1) the plot NFT to have accrued unclaimed reward coins (a normal occurrence for self-custody farmers who don't immediately sweep every reward) and (2) the user or any UI/CLI flow invoking `melt()` without first invoking `claim_rewards()`. Because `melt()` performs no cross-check with `plotnft2_store.get_pool_rewards()`, there is nothing in the driver layer preventing this sequence; whether it is prevented depends on whether higher-level UI/CLI code (which I could not fully verify from the index — some call sites in `chia/wallet/plotnft_wallet/plotnft_wallet.py` referencing melt-related code were not fully visible in this session) enforces claim-before-melt ordering.

### Recommendation
Have `PlotNFT.melt()` (or the wallet-level caller that invokes it) check `plotnft2_store.get_pool_rewards(plotnft_id=...)` and refuse to melt (or auto-claim first) whenever unclaimed `PoolReward` coins exist for that `launcher_id`, mirroring the existing "reject melt while pooling" check for the "reject melt while unclaimed rewards exist" case.

### Proof of Concept
Conceptual reproduction (structural, based on driver code — not executed):
1. Create a self-custody `PlotNFT` via `PlotNFT2Wallet.create_new()`.
2. Farm blocks paying to `plotnft.p2_singleton_puzzle_hash` so that `PoolReward` coins accumulate for `plotnft.singleton_struct.launcher_id`, mirroring the existing test flow in `test_plotnft_self_custody_claim`. [7](#0-6) 
3. Instead of calling `plotnft.claim_pool_rewards(...)`, call `plotnft.melt()` directly, pushing the resulting `CoinSpend` (`CREATE_COIN 0 -113`). [1](#0-0) 
4. After the melt spend confirms, the singleton no longer exists; attempt to spend the earlier `PoolReward` coin via `claim_pool_rewards()`/`forward_pool_reward()` — this now requires a `SingletonMember` proof from a singleton coin that no longer exists, so the reward coin can never be spent, permanently freezing those funds — analogous to the `frozen AlcxReward` result shown in the original Alchemix PoC.

### Citations

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

**File:** chia/pools/plotnft_drivers.py (L750-786)
```python

@dataclass(kw_only=True, frozen=True)
class RewardPuzzle:
    singleton_id: bytes32

    @property
    def singleton_member(self) -> SingletonMember:
        return SingletonMember(singleton_id=self.singleton_id)

    def puzzle_with_restrictions(self) -> PuzzleWithRestrictions:
        return PuzzleWithRestrictions(nonce=0, restrictions=[], puzzle=self.singleton_member)

    def puzzle(self) -> Program:
        return self.puzzle_with_restrictions().puzzle_reveal()

    def puzzle_hash(self) -> bytes32:
        return self.puzzle().get_tree_hash()

    def solve(
        self, singleton_inner_puzzle_hash: bytes32, delegated_puzzle_and_solution: DelegatedPuzzleAndSolution
    ) -> Program:
        return self.puzzle_with_restrictions().solve(
            [],
            [],
            self.singleton_member.solve(singleton_inner_puzzle_hash),
            delegated_puzzle_and_solution,
        )


@dataclass(kw_only=True, frozen=True)
class PoolReward(RewardPuzzle):
    coin: Coin

    @property
    def height(self) -> uint32:
        return uint32.from_bytes(self.coin.parent_coin_info[28:])

```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L149-199)
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

**File:** chia/_tests/pools/test_plotnft_v2_drivers.py (L285-308)
```python
# PlotNFT claims pooling rewards while self custody
@pytest.mark.anyio
async def test_plotnft_self_custody_claim(cost_logger: CostLogger) -> None:
    async with sim_and_client() as (sim, sim_client):
        plotnft = await mint_plotnft(sim=sim, sim_client=sim_client, desired_state="self_custody")
        reward = await mint_reward(sim=sim, sim_client=sim_client, singleton_id=plotnft.singleton_struct.launcher_id)

        with pytest.raises(
            ValueError, match=re.escape("Cannot forward pool reward while self pooling. Try `claim_pool_rewards`")
        ):
            plotnft.forward_pool_reward(reward=reward)

        with pytest.raises(
            ValueError,
            match=re.escape("Number of rewards and delegated puzzles and solutions must match"),
        ):
            plotnft.claim_pool_rewards(rewards_to_claim=[reward], reward_delegated_puzzles_and_solutions=[])

        reward_dpuz_and_sol = DelegatedPuzzleAndSolution(
            puzzle=ACS, solution=Program.to([CreateCoin(bytes32.zeros, uint64(1)).to_program()])
        )
        coin_spends = plotnft.claim_pool_rewards(
            rewards_to_claim=[reward], reward_delegated_puzzles_and_solutions=[reward_dpuz_and_sol]
        )
```
