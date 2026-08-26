# Q2200: mWOMSVBaseRewarder.updateFor - donateRewards(uint256,address) is additionally exposed to any caller for already-registered reward tokens

## Question
rewards/mWOMSVBaseRewarder.sol - this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Can an unprivileged attacker controlling the victim address and the block at which their index is pinned, under the computed forfeit lands just above the _amount / 1000 dust threshold, exploit this through `updateFor(address _account)` to break the reconciliation between `forfeitAmount` and `rewardInfo.rewardPerTokenStored` and the invariant that only an authorised manager may decide when and by how much the reward index moves, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: donateRewards(uint256,address) is additionally exposed to any caller for already-registered reward tokens)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Precondition: the computed forfeit lands just above the _amount / 1000 dust threshold.
- Invariant to test: only an authorised manager may decide when and by how much the reward index moves; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the computed forfeit lands just above the _amount / 1000 dust threshold, then assert `forfeitAmount` and `rewardInfo.rewardPerTokenStored` end identical in both runs.
