# Q0954: WomUp.stake - no reentrancy guard on any balance-mutating function

## Question
Consider wombat/WomUp.sol, where stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Assuming _totalSupply exceeds the mWOM balance the contract actually holds, can an unprivileged attacker turn this into a divergence between `rewardRate * duration` and `IERC20(mgp).balanceOf(address(this))` via `stake(uint256 _amount)`, breaking the invariant that every function that mutates the stake ledger must share one reentrancy domain and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `stake(uint256 _amount)` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block, with the WOM immediately converted 1:1 into mWOM
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: _totalSupply exceeds the mWOM balance the contract actually holds.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `rewardRate * duration` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish _totalSupply exceeds the mWOM balance the contract actually holds, have the attacker run `stake(uint256 _amount)`, then assert the victim's claimable value and the `rewardRate * duration` versus `IERC20(mgp).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
