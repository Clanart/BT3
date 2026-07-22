Q11947: quote-execution divergence in live quoter callback path when a weighted liquidity add uses cursor bounds that hug the active bin

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/lens/MetricOmmSwapQuoter.sol::quoteLiveExactIn` with `zeroForOneBitMap`, `amountInMaximum`, and `amountOutMinimum` around exact-output recursion edges while a weighted liquidity add uses cursor bounds that hug the active bin, so that a public quote surface returns a result that predictably causes a loss-making live execution under nearly the same state along `quoteLiveExactIn -> pool.swap simulation via callback reverts -> decode swap deltas -> quote consumer uses result`, corrupting quoted input/output, path decoding, callback caller binding, and any integrator decision based on the quote? Although this is a quote surface, the user can exploit it if the quoted result predictably induces a live loss-making execution against the same pool state. Obtain a live quote, shift the state through a tiny public action, and execute before the consumer notices the divergence.

Target
- File/function: metric-periphery/contracts/lens/MetricOmmSwapQuoter.sol::quoteLiveExactIn
- Entrypoint: metric-periphery/contracts/lens/MetricOmmSwapQuoter.sol::quoteLiveExactIn
- Attacker controls: `zeroForOneBitMap`, `amountInMaximum`, and `amountOutMinimum` around exact-output recursion edges
- Exploit idea: Reach `quoteLiveExactIn -> pool.swap simulation via callback reverts -> decode swap deltas -> quote consumer uses result` in a live public flow and show that obtain a live quote, shift the state through a tiny public action, and execute before the consumer notices the divergence. The exact value at risk is quoted input/output, path decoding, callback caller binding, and any integrator decision based on the quote.
- Invariant to test: A quote helper intended for live routing must not diverge from the live path in a way that predictably exceeds the contest loss thresholds. The concrete assertion should cover quoted input/output, path decoding, callback caller binding, and any integrator decision based on the quote.
- Expected Immunefi impact: Medium deterministic loss-making execution by integrators or users who trust the quote path.
- Fast validation: Compare live quote results with the next real swap under the same state and flag any deterministic divergence large enough to exceed Sherlock thresholds.
