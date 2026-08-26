# Q4758: WombatBribeManager.harvestSinglePool - harvestSinglePool accepts unregistered lp addresses

## Question
Note that in wombat/WombatBribeManager.sol, harvestSinglePool() reads poolInfos[lp].rewarder for every caller-supplied address with no membership check and forwards the whole array into WombatStaking.vote, so unregistered entries carry a zero rewarder into the queue step. Can an attacker holding only tokens bought on market reach it via `harvestSinglePool(address[] _lps)` under delegatedPool is unset so the delegate legs are skipped and force `targetVote computed in castVotes` apart from `totalVotes() from veWom.balanceOf(wombatStaking)`, breaking the invariant that only registered pools may be forwarded into the voter and the reward queue for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `harvestSinglePool(address[] _lps)` (mechanism: harvestSinglePool accepts unregistered lp addresses)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestSinglePool(address[] _lps)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the lp array, forwarded straight into WombatStaking.vote with zero deltas and caller set to address(0)
- Exploit idea: harvestSinglePool() reads poolInfos[lp].rewarder for every caller-supplied address with no membership check and forwards the whole array into WombatStaking.vote, so unregistered entries carry a zero rewarder into the queue step. Precondition: delegatedPool is unset so the delegate legs are skipped.
- Invariant to test: only registered pools may be forwarded into the voter and the reward queue; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange delegatedPool is unset so the delegate legs are skipped, call `harvestSinglePool(address[] _lps)`, and assert `targetVote computed in castVotes` equals `totalVotes() from veWom.balanceOf(wombatStaking)` and that no account can withdraw more than it put in.
