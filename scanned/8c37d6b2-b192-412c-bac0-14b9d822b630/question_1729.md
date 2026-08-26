# Q1729: WomUp.stake - no reentrancy guard on any balance-mutating function

## Question
wombat/WomUp.sol: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. With _amount and the block, with the WOM immediately converted 1:1 into mWOM under attacker control and the target helper leaves a non-zero allowance after depositFor, can an unprivileged caller sequence `stake(uint256 _amount)` so that `_totalSupply` and `IERC20(mWom).balanceOf(address(this))` no longer reconcile, violating the invariant that every function that mutates the stake ledger must share one reentrancy domain and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `stake(uint256 _amount)` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block, with the WOM immediately converted 1:1 into mWOM
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: the target helper leaves a non-zero allowance after depositFor.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `_totalSupply` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the target helper leaves a non-zero allowance after depositFor, have the attacker run `stake(uint256 _amount)`, then assert the victim's claimable value and the `_totalSupply` versus `IERC20(mWom).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
