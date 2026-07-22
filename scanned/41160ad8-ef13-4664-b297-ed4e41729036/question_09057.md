Q9057: probe-pay race in multi-hop exact input when the router already holds leftover WETH or ERC20 from an earlier step in the same transaction

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInput` with `multicall` ordering that mixes permit, swap, unwrap, sweep, and refund steps while the router already holds leftover WETH or ERC20 from an earlier step in the same transaction, so that the liquidity-adder probe phase measures one state but the paid phase executes against another reachable state along `exactInput -> hop loop -> per-hop callback context -> pool.swap -> amountIn/out extraction`, corrupting path connectivity, hop-local payer changes, hop-local direction bits, and the final output minimum? The user controls every path component, so path-validation and hop-accounting mistakes are first-class unprivileged exploit surfaces. Move the pool publicly between probe and payment so the scaled shares no longer correspond to the probed token requirements.

Target
- File/function: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInput
- Entrypoint: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInput
- Attacker controls: `multicall` ordering that mixes permit, swap, unwrap, sweep, and refund steps
- Exploit idea: Reach `exactInput -> hop loop -> per-hop callback context -> pool.swap -> amountIn/out extraction` in a live public flow and show that move the pool publicly between probe and payment so the scaled shares no longer correspond to the probed token requirements. The exact value at risk is path connectivity, hop-local payer changes, hop-local direction bits, and the final output minimum.
- Invariant to test: Weighted liquidity add must either revalidate the probed assumptions or revert; it must never silently mint under a stale quote. The concrete assertion should cover path connectivity, hop-local payer changes, hop-local direction bits, and the final output minimum.
- Expected Immunefi impact: Medium/High LP-principal loss or broken liquidity add functionality.
- Fast validation: Use deliberately awkward repeated-token paths and assert each hop's actual input/output matches the path direction and the next hop's expected token.
