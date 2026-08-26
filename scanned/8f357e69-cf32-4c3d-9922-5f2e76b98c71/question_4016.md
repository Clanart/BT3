# Q4016: mWOMSVBaseRewarder.updateFor - donateRewards(uint256,address) is additionally exposed to any caller for already-registered reward tokens

## Question
rewards/mWOMSVBaseRewarder.sol - this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Can an unprivileged attacker controlling the victim address and the block at which their index is pinned, under the attacker locks one block before a known large settlement and unlocks one block after, exploit this through `updateFor(address _account)` to break the reconciliation between `totalStaked()` and `IERC20(mWOMSV).totalSupply()` and the invariant that only an authorised manager may decide when and by how much the reward index moves, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: donateRewards(uint256,address) is additionally exposed to any caller for already-registered reward tokens)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Precondition: the attacker locks one block before a known large settlement and unlocks one block after.
- Invariant to test: only an authorised manager may decide when and by how much the reward index moves; concretely, `totalStaked()` must stay reconciled with `IERC20(mWOMSV).totalSupply()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker locks one block before a known large settlement and unlocks one block after, then assert `totalStaked()` and `IERC20(mWOMSV).totalSupply()` end identical in both runs.
