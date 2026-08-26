# Q5254: AnkrBNBPoolHelper.withdraw - burnReceiptToken is the last step and is not atomic with the payout

## Question
In wombat/AnkrBNBPoolHelper.sol, withdraw() pays out, then unstakes, then calls IWombatStaking(wombatStaking).burnReceiptToken, so between the payout and the burn the receipt-token supply still claims backing that has already left. Starting from a state where the attacker deposits and withdraws through the helper inside one transaction, can an unprivileged EOA use `withdraw(uint256 _liquidity, uint256 _minAmount)` to leave `IERC20(stakingToken).totalSupply()` inconsistent with `the MasterWombat staked balance for pid`, violating the invariant that receipt supply must fall in the same step as the backing it represents and extracting Critical - Protocol insolvency?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: burnReceiptToken is the last step and is not atomic with the payout)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity, _minAmount and the ordering against the lockedAmount check
- Exploit idea: withdraw() pays out, then unstakes, then calls IWombatStaking(wombatStaking).burnReceiptToken, so between the payout and the burn the receipt-token supply still claims backing that has already left. Precondition: the attacker deposits and withdraws through the helper inside one transaction.
- Invariant to test: receipt supply must fall in the same step as the backing it represents; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker deposits and withdraws through the helper inside one transaction, call `withdraw(uint256 _liquidity, uint256 _minAmount)`, and assert `IERC20(stakingToken).totalSupply()` equals `the MasterWombat staked balance for pid` and that no account can withdraw more than it put in.
