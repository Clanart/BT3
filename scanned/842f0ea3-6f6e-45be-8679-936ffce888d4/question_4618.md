# Q4618: vlMGPBaseRewarder.getReward - queueMGP(uint256,address,address) is the MGP entry point used by MasterMagpie._sendMGPForVlMGPPool

## Question
In rewards/vlMGPBaseRewarder.sol, this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Can an unprivileged attacker reach this through `getReward(address _account, address _receiver)` while the victim has not settled for several epochs and holds a large userRewards balance, and drive `rewards[_rewardToken].historicalRewards` out of agreement with `IERC20(_rewardToken).balanceOf(address(this))` - breaking the invariant that only an authorised manager may decide when and by how much the reward index moves - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: queueMGP(uint256,address,address) is the MGP entry point used by MasterMagpie._sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Precondition: the victim has not settled for several epochs and holds a large userRewards balance.
- Invariant to test: only an authorised manager may decide when and by how much the reward index moves; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `getReward(address _account, address _receiver)` sequence atomically under the victim has not settled for several epochs and holds a large userRewards balance, asserting at the end that `rewards[_rewardToken].historicalRewards` still equals `IERC20(_rewardToken).balanceOf(address(this))` and the PoC's balance delta is non-positive.
