# Q3820: WombatPoolHelperV2.depositLP - receipt-token delta credited to an attacker-chosen beneficiary

## Question
In wombat/WombatPoolHelperV2.sol, _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Does `depositLP(uint256 _lpAmount)` let an unprivileged caller exploit that under the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, so that `_liquidity burned via burnReceiptToken` diverges from `the deposit-token balance delta paid out by WombatStaking.withdraw`, the invariant that the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositLP(uint256 _lpAmount)` (mechanism: receipt-token delta credited to an attacker-chosen beneficiary)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Precondition: the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes.
- Invariant to test: the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `depositLP(uint256 _lpAmount)`: constrain the setup so that the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, fuzz the attacker inputs (_lpAmount), and assert after every call that the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution.
