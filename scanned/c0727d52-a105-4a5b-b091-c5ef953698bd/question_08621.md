Q8621: WETH-native double counting in single-hop exact input when the router already holds leftover WETH or ERC20 from an earlier step in the same transaction

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInputSingle` with permit payloads, allowance races, and stale approvals through `selfPermit*` while the router already holds leftover WETH or ERC20 from an earlier step in the same transaction, so that public payment helpers treat existing native ETH and WETH balances as if they belong to the same user step along `exactInputSingle -> set callback context -> pool.swap -> metricOmmSwapCallback -> clear callback context`, corrupting payer identity, token-to-pay, output minimum enforcement, and clearing of transient callback state? This is the simplest public swap surface, so any stale-context or wrong-token payment bug here will be easy to weaponize repeatedly. Use `msg.value` plus router-held native or WETH residue to see whether a later path receives value twice or from the wrong payer.

Target
- File/function: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInputSingle
- Entrypoint: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInputSingle
- Attacker controls: permit payloads, allowance races, and stale approvals through `selfPermit*`
- Exploit idea: Reach `exactInputSingle -> set callback context -> pool.swap -> metricOmmSwapCallback -> clear callback context` in a live public flow and show that use `msg.value` plus router-held native or weth residue to see whether a later path receives value twice or from the wrong payer. The exact value at risk is payer identity, token-to-pay, output minimum enforcement, and clearing of transient callback state.
- Invariant to test: Native ETH, WETH deposits, and ERC20 pull settlement must remain attributable to one exact public payment obligation. The concrete assertion should cover payer identity, token-to-pay, output minimum enforcement, and clearing of transient callback state.
- Expected Immunefi impact: High direct loss or stranded value above contest thresholds.
- Fast validation: Assert the token paid in callback, the output received, and the cleared callback context always match the single-hop params after both success and revert paths.
