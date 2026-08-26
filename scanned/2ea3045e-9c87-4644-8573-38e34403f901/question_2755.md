# Q2755: WombatPoolHelperV2.depositLP - receipt-token delta credited to an attacker-chosen beneficiary

## Question
wombat/WombatPoolHelperV2.sol - _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Can an unprivileged attacker controlling _lpAmount, under the caller sets _minAmount to zero on the withdrawal leg, exploit this through `depositLP(uint256 _lpAmount)` to break the reconciliation between `pid cached at construction` and `pools[lpToken].pid in WombatStaking` and the invariant that the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositLP(uint256 _lpAmount)` (mechanism: receipt-token delta credited to an attacker-chosen beneficiary)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Precondition: the caller sets _minAmount to zero on the withdrawal leg.
- Invariant to test: the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_lpAmount) under the caller sets _minAmount to zero on the withdrawal leg, asserting on every row that the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution.
