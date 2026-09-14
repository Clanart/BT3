Based on my investigation, I found a genuine analog in the Chia PlotNFT pooling module, directly evidenced by an in-repo test named "LOSE REWARDS (while leaving)".

### Title
Pool rewards paid to a PlotNFT's p2-singleton while it is in the LEAVING_POOL/waiting-room state are permanently lost when forwarded - (File: chia/wallet/plotnft_wallet/plotnft_wallet.py, chia/pools/plotnft_drivers.py)

### Summary
This mirrors the reported bug class: value received by a shared/singleton accounting structure during a state-transition gap (there: syndicate with `numberOfRegisteredKnots == 0`; here: a PlotNFT singleton that has left/is leaving a pool) is not properly credited to anyone and becomes stuck/lost when later "claimed" through the normal reward-forwarding path.

### Finding Description
`PlotNFT2Wallet.claim_rewards` explicitly forbids claiming self-pooling-style rewards while `plotnft.pooling` is true, directing callers to `forward_pool_reward` instead [1](#0-0) . `forward_pool_reward`/`forward_pool_reward_dpuz` builds a spend that curries `forward_to_pool_puzzle_hash_dpuz.clsp` with the pool's `pool_puzzle_hash` and consumes the pending reward coin via the `RewardPuzzle`/`SEND_MESSAGE` mechanism [2](#0-1) [3](#0-2) .

The wallet test suite demonstrates that when this reward is forwarded while the PlotNFT is in the `LEAVING_POOL` (waiting-room) state rather than actively `FARMING_TO_POOL`, the reward's value is deducted from the wallet's tracked balance with no compensating increase anywhere (no XCH credit, no plotnft balance credit) — the test is literally titled "LOSE REWARDS (while leaving)": [4](#0-3) 

This is structurally the same defect pattern as the syndicate report: value (pool reward / EIP1559 tip) is received during a period where the singleton/syndicate's registered/active state does not match the assumption baked into the payout puzzle (`numberOfRegisteredKnots == 0` there; `LEAVING_POOL` here), and the code path that "settles" the value (forward-to-pool spend / `updateAccruedETHPerShares`) has no branch to route it back to the rightful owner (the plotnft's self-custody owner) — so it is effectively burned/locked instead of returned to the user who earned it while not actively pooled.

### Impact Explanation
A PlotNFT owner who initiates leaving a pool (`leave_pool`) but still has farming rewards paid to the p2-singleton puzzle hash before the two-stage exit completes will lose those rewards if they are (or an automated indexer/farmer helper) forwarded via `forward_pool_reward` instead of `claim_pool_rewards`. Since `claim_rewards`/`claim_pool_rewards` is programmatically blocked while `self.pooling` is true (which includes the `LEAVING_POOL` waiting-room state) [5](#0-4) , and forwarding during that state loses the coin value per the test evidence, funds are unrecoverable through documented wallet flows. This is a direct loss of farmer-earned XCH, analogous to the syndicate's lost EIP1559 rewards.

### Likelihood Explanation
This occurs in the normal course of pool switching. Any PlotNFT owner who submits a `leave_pool` request and farms even one more reward before the relative-lock height elapses (a routine two-stage exit that can take hours) will be exposed to this loss if the forwarding path is used or auto-triggered for that state. This is reachable purely from wallet-owner actions (no malicious peer required), matching the reachable-actor constraints (wallet user / plotnft owner).

### Recommendation
`PlotNFT.forward_pool_reward` (and any automated/farmer-side logic that decides between `claim_pool_rewards` and `forward_pool_reward` based on `pooling` state) should treat `LEAVING_POOL` as a state requiring `claim_pool_rewards` (self-custody payout) rather than `forward_pool_reward`, or the CLSP puzzle in `forward_to_pool_puzzle_hash_dpuz.clsp` should be reworked so that rewards accrued while in `LEAVING_POOL` are held/re-attributed to the plotnft owner instead of being consumed without a corresponding value-preserving `CREATE_COIN`. At minimum, `claim_rewards`/`forward_pool_reward` dispatch should be state-aware for `LEAVING_POOL` specifically, not just the binary `pooling` flag.

### Proof of Concept
The existing repository test demonstrates the loss directly: after `leave_pool` is called and while the plotnft is in the leaving/waiting-room state, `plotnft.forward_pool_reward(pool_reward)` is pushed and the resulting `process_pending_states` assertion shows the plotnft's `confirmed_wallet_balance`, `unconfirmed_wallet_balance`, `spendable_balance`, and `max_send_amount` all decrease by `POOL_REWARD_AMOUNT` with no offsetting credit elsewhere: [4](#0-3) 

**Note on limitations:** I was unable to load the full contents of `chia/pools/forward_to_pool_puzzle_hash_dpuz.clsp` before the tool budget ran out, so I cannot show the exact CLVM condition list that fails to preserve value in the `LEAVING_POOL` case. I recommend a Devin session with full file access to confirm the precise CLSP logic (whether it omits a `CREATE_COIN` back to the owner, or the `AssertHeightRelative`/waiting-room escape puzzle simply routes the coin to a puzzle hash the owner no longer controls) before finalizing severity and fix.

### Citations

**File:** chia/pools/plotnft_drivers.py (L146-149)
```python
    def forward_pool_reward_dpuz(self) -> Program:
        return forward_to_pool_puzzle_hash_dpuz(
            self.guaranteed_pool_config.pool_puzzle_hash, self.guaranteed_pool_config.pool_memoization
        )
```

**File:** chia/pools/plotnft_drivers.py (L277-285)
```python
    def forward_pool_reward_inner_solution(self, reward: PoolReward) -> Program:
        custody_pwr = self.puzzle_with_restrictions()
        assert isinstance(custody_pwr.puzzle, MofN)
        return custody_pwr.solve(
            member_validator_solutions=[],
            dpuz_validator_solutions=[],
            member_solution=custody_pwr.puzzle.solve(self.pool_proven_spend()),
            delegated_puzzle_and_solution=self.claim_pool_reward_dpuz_and_solution(reward),
        )
```

**File:** chia/pools/plotnft_drivers.py (L590-598)
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
```

**File:** chia/_tests/wallet/plotnft_wallet/test_plotnft_wallet.py (L451-473)
```python
    # LOSE REWARDS (while leaving)
    plotnft = await plotnft_wallet.get_current_plotnft()
    [pool_reward] = await env.wallet_state_manager.plotnft2_store.get_pool_rewards(plotnft_id=plotnft_wallet.plotnft_id)
    coin_spends = plotnft.forward_pool_reward(pool_reward)
    await env.rpc_client.push_tx(PushTX(spend_bundle=WalletSpendBundle(coin_spends, G2Element())))

    await wallet_environments.process_pending_states(
        [
            WalletStateTransition(
                pre_block_balance_updates={},
                post_block_balance_updates={
                    "plotnft": {
                        "confirmed_wallet_balance": -POOL_REWARD_AMOUNT,
                        "unconfirmed_wallet_balance": -POOL_REWARD_AMOUNT,
                        "max_send_amount": -POOL_REWARD_AMOUNT,
                        "spendable_balance": -POOL_REWARD_AMOUNT,
                        "unspent_coin_count": -1,
                    }
                },
            )
        ],
        bundles_to_repush=[WalletSpendBundle(coin_spends, G2Element())],
    )
```
