# Q5220: WombatBribeManager.claimAllBribes - claimAllBribes settles any victim across every pool

## Question
wombat/WombatBribeManager.sol - claimAllBribes(address) is public with no caller check and walks the whole pools array settling the target, including the delegated pool leg, so a third party can force a full settlement of any voter. Can an unprivileged attacker controlling _for (any victim) and the block at which every pool rewarder is settled for them, under the attacker passes the same lp address several times in one array, exploit this through `claimAllBribes(address _for)` to break the reconciliation between `targetVote computed in castVotes` and `totalVotes() from veWom.balanceOf(wombatStaking)` and the invariant that a full settlement across every pool must be initiated by the position owner, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: claimAllBribes settles any victim across every pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: claimAllBribes(address) is public with no caller check and walks the whole pools array settling the target, including the delegated pool leg, so a third party can force a full settlement of any voter. Precondition: the attacker passes the same lp address several times in one array.
- Invariant to test: a full settlement across every pool must be initiated by the position owner; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker passes the same lp address several times in one array, snapshot `targetVote computed in castVotes` and `totalVotes() from veWom.balanceOf(wombatStaking)`, run the attacker's `claimAllBribes(address _for)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
