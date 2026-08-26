# Q3220: vlMGPBaseRewarder.getRewards - forfeit erased by settling during cooldown

## Question
rewards/vlMGPBaseRewarder.sol - _calExpireForfeit() multiplies the pending amount by vlMGP.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Can an unprivileged attacker controlling the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor, under the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, exploit this through `getRewards(address _account, address _receiver, address[] _rewardTokens)` to break the reconciliation between `_calExpireForfeit(account,_amount)` and `vlMGP.getRewardablePercentWAD(account)` and the invariant that the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: forfeit erased by settling during cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _calExpireForfeit() multiplies the pending amount by vlMGP.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Precondition: the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them.
- Invariant to test: the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `vlMGP.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `getRewards(address _account, address _receiver, address[] _rewardTokens)` sequence atomically under the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, asserting at the end that `_calExpireForfeit(account,_amount)` still equals `vlMGP.getRewardablePercentWAD(account)` and the PoC's balance delta is non-positive.
