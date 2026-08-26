# Q0086: WomUp.stake - no reentrancy guard on any balance-mutating function

## Question
Note that in wombat/WomUp.sol, stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Can an attacker holding only tokens bought on market reach it via `stake(uint256 _amount)` under the attacker is the only staker for a single block and force `rewards[account]` apart from `IERC20(mgp).balanceOf(address(this))`, breaking the invariant that every function that mutates the stake ledger must share one reentrancy domain for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `stake(uint256 _amount)` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block, with the WOM immediately converted 1:1 into mWOM
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: the attacker is the only staker for a single block.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `rewards[account]` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker is the only staker for a single block, then assert `rewards[account]` and `IERC20(mgp).balanceOf(address(this))` end identical in both runs.
