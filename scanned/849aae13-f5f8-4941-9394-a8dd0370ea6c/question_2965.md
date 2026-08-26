# Q2965: WombatPoolHelperV2.withdraw - burnReceiptToken is the last step and is not atomic with the payout

## Question
In wombat/WombatPoolHelperV2.sol, withdraw() pays out, then unstakes, then calls IWombatStaking(wombatStaking).burnReceiptToken, so between the payout and the burn the receipt-token supply still claims backing that has already left. Starting from a state where the caller sets _minAmount to zero on the withdrawal leg, can an unprivileged EOA use `withdraw(uint256 _liquidity, uint256 _minAmount)` to leave `_minimumLiquidity supplied by the caller` inconsistent with `the LP actually minted by the Wombat pool`, violating the invariant that receipt supply must fall in the same step as the backing it represents and extracting Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: burnReceiptToken is the last step and is not atomic with the payout)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount
- Exploit idea: withdraw() pays out, then unstakes, then calls IWombatStaking(wombatStaking).burnReceiptToken, so between the payout and the burn the receipt-token supply still claims backing that has already left. Precondition: the caller sets _minAmount to zero on the withdrawal leg.
- Invariant to test: receipt supply must fall in the same step as the backing it represents; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `withdraw(uint256 _liquidity, uint256 _minAmount)`: constrain the setup so that the caller sets _minAmount to zero on the withdrawal leg, fuzz the attacker inputs (_liquidity and _minAmount), and assert after every call that receipt supply must fall in the same step as the backing it represents.
