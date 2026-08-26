# Q3204: mWOMSVBaseRewarder.updateFor - donateRewards(uint256,address) is additionally exposed to any caller for already-registered reward tokens

## Question
In rewards/mWOMSVBaseRewarder.sol, this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Can an unprivileged attacker reach this through `updateFor(address _account)` while the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, and drive `_calExpireForfeit(account,_amount)` out of agreement with `mWOMSV.getRewardablePercentWAD(account)` - breaking the invariant that only an authorised manager may decide when and by how much the reward index moves - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: donateRewards(uint256,address) is additionally exposed to any caller for already-registered reward tokens)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Precondition: the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them.
- Invariant to test: only an authorised manager may decide when and by how much the reward index moves; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `mWOMSV.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, call `updateFor(address _account)`, and assert `_calExpireForfeit(account,_amount)` equals `mWOMSV.getRewardablePercentWAD(account)` and that no account can withdraw more than it put in.
