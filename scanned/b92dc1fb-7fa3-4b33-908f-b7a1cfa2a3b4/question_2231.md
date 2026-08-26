# Q2231: WombatBribeManager.castVotesAndClaimBribes - offsetting deltas keep the net total unchanged

## Question
Note that in wombat/WombatBribeManager.sol, because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Can an attacker holding only tokens bought on market reach it via `castVotesAndClaimBribes(address[] lps, bool swapForBnb)` under the attacker locks vlMGP, votes and casts inside a single transaction and force `earnedRewards reported by claimAllBribes` apart from `the tokens actually transferred by getReward`, breaking the invariant that a rebalancing vote must be validated against the real per-pool positions it creates for Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotesAndClaimBribes(address[] lps, bool swapForBnb)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotesAndClaimBribes(address[] lps, bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the cast and the immediately following claim inside one transaction
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the attacker locks vlMGP, votes and casts inside a single transaction.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `earnedRewards reported by claimAllBribes` must stay reconciled with `the tokens actually transferred by getReward`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (the cast and the immediately following claim inside one transaction) under the attacker locks vlMGP, votes and casts inside a single transaction, asserting on every row that a rebalancing vote must be validated against the real per-pool positions it creates.
