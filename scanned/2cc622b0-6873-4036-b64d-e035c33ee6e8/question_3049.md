# Q3049: vlMGPBaseRewarder.getReward - _queueNewRewardsWithoutTransfer credits value with no matching balance

## Question
Consider rewards/vlMGPBaseRewarder.sol, where _queueNewRewardsWithoutTransfer() raises historicalRewards and rewardPerTokenStored without any token transfer, relying on the forfeited amount already sitting in the contract, so any path that reaches it without a real retained balance promises tokens the pool does not hold. Assuming a large MGP distribution has just been queued and no account has settled yet, can an unprivileged attacker turn this into a divergence between `totalStaked()` and `IERC20(vlMGP).totalSupply()` via `getReward(address _account, address _receiver)`, breaking the invariant that the reward index may only be raised against tokens the contract has actually retained and producing Critical - Protocol insolvency?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: _queueNewRewardsWithoutTransfer credits value with no matching balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _queueNewRewardsWithoutTransfer() raises historicalRewards and rewardPerTokenStored without any token transfer, relying on the forfeited amount already sitting in the contract, so any path that reaches it without a real retained balance promises tokens the pool does not hold. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: the reward index may only be raised against tokens the contract has actually retained; concretely, `totalStaked()` must stay reconciled with `IERC20(vlMGP).totalSupply()`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange a large MGP distribution has just been queued and no account has settled yet, call `getReward(address _account, address _receiver)`, and assert `totalStaked()` equals `IERC20(vlMGP).totalSupply()` and that no account can withdraw more than it put in.
