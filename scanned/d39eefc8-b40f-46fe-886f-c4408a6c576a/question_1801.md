# Q1801: WomUp.withdraw - withdraw draws from a shared mWOM balance with no reservation

## Question
Consider wombat/WomUp.sol, where withdraw() reduces _balances and _totalSupply and then transfers mWOM out of whatever the contract holds, with no check that the remaining balance still covers the remaining _totalSupply. Assuming the target helper leaves a non-zero allowance after depositFor, can an unprivileged attacker turn this into a divergence between `_balances[account]` and `_totalSupply` via `withdraw(uint256 amount, bool claim)`, breaking the invariant that the contract must always hold at least _totalSupply of the redemption asset and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: withdraw draws from a shared mWOM balance with no reservation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: withdraw() reduces _balances and _totalSupply and then transfers mWOM out of whatever the contract holds, with no check that the remaining balance still covers the remaining _totalSupply. Precondition: the target helper leaves a non-zero allowance after depositFor.
- Invariant to test: the contract must always hold at least _totalSupply of the redemption asset; concretely, `_balances[account]` must stay reconciled with `_totalSupply`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (amount and whether the claim leg runs in the same call) under the target helper leaves a non-zero allowance after depositFor, asserting on every row that the contract must always hold at least _totalSupply of the redemption asset.
