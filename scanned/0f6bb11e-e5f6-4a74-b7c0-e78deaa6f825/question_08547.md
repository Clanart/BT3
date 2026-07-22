Q8547: residue theft in single-hop exact input when a weighted liquidity add uses cursor bounds that hug the active bin

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInputSingle` with `zeroForOneBitMap`, `amountInMaximum`, and `amountOutMinimum` around exact-output recursion edges while a weighted liquidity add uses cursor bounds that hug the active bin, so that router-held ETH or ERC20 residue from one public step becomes claimable by a later caller through a helper along `exactInputSingle -> set callback context -> pool.swap -> metricOmmSwapCallback -> clear callback context`, corrupting payer identity, token-to-pay, output minimum enforcement, and clearing of transient callback state? This is the simplest public swap surface, so any stale-context or wrong-token payment bug here will be easy to weaponize repeatedly. Strand value on the router through a legitimate path, then claim it through an unwrap, sweep, or refund helper.

Target
- File/function: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInputSingle
- Entrypoint: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInputSingle
- Attacker controls: `zeroForOneBitMap`, `amountInMaximum`, and `amountOutMinimum` around exact-output recursion edges
- Exploit idea: Reach `exactInputSingle -> set callback context -> pool.swap -> metricOmmSwapCallback -> clear callback context` in a live public flow and show that strand value on the router through a legitimate path, then claim it through an unwrap, sweep, or refund helper. The exact value at risk is payer identity, token-to-pay, output minimum enforcement, and clearing of transient callback state.
- Invariant to test: No public helper may transfer value that is economically attributable to a different user's earlier router step. The concrete assertion should cover payer identity, token-to-pay, output minimum enforcement, and clearing of transient callback state.
- Expected Immunefi impact: High direct loss of user-approved token or ETH value.
- Fast validation: Assert the token paid in callback, the output received, and the cleared callback context always match the single-hop params after both success and revert paths.
