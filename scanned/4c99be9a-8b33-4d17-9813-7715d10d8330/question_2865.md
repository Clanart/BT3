# Q2865: mWOMSVBaseRewarder.getRewards - attacker-chosen reward-token list reaches getRewards

## Question
In rewards/mWOMSVBaseRewarder.sol, MasterMagpie._multiClaim forwards the caller's _rewardTokens[i] verbatim, so the attacker selects which tokens are settled and which are left accruing while the MGP-side rewardDebt is advanced regardless. Can an unprivileged attacker reach this through `getRewards(address _account, address _receiver, address[] _rewardTokens)` while a large MGP distribution has just been queued and no account has settled yet, and drive `_calExpireForfeit(account,_amount)` out of agreement with `mWOMSV.getRewardablePercentWAD(account)` - breaking the invariant that the set of tokens a claimer names must not change the total value they are entitled to - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: attacker-chosen reward-token list reaches getRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: MasterMagpie._multiClaim forwards the caller's _rewardTokens[i] verbatim, so the attacker selects which tokens are settled and which are left accruing while the MGP-side rewardDebt is advanced regardless. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: the set of tokens a claimer names must not change the total value they are entitled to; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `mWOMSV.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish a large MGP distribution has just been queued and no account has settled yet, have the attacker run `getRewards(address _account, address _receiver, address[] _rewardTokens)`, then assert the victim's claimable value and the `_calExpireForfeit(account,_amount)` versus `mWOMSV.getRewardablePercentWAD(account)` relation are unchanged by the attacker's transaction.
