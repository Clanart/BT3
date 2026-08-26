# Q5939: WombatBribeManager.castVotesAndClaimBribes - offsetting deltas keep the net total unchanged

## Question
Consider wombat/WombatBribeManager.sol, where because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Assuming the attacker has just cancelled a cooldown so getUserVotable jumped upward, can an unprivileged attacker turn this into a divergence between `userTotalVotedInVlmgp[msg.sender]` and `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` via `castVotesAndClaimBribes(address[] lps, bool swapForBnb)`, breaking the invariant that a rebalancing vote must be validated against the real per-pool positions it creates and producing Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotesAndClaimBribes(address[] lps, bool swapForBnb)` (mechanism: offsetting deltas keep the net total unchanged)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotesAndClaimBribes(address[] lps, bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the cast and the immediately following claim inside one transaction
- Exploit idea: because the ceiling test uses only the accumulated net totalUserVote, an array that adds to one pool and removes from another leaves userTotalVotedInVlmgp untouched while both per-pool totals and both rewarder balances move. Precondition: the attacker has just cancelled a cooldown so getUserVotable jumped upward.
- Invariant to test: a rebalancing vote must be validated against the real per-pool positions it creates; concretely, `userTotalVotedInVlmgp[msg.sender]` must stay reconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Foundry fork test against the deployed pool: set up the attacker has just cancelled a cooldown so getUserVotable jumped upward, snapshot `userTotalVotedInVlmgp[msg.sender]` and `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`, run the attacker's `castVotesAndClaimBribes(address[] lps, bool swapForBnb)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
