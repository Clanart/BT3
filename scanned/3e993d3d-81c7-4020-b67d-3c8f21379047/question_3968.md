# Q3968: BaseRewardPoolV2.updateFor - stake, claim and unstake inside one block

## Question
In rewards/BaseRewardPoolV2.sol, balanceOf() is an instantaneous read of MasterMagpie's UserInfo.amount with no time weighting, so an attacker can deposit, trigger updateFor/getReward and withdraw inside a single transaction to capture an index jump they contributed nothing to. Does `updateFor(address _account)` let an unprivileged caller exploit that under the reward token charges a transfer fee so the received balance is below the requested amount, so that `rewardTokens.length` diverges from `isRewardToken[_rewardToken]`, the invariant that reward share must be weighted by time held, not by balance at the instant the index moves is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: stake, claim and unstake inside one block)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: balanceOf() is an instantaneous read of MasterMagpie's UserInfo.amount with no time weighting, so an attacker can deposit, trigger updateFor/getReward and withdraw inside a single transaction to capture an index jump they contributed nothing to. Precondition: the reward token charges a transfer fee so the received balance is below the requested amount.
- Invariant to test: reward share must be weighted by time held, not by balance at the instant the index moves; concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the reward token charges a transfer fee so the received balance is below the requested amount, snapshot `rewardTokens.length` and `isRewardToken[_rewardToken]`, run the attacker's `updateFor(address _account)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
