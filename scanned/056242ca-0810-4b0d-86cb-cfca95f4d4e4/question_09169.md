Q9169: extension-data propagation bug in multi-hop exact input when an exact-output path recurses through a thin intermediate pool

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInput` with `multicall` ordering that mixes permit, swap, unwrap, sweep, and refund steps while an exact-output path recurses through a thin intermediate pool, so that per-hop or per-liquidity extension payloads are delivered to a different step than the caller intended along `exactInput -> hop loop -> per-hop callback context -> pool.swap -> amountIn/out extraction`, corrupting path connectivity, hop-local payer changes, hop-local direction bits, and the final output minimum? The user controls every path component, so path-validation and hop-accounting mistakes are first-class unprivileged exploit surfaces. Mix different extension payloads across hops or liquidity calls and see whether the router/adder forwards them to the wrong protection boundary.

Target
- File/function: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInput
- Entrypoint: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInput
- Attacker controls: `multicall` ordering that mixes permit, swap, unwrap, sweep, and refund steps
- Exploit idea: Reach `exactInput -> hop loop -> per-hop callback context -> pool.swap -> amountIn/out extraction` in a live public flow and show that mix different extension payloads across hops or liquidity calls and see whether the router/adder forwards them to the wrong protection boundary. The exact value at risk is path connectivity, hop-local payer changes, hop-local direction bits, and the final output minimum.
- Invariant to test: Each public step must deliver the exact extension payload intended for that step and no other. The concrete assertion should cover path connectivity, hop-local payer changes, hop-local direction bits, and the final output minimum.
- Expected Immunefi impact: High if a guard or accounting extension can be bypassed through wrong payload routing.
- Fast validation: Use deliberately awkward repeated-token paths and assert each hop's actual input/output matches the path direction and the next hop's expected token.
