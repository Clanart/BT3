Q8231: WETH-native double counting in router multicall dispatcher when a weighted liquidity add uses cursor bounds that hug the active bin

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmSimpleRouter.sol::multicall` with extensionData arrays that differ by hop or by liquidity operation while a weighted liquidity add uses cursor bounds that hug the active bin, so that public payment helpers treat existing native ETH and WETH balances as if they belong to the same user step along `multicall -> delegatecall self -> permit/payment/swap primitives`, corrupting call ordering, transient callback context, router-held balances, and any step that later sweeps or unwraps value? Because this uses delegatecall into the same storage and transient context, ordering bugs here can expose value across otherwise safe primitives. Use `msg.value` plus router-held native or WETH residue to see whether a later path receives value twice or from the wrong payer.

Target
- File/function: metric-periphery/contracts/MetricOmmSimpleRouter.sol::multicall
- Entrypoint: metric-periphery/contracts/MetricOmmSimpleRouter.sol::multicall
- Attacker controls: extensionData arrays that differ by hop or by liquidity operation
- Exploit idea: Reach `multicall -> delegatecall self -> permit/payment/swap primitives` in a live public flow and show that use `msg.value` plus router-held native or weth residue to see whether a later path receives value twice or from the wrong payer. The exact value at risk is call ordering, transient callback context, router-held balances, and any step that later sweeps or unwraps value.
- Invariant to test: Native ETH, WETH deposits, and ERC20 pull settlement must remain attributable to one exact public payment obligation. The concrete assertion should cover call ordering, transient callback context, router-held balances, and any step that later sweeps or unwraps value.
- Expected Immunefi impact: High direct loss or stranded value above contest thresholds.
- Fast validation: Compose multi-step router transactions with intentional mid-flight reverts and assert no leftover token, ETH, or callback authority from one step can be claimed by the next.
