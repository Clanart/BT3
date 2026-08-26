# Q2864: vlMGPBaseRewarder.getRewards - attacker-chosen reward-token list reaches getRewards

## Question
rewards/vlMGPBaseRewarder.sol: MasterMagpie._multiClaim forwards the caller's _rewardTokens[i] verbatim, so the attacker selects which tokens are settled and which are left accruing while the MGP-side rewardDebt is advanced regardless. Under a large MGP distribution has just been queued and no account has settled yet, is there an unprivileged sequence of `getRewards(address _account, address _receiver, address[] _rewardTokens)` that leaves `_calExpireForfeit(account,_amount)` unreconciled with `vlMGP.getRewardablePercentWAD(account)`, violates the invariant that the set of tokens a claimer names must not change the total value they are entitled to, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: attacker-chosen reward-token list reaches getRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: MasterMagpie._multiClaim forwards the caller's _rewardTokens[i] verbatim, so the attacker selects which tokens are settled and which are left accruing while the MGP-side rewardDebt is advanced regardless. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: the set of tokens a claimer names must not change the total value they are entitled to; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `vlMGP.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `getRewards(address _account, address _receiver, address[] _rewardTokens)` sequence atomically under a large MGP distribution has just been queued and no account has settled yet, asserting at the end that `_calExpireForfeit(account,_amount)` still equals `vlMGP.getRewardablePercentWAD(account)` and the PoC's balance delta is non-positive.
