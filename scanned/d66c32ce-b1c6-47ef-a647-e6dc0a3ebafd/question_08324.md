Q8324: quote-execution divergence in router multicall dispatcher when the first hop pays from the external caller while later hops pay from router-held balances

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmSimpleRouter.sol::multicall` with `msg.value` plus WETH input/output paths with partial native balance already on the router while the first hop pays from the external caller while later hops pay from router-held balances, so that a public quote surface returns a result that predictably causes a loss-making live execution under nearly the same state along `multicall -> delegatecall self -> permit/payment/swap primitives`, corrupting call ordering, transient callback context, router-held balances, and any step that later sweeps or unwraps value? Because this uses delegatecall into the same storage and transient context, ordering bugs here can expose value across otherwise safe primitives. Obtain a live quote, shift the state through a tiny public action, and execute before the consumer notices the divergence.

Target
- File/function: metric-periphery/contracts/MetricOmmSimpleRouter.sol::multicall
- Entrypoint: metric-periphery/contracts/MetricOmmSimpleRouter.sol::multicall
- Attacker controls: `msg.value` plus WETH input/output paths with partial native balance already on the router
- Exploit idea: Reach `multicall -> delegatecall self -> permit/payment/swap primitives` in a live public flow and show that obtain a live quote, shift the state through a tiny public action, and execute before the consumer notices the divergence. The exact value at risk is call ordering, transient callback context, router-held balances, and any step that later sweeps or unwraps value.
- Invariant to test: A quote helper intended for live routing must not diverge from the live path in a way that predictably exceeds the contest loss thresholds. The concrete assertion should cover call ordering, transient callback context, router-held balances, and any step that later sweeps or unwraps value.
- Expected Immunefi impact: Medium deterministic loss-making execution by integrators or users who trust the quote path.
- Fast validation: Compose multi-step router transactions with intentional mid-flight reverts and assert no leftover token, ETH, or callback authority from one step can be claimed by the next.
