# Q1814: WombatPoolHelperV2.deposit - receipt-token delta credited to an attacker-chosen beneficiary

## Question
In wombat/WombatPoolHelperV2.sol, _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Does `deposit(uint256 _amount, uint256 _minimumLiquidity)` let an unprivileged caller exploit that under the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, so that `this.balance(msg.sender)` diverges from `lockedAmount[msg.sender]`, the invariant that the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: receipt-token delta credited to an attacker-chosen beneficiary)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity
- Exploit idea: _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Precondition: the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction.
- Invariant to test: the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `deposit(uint256 _amount, uint256 _minimumLiquidity)`: constrain the setup so that the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, fuzz the attacker inputs (_amount and _minimumLiquidity), and assert after every call that the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution.
