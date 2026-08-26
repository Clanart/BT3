# Q4489: WombatBribeManager.vote - harvestSinglePool drains pending bribes with no caller fee

## Question
wombat/WombatBribeManager.sol - harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Can an unprivileged attacker controlling every lp address and every signed delta, including duplicates and offsetting positive and negative entries, under delegatedPool is unset so the delegate legs are skipped, exploit this through `vote(address[] _lps, int256[] _deltas)` to break the reconciliation between `earnedRewards reported by claimAllBribes` and `the tokens actually transferred by getReward` and the invariant that a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: harvestSinglePool drains pending bribes with no caller fee)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Precondition: delegatedPool is unset so the delegate legs are skipped.
- Invariant to test: a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge; concretely, `earnedRewards reported by claimAllBribes` must stay reconciled with `the tokens actually transferred by getReward`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `vote(address[] _lps, int256[] _deltas)` sequence atomically under delegatedPool is unset so the delegate legs are skipped, asserting at the end that `earnedRewards reported by claimAllBribes` still equals `the tokens actually transferred by getReward` and the PoC's balance delta is non-positive.
