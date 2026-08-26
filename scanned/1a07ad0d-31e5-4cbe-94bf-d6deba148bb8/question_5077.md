# Q5077: WombatPoolHelper.deposit - receipt-token delta credited to an attacker-chosen beneficiary

## Question
wombat/WombatPoolHelper.sol - _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Can an unprivileged attacker controlling _amount and _minimumLiquidity, forwarded verbatim into the Wombat pool, under the attacker has moved the wom/mWom Wombat pool immediately before calling, exploit this through `deposit(uint256 _amount, uint256 _minimumLiquidity)` to break the reconciliation between `this.balance(msg.sender)` and `lockedAmount[msg.sender]` and the invariant that the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: receipt-token delta credited to an attacker-chosen beneficiary)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity, forwarded verbatim into the Wombat pool
- Exploit idea: _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Precondition: the attacker has moved the wom/mWom Wombat pool immediately before calling.
- Invariant to test: the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `deposit(uint256 _amount, uint256 _minimumLiquidity)`: constrain the setup so that the attacker has moved the wom/mWom Wombat pool immediately before calling, fuzz the attacker inputs (_amount and _minimumLiquidity, forwarded verbatim into the Wombat pool), and assert after every call that the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution.
