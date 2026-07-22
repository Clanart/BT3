Q8480: path-direction mismatch in single-hop exact input when a quoter result is consumed after a small state-moving transaction but before the user notices

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInputSingle` with quote or depth reads taken immediately before the user executes the live trade while a quoter result is consumed after a small state-moving transaction but before the user notices, so that the public path, bitmap, and hop token assumptions stop matching the pool actually called along `exactInputSingle -> set callback context -> pool.swap -> metricOmmSwapCallback -> clear callback context`, corrupting payer identity, token-to-pay, output minimum enforcement, and clearing of transient callback state? This is the simplest public swap surface, so any stale-context or wrong-token payment bug here will be easy to weaponize repeatedly. Use a valid-looking path whose repeated token or repeated pool shape stresses the hop-direction derivation and payer updates.

Target
- File/function: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInputSingle
- Entrypoint: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInputSingle
- Attacker controls: quote or depth reads taken immediately before the user executes the live trade
- Exploit idea: Reach `exactInputSingle -> set callback context -> pool.swap -> metricOmmSwapCallback -> clear callback context` in a live public flow and show that use a valid-looking path whose repeated token or repeated pool shape stresses the hop-direction derivation and payer updates. The exact value at risk is payer identity, token-to-pay, output minimum enforcement, and clearing of transient callback state.
- Invariant to test: Each hop must consume the exact token and direction implied by the user-supplied path and bitmap. The concrete assertion should cover payer identity, token-to-pay, output minimum enforcement, and clearing of transient callback state.
- Expected Immunefi impact: High direct user loss through settlement against the wrong pool or wrong token leg.
- Fast validation: Assert the token paid in callback, the output received, and the cleared callback context always match the single-hop params after both success and revert paths.
