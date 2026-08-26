# Q4910: vlMGPBaseRewarder.getReward - queueMGP(uint256,address,address) is the MGP entry point used by MasterMagpie._sendMGPForVlMGPPool

## Question
rewards/vlMGPBaseRewarder.sol - this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Can an unprivileged attacker controlling the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path, under a registered reward token has begun reverting on transfer, exploit this through `getReward(address _account, address _receiver)` to break the reconciliation between `_calExpireForfeit(account,_amount)` and `vlMGP.getRewardablePercentWAD(account)` and the invariant that only an authorised manager may decide when and by how much the reward index moves, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: queueMGP(uint256,address,address) is the MGP entry point used by MasterMagpie._sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Precondition: a registered reward token has begun reverting on transfer.
- Invariant to test: only an authorised manager may decide when and by how much the reward index moves; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `vlMGP.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a registered reward token has begun reverting on transfer, then assert `_calExpireForfeit(account,_amount)` and `vlMGP.getRewardablePercentWAD(account)` end identical in both runs.
