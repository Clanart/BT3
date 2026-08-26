# Q4120: vlMGPBaseRewarder.getRewards - _queueNewRewardsWithoutTransfer credits value with no matching balance

## Question
In rewards/vlMGPBaseRewarder.sol, _queueNewRewardsWithoutTransfer() raises historicalRewards and rewardPerTokenStored without any token transfer, relying on the forfeited amount already sitting in the contract, so any path that reaches it without a real retained balance promises tokens the pool does not hold. Can an unprivileged attacker reach this through `getRewards(address _account, address _receiver, address[] _rewardTokens)` while the attacker locks one block before a known large settlement and unlocks one block after, and drive `forfeitAmount` out of agreement with `rewardInfo.rewardPerTokenStored` - breaking the invariant that the reward index may only be raised against tokens the contract has actually retained - for Critical - Protocol insolvency?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: _queueNewRewardsWithoutTransfer credits value with no matching balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _queueNewRewardsWithoutTransfer() raises historicalRewards and rewardPerTokenStored without any token transfer, relying on the forfeited amount already sitting in the contract, so any path that reaches it without a real retained balance promises tokens the pool does not hold. Precondition: the attacker locks one block before a known large settlement and unlocks one block after.
- Invariant to test: the reward index may only be raised against tokens the contract has actually retained; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `getRewards(address _account, address _receiver, address[] _rewardTokens)` sequence atomically under the attacker locks one block before a known large settlement and unlocks one block after, asserting at the end that `forfeitAmount` still equals `rewardInfo.rewardPerTokenStored` and the PoC's balance delta is non-positive.
