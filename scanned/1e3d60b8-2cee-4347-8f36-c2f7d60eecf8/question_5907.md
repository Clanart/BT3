# Q5907: WombatBribeManager.castVotes - delegatedPool harvestAll runs inside every cast

## Question
wombat/WombatBribeManager.sol: castVotes() ends with IDelegateVoteRewardPool(delegatedPool).harvestAll(), so any revert inside the delegate pool's reward handling blocks every cast and therefore the whole gauge from being updated. With the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination under attacker control and the attacker has just cancelled a cooldown so getUserVotable jumped upward, can an unprivileged caller sequence `castVotes(bool swapForBnb)` so that `delegatedPool votes` and `totalVlMgpInVote` no longer reconcile, violating the invariant that an optional delegate leg must not be able to block the core gauge update and realising High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: delegatedPool harvestAll runs inside every cast)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() ends with IDelegateVoteRewardPool(delegatedPool).harvestAll(), so any revert inside the delegate pool's reward handling blocks every cast and therefore the whole gauge from being updated. Precondition: the attacker has just cancelled a cooldown so getUserVotable jumped upward.
- Invariant to test: an optional delegate leg must not be able to block the core gauge update; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Invariant/fuzz run over `castVotes(bool swapForBnb)`: constrain the setup so that the attacker has just cancelled a cooldown so getUserVotable jumped upward, fuzz the attacker inputs (the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination), and assert after every call that an optional delegate leg must not be able to block the core gauge update.
