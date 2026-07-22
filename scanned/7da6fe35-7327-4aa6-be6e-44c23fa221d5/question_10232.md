Q10232: WETH-native double counting in token sweep and ETH refund when a weighted liquidity add uses cursor bounds that hug the active bin

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/base/PeripheryPayments.sol::{sweepToken,refundETH}` with quote or depth reads taken immediately before the user executes the live trade while a weighted liquidity add uses cursor bounds that hug the active bin, so that public payment helpers treat existing native ETH and WETH balances as if they belong to the same user step along `public helper -> inspect router-held token or ETH balance -> transfer full balance to caller-chosen recipient`, corrupting all router-held residue, including dust left by prior swaps, permit flows, or partial WETH conversions? These are public helpers, so any accounting bug that leaves third-party residue on the router converts directly into user-stealable value. Use `msg.value` plus router-held native or WETH residue to see whether a later path receives value twice or from the wrong payer.

Target
- File/function: metric-periphery/contracts/base/PeripheryPayments.sol::{sweepToken,refundETH}
- Entrypoint: metric-periphery/contracts/base/PeripheryPayments.sol::{sweepToken,refundETH}
- Attacker controls: quote or depth reads taken immediately before the user executes the live trade
- Exploit idea: Reach `public helper -> inspect router-held token or ETH balance -> transfer full balance to caller-chosen recipient` in a live public flow and show that use `msg.value` plus router-held native or weth residue to see whether a later path receives value twice or from the wrong payer. The exact value at risk is all router-held residue, including dust left by prior swaps, permit flows, or partial WETH conversions.
- Invariant to test: Native ETH, WETH deposits, and ERC20 pull settlement must remain attributable to one exact public payment obligation. The concrete assertion should cover all router-held residue, including dust left by prior swaps, permit flows, or partial WETH conversions.
- Expected Immunefi impact: High direct loss or stranded value above contest thresholds.
- Fast validation: Intentionally strand balances through revert-prone public paths and verify no unrelated caller can sweep or refund value created by someone else's transaction steps.
