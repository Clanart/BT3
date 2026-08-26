# Q0154: LogExpMath.exp - an out-of-range operand reverts and blocks the harvest path

## Question
In libraries/LogExpMath.sol, these routines revert on operands outside their supported domain, and because SmartWomConvert sits inside WombatStaking._sendRewards, such a revert propagates to every deposit, depositLP and withdraw for the pool. Starting from a state where the attacker has pushed the wom/mWom pool far off peg in the same transaction, can an unprivileged EOA use `exp(int256 x)` to leave `maxSwapAmount() in SmartWomConvert` inconsistent with `IAsset cash and liability`, violating the invariant that a pricing routine on the principal path must not be able to revert on attacker-reachable inputs and extracting High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: libraries/LogExpMath.sol -> `exp(int256 x)` (mechanism: an out-of-range operand reverts and blocks the harvest path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `exp(int256 x)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the Wombat pool state consumed by SmartWomConvert.maxSwapAmount, which the attacker moves in the same transaction
- Exploit idea: these routines revert on operands outside their supported domain, and because SmartWomConvert sits inside WombatStaking._sendRewards, such a revert propagates to every deposit, depositLP and withdraw for the pool. Precondition: the attacker has pushed the wom/mWom pool far off peg in the same transaction.
- Invariant to test: a pricing routine on the principal path must not be able to revert on attacker-reachable inputs; concretely, `maxSwapAmount() in SmartWomConvert` must stay reconciled with `IAsset cash and liability`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Foundry fork test against the deployed pool: set up the attacker has pushed the wom/mWom pool far off peg in the same transaction, snapshot `maxSwapAmount() in SmartWomConvert` and `IAsset cash and liability`, run the attacker's `exp(int256 x)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
