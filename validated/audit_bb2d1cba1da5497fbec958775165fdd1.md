### Title
`PlotNFT2Wallet.claim_rewards` allows `fee == total_reward_amount`, producing a zero-value payout coin and permanently burning the claimed pool rewards - (File: chia/wallet/plotnft_wallet/plotnft_wallet.py)

### Summary
`PlotNFT2Wallet.claim_rewards` only rejects a fee that is *strictly greater* than the sum of the rewards being claimed, using `if fee > total_reward_amount: raise ValueError(...)`. This mirrors the Y2K-Finance `Carousel.mintRollovers` bug where a check used `<` instead of `<=`, letting `assets == relayerFee` slip through and mint `0` shares. Here, when `fee == total_reward_amount`, the code proceeds to build a `CreateCoin` to `rewards_claim_puzhash` with `amount=uint64(total_reward_amount - fee)`, i.e. amount `0`. [1](#0-0) 

### Finding Description
`claim_rewards` collects all unclaimed pool-reward coins for a PlotNFT (`rewards_to_claim`), sums their amount into `total_reward_amount`, and validates the caller-supplied `fee` with:
```
if fee > total_reward_amount:
    raise ValueError("Fee is greater than the total amount of rewards")
``` [2](#0-1) 

This permits `fee == total_reward_amount`. The code then builds the payout `CreateCoin` with `amount=uint64(total_reward_amount - fee)` (i.e., `0`), inside the delegated puzzle passed to `plotnft.claim_pool_rewards(...)`, and also records the resulting `additions` list with a `Coin(..., amount=uint64(total_reward_amount - fee))` (again `0`) in the outgoing `TransactionRecord`. [3](#0-2) 

The `rewards_to_claim` coins are consumed as removals in the same spend bundle regardless of whether the resulting payout coin has any value, so once this spend is pushed and confirmed, the pool-reward coins are spent, the plotnft2_store no longer lists them as pending, and their entire value is destroyed as "fee" while the actual payout coin created is worth `0`. This is the same failure pattern as the Sherlock finding: a boundary condition (`>` instead of `>=`, i.e., allowing the "equal" case) lets a value-bearing operation silently degrade to zero, and the accounting state (spent rewards / rollover queue) moves forward as if a real transfer happened.

### Impact Explanation
This is reachable by any local wallet owner of a PlotNFT (via the `claim_rewards` wallet RPC entrypoint / CLI), passing an unusually chosen `fee` equal to the summed value of pending rewards. The result is: the reward coins are irreversibly spent (fee paid entirely to consume them, no farmer benefit), and the intended payout coin created has amount `0`. Note that a zero-amount coin is atypical/dust and would be essentially unspendable/uneconomical, and depending on downstream indexing this can also look like a silent loss of the entire claimed reward balance instead of a `ValueError` guard rejecting the operation as intended. This is a fund-loss-adjacent boundary/logic bug in a wallet-reachable code path that handles supply-bearing coin creation, matching the "no-impact from divide/zero" class explicitly excluded, but distinctly matching the "mints 0 shares, real value silently destroyed" class the report targets.

### Likelihood Explanation
Requires the wallet owner (or any code path/automation calling `claim_rewards`) to specify `fee == total_reward_amount` exactly. This is a plausible but narrow user input (e.g., a rounding bug or fee-estimation logic that computes fee = full reward balance under a "claim everything" flow). Unlike the Sherlock finding, this is not attacker-controlled by a third party against a victim; it is a self-inflicted misconfiguration path, which lowers likelihood versus the original finding, but the underlying boundary defect (`>` vs `>=`) is identical to the flagged root cause.

### Recommendation
Change the guard to reject the equality case as well, mirroring the audited fix (`assets <= relayerFee` instead of `<`):
```python
if fee >= total_reward_amount:
    raise ValueError("Fee must be less than the total amount of rewards")
```
This prevents constructing a `CreateCoin` with amount `0` and losing the entire claimed reward value to fees.

### Proof of Concept
1. A PlotNFT2Wallet has pending pool rewards totaling `total_reward_amount = X` mojos, tracked via `plotnft2_store.get_pool_rewards`. [4](#0-3) 
2. Caller invokes `claim_rewards(fee=uint64(X))` (fee exactly equal to total rewards).
3. The check `if fee > total_reward_amount` passes (since `fee == total_reward_amount`, not `>`), so no exception is raised. [5](#0-4) 
4. `plotnft.claim_pool_rewards(...)` is called with a `CreateCoin(puzzle_hash=rewards_claim_puzhash, amount=uint64(total_reward_amount - fee))`, i.e., `amount=uint64(0)`. [6](#0-5) 
5. The spend bundle consumes all `rewards_to_claim` coins as removals and is pushed; the transaction record reflects an addition coin of amount `0` to `rewards_claim_puzhash`. [7](#0-6) 
6. Once confirmed, the reward coins are permanently spent for zero payout value.

**Note on verification limits**: I could not fully trace `plotnft.claim_pool_rewards` inside `chia/pools/plotnft_drivers.py` (index truncation prevented reading its body) to confirm whether the underlying CLVM/consensus layer independently rejects a `CREATE_COIN` condition with `amount == 0` at the block-validation level (which could turn this into a hard transaction failure rather than a silent value-loss). If such a check exists, the practical impact would be "spend fails, funds not lost but stuck until the user retries with a correct fee" — still a wallet-reachable griefing/DoS-of-self bug analogous to the original report, but not a definite fund-loss. Confirming this requires reading the full `plotnft_drivers.py` and the coin-amount validation path in `chia/consensus/`, which was not available within the current tool budget.

### Citations

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L156-222)
```python
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
```
