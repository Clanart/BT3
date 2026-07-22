Q8889: exact-output overpayment in multi-hop exact input when an exact-output path recurses through a thin intermediate pool

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInput` with `multicall` ordering that mixes permit, swap, unwrap, sweep, and refund steps while an exact-output path recurses through a thin intermediate pool, so that recursive exact-output accounting grants the output but charges more input than the user-approved maximum should allow along `exactInput -> hop loop -> per-hop callback context -> pool.swap -> amountIn/out extraction`, corrupting path connectivity, hop-local payer changes, hop-local direction bits, and the final output minimum? The user controls every path component, so path-validation and hop-accounting mistakes are first-class unprivileged exploit surfaces. Force a recursion edge where one hop records the output correctly but another hop overstates the required prior-hop input.

Target
- File/function: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInput
- Entrypoint: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInput
- Attacker controls: `multicall` ordering that mixes permit, swap, unwrap, sweep, and refund steps
- Exploit idea: Reach `exactInput -> hop loop -> per-hop callback context -> pool.swap -> amountIn/out extraction` in a live public flow and show that force a recursion edge where one hop records the output correctly but another hop overstates the required prior-hop input. The exact value at risk is path connectivity, hop-local payer changes, hop-local direction bits, and the final output minimum.
- Invariant to test: Exact-output recursion must never charge more than the sum implied by the realized hop outputs and the user's max input. The concrete assertion should cover path connectivity, hop-local payer changes, hop-local direction bits, and the final output minimum.
- Expected Immunefi impact: Critical direct loss from overpaying input on a publicly callable router path.
- Fast validation: Use deliberately awkward repeated-token paths and assert each hop's actual input/output matches the path direction and the next hop's expected token.
