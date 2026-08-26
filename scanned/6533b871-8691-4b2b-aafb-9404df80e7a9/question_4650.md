# Q4650: WombatBribeManager.castVotes - lastCastTime is written but never enforced

## Question
Note that in wombat/WombatBribeManager.sol, castVotes() sets lastCastTime = block.timestamp at the top and nothing anywhere reads it as a rate limit, so there is no minimum interval between casts and no protection against repeated casts inside one block. Can an attacker holding only tokens bought on market reach it via `castVotes(bool swapForBnb)` under delegatedPool is unset so the delegate legs are skipped and force `earnedRewards reported by claimAllBribes` apart from `the tokens actually transferred by getReward`, breaking the invariant that a recorded cadence variable must actually gate the operation it appears to pace for Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: lastCastTime is written but never enforced)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() sets lastCastTime = block.timestamp at the top and nothing anywhere reads it as a rate limit, so there is no minimum interval between casts and no protection against repeated casts inside one block. Precondition: delegatedPool is unset so the delegate legs are skipped.
- Invariant to test: a recorded cadence variable must actually gate the operation it appears to pace; concretely, `earnedRewards reported by claimAllBribes` must stay reconciled with `the tokens actually transferred by getReward`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Single-transaction PoC contract executing the whole `castVotes(bool swapForBnb)` sequence atomically under delegatedPool is unset so the delegate legs are skipped, asserting at the end that `earnedRewards reported by claimAllBribes` still equals `the tokens actually transferred by getReward` and the PoC's balance delta is non-positive.
