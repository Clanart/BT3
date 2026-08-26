# Q4619: mWOMSVBaseRewarder.getReward - donateRewards(uint256,address) is additionally exposed to any caller for already-registered reward tokens

## Question
rewards/mWOMSVBaseRewarder.sol: this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. With the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path under attacker control and the victim has not settled for several epochs and holds a large userRewards balance, can an unprivileged caller sequence `getReward(address _account, address _receiver)` so that `rewards[_rewardToken].historicalRewards` and `IERC20(_rewardToken).balanceOf(address(this))` no longer reconcile, violating the invariant that only an authorised manager may decide when and by how much the reward index moves and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: donateRewards(uint256,address) is additionally exposed to any caller for already-registered reward tokens)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Precondition: the victim has not settled for several epochs and holds a large userRewards balance.
- Invariant to test: only an authorised manager may decide when and by how much the reward index moves; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the victim has not settled for several epochs and holds a large userRewards balance, have the attacker run `getReward(address _account, address _receiver)`, then assert the victim's claimable value and the `rewards[_rewardToken].historicalRewards` versus `IERC20(_rewardToken).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
