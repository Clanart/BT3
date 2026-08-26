# Q5537: WombatPoolHelperV2.withdraw - burnReceiptToken is the last step and is not atomic with the payout

## Question
wombat/WombatPoolHelperV2.sol: withdraw() pays out, then unstakes, then calls IWombatStaking(wombatStaking).burnReceiptToken, so between the payout and the burn the receipt-token supply still claims backing that has already left. Under the receipt token is minted to the helper while the credit is directed at a different address, is there an unprivileged sequence of `withdraw(uint256 _liquidity, uint256 _minAmount)` that leaves `pid cached at construction` unreconciled with `pools[lpToken].pid in WombatStaking`, violates the invariant that receipt supply must fall in the same step as the backing it represents, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: burnReceiptToken is the last step and is not atomic with the payout)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount
- Exploit idea: withdraw() pays out, then unstakes, then calls IWombatStaking(wombatStaking).burnReceiptToken, so between the payout and the burn the receipt-token supply still claims backing that has already left. Precondition: the receipt token is minted to the helper while the credit is directed at a different address.
- Invariant to test: receipt supply must fall in the same step as the backing it represents; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the receipt token is minted to the helper while the credit is directed at a different address, have the attacker run `withdraw(uint256 _liquidity, uint256 _minAmount)`, then assert the victim's claimable value and the `pid cached at construction` versus `pools[lpToken].pid in WombatStaking` relation are unchanged by the attacker's transaction.
