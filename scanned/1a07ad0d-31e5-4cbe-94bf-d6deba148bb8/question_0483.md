# Q0483: BribeRewardPool.donateRewards - balanceOf override diverges from the inherited totalStaked semantics

## Question
In rewards/BribeRewardPool.sol, BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Does `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` let an unprivileged caller exploit that under a large bribe for the gauge is pending and no cast has run yet, so that `totalSupply` diverges from `the sum of userVotedForPoolInVlmgp over all voters for this pool`, the invariant that all reward math in a contract must read the balance ledger the contract actually maintains is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` (mechanism: balanceOf override diverges from the inherited totalStaked semantics)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward and which already-registered bribe token is provisioned
- Exploit idea: BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Precondition: a large bribe for the gauge is pending and no cast has run yet.
- Invariant to test: all reward math in a contract must read the balance ledger the contract actually maintains; concretely, `totalSupply` must stay reconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange a large bribe for the gauge is pending and no cast has run yet, call `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`, and assert `totalSupply` equals `the sum of userVotedForPoolInVlmgp over all voters for this pool` and that no account can withdraw more than it put in.
