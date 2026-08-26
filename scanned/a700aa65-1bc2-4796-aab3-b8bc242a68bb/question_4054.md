# Q4054: BribeRewardPool.stakeFor - stake credited before the bribe for the epoch is queued

## Question
Note that in rewards/BribeRewardPool.sol, WombatBribeManager.voteAndCast calls vote(), which runs stakeFor, and then castVotes(), which queues the harvested bribes into rewardPerTokenStored, so balance created in the first half of the transaction earns on the bribe delivered in the second half. Can an attacker holding only tokens bought on market reach it via `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` under the stakingToken fixed at construction has different decimals from vlMGP and force `_balances[account]` apart from `totalSupply`, breaking the invariant that bribe share must be weighted by time committed before the bribe arrived for Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: stake credited before the bribe for the epoch is queued)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: WombatBribeManager.voteAndCast calls vote(), which runs stakeFor, and then castVotes(), which queues the harvested bribes into rewardPerTokenStored, so balance created in the first half of the transaction earns on the bribe delivered in the second half. Precondition: the stakingToken fixed at construction has different decimals from vlMGP.
- Invariant to test: bribe share must be weighted by time committed before the bribe arrived; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the stakingToken fixed at construction has different decimals from vlMGP, then assert `_balances[account]` and `totalSupply` end identical in both runs.
