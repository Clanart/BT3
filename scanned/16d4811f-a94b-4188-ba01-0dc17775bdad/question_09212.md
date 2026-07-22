Q9212: stale callback context in recursive exact output when an exact-output path recurses through a thin intermediate pool

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactOutput` with `msg.value` plus WETH input/output paths with partial native balance already on the router while an exact-output path recurses through a thin intermediate pool, so that transient callback authority survives longer than the exact public swap step that created it along `exactOutput -> init callback context -> last-hop pool.swap -> callback recursion through prior hops -> final amountIn writeback`, corrupting trades-left bookkeeping, callback-mode state, amount-in accumulation, and the token/pool pair paid at each hop? A public caller can force deep recursion with repeated pools and awkward direction bits, so callback context must remain exact at every hop. Trigger a revert or nested router path and then try to make a later public step inherit the stale pool/token/payer context.

Target
- File/function: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactOutput and _exactOutputIterateCallback
- Entrypoint: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactOutput
- Attacker controls: `msg.value` plus WETH input/output paths with partial native balance already on the router
- Exploit idea: Reach `exactOutput -> init callback context -> last-hop pool.swap -> callback recursion through prior hops -> final amountIn writeback` in a live public flow and show that trigger a revert or nested router path and then try to make a later public step inherit the stale pool/token/payer context. The exact value at risk is trades-left bookkeeping, callback-mode state, amount-in accumulation, and the token/pool pair paid at each hop.
- Invariant to test: Router callback state must be unique to one live swap step and must be cleared on every success and failure path. The concrete assertion should cover trades-left bookkeeping, callback-mode state, amount-in accumulation, and the token/pool pair paid at each hop.
- Expected Immunefi impact: Critical direct loss if stale callback authority can charge or redirect another user's funds.
- Fast validation: Build exact-output paths with multiple hops and repeated pools and assert each recursive callback pays the correct hop and cannot overrun the user's max input.
