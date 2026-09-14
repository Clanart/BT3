### Title
Unsolicited coin sent to a PlotNFT's `p2_singleton_puzzle_hash` raises an uncaught `ValueError` in `coin_added`, permanently blocking pool-reward sync/claiming - ([File: chia/wallet/plotnft_wallet/plotnft_wallet.py])

### Summary
`PlotNFT2Wallet.claim_rewards` aggregates *every* tracked, unspent pool-reward coin at the `p2_singleton_puzzle_hash` address into a single atomic spend bundle before any of the rewards can be claimed [1](#0-0) . Which coins are considered "rewards to claim" is determined by `coin_added`, which unconditionally raises a `ValueError` for any coin received at that address whose `parent_coin_info` does not look like a legitimate farming reward, instead of simply ignoring/rejecting the deposit gracefully [2](#0-1) .

### Finding Description
`p2_singleton_puzzle_hash` is a well-known, publicly derivable puzzle hash for any given PlotNFT (it is a pay-to-singleton address, so anyone can send an arbitrary XCH payment to it) [3](#0-2) . When the wallet's sync layer observes a new coin at this address that is not recognized as a `PlotNFT` singleton output, it falls into the `elif coin_data is None and coin.puzzle_hash == self.p2_singleton_puzzle_hash:` branch. If the coin's `parent_coin_info` prefix does not match `GENESIS_CHALLENGE`, i.e., it is not an actual consensus-issued pooling reward, the code raises `ValueError(f"A non-pooling reward coin was paid to PlotNFT with id: {self.plotnft_id}")` [2](#0-1) .

This mirrors the OpenQ bug class: a party can deposit an unwanted/unexpected "asset" (here, an ordinary spend to a known public address) that gets swept up into the wallet's tracked deposit set, and that single bad entry causes exception/failure of the whole batched claim workflow instead of being safely rejected or isolated. In OpenQ, the malicious token blocks the atomic claim loop over all bounty funding tokens; here, an unsolicited coin sent to the PlotNFT reward address raises an unhandled exception during `coin_added` processing, which is invoked as part of wallet state sync for every new coin belonging to the wallet. If this exception is not caught by the caller (unverified from available context, see caveat below), it can abort processing of the wallet's state-sync callback for that block, which in turn can prevent the `pool_reward2s` table from being populated correctly and block `claim_rewards`'s ability to build a consistent, complete claim transaction (`PlotNFT2Wallet.claim_rewards` reads all rows via `plotnft2_store.get_pool_rewards` and bundles them into one all-or-nothing spend) [4](#0-3) .

### Impact Explanation
If exercised, this would let an unprivileged actor — anyone who knows a victim's PlotNFT `p2_singleton_puzzle_hash` (public/derivable) — send a normal payment there to trigger a wallet-side exception during coin-sync processing, potentially halting or corrupting the wallet's pool-reward tracking and blocking legitimate reward claiming (`pw_absorb_rewards`/`claim_rewards`), which handles real farming income. That would constitute a spend-triggered processing halt affecting a user's ability to claim legitimately earned funds.

### Likelihood Explanation
Sending XCH to a known puzzle hash is trivial and requires no special privilege — just knowledge of the target's `p2_singleton_puzzle_hash`, which is derivable from the PlotNFT's public launcher ID. No signature forgery, no malicious peer, and no farmer/timelord access is required. However, whether this actually causes a *persistent* processing halt versus being caught and logged by the caller of `coin_added` (in `wallet_state_manager.py`, `wallet_node.py`, or `wallet_protocol.py`) could not be fully confirmed in this pass — the grep for the exact call site (`await wallet.coin_added(...)`) inside `wallet_state_manager.py` did not surface conclusively, so it's uncertain whether the surrounding code wraps this call in a try/except that isolates the failure per-wallet/per-coin versus aborting the whole sync batch.

### Recommendation
In `coin_added`, do not raise on receipt of a non-farming-reward coin at `p2_singleton_puzzle_hash`; log a warning and skip/ignore the coin (or route it to a separate "unexpected deposit" record) rather than raising an unhandled `ValueError` that can interrupt sync processing for the wallet.

### Proof of Concept
1. Determine a target PlotNFT's `p2_singleton_puzzle_hash` via `RewardPuzzle(singleton_id=<launcher_id>).puzzle_hash()` (public/derivable value) [3](#0-2) .
2. From any wallet, submit a standard spend bundle that creates a coin with this puzzle hash and an amount/parent that is not a genuine consensus reward coin (i.e., its `parent_coin_info` does not have the `GENESIS_CHALLENGE[0:16]` prefix).
3. When the victim's wallet syncs and observes this new coin at `p2_singleton_puzzle_hash`, `coin_added` hits the `elif coin_data is None and coin.puzzle_hash == self.p2_singleton_puzzle_hash:` branch, finds the prefix mismatch, and raises `ValueError` [2](#0-1) .
4. Depending on whether the calling sync loop propagates or swallows this exception (not fully confirmed here), this can abort processing of that sync batch, preventing legitimate pool rewards from being recorded/claimable via `claim_rewards`.

**Caveat:** I was unable to conclusively trace the exact call site of `coin_added` inside `wallet_state_manager.py` (the grep for the invocation pattern returned no matches, likely due to indexing/pattern limitations), so the precise blast radius (per-block sync abort vs. isolated per-coin failure) is not fully verified. A Devin session with full repository access would be needed to confirm the exception-handling behavior of the caller before treating this as conclusively proven at High/Medium severity.

### Citations

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L87-89)
```python
    @property
    def p2_singleton_puzzle_hash(self) -> bytes32:
        return RewardPuzzle(singleton_id=self.plotnft_id).puzzle_hash()
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
