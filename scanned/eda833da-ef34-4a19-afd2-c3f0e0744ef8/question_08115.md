Q8115: exact-output overpayment in router multicall dispatcher when a quoter result is consumed after a small state-moving transaction but before the user notices

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmSimpleRouter.sol::multicall` with `zeroForOneBitMap`, `amountInMaximum`, and `amountOutMinimum` around exact-output recursion edges while a quoter result is consumed after a small state-moving transaction but before the user notices, so that recursive exact-output accounting grants the output but charges more input than the user-approved maximum should allow along `multicall -> delegatecall self -> permit/payment/swap primitives`, corrupting call ordering, transient callback context, router-held balances, and any step that later sweeps or unwraps value? Because this uses delegatecall into the same storage and transient context, ordering bugs here can expose value across otherwise safe primitives. Force a recursion edge where one hop records the output correctly but another hop overstates the required prior-hop input.

Target
- File/function: metric-periphery/contracts/MetricOmmSimpleRouter.sol::multicall
- Entrypoint: metric-periphery/contracts/MetricOmmSimpleRouter.sol::multicall
- Attacker controls: `zeroForOneBitMap`, `amountInMaximum`, and `amountOutMinimum` around exact-output recursion edges
- Exploit idea: Reach `multicall -> delegatecall self -> permit/payment/swap primitives` in a live public flow and show that force a recursion edge where one hop records the output correctly but another hop overstates the required prior-hop input. The exact value at risk is call ordering, transient callback context, router-held balances, and any step that later sweeps or unwraps value.
- Invariant to test: Exact-output recursion must never charge more than the sum implied by the realized hop outputs and the user's max input. The concrete assertion should cover call ordering, transient callback context, router-held balances, and any step that later sweeps or unwraps value.
- Expected Immunefi impact: Critical direct loss from overpaying input on a publicly callable router path.
- Fast validation: Compose multi-step router transactions with intentional mid-flight reverts and assert no leftover token, ETH, or callback authority from one step can be claimed by the next.
