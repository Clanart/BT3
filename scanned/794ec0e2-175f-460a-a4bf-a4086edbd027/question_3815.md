# Q3815: mWOMSVBaseRewarder.getReward - dust threshold waives the forfeit entirely

## Question
rewards/mWOMSVBaseRewarder.sol: _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. With the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path under attacker control and totalStaked is zero and queuedRewards holds a backlog, can an unprivileged caller sequence `getReward(address _account, address _receiver)` so that `balanceOf(account)` and `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked` no longer reconcile, violating the invariant that a rounding convenience must not create a settlement size at which the forfeit rule stops applying and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: dust threshold waives the forfeit entirely)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Precondition: totalStaked is zero and queuedRewards holds a backlog.
- Invariant to test: a rounding convenience must not create a settlement size at which the forfeit rule stops applying; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path) under totalStaked is zero and queuedRewards holds a backlog, asserting on every row that a rounding convenience must not create a settlement size at which the forfeit rule stops applying.
