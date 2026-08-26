# Q5447: WombatPoolHelperV2.depositFor - receipt-token delta credited to an attacker-chosen beneficiary

## Question
wombat/WombatPoolHelperV2.sol: _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Under the receipt token is minted to the helper while the credit is directed at a different address, is there an unprivileged sequence of `depositFor(uint256 _amount, address _for)` that leaves `pid cached at construction` unreconciled with `pools[lpToken].pid in WombatStaking`, violates the invariant that the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: receipt-token delta credited to an attacker-chosen beneficiary)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any address) and _amount, with _minimumLiquidity hardcoded to zero
- Exploit idea: _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Precondition: the receipt token is minted to the helper while the credit is directed at a different address.
- Invariant to test: the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the receipt token is minted to the helper while the credit is directed at a different address, then assert `pid cached at construction` and `pools[lpToken].pid in WombatStaking` end identical in both runs.
