# Q3509: vlMGPBaseRewarder.getReward - unbounded rewardTokens array blocks the claim-all path

## Question
rewards/vlMGPBaseRewarder.sol: queueNewRewards pushes new reward tokens with no removal path and getReward() iterates the whole array, so one reward token that begins reverting on transfer disables settlement of all the others for every user. With the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path under attacker control and the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, can an unprivileged caller sequence `getReward(address _account, address _receiver)` so that `_calExpireForfeit(account,_amount)` and `vlMGP.getRewardablePercentWAD(account)` no longer reconcile, violating the invariant that a single misbehaving reward token must not block settlement of the remaining tokens and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: unbounded rewardTokens array blocks the claim-all path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: queueNewRewards pushes new reward tokens with no removal path and getReward() iterates the whole array, so one reward token that begins reverting on transfer disables settlement of all the others for every user. Precondition: the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them.
- Invariant to test: a single misbehaving reward token must not block settlement of the remaining tokens; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `vlMGP.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, then assert `_calExpireForfeit(account,_amount)` and `vlMGP.getRewardablePercentWAD(account)` end identical in both runs.
