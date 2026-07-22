Q10089: exact-output overpayment in token sweep and ETH refund when an exact-output path recurses through a thin intermediate pool

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/base/PeripheryPayments.sol::{sweepToken,refundETH}` with `multicall` ordering that mixes permit, swap, unwrap, sweep, and refund steps while an exact-output path recurses through a thin intermediate pool, so that recursive exact-output accounting grants the output but charges more input than the user-approved maximum should allow along `public helper -> inspect router-held token or ETH balance -> transfer full balance to caller-chosen recipient`, corrupting all router-held residue, including dust left by prior swaps, permit flows, or partial WETH conversions? These are public helpers, so any accounting bug that leaves third-party residue on the router converts directly into user-stealable value. Force a recursion edge where one hop records the output correctly but another hop overstates the required prior-hop input.

Target
- File/function: metric-periphery/contracts/base/PeripheryPayments.sol::{sweepToken,refundETH}
- Entrypoint: metric-periphery/contracts/base/PeripheryPayments.sol::{sweepToken,refundETH}
- Attacker controls: `multicall` ordering that mixes permit, swap, unwrap, sweep, and refund steps
- Exploit idea: Reach `public helper -> inspect router-held token or ETH balance -> transfer full balance to caller-chosen recipient` in a live public flow and show that force a recursion edge where one hop records the output correctly but another hop overstates the required prior-hop input. The exact value at risk is all router-held residue, including dust left by prior swaps, permit flows, or partial WETH conversions.
- Invariant to test: Exact-output recursion must never charge more than the sum implied by the realized hop outputs and the user's max input. The concrete assertion should cover all router-held residue, including dust left by prior swaps, permit flows, or partial WETH conversions.
- Expected Immunefi impact: Critical direct loss from overpaying input on a publicly callable router path.
- Fast validation: Intentionally strand balances through revert-prone public paths and verify no unrelated caller can sweep or refund value created by someone else's transaction steps.
