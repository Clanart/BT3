# Q5043: mWOMSVBaseRewarder.getRewards - _queueNewRewardsWithoutTransfer credits value with no matching balance

## Question
In rewards/mWOMSVBaseRewarder.sol, _queueNewRewardsWithoutTransfer() raises historicalRewards and rewardPerTokenStored without any token transfer, relying on the forfeited amount already sitting in the contract, so any path that reaches it without a real retained balance promises tokens the pool does not hold. Starting from a state where the attacker settles the same reward token through two separate multiclaimSpec calls in one block, can an unprivileged EOA use `getRewards(address _account, address _receiver, address[] _rewardTokens)` to leave `userRewards[_rewardToken][account]` inconsistent with `rewards[_rewardToken].rewardPerTokenStored`, violating the invariant that the reward index may only be raised against tokens the contract has actually retained and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: _queueNewRewardsWithoutTransfer credits value with no matching balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _queueNewRewardsWithoutTransfer() raises historicalRewards and rewardPerTokenStored without any token transfer, relying on the forfeited amount already sitting in the contract, so any path that reaches it without a real retained balance promises tokens the pool does not hold. Precondition: the attacker settles the same reward token through two separate multiclaimSpec calls in one block.
- Invariant to test: the reward index may only be raised against tokens the contract has actually retained; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor) under the attacker settles the same reward token through two separate multiclaimSpec calls in one block, asserting on every row that the reward index may only be raised against tokens the contract has actually retained.
