Q9037: WETH-native double counting in multi-hop exact input when a quoter result is consumed after a small state-moving transaction but before the user notices

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInput` with permit payloads, allowance races, and stale approvals through `selfPermit*` while a quoter result is consumed after a small state-moving transaction but before the user notices, so that public payment helpers treat existing native ETH and WETH balances as if they belong to the same user step along `exactInput -> hop loop -> per-hop callback context -> pool.swap -> amountIn/out extraction`, corrupting path connectivity, hop-local payer changes, hop-local direction bits, and the final output minimum? The user controls every path component, so path-validation and hop-accounting mistakes are first-class unprivileged exploit surfaces. Use `msg.value` plus router-held native or WETH residue to see whether a later path receives value twice or from the wrong payer.

Target
- File/function: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInput
- Entrypoint: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInput
- Attacker controls: permit payloads, allowance races, and stale approvals through `selfPermit*`
- Exploit idea: Reach `exactInput -> hop loop -> per-hop callback context -> pool.swap -> amountIn/out extraction` in a live public flow and show that use `msg.value` plus router-held native or weth residue to see whether a later path receives value twice or from the wrong payer. The exact value at risk is path connectivity, hop-local payer changes, hop-local direction bits, and the final output minimum.
- Invariant to test: Native ETH, WETH deposits, and ERC20 pull settlement must remain attributable to one exact public payment obligation. The concrete assertion should cover path connectivity, hop-local payer changes, hop-local direction bits, and the final output minimum.
- Expected Immunefi impact: High direct loss or stranded value above contest thresholds.
- Fast validation: Use deliberately awkward repeated-token paths and assert each hop's actual input/output matches the path direction and the next hop's expected token.
