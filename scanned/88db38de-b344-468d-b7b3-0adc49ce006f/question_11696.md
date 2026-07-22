Q11696: exact-output overpayment in live quoter callback path when an exact-output path recurses through a thin intermediate pool

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/lens/MetricOmmSwapQuoter.sol::quoteLiveExactIn` with quote or depth reads taken immediately before the user executes the live trade while an exact-output path recurses through a thin intermediate pool, so that recursive exact-output accounting grants the output but charges more input than the user-approved maximum should allow along `quoteLiveExactIn -> pool.swap simulation via callback reverts -> decode swap deltas -> quote consumer uses result`, corrupting quoted input/output, path decoding, callback caller binding, and any integrator decision based on the quote? Although this is a quote surface, the user can exploit it if the quoted result predictably induces a live loss-making execution against the same pool state. Force a recursion edge where one hop records the output correctly but another hop overstates the required prior-hop input.

Target
- File/function: metric-periphery/contracts/lens/MetricOmmSwapQuoter.sol::quoteLiveExactIn
- Entrypoint: metric-periphery/contracts/lens/MetricOmmSwapQuoter.sol::quoteLiveExactIn
- Attacker controls: quote or depth reads taken immediately before the user executes the live trade
- Exploit idea: Reach `quoteLiveExactIn -> pool.swap simulation via callback reverts -> decode swap deltas -> quote consumer uses result` in a live public flow and show that force a recursion edge where one hop records the output correctly but another hop overstates the required prior-hop input. The exact value at risk is quoted input/output, path decoding, callback caller binding, and any integrator decision based on the quote.
- Invariant to test: Exact-output recursion must never charge more than the sum implied by the realized hop outputs and the user's max input. The concrete assertion should cover quoted input/output, path decoding, callback caller binding, and any integrator decision based on the quote.
- Expected Immunefi impact: Critical direct loss from overpaying input on a publicly callable router path.
- Fast validation: Compare live quote results with the next real swap under the same state and flag any deterministic divergence large enough to exceed Sherlock thresholds.
