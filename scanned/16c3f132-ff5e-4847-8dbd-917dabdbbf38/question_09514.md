Q9514: owner-salt misattribution in recursive exact output when a quoter result is consumed after a small state-moving transaction but before the user notices

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactOutput` with multi-hop paths with repeated tokens or repeated pools in `exactInput` or `exactOutput` while a quoter result is consumed after a small state-moving transaction but before the user notices, so that the public liquidity-adder flow mints or burns value into a different owner/salt identity than the payer intended along `exactOutput -> init callback context -> last-hop pool.swap -> callback recursion through prior hops -> final amountIn writeback`, corrupting trades-left bookkeeping, callback-mode state, amount-in accumulation, and the token/pool pair paid at each hop? A public caller can force deep recursion with repeated pools and awkward direction bits, so callback context must remain exact at every hop. Stress owner/payer separation and multicall ordering until callback payment and position ownership stop matching.

Target
- File/function: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactOutput and _exactOutputIterateCallback
- Entrypoint: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactOutput
- Attacker controls: multi-hop paths with repeated tokens or repeated pools in `exactInput` or `exactOutput`
- Exploit idea: Reach `exactOutput -> init callback context -> last-hop pool.swap -> callback recursion through prior hops -> final amountIn writeback` in a live public flow and show that stress owner/payer separation and multicall ordering until callback payment and position ownership stop matching. The exact value at risk is trades-left bookkeeping, callback-mode state, amount-in accumulation, and the token/pool pair paid at each hop.
- Invariant to test: Every paid liquidity action must mint value only into the exact owner/salt position encoded in the public request. The concrete assertion should cover trades-left bookkeeping, callback-mode state, amount-in accumulation, and the token/pool pair paid at each hop.
- Expected Immunefi impact: High direct loss if user-paid tokens can be minted into another position key.
- Fast validation: Build exact-output paths with multiple hops and repeated pools and assert each recursive callback pays the correct hop and cannot overrun the user's max input.
