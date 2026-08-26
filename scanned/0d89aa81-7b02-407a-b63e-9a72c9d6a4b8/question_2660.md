# Q2660: BribeRewardPool.updateFor - balanceOf override diverges from the inherited totalStaked semantics

## Question
rewards/BribeRewardPool.sol: BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Under the bribe token has begun reverting on transfer, is there an unprivileged sequence of `updateFor(address _account) inherited from BaseRewardPoolV2` that leaves `totalSupply` unreconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`, violates the invariant that all reward math in a contract must read the balance ledger the contract actually maintains, and delivers Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `updateFor(address _account) inherited from BaseRewardPoolV2` (mechanism: balanceOf override diverges from the inherited totalStaked semantics)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their bribe index is pinned
- Exploit idea: BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Precondition: the bribe token has begun reverting on transfer.
- Invariant to test: all reward math in a contract must read the balance ledger the contract actually maintains; concretely, `totalSupply` must stay reconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the bribe token has begun reverting on transfer, have the attacker run `updateFor(address _account) inherited from BaseRewardPoolV2`, then assert the victim's claimable value and the `totalSupply` versus `the sum of userVotedForPoolInVlmgp over all voters for this pool` relation are unchanged by the attacker's transaction.
