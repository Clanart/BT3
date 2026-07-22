Q9438: WETH-native double counting in recursive exact output when a quoter result is consumed after a small state-moving transaction but before the user notices

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactOutput` with `owner`, `salt`, and weighted-share vectors through the liquidity adder while a quoter result is consumed after a small state-moving transaction but before the user notices, so that public payment helpers treat existing native ETH and WETH balances as if they belong to the same user step along `exactOutput -> init callback context -> last-hop pool.swap -> callback recursion through prior hops -> final amountIn writeback`, corrupting trades-left bookkeeping, callback-mode state, amount-in accumulation, and the token/pool pair paid at each hop? A public caller can force deep recursion with repeated pools and awkward direction bits, so callback context must remain exact at every hop. Use `msg.value` plus router-held native or WETH residue to see whether a later path receives value twice or from the wrong payer.

Target
- File/function: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactOutput and _exactOutputIterateCallback
- Entrypoint: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactOutput
- Attacker controls: `owner`, `salt`, and weighted-share vectors through the liquidity adder
- Exploit idea: Reach `exactOutput -> init callback context -> last-hop pool.swap -> callback recursion through prior hops -> final amountIn writeback` in a live public flow and show that use `msg.value` plus router-held native or weth residue to see whether a later path receives value twice or from the wrong payer. The exact value at risk is trades-left bookkeeping, callback-mode state, amount-in accumulation, and the token/pool pair paid at each hop.
- Invariant to test: Native ETH, WETH deposits, and ERC20 pull settlement must remain attributable to one exact public payment obligation. The concrete assertion should cover trades-left bookkeeping, callback-mode state, amount-in accumulation, and the token/pool pair paid at each hop.
- Expected Immunefi impact: High direct loss or stranded value above contest thresholds.
- Fast validation: Build exact-output paths with multiple hops and repeated pools and assert each recursive callback pays the correct hop and cannot overrun the user's max input.
