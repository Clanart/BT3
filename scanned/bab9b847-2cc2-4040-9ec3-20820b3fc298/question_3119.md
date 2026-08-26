# Q3119: mWOMSVBaseRewarder.getReward - donateRewards(uint256,address) is additionally exposed to any caller for already-registered reward tokens

## Question
In rewards/mWOMSVBaseRewarder.sol, this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Does `getReward(address _account, address _receiver)` let an unprivileged caller exploit that under a large MGP distribution has just been queued and no account has settled yet, so that `userRewards[_rewardToken][account]` diverges from `rewards[_rewardToken].rewardPerTokenStored`, the invariant that only an authorised manager may decide when and by how much the reward index moves is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: donateRewards(uint256,address) is additionally exposed to any caller for already-registered reward tokens)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: only an authorised manager may decide when and by how much the reward index moves; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path) under a large MGP distribution has just been queued and no account has settled yet, asserting on every row that only an authorised manager may decide when and by how much the reward index moves.
