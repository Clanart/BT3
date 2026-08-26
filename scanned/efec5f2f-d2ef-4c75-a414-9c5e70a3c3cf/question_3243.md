# Q3243: WombatPoolHelperV2.depositFor - receipt-token delta credited to an attacker-chosen beneficiary

## Question
In wombat/WombatPoolHelperV2.sol, _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Starting from a state where a residual stakingToken balance from an earlier rounding sits on the helper, can an unprivileged EOA use `depositFor(uint256 _amount, address _for)` to leave `pid cached at construction` inconsistent with `pools[lpToken].pid in WombatStaking`, violating the invariant that the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: receipt-token delta credited to an attacker-chosen beneficiary)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any address) and _amount, with _minimumLiquidity hardcoded to zero
- Exploit idea: _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Precondition: a residual stakingToken balance from an earlier rounding sits on the helper.
- Invariant to test: the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish a residual stakingToken balance from an earlier rounding sits on the helper, have the attacker run `depositFor(uint256 _amount, address _for)`, then assert the victim's claimable value and the `pid cached at construction` versus `pools[lpToken].pid in WombatStaking` relation are unchanged by the attacker's transaction.
