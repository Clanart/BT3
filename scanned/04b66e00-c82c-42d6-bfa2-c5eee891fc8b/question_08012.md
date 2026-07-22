Q8012: stale callback context in router multicall dispatcher when an exact-output path recurses through a thin intermediate pool

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmSimpleRouter.sol::multicall` with `msg.value` plus WETH input/output paths with partial native balance already on the router while an exact-output path recurses through a thin intermediate pool, so that transient callback authority survives longer than the exact public swap step that created it along `multicall -> delegatecall self -> permit/payment/swap primitives`, corrupting call ordering, transient callback context, router-held balances, and any step that later sweeps or unwraps value? Because this uses delegatecall into the same storage and transient context, ordering bugs here can expose value across otherwise safe primitives. Trigger a revert or nested router path and then try to make a later public step inherit the stale pool/token/payer context.

Target
- File/function: metric-periphery/contracts/MetricOmmSimpleRouter.sol::multicall
- Entrypoint: metric-periphery/contracts/MetricOmmSimpleRouter.sol::multicall
- Attacker controls: `msg.value` plus WETH input/output paths with partial native balance already on the router
- Exploit idea: Reach `multicall -> delegatecall self -> permit/payment/swap primitives` in a live public flow and show that trigger a revert or nested router path and then try to make a later public step inherit the stale pool/token/payer context. The exact value at risk is call ordering, transient callback context, router-held balances, and any step that later sweeps or unwraps value.
- Invariant to test: Router callback state must be unique to one live swap step and must be cleared on every success and failure path. The concrete assertion should cover call ordering, transient callback context, router-held balances, and any step that later sweeps or unwraps value.
- Expected Immunefi impact: Critical direct loss if stale callback authority can charge or redirect another user's funds.
- Fast validation: Compose multi-step router transactions with intentional mid-flight reverts and assert no leftover token, ETH, or callback authority from one step can be claimed by the next.
