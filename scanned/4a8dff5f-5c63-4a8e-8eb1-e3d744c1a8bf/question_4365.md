# Q4365: mWOMSVBaseRewarder.updateFor - donateRewards(uint256,address) is additionally exposed to any caller for already-registered reward tokens

## Question
rewards/mWOMSVBaseRewarder.sol: this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. With the victim address and the block at which their index is pinned under attacker control and the victim has not settled for several epochs and holds a large userRewards balance, can an unprivileged caller sequence `updateFor(address _account)` so that `balanceOf(account)` and `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked` no longer reconcile, violating the invariant that only an authorised manager may decide when and by how much the reward index moves and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: donateRewards(uint256,address) is additionally exposed to any caller for already-registered reward tokens)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Precondition: the victim has not settled for several epochs and holds a large userRewards balance.
- Invariant to test: only an authorised manager may decide when and by how much the reward index moves; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the victim has not settled for several epochs and holds a large userRewards balance, then assert `balanceOf(account)` and `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked` end identical in both runs.
