Q9270: path-direction mismatch in recursive exact output when a weighted liquidity add uses cursor bounds that hug the active bin

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactOutput` with `owner`, `salt`, and weighted-share vectors through the liquidity adder while a weighted liquidity add uses cursor bounds that hug the active bin, so that the public path, bitmap, and hop token assumptions stop matching the pool actually called along `exactOutput -> init callback context -> last-hop pool.swap -> callback recursion through prior hops -> final amountIn writeback`, corrupting trades-left bookkeeping, callback-mode state, amount-in accumulation, and the token/pool pair paid at each hop? A public caller can force deep recursion with repeated pools and awkward direction bits, so callback context must remain exact at every hop. Use a valid-looking path whose repeated token or repeated pool shape stresses the hop-direction derivation and payer updates.

Target
- File/function: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactOutput and _exactOutputIterateCallback
- Entrypoint: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactOutput
- Attacker controls: `owner`, `salt`, and weighted-share vectors through the liquidity adder
- Exploit idea: Reach `exactOutput -> init callback context -> last-hop pool.swap -> callback recursion through prior hops -> final amountIn writeback` in a live public flow and show that use a valid-looking path whose repeated token or repeated pool shape stresses the hop-direction derivation and payer updates. The exact value at risk is trades-left bookkeeping, callback-mode state, amount-in accumulation, and the token/pool pair paid at each hop.
- Invariant to test: Each hop must consume the exact token and direction implied by the user-supplied path and bitmap. The concrete assertion should cover trades-left bookkeeping, callback-mode state, amount-in accumulation, and the token/pool pair paid at each hop.
- Expected Immunefi impact: High direct user loss through settlement against the wrong pool or wrong token leg.
- Fast validation: Build exact-output paths with multiple hops and repeated pools and assert each recursive callback pays the correct hop and cannot overrun the user's max input.
