# Q2977: vlMGPBaseRewarder.getReward - dust threshold waives the forfeit entirely

## Question
In rewards/vlMGPBaseRewarder.sol, _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Starting from a state where a large MGP distribution has just been queued and no account has settled yet, can an unprivileged EOA use `getReward(address _account, address _receiver)` to leave `userRewards[_rewardToken][account]` inconsistent with `rewards[_rewardToken].rewardPerTokenStored`, violating the invariant that a rounding convenience must not create a settlement size at which the forfeit rule stops applying and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: dust threshold waives the forfeit entirely)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: a rounding convenience must not create a settlement size at which the forfeit rule stops applying; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path) under a large MGP distribution has just been queued and no account has settled yet, asserting on every row that a rounding convenience must not create a settlement size at which the forfeit rule stops applying.
