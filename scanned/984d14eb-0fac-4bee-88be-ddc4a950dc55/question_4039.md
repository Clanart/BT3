# Q4039: BribeRewardPool.stakeFor - share ledger tracks votes rather than transferred value

## Question
Note that in rewards/BribeRewardPool.sol, stakeFor() and withdrawFor() move totalSupply and _balances purely from vote deltas with no token ever changing hands, so a voter can create and destroy bribe share at will inside one transaction. Can an attacker holding only tokens bought on market reach it via `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` under the stakingToken fixed at construction has different decimals from vlMGP and force `rewards[_rewardToken].queuedRewards` apart from `totalSupply at the moment of the flush`, breaking the invariant that a reward-share ledger must be backed by value that is actually committed and costly to move for Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: share ledger tracks votes rather than transferred value)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: stakeFor() and withdrawFor() move totalSupply and _balances purely from vote deltas with no token ever changing hands, so a voter can create and destroy bribe share at will inside one transaction. Precondition: the stakingToken fixed at construction has different decimals from vlMGP.
- Invariant to test: a reward-share ledger must be backed by value that is actually committed and costly to move; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the stakingToken fixed at construction has different decimals from vlMGP, call `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`, and assert `rewards[_rewardToken].queuedRewards` equals `totalSupply at the moment of the flush` and that no account can withdraw more than it put in.
