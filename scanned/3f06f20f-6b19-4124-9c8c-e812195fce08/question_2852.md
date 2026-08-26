# Q2852: AnkrBNBPoolHelper.withdraw - burnReceiptToken is the last step and is not atomic with the payout

## Question
wombat/AnkrBNBPoolHelper.sol: withdraw() pays out, then unstakes, then calls IWombatStaking(wombatStaking).burnReceiptToken, so between the payout and the burn the receipt-token supply still claims backing that has already left. With _liquidity, _minAmount and the ordering against the lockedAmount check under attacker control and the caller sets _minAmount to zero on the withdrawal leg, can an unprivileged caller sequence `withdraw(uint256 _liquidity, uint256 _minAmount)` so that `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid` no longer reconcile, violating the invariant that receipt supply must fall in the same step as the backing it represents and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: burnReceiptToken is the last step and is not atomic with the payout)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity, _minAmount and the ordering against the lockedAmount check
- Exploit idea: withdraw() pays out, then unstakes, then calls IWombatStaking(wombatStaking).burnReceiptToken, so between the payout and the burn the receipt-token supply still claims backing that has already left. Precondition: the caller sets _minAmount to zero on the withdrawal leg.
- Invariant to test: receipt supply must fall in the same step as the backing it represents; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the caller sets _minAmount to zero on the withdrawal leg, call `withdraw(uint256 _liquidity, uint256 _minAmount)`, and assert `IERC20(stakingToken).totalSupply()` equals `the MasterWombat staked balance for pid` and that no account can withdraw more than it put in.
