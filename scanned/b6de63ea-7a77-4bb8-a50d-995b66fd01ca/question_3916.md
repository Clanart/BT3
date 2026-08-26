# Q3916: WombatPoolHelperV2.depositNative - _minimumLiquidity is caller-supplied on the deposit leg

## Question
In wombat/WombatPoolHelperV2.sol, deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Starting from a state where the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, can an unprivileged EOA use `depositNative(uint256 _minimumLiquidity)` to leave `_liquidity burned via burnReceiptToken` inconsistent with `the deposit-token balance delta paid out by WombatStaking.withdraw`, violating the invariant that a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded and extracting Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: _minimumLiquidity is caller-supplied on the deposit leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Precondition: the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes.
- Invariant to test: a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, have the attacker run `depositNative(uint256 _minimumLiquidity)`, then assert the victim's claimable value and the `_liquidity burned via burnReceiptToken` versus `the deposit-token balance delta paid out by WombatStaking.withdraw` relation are unchanged by the attacker's transaction.
