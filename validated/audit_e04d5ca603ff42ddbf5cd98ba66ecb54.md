## Title
`PlotNFT2Wallet.claim_rewards` builds a single spend bundle over all unclaimed pool rewards without excluding coins already spent (e.g. via a race, reorg desync, or duplicate/interrupted claim), so one stale/already-spent reward coin causes the entire claim to fail as a double-spend, denying the owner all otherwise-claimable rewards — ([File: chia/wallet/plotnft_wallet/plotnft_wallet.py])

### Summary
The reported bug class is: a persistent list of "deposits"/entries is consulted to build a claim/settlement transaction, but the list is not properly kept in sync with on-chain consumption of those entries, so one stale entry in the list poisons the whole batched claim transaction and the legitimate claimer gets nothing. The closest reachable analog in this codebase is `PlotNFT2Wallet.claim_rewards`, which reads `pool_reward2s` rows via `PlotNFTStore.get_pool_rewards()` and blindly spends *all* of them together in one `WalletSpendBundle`, with each reward spend chained to the first via `AssertCoinAnnouncement`/`CreateCoinAnnouncement`.

### Finding Description
`PlotNFT2Wallet.claim_rewards()` fetches unspent-looking reward records from the local `pool_reward2s` table: [1](#0-0) 

It then builds one composite `PlotNFT.claim_pool_rewards(...)` spend bundle that spends the singleton plus **every** reward coin in `rewards_to_claim`, using `AssertCoinAnnouncement`/`CreateCoinAnnouncement` to chain them into a single atomic spend: [2](#0-1) [3](#0-2) 

The reward rows are marked spent only by an explicit call to `mark_pool_reward_as_spent`, and rows are only excluded from `get_pool_rewards()` when `spent_height IS NULL`: [4](#0-3) 

If a reward coin recorded in `pool_reward2s` becomes unspendable outside the normal `mark_pool_reward_as_spent` update path — e.g. the DB entry is not yet marked spent when `claim_rewards` runs again after a prior claim attempt whose confirmation is delayed, or a short reorg reverts the `spent_height` clear via `rollback_to_block` while the true chain state has already advanced past it, or wallet sync misses updating `spent_height` for a particular coin due to any sync edge case — the batch will include a coin that is no longer actually unspent on-chain. Because all rewards are combined into **one** `SpendBundle` chained by coin announcements, when that bundle is submitted, the already-spent coin causes a `DOUBLE_SPEND` rejection for the *entire* bundle, not just the stale entry. This blocks the owner from claiming any of the other, perfectly valid, reward coins in the same batch until the stale entry is manually resolved (this mirrors `BountyCore`'s pattern where one refunded-but-not-removed `nftDeposits[i]` entry causes the whole claim loop to fail).

This differs from the audited Solidity bug in mechanism (chia has no analogous inflight “deposit index” living inside a smart contract; it lives in the wallet’s local SQLite mirror of chain state) but the root cause and effect class are the same: a locally-tracked collection of claimable items is not reliably kept congruent with actual on-chain spend status before being consumed in one atomic batch operation, and no coin is individually validated/filtered before being included, so one stale item causes total claim failure (a spend-triggered transaction-processing halt for the legitimate claimant).

### Impact Explanation
This is a self-DoS on the plotnft/pool reward claim path: it does not create unauthorized coin movement, forge identity, or divert farming rewards to an attacker, but it can indefinitely block a farmer/pool participant from spending their own p2-singleton reward coins in a single call, because `claim_rewards` has no fallback that drops the offending coin and retries with the remainder — every retry re-reads the same stale set from `pool_reward2s` and re-fails identically until the stale row's `spent_height` is corrected (which currently only happens via wallet resync noticing the coin is spent). This is a legitimate availability impact on the owner's own funds rather than a fund-theft or consensus-divergence bug, so it sits at the boundary of the accepted “spend-triggered transaction-processing halt” category, but is inherently self-inflicted/local and does not meet the bar of coin-set divergence, invalid block/spend acceptance, or theft required for High/Critical severity by these validation rules.

### Likelihood Explanation
Requires a specific desync between the `pool_reward2s` DB state and true chain state (e.g. a reorg boundary interacting with `rollback_to_block`, or overlapping/duplicate claim submissions racing against wallet sync) — this is a corner-case reliability bug rather than something an unprivileged external attacker can trigger against another user's wallet, since `pool_reward2s` is local, per-wallet, unauthenticated-RPC-caller state, not attacker-influenced remote input.

### Recommendation
Before building the aggregate claim spend bundle, re-validate each `PoolReward` coin's on-chain unspent status (or catch `DOUBLE_SPEND`/mempool rejection per-coin and retry excluding the offending coin), and ensure `mark_pool_reward_as_spent` / `rollback_to_block` interactions in `PlotNFTStore` cannot leave a truly-spent coin marked as claimable. Consider batching claims so a single bad coin can be dropped and the remainder still submitted, analogous to filtering refunded deposits out of `nftDeposits` before iterating claims in the original report.

### Proof of Concept
Not independently reproducible from static analysis alone — the trigger condition (a reward coin recorded unspent in `pool_reward2s` while actually spent on-chain) depends on a specific sync/reorg timing window that would require a running two-node simulator test (e.g. extending `chia/_tests/wallet/plotnft_wallet/test_plotnft_wallet.py`) to reliably reproduce. I was unable to fully verify that `rollback_to_block`/sync logic actually permits this desync in practice within the scope of this review; this should be treated as a plausible-but-unconfirmed reliability issue pending a live reproduction, not a confirmed exploitable vulnerability.

### Citations

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L156-161)
```python
        rewards_to_claim = await self.wallet_state_manager.plotnft2_store.get_pool_rewards(plotnft_id=self.plotnft_id)
        if len(rewards_to_claim) == 0:
            raise ValueError("No rewards to claim")
        total_reward_amount = uint64(sum(reward.coin.amount for reward in rewards_to_claim))
        if fee > total_reward_amount:
            raise ValueError("Fee is greater than the total amount of rewards")
```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L163-198)
```python
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

**File:** chia/wallet/plotnft_wallet/plotnft_store.py (L134-218)
```python
    async def mark_pool_reward_as_spent(self, *, reward_id: bytes32, spent_height: uint32) -> None:
        async with self.db_wrapper.writer_maybe_transaction() as conn:
            await conn.execute_insert(
                "UPDATE pool_reward2s SET spent_height = ? WHERE coin_id = ?",
                (spent_height, reward_id),
            )

    async def get_latest_plotnft(self, launcher_id: bytes32) -> PlotNFT:
        async with self.db_wrapper.reader() as conn:
            rows = await conn.execute_fetchall(
                """
                SELECT *
                FROM plotnft2s
                WHERE launcher_id=?
                ORDER BY created_height DESC
                LIMIT 1;
                """,
                (launcher_id,),
            )
            return _row_to_plotnft(next(iter(rows)), self.genesis_challenge)

    async def get_plotnft_created_height(self, *, coin_id: bytes32) -> uint32:
        """Gets the height at which the specific interation of a plotnft was created"""
        async with self.db_wrapper.reader() as conn:
            rows = await conn.execute_fetchall("SELECT created_height from plotnft2s where coin_id = ?", (coin_id,))
            if len(list(rows)) == 0:
                raise ValueError(f"coin ID {coin_id} not found in PlotNFTStore")
            row = next(iter(rows))
            return uint32(row[0])

    async def get_latest_remark(self, launcher_id: bytes32) -> str:
        async with self.db_wrapper.reader() as conn:
            rows = await conn.execute_fetchall(
                """
                SELECT remark
                FROM plotnft2s
                WHERE launcher_id=?
                AND remark IS NOT NULL
                ORDER BY created_height DESC
                LIMIT 1;
                """,
                (launcher_id,),
            )
            return str(next(iter(rows))[0])

    async def get_plotnfts(self, *, coin_ids: list[bytes32]) -> list[PlotNFT]:
        if coin_ids == []:
            raise ValueError("coin_ids must not be empty")
        async with self.db_wrapper.reader() as conn:
            rows = await conn.execute_fetchall(
                f"SELECT * from plotnft2s where coin_id in ({', '.join(['?'] * len(coin_ids))})", coin_ids
            )
            plot_nfts_selected = [_row_to_plotnft(row, self.genesis_challenge) for row in rows]
            if len(plot_nfts_selected) != len(coin_ids):
                symmetric_difference = set(bytes32(row[0]) for row in rows) ^ set(coin_ids)
                raise ValueError(f"coin IDs {symmetric_difference} not found in PlotNFTStore")
            else:
                return plot_nfts_selected

    async def get_pool_rewards(
        self,
        *,
        plotnft_id: bytes32,
        max: int = DEFAULT_POOL_REWARDS_PER_CLAIM,
        include_spent: bool = False,
    ) -> list[PoolReward]:
        async with self.db_wrapper.reader() as conn:
            rows = await conn.execute_fetchall(
                (
                    "SELECT * from pool_reward2s WHERE singleton_id = ?"
                    + (" AND spent_height IS NULL" if not include_spent else "")
                    + " ORDER BY spent_height ASC LIMIT ?"
                ),
                (plotnft_id, max),
            )
            pool_rewards_selected = [
                PoolReward(
                    coin=Coin(
                        parent_coin_info=bytes32(row[1]), puzzle_hash=bytes32(row[2]), amount=uint64.from_bytes(row[3])
                    ),
                    singleton_id=bytes32(row[4]),
                )
                for row in rows
            ]
            return pool_rewards_selected
```
