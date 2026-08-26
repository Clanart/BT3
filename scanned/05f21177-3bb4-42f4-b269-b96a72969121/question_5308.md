# Q5308: WombatPoolHelper.deposit - receipt-token delta credited to an attacker-chosen beneficiary

## Question
In wombat/WombatPoolHelper.sol, _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Starting from a state where the attacker deposits and withdraws through the helper inside one transaction, can an unprivileged EOA use `deposit(uint256 _amount, uint256 _minimumLiquidity)` to leave `IERC20(stakingToken).totalSupply()` inconsistent with `the MasterWombat staked balance for pid`, violating the invariant that the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: receipt-token delta credited to an attacker-chosen beneficiary)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity, forwarded verbatim into the Wombat pool
- Exploit idea: _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Precondition: the attacker deposits and withdraws through the helper inside one transaction.
- Invariant to test: the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker deposits and withdraws through the helper inside one transaction, then assert `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid` end identical in both runs.
