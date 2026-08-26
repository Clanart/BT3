# Q2261: WomUp.getReward - getReward approves vlMGP without resetting

## Question
In wombat/WomUp.sol, getReward() calls IERC20(mgp).safeApprove(address(vlMGP), reward) with no reset, so any lockFor that under-consumes bricks reward claiming for every participant at once. Starting from a state where the attacker migrates and withdraws inside one transaction, can an unprivileged EOA use `getReward()` to leave `_balances[account]` inconsistent with `_totalSupply`, violating the invariant that the reward claim path must remain usable regardless of allowance residue and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WomUp.sol -> `getReward()` (mechanism: getReward approves vlMGP without resetting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which accrued MGP is locked into vlMGP for the caller
- Exploit idea: getReward() calls IERC20(mgp).safeApprove(address(vlMGP), reward) with no reset, so any lockFor that under-consumes bricks reward claiming for every participant at once. Precondition: the attacker migrates and withdraws inside one transaction.
- Invariant to test: the reward claim path must remain usable regardless of allowance residue; concretely, `_balances[account]` must stay reconciled with `_totalSupply`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker migrates and withdraws inside one transaction, call `getReward()`, and assert `_balances[account]` equals `_totalSupply` and that no account can withdraw more than it put in.
