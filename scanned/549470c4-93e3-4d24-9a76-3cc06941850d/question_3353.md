# Q3353: BaseRewardPoolV2.updateFor - stake, claim and unstake inside one block

## Question
Note that in rewards/BaseRewardPoolV2.sol, balanceOf() is an instantaneous read of MasterMagpie's UserInfo.amount with no time weighting, so an attacker can deposit, trigger updateFor/getReward and withdraw inside a single transaction to capture an index jump they contributed nothing to. Can an attacker holding only tokens bought on market reach it via `updateFor(address _account)` under the attacker funds the action with a flash loan of the staking token repaid in the same transaction and force `rewards[_rewardToken].historicalRewards` apart from `IERC20(_rewardToken).balanceOf(address(this))`, breaking the invariant that reward share must be weighted by time held, not by balance at the instant the index moves for Critical - Direct theft of user funds?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: stake, claim and unstake inside one block)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: balanceOf() is an instantaneous read of MasterMagpie's UserInfo.amount with no time weighting, so an attacker can deposit, trigger updateFor/getReward and withdraw inside a single transaction to capture an index jump they contributed nothing to. Precondition: the attacker funds the action with a flash loan of the staking token repaid in the same transaction.
- Invariant to test: reward share must be weighted by time held, not by balance at the instant the index moves; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the victim address and the exact block in which their reward index is snapshotted) under the attacker funds the action with a flash loan of the staking token repaid in the same transaction, asserting on every row that reward share must be weighted by time held, not by balance at the instant the index moves.
