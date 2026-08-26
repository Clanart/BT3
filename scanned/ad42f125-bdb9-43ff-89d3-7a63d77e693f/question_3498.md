# Q3498: WombatPoolHelperV2.withdraw - burnReceiptToken is the last step and is not atomic with the payout

## Question
In wombat/WombatPoolHelperV2.sol, withdraw() pays out, then unstakes, then calls IWombatStaking(wombatStaking).burnReceiptToken, so between the payout and the burn the receipt-token supply still claims backing that has already left. Can an unprivileged attacker reach this through `withdraw(uint256 _liquidity, uint256 _minAmount)` while a residual stakingToken balance from an earlier rounding sits on the helper, and drive `pid cached at construction` out of agreement with `pools[lpToken].pid in WombatStaking` - breaking the invariant that receipt supply must fall in the same step as the backing it represents - for Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: burnReceiptToken is the last step and is not atomic with the payout)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount
- Exploit idea: withdraw() pays out, then unstakes, then calls IWombatStaking(wombatStaking).burnReceiptToken, so between the payout and the burn the receipt-token supply still claims backing that has already left. Precondition: a residual stakingToken balance from an earlier rounding sits on the helper.
- Invariant to test: receipt supply must fall in the same step as the backing it represents; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `withdraw(uint256 _liquidity, uint256 _minAmount)` sequence atomically under a residual stakingToken balance from an earlier rounding sits on the helper, asserting at the end that `pid cached at construction` still equals `pools[lpToken].pid in WombatStaking` and the PoC's balance delta is non-positive.
