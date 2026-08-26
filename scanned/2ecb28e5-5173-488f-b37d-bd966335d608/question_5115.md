# Q5115: WombatBribeManager.castVotes - delegatedPool harvestAll runs inside every cast

## Question
wombat/WombatBribeManager.sol: castVotes() ends with IDelegateVoteRewardPool(delegatedPool).harvestAll(), so any revert inside the delegate pool's reward handling blocks every cast and therefore the whole gauge from being updated. With the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination under attacker control and the attacker passes the same lp address several times in one array, can an unprivileged caller sequence `castVotes(bool swapForBnb)` so that `totalVlMgpInVote` and `sum of userTotalVotedInVlmgp over all voters` no longer reconcile, violating the invariant that an optional delegate leg must not be able to block the core gauge update and realising High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: delegatedPool harvestAll runs inside every cast)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() ends with IDelegateVoteRewardPool(delegatedPool).harvestAll(), so any revert inside the delegate pool's reward handling blocks every cast and therefore the whole gauge from being updated. Precondition: the attacker passes the same lp address several times in one array.
- Invariant to test: an optional delegate leg must not be able to block the core gauge update; concretely, `totalVlMgpInVote` must stay reconciled with `sum of userTotalVotedInVlmgp over all voters`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker passes the same lp address several times in one array, then assert `totalVlMgpInVote` and `sum of userTotalVotedInVlmgp over all voters` end identical in both runs.
