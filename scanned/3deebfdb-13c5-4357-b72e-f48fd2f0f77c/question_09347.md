Q9347: residue theft in recursive exact output when a weighted liquidity add uses cursor bounds that hug the active bin

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactOutput` with `zeroForOneBitMap`, `amountInMaximum`, and `amountOutMinimum` around exact-output recursion edges while a weighted liquidity add uses cursor bounds that hug the active bin, so that router-held ETH or ERC20 residue from one public step becomes claimable by a later caller through a helper along `exactOutput -> init callback context -> last-hop pool.swap -> callback recursion through prior hops -> final amountIn writeback`, corrupting trades-left bookkeeping, callback-mode state, amount-in accumulation, and the token/pool pair paid at each hop? A public caller can force deep recursion with repeated pools and awkward direction bits, so callback context must remain exact at every hop. Strand value on the router through a legitimate path, then claim it through an unwrap, sweep, or refund helper.

Target
- File/function: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactOutput and _exactOutputIterateCallback
- Entrypoint: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exactOutput
- Attacker controls: `zeroForOneBitMap`, `amountInMaximum`, and `amountOutMinimum` around exact-output recursion edges
- Exploit idea: Reach `exactOutput -> init callback context -> last-hop pool.swap -> callback recursion through prior hops -> final amountIn writeback` in a live public flow and show that strand value on the router through a legitimate path, then claim it through an unwrap, sweep, or refund helper. The exact value at risk is trades-left bookkeeping, callback-mode state, amount-in accumulation, and the token/pool pair paid at each hop.
- Invariant to test: No public helper may transfer value that is economically attributable to a different user's earlier router step. The concrete assertion should cover trades-left bookkeeping, callback-mode state, amount-in accumulation, and the token/pool pair paid at each hop.
- Expected Immunefi impact: High direct loss of user-approved token or ETH value.
- Fast validation: Build exact-output paths with multiple hops and repeated pools and assert each recursive callback pays the correct hop and cannot overrun the user's max input.
