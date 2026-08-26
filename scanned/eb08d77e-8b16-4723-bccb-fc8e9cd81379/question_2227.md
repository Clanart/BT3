# Q2227: WombatPoolHelper.depositNative - receipt-token delta credited to an attacker-chosen beneficiary

## Question
wombat/WombatPoolHelper.sol - _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Can an unprivileged attacker controlling msg.value and _minimumLiquidity, under the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, exploit this through `depositNative(uint256 _minimumLiquidity)` to break the reconciliation between `_minimumLiquidity supplied by the caller` and `the LP actually minted by the Wombat pool` and the invariant that the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: receipt-token delta credited to an attacker-chosen beneficiary)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Precondition: the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction.
- Invariant to test: the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `depositNative(uint256 _minimumLiquidity)`: constrain the setup so that the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, fuzz the attacker inputs (msg.value and _minimumLiquidity), and assert after every call that the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution.
