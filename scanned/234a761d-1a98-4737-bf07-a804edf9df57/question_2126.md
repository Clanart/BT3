# Q2126: BaseRewardPool.updateFor - stake, claim and unstake inside one block

## Question
In rewards/BaseRewardPool.sol, balanceOf() is an instantaneous read of MasterMagpie's UserInfo.amount with no time weighting, so an attacker can deposit, trigger updateFor/getReward and withdraw inside a single transaction to capture an index jump they contributed nothing to. Starting from a state where the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, can an unprivileged EOA use `updateFor(address _account)` to leave `userRewards[_rewardToken][account]` inconsistent with `earned(account,_rewardToken)`, violating the invariant that reward share must be weighted by time held, not by balance at the instant the index moves and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: stake, claim and unstake inside one block)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: balanceOf() is an instantaneous read of MasterMagpie's UserInfo.amount with no time weighting, so an attacker can deposit, trigger updateFor/getReward and withdraw inside a single transaction to capture an index jump they contributed nothing to. Precondition: the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18.
- Invariant to test: reward share must be weighted by time held, not by balance at the instant the index moves; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, snapshot `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)`, run the attacker's `updateFor(address _account)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
