# Q2952: WomUp.stake - no reentrancy guard on any balance-mutating function

## Question
In wombat/WomUp.sol, stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Can an unprivileged attacker reach this through `stake(uint256 _amount)` while the attacker stakes one wei so _totalSupply is non-zero but every division truncates, and drive `rewardRate * duration` out of agreement with `IERC20(mgp).balanceOf(address(this))` - breaking the invariant that every function that mutates the stake ledger must share one reentrancy domain - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `stake(uint256 _amount)` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block, with the WOM immediately converted 1:1 into mWOM
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: the attacker stakes one wei so _totalSupply is non-zero but every division truncates.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `rewardRate * duration` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the attacker stakes one wei so _totalSupply is non-zero but every division truncates, snapshot `rewardRate * duration` and `IERC20(mgp).balanceOf(address(this))`, run the attacker's `stake(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
