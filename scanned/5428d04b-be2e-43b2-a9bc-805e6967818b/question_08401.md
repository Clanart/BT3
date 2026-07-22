Q8401: stale callback context in single-hop exact input when the first hop pays from the external caller while later hops pay from router-held balances

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInputSingle` with `multicall` ordering that mixes permit, swap, unwrap, sweep, and refund steps while the first hop pays from the external caller while later hops pay from router-held balances, so that transient callback authority survives longer than the exact public swap step that created it along `exactInputSingle -> set callback context -> pool.swap -> metricOmmSwapCallback -> clear callback context`, corrupting payer identity, token-to-pay, output minimum enforcement, and clearing of transient callback state? This is the simplest public swap surface, so any stale-context or wrong-token payment bug here will be easy to weaponize repeatedly. Trigger a revert or nested router path and then try to make a later public step inherit the stale pool/token/payer context.

Target
- File/function: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInputSingle
- Entrypoint: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInputSingle
- Attacker controls: `multicall` ordering that mixes permit, swap, unwrap, sweep, and refund steps
- Exploit idea: Reach `exactInputSingle -> set callback context -> pool.swap -> metricOmmSwapCallback -> clear callback context` in a live public flow and show that trigger a revert or nested router path and then try to make a later public step inherit the stale pool/token/payer context. The exact value at risk is payer identity, token-to-pay, output minimum enforcement, and clearing of transient callback state.
- Invariant to test: Router callback state must be unique to one live swap step and must be cleared on every success and failure path. The concrete assertion should cover payer identity, token-to-pay, output minimum enforcement, and clearing of transient callback state.
- Expected Immunefi impact: Critical direct loss if stale callback authority can charge or redirect another user's funds.
- Fast validation: Assert the token paid in callback, the output received, and the cleared callback context always match the single-hop params after both success and revert paths.
