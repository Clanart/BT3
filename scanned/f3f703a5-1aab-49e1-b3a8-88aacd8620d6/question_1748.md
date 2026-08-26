# Q1748: BribeRewardPool.updateFor - balanceOf override diverges from the inherited totalStaked semantics

## Question
rewards/BribeRewardPool.sol: BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Under totalSupply is zero because every voter has unvoted, is there an unprivileged sequence of `updateFor(address _account) inherited from BaseRewardPoolV2` that leaves `rewards[_rewardToken].queuedRewards` unreconciled with `totalSupply at the moment of the flush`, violates the invariant that all reward math in a contract must read the balance ledger the contract actually maintains, and delivers Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `updateFor(address _account) inherited from BaseRewardPoolV2` (mechanism: balanceOf override diverges from the inherited totalStaked semantics)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their bribe index is pinned
- Exploit idea: BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Precondition: totalSupply is zero because every voter has unvoted.
- Invariant to test: all reward math in a contract must read the balance ledger the contract actually maintains; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange totalSupply is zero because every voter has unvoted, call `updateFor(address _account) inherited from BaseRewardPoolV2`, and assert `rewards[_rewardToken].queuedRewards` equals `totalSupply at the moment of the flush` and that no account can withdraw more than it put in.
