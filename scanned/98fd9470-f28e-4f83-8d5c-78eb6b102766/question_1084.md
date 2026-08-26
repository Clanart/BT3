# Q1084: LogExpMath.ln - an out-of-range operand reverts and blocks the harvest path

## Question
Note that in libraries/LogExpMath.sol, these routines revert on operands outside their supported domain, and because SmartWomConvert sits inside WombatStaking._sendRewards, such a revert propagates to every deposit, depositLP and withdraw for the pool. Can an attacker holding only tokens bought on market reach it via `ln(int256 a)` under the attacker routes many small conversions rather than one large one and force `the exponent operand` apart from `the bounds enforced before the call`, breaking the invariant that a pricing routine on the principal path must not be able to revert on attacker-reachable inputs for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: libraries/LogExpMath.sol -> `ln(int256 a)` (mechanism: an out-of-range operand reverts and blocks the harvest path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `ln(int256 a)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the operand range reached through the Wombat pricing that SmartWomConvert reads
- Exploit idea: these routines revert on operands outside their supported domain, and because SmartWomConvert sits inside WombatStaking._sendRewards, such a revert propagates to every deposit, depositLP and withdraw for the pool. Precondition: the attacker routes many small conversions rather than one large one.
- Invariant to test: a pricing routine on the principal path must not be able to revert on attacker-reachable inputs; concretely, `the exponent operand` must stay reconciled with `the bounds enforced before the call`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Invariant/fuzz run over `ln(int256 a)`: constrain the setup so that the attacker routes many small conversions rather than one large one, fuzz the attacker inputs (the operand range reached through the Wombat pricing that SmartWomConvert reads), and assert after every call that a pricing routine on the principal path must not be able to revert on attacker-reachable inputs.
