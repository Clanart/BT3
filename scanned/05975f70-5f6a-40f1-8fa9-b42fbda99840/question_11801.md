Q11801: WETH-native double counting in live quoter callback path when the first hop pays from the external caller while later hops pay from router-held balances

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/lens/MetricOmmSwapQuoter.sol::quoteLiveExactIn` with `multicall` ordering that mixes permit, swap, unwrap, sweep, and refund steps while the first hop pays from the external caller while later hops pay from router-held balances, so that public payment helpers treat existing native ETH and WETH balances as if they belong to the same user step along `quoteLiveExactIn -> pool.swap simulation via callback reverts -> decode swap deltas -> quote consumer uses result`, corrupting quoted input/output, path decoding, callback caller binding, and any integrator decision based on the quote? Although this is a quote surface, the user can exploit it if the quoted result predictably induces a live loss-making execution against the same pool state. Use `msg.value` plus router-held native or WETH residue to see whether a later path receives value twice or from the wrong payer.

Target
- File/function: metric-periphery/contracts/lens/MetricOmmSwapQuoter.sol::quoteLiveExactIn
- Entrypoint: metric-periphery/contracts/lens/MetricOmmSwapQuoter.sol::quoteLiveExactIn
- Attacker controls: `multicall` ordering that mixes permit, swap, unwrap, sweep, and refund steps
- Exploit idea: Reach `quoteLiveExactIn -> pool.swap simulation via callback reverts -> decode swap deltas -> quote consumer uses result` in a live public flow and show that use `msg.value` plus router-held native or weth residue to see whether a later path receives value twice or from the wrong payer. The exact value at risk is quoted input/output, path decoding, callback caller binding, and any integrator decision based on the quote.
- Invariant to test: Native ETH, WETH deposits, and ERC20 pull settlement must remain attributable to one exact public payment obligation. The concrete assertion should cover quoted input/output, path decoding, callback caller binding, and any integrator decision based on the quote.
- Expected Immunefi impact: High direct loss or stranded value above contest thresholds.
- Fast validation: Compare live quote results with the next real swap under the same state and flag any deterministic divergence large enough to exceed Sherlock thresholds.
