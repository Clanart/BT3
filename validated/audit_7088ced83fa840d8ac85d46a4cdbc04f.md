Based on the pool reward-claiming code, there is an analogous "user can direct their own future rewards to an unspendable/zero puzzle hash" pattern, but it requires an important caveat: unlike the original Solidity report where `claimRewards(address(0))` is an immediate, single-call self-burn, the Chia analog is two-stage — the destination is set once (at pool-wallet creation / join-pool time) and only later consumed when rewards are claimed.

### Title
Unvalidated `target_puzzle_hash` in Pool Wallet self-pooling state allows user to permanently burn their own claimed pool rewards - (File: chia/pools/pool_wallet.py)

### Summary
`PoolWallet.claim_pool_rewards()` pays every absorbed pool reward to `current_state.current.target_puzzle_hash` [1](#0-0) , a value taken directly from the `PoolState` committed on-chain when the pool wallet is created or transitions state. Neither the pool-wallet creation flow nor `claim_pool_rewards()` itself validates that this puzzle hash is a spendable, non-degenerate destination (e.g. not `bytes32.zeros`), mirroring the original `BasePool#claimRewards` issue where `_receiver` was never checked against `address(0)`.

### Finding Description
`target_puzzle_hash` in `PoolState` represents the final payout destination for self-pooled rewards [2](#0-1) . It is supplied by the wallet owner at pool-wallet creation time (`initial_target_state`) and is carried unchanged through `PoolWalletInfo.current`. When `claim_pool_rewards()` runs, it builds absorb spends and unconditionally records an outgoing transaction paying the full absorbed amount to `current_state.current.target_puzzle_hash` [3](#0-2) . No check anywhere in this path rejects a zero/burn puzzle hash before it is embedded into the singleton's committed `PoolState` or before it is used as a `CREATE_COIN` destination during absorb.

The newer `PlotNFT`/`PlotNFT2Wallet` pooling path shows the same unguarded pattern more directly: `PlotNFT.claim_pool_rewards()` accepts an arbitrary `DelegatedPuzzleAndSolution` per reward being claimed and builds a `CREATE_COIN` condition from whatever puzzle hash that delegated puzzle chooses [4](#0-3) , and the project's own test suite exercises exactly a zero-hash destination (`CreateCoin(bytes32.zeros, uint64(1))`) as a valid claim target with no rejection [5](#0-4) . This confirms the CLVM/driver layer imposes no restriction on the destination puzzle hash used when claiming pool rewards — it is trusted, unchecked user input, just like `_receiver` in the reported Solidity bug.

### Impact Explanation
If a user (or a compromised/buggy front-end acting on their behalf) sets `target_puzzle_hash` to `bytes32.zeros` or any other unspendable/unknown puzzle hash when creating a self-pooling wallet, every subsequent `claim_pool_rewards()` absorb permanently and irrecoverably burns that user's farming rewards, since the coin is created but no key/puzzle can ever spend it. This is a direct, on-chain, irreversible loss-of-funds condition, matching the "Medium" classification of the source report (self-directed reward burn due to missing zero/invalid-destination validation).

### Likelihood Explanation
This requires the same trigger as the original report: the coin owner themselves supplying a bad destination address, typically through a buggy wallet UI, RPC misuse, or user error, rather than a malicious third party. Reachability is confirmed as a design gap (no validation anywhere in `pool_wallet.py` or `plotnft_drivers.py`), but I could not fully verify within the available index whether any RPC-level guard exists in the `pw_join_pool` / `create_new_wallet` self-pooling RPC handlers that might reject a zero target puzzle hash before it reaches `PoolWallet`/`PlotNFT` state — this should be double-checked in the live repo, since the RPC layer for pool-wallet creation was not fully inspectable here.

### Recommendation
Add an explicit check rejecting `target_puzzle_hash == bytes32.zeros` (and any other well-known burn/null puzzle hash) at the point where `PoolState`/`target_puzzle_hash` is first accepted (pool wallet creation, `pw_join_pool`, `pw_self_pool`) and defensively again inside `PoolWallet.claim_pool_rewards()` / `PlotNFT.claim_pool_rewards()` before building the payout `CREATE_COIN` condition, so absorbed rewards can never be sent to a provably unspendable destination.

### Proof of Concept
Not independently reproducible from the indexed code alone (would require running the RPC creation flow end-to-end), but the mechanism is demonstrated directly by the existing test `test_plotnft_self_custody_claim`, which claims a pool reward using `CreateCoin(bytes32.zeros, uint64(1))` as the payout puzzle hash and succeeds without any rejection: [5](#0-4) .

### Citations

**File:** chia/pools/pool_wallet.py (L793-806)
```python
        # The claim spend, minus the fee amount from the main wallet
        async with action_scope.use() as interface:
            interface.side_effects.transactions.append(
                self.wallet_state_manager.new_outgoing_transaction(
                    wallet_id=uint32(self.wallet_id),
                    puzzle_hash=current_state.current.target_puzzle_hash,
                    amount=uint64(total_amount),
                    fee=fee,
                    spend_bundle=claim_spend,
                    additions=[add for add in claim_spend.additions() if add.amount == last_solution.coin.amount],
                    removals=claim_spend.removals(),
                    name=claim_spend.name(),
                )
            )
```

**File:** .cursor/context/pools.md (L34-34)
```markdown
- `target_puzzle_hash` means final payout destination, not the p2-singleton address where pool rewards are initially farmed. In self-pooling it is a local wallet puzzle hash; in farming-to-pool it is the pool's target puzzle hash.
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

**File:** chia/_tests/pools/test_plotnft_v2_drivers.py (L303-322)
```python
        reward_dpuz_and_sol = DelegatedPuzzleAndSolution(
            puzzle=ACS, solution=Program.to([CreateCoin(bytes32.zeros, uint64(1)).to_program()])
        )
        coin_spends = plotnft.claim_pool_rewards(
            rewards_to_claim=[reward], reward_delegated_puzzles_and_solutions=[reward_dpuz_and_sol]
        )
        result = await sim_client.push_tx(
            cost_logger.add_cost(
                "Claim Pool Reward",
                WalletSpendBundle(
                    coin_spends,
                    sign_spend(coin_spends, sim.defaults.AGG_SIG_ME_ADDITIONAL_DATA),
                ),
            )
        )
        assert result == (MempoolInclusionStatus.SUCCESS, None)
        await sim.farm_block()

        # Make sure the pooling reward did what it was supposed to
        assert len(await sim_client.get_coin_records_by_puzzle_hash(bytes32.zeros)) == 1
```
