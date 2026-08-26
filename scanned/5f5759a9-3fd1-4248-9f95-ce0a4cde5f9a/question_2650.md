# Q2650: mWOMSVBaseRewarder.getReward - donateRewards(uint256,address) is additionally exposed to any caller for already-registered reward tokens

## Question
In rewards/mWOMSVBaseRewarder.sol, this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Can an unprivileged attacker reach this through `getReward(address _account, address _receiver)` while the computed forfeit lands just above the _amount / 1000 dust threshold, and drive `_calExpireForfeit(account,_amount)` out of agreement with `mWOMSV.getRewardablePercentWAD(account)` - breaking the invariant that only an authorised manager may decide when and by how much the reward index moves - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: donateRewards(uint256,address) is additionally exposed to any caller for already-registered reward tokens)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Precondition: the computed forfeit lands just above the _amount / 1000 dust threshold.
- Invariant to test: only an authorised manager may decide when and by how much the reward index moves; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `mWOMSV.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the computed forfeit lands just above the _amount / 1000 dust threshold, have the attacker run `getReward(address _account, address _receiver)`, then assert the victim's claimable value and the `_calExpireForfeit(account,_amount)` versus `mWOMSV.getRewardablePercentWAD(account)` relation are unchanged by the attacker's transaction.
