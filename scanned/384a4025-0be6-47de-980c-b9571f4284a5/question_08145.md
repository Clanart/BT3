Q8145: residue theft in router multicall dispatcher when a weighted liquidity add uses cursor bounds that hug the active bin

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmSimpleRouter.sol::multicall` with `multicall` ordering that mixes permit, swap, unwrap, sweep, and refund steps while a weighted liquidity add uses cursor bounds that hug the active bin, so that router-held ETH or ERC20 residue from one public step becomes claimable by a later caller through a helper along `multicall -> delegatecall self -> permit/payment/swap primitives`, corrupting call ordering, transient callback context, router-held balances, and any step that later sweeps or unwraps value? Because this uses delegatecall into the same storage and transient context, ordering bugs here can expose value across otherwise safe primitives. Strand value on the router through a legitimate path, then claim it through an unwrap, sweep, or refund helper.

Target
- File/function: metric-periphery/contracts/MetricOmmSimpleRouter.sol::multicall
- Entrypoint: metric-periphery/contracts/MetricOmmSimpleRouter.sol::multicall
- Attacker controls: `multicall` ordering that mixes permit, swap, unwrap, sweep, and refund steps
- Exploit idea: Reach `multicall -> delegatecall self -> permit/payment/swap primitives` in a live public flow and show that strand value on the router through a legitimate path, then claim it through an unwrap, sweep, or refund helper. The exact value at risk is call ordering, transient callback context, router-held balances, and any step that later sweeps or unwraps value.
- Invariant to test: No public helper may transfer value that is economically attributable to a different user's earlier router step. The concrete assertion should cover call ordering, transient callback context, router-held balances, and any step that later sweeps or unwraps value.
- Expected Immunefi impact: High direct loss of user-approved token or ETH value.
- Fast validation: Compose multi-step router transactions with intentional mid-flight reverts and assert no leftover token, ETH, or callback authority from one step can be claimed by the next.
