Q8740: quote-execution divergence in single-hop exact input when the router already holds leftover WETH or ERC20 from an earlier step in the same transaction

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInputSingle` with `msg.value` plus WETH input/output paths with partial native balance already on the router while the router already holds leftover WETH or ERC20 from an earlier step in the same transaction, so that a public quote surface returns a result that predictably causes a loss-making live execution under nearly the same state along `exactInputSingle -> set callback context -> pool.swap -> metricOmmSwapCallback -> clear callback context`, corrupting payer identity, token-to-pay, output minimum enforcement, and clearing of transient callback state? This is the simplest public swap surface, so any stale-context or wrong-token payment bug here will be easy to weaponize repeatedly. Obtain a live quote, shift the state through a tiny public action, and execute before the consumer notices the divergence.

Target
- File/function: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInputSingle
- Entrypoint: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactInputSingle
- Attacker controls: `msg.value` plus WETH input/output paths with partial native balance already on the router
- Exploit idea: Reach `exactInputSingle -> set callback context -> pool.swap -> metricOmmSwapCallback -> clear callback context` in a live public flow and show that obtain a live quote, shift the state through a tiny public action, and execute before the consumer notices the divergence. The exact value at risk is payer identity, token-to-pay, output minimum enforcement, and clearing of transient callback state.
- Invariant to test: A quote helper intended for live routing must not diverge from the live path in a way that predictably exceeds the contest loss thresholds. The concrete assertion should cover payer identity, token-to-pay, output minimum enforcement, and clearing of transient callback state.
- Expected Immunefi impact: Medium deterministic loss-making execution by integrators or users who trust the quote path.
- Fast validation: Assert the token paid in callback, the output received, and the cleared callback context always match the single-hop params after both success and revert paths.
