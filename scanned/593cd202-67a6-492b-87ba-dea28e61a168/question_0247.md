# Q0247: LogExpMath.ln - an out-of-range operand reverts and blocks the harvest path

## Question
libraries/LogExpMath.sol: these routines revert on operands outside their supported domain, and because SmartWomConvert sits inside WombatStaking._sendRewards, such a revert propagates to every deposit, depositLP and withdraw for the pool. With the operand range reached through the Wombat pricing that SmartWomConvert reads under attacker control and the attacker has pushed the wom/mWom pool far off peg in the same transaction, can an unprivileged caller sequence `ln(int256 a)` so that `the exponent operand` and `the bounds enforced before the call` no longer reconcile, violating the invariant that a pricing routine on the principal path must not be able to revert on attacker-reachable inputs and realising High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: libraries/LogExpMath.sol -> `ln(int256 a)` (mechanism: an out-of-range operand reverts and blocks the harvest path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `ln(int256 a)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the operand range reached through the Wombat pricing that SmartWomConvert reads
- Exploit idea: these routines revert on operands outside their supported domain, and because SmartWomConvert sits inside WombatStaking._sendRewards, such a revert propagates to every deposit, depositLP and withdraw for the pool. Precondition: the attacker has pushed the wom/mWom pool far off peg in the same transaction.
- Invariant to test: a pricing routine on the principal path must not be able to revert on attacker-reachable inputs; concretely, `the exponent operand` must stay reconciled with `the bounds enforced before the call`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Invariant/fuzz run over `ln(int256 a)`: constrain the setup so that the attacker has pushed the wom/mWom pool far off peg in the same transaction, fuzz the attacker inputs (the operand range reached through the Wombat pricing that SmartWomConvert reads), and assert after every call that a pricing routine on the principal path must not be able to revert on attacker-reachable inputs.
