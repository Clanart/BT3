### Title
Melting a self-custody PlotNFT permanently freezes unclaimed pool-reward coins - (File: `chia/wallet/plotnft_wallet/plotnft_wallet.py`, `chia/pools/plotnft_drivers.py`)

### Summary
`PlotNFT2Wallet.melt_plotnft()` lets a self-custody plot NFT owner burn/melt the plot-NFT singleton with no check for outstanding, unclaimed pool reward coins tracked in `plotnft2_store`. Claiming those rewards (`claim_rewards` / `PlotNFT.claim_pool_rewards`) requires spending the live singleton (it uses a `SendMessage` condition sent from the singleton to each reward coin so the reward coin's `SingletonMember` puzzle will authorize the spend). Once the singleton is melted, there is no longer a live singleton coin able to send that authorizing message, so any reward coin recorded via `plotnft2_store.add_pool_reward()` (self-pooling rewards paid to `p2_singleton_puzzle_hash`) but not yet swept becomes permanently unspendable. This is structurally the same bug class as the reported veALCX issue: burn/withdraw of the "ownership" token happens without first forcing/validating claim of pending rewards tied to that ownership token.

### Finding Description
- `PlotNFT.melt()` in `chia/pools/plotnft_drivers.py` (lines 723-748) only guards against melting while pooling (`if self.pooling: raise ValueError("Cannot melt a pooling PlotNFT")`), and issues the melt condition (`CREATE_COIN 0 -113`) that destroys the singleton. [1](#0-0) 
- `PlotNFT2Wallet.melt_plotnft()` in `chia/wallet/plotnft_wallet/plotnft_wallet.py` (lines 483-514) calls `plotnft.melt(...)` directly and does not check `self.wallet_state_manager.plotnft2_store.get_pool_rewards(plotnft_id=self.plotnft_id)` before allowing the melt, unlike `claim_rewards()` a few lines above which does check for and requires those rewards. [2](#0-1) 
- Claiming pool rewards (`PlotNFT2Wallet.claim_rewards`, `PlotNFT.claim_pool_rewards`) is only possible while the singleton exists: it builds a `singleton_action_spend` that issues a `SendMessage` condition from the singleton to each `PoolReward` coin, and the reward coin's `RewardPuzzle`/`SingletonMember` requires that authorizing message from the singleton to spend. [3](#0-2) [4](#0-3) 
- `plotnft2_store.get_pool_rewards`/`add_pool_reward` is how the wallet tracks unswept self-pooling reward coins paid to `p2_singleton_puzzle_hash`, exactly the state that `claim_rewards()` checks and `melt_plotnft()` ignores. [5](#0-4) [6](#0-5) 

Once the singleton coin is burned (melted), there is no longer any live coin that can produce the `SendMessage` from the singleton required by the `RewardPuzzle`/`SingletonMember` to authorize spending the outstanding reward coin(s), so the funds become permanently unspendable — matching the "permanent freezing of unclaimed yield" impact class described in the external report (burn of the ownership token before pending rewards tied to it are settled).

### Impact Explanation
Any self-custody Plot NFT owner who has unswept self-pooling reward coins (farmed but not yet claimed via `claim_rewards`/`pw_absorb_rewards`) and calls `melt_plotnft` (exposed via `MeltPlotNFTCMD` / wallet RPC) will permanently and irrecoverably lose those reward coins, since the singleton required to authorize their spend no longer exists after melting. This is a permanent freezing/loss-of-funds condition reachable by an ordinary, unprivileged wallet user performing a single locally-initiated spend (the melt transaction) — no malicious peer or privileged actor is required.

### Likelihood Explanation
This requires no adversary — it can happen from ordinary user behavior: farm/self-pool for a while, accumulate reward coins in `p2_singleton_puzzle_hash`, and then choose to melt the plot NFT (e.g., to fully exit pooling/self-custody) without first calling `claim_rewards`. There is no code path enforcing claim-before-melt, and the CLI/RPC (`melt_plotnft`, `MeltPlotNFTCMD`) does not warn or block this. The condition is straightforward to trigger and does not depend on timing races, reorgs, or attacker cooperation.

### Recommendation
In `PlotNFT2Wallet.melt_plotnft()` (and/or in `PlotNFT.melt()`), check `plotnft2_store.get_pool_rewards(plotnft_id=self.plotnft_id)` before constructing the melt spend, and:
1. Reject the melt with a clear error (mirroring the `claim_rewards` "Cannot claim rewards while pooling" style guard) if unclaimed pool reward coins exist for this launcher id, forcing the user to call `claim_rewards` first; or
2. Automatically batch a `claim_pool_rewards` spend for all outstanding rewards into the same spend bundle prior to/concurrent with the melt spend so rewards are swept atomically before the singleton is destroyed.

### Proof of Concept
Concrete on-chain PoC was not runnable within this analysis (no test harness execution available here), but the vulnerable code path is directly verifiable by inspection:
1. Create a self-custody `PlotNFT2Wallet` (`desired_state="self_custody"` as in `chia/_tests/pools/test_plotnft_v2_drivers.py::test_plotnft_self_custody_claim`).
2. Farm a block to the `p2_singleton_puzzle_hash` so a `PoolReward` coin is added via `coin_added` → `plotnft2_store.add_pool_reward` (`chia/wallet/plotnft_wallet/plotnft_wallet.py:651-657`).
3. Instead of calling `claim_rewards`, call `melt_plotnft` (`chia/wallet/plotnft_wallet/plotnft_wallet.py:483-514`), which succeeds because it performs no check against `plotnft2_store.get_pool_rewards`.
4. After the melt spend confirms, the plot-NFT singleton coin is destroyed (`CREATE_COIN 0 -113`), and any subsequent attempt to call `claim_rewards`/`claim_pool_rewards` for the still-unswept `PoolReward` coin will fail because there is no live singleton coin able to spend `self.singleton_action_spend(...)` and issue the required `SendMessage` to the reward coin's `SingletonMember` puzzle — permanently freezing that coin's XCH. [2](#0-1) [1](#0-0) [3](#0-2)

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

**File:** chia/pools/plotnft_drivers.py (L751-777)
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

```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L149-161)
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

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L651-657)
```python
        elif coin_data is None and coin.puzzle_hash == self.p2_singleton_puzzle_hash:
            if coin.parent_coin_info[0:16] == self.wallet_state_manager.constants.GENESIS_CHALLENGE[0:16]:
                await self.wallet_state_manager.plotnft2_store.add_pool_reward(
                    pool_reward=PoolReward(singleton_id=self.plotnft_id, coin=coin)
                )
            else:
                raise ValueError(f"A non-pooling reward coin was paid to PlotNFT with id: {self.plotnft_id}")
```
