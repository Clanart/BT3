Q8861: path-direction mismatch in multi-hop exact input when the router already holds leftover WETH or ERC20 from an earlier step in the same transaction

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInput` with permit payloads, allowance races, and stale approvals through `selfPermit*` while the router already holds leftover WETH or ERC20 from an earlier step in the same transaction, so that the public path, bitmap, and hop token assumptions stop matching the pool actually called along `exactInput -> hop loop -> per-hop callback context -> pool.swap -> amountIn/out extraction`, corrupting path connectivity, hop-local payer changes, hop-local direction bits, and the final output minimum? The user controls every path component, so path-validation and hop-accounting mistakes are first-class unprivileged exploit surfaces. Use a valid-looking path whose repeated token or repeated pool shape stresses the hop-direction derivation and payer updates.

Target
- File/function: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInput
- Entrypoint: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInput
- Attacker controls: permit payloads, allowance races, and stale approvals through `selfPermit*`
- Exploit idea: Reach `exactInput -> hop loop -> per-hop callback context -> pool.swap -> amountIn/out extraction` in a live public flow and show that use a valid-looking path whose repeated token or repeated pool shape stresses the hop-direction derivation and payer updates. The exact value at risk is path connectivity, hop-local payer changes, hop-local direction bits, and the final output minimum.
- Invariant to test: Each hop must consume the exact token and direction implied by the user-supplied path and bitmap. The concrete assertion should cover path connectivity, hop-local payer changes, hop-local direction bits, and the final output minimum.
- Expected Immunefi impact: High direct user loss through settlement against the wrong pool or wrong token leg.
- Fast validation: Use deliberately awkward repeated-token paths and assert each hop's actual input/output matches the path direction and the next hop's expected token.
