# Q3510: mWOMSVBaseRewarder.getReward - unbounded rewardTokens array blocks the claim-all path

## Question
Consider rewards/mWOMSVBaseRewarder.sol, where queueNewRewards pushes new reward tokens with no removal path and getReward() iterates the whole array, so one reward token that begins reverting on transfer disables settlement of all the others for every user. Assuming the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, can an unprivileged attacker turn this into a divergence between `_calExpireForfeit(account,_amount)` and `mWOMSV.getRewardablePercentWAD(account)` via `getReward(address _account, address _receiver)`, breaking the invariant that a single misbehaving reward token must not block settlement of the remaining tokens and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: unbounded rewardTokens array blocks the claim-all path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: queueNewRewards pushes new reward tokens with no removal path and getReward() iterates the whole array, so one reward token that begins reverting on transfer disables settlement of all the others for every user. Precondition: the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them.
- Invariant to test: a single misbehaving reward token must not block settlement of the remaining tokens; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `mWOMSV.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `getReward(address _account, address _receiver)` sequence atomically under the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, asserting at the end that `_calExpireForfeit(account,_amount)` still equals `mWOMSV.getRewardablePercentWAD(account)` and the PoC's balance delta is non-positive.
