Q11674: path-direction mismatch in live quoter callback path when a quoter result is consumed after a small state-moving transaction but before the user notices

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/lens/MetricOmmSwapQuoter.sol::quoteLiveExactIn` with multi-hop paths with repeated tokens or repeated pools in `exactInput` or `exactOutput` while a quoter result is consumed after a small state-moving transaction but before the user notices, so that the public path, bitmap, and hop token assumptions stop matching the pool actually called along `quoteLiveExactIn -> pool.swap simulation via callback reverts -> decode swap deltas -> quote consumer uses result`, corrupting quoted input/output, path decoding, callback caller binding, and any integrator decision based on the quote? Although this is a quote surface, the user can exploit it if the quoted result predictably induces a live loss-making execution against the same pool state. Use a valid-looking path whose repeated token or repeated pool shape stresses the hop-direction derivation and payer updates.

Target
- File/function: metric-periphery/contracts/lens/MetricOmmSwapQuoter.sol::quoteLiveExactIn
- Entrypoint: metric-periphery/contracts/lens/MetricOmmSwapQuoter.sol::quoteLiveExactIn
- Attacker controls: multi-hop paths with repeated tokens or repeated pools in `exactInput` or `exactOutput`
- Exploit idea: Reach `quoteLiveExactIn -> pool.swap simulation via callback reverts -> decode swap deltas -> quote consumer uses result` in a live public flow and show that use a valid-looking path whose repeated token or repeated pool shape stresses the hop-direction derivation and payer updates. The exact value at risk is quoted input/output, path decoding, callback caller binding, and any integrator decision based on the quote.
- Invariant to test: Each hop must consume the exact token and direction implied by the user-supplied path and bitmap. The concrete assertion should cover quoted input/output, path decoding, callback caller binding, and any integrator decision based on the quote.
- Expected Immunefi impact: High direct user loss through settlement against the wrong pool or wrong token leg.
- Fast validation: Compare live quote results with the next real swap under the same state and flag any deterministic divergence large enough to exceed Sherlock thresholds.
