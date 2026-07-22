Q10051: path-direction mismatch in token sweep and ETH refund when an exact-output path recurses through a thin intermediate pool

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/base/PeripheryPayments.sol::{sweepToken,refundETH}` with `zeroForOneBitMap`, `amountInMaximum`, and `amountOutMinimum` around exact-output recursion edges while an exact-output path recurses through a thin intermediate pool, so that the public path, bitmap, and hop token assumptions stop matching the pool actually called along `public helper -> inspect router-held token or ETH balance -> transfer full balance to caller-chosen recipient`, corrupting all router-held residue, including dust left by prior swaps, permit flows, or partial WETH conversions? These are public helpers, so any accounting bug that leaves third-party residue on the router converts directly into user-stealable value. Use a valid-looking path whose repeated token or repeated pool shape stresses the hop-direction derivation and payer updates.

Target
- File/function: metric-periphery/contracts/base/PeripheryPayments.sol::{sweepToken,refundETH}
- Entrypoint: metric-periphery/contracts/base/PeripheryPayments.sol::{sweepToken,refundETH}
- Attacker controls: `zeroForOneBitMap`, `amountInMaximum`, and `amountOutMinimum` around exact-output recursion edges
- Exploit idea: Reach `public helper -> inspect router-held token or ETH balance -> transfer full balance to caller-chosen recipient` in a live public flow and show that use a valid-looking path whose repeated token or repeated pool shape stresses the hop-direction derivation and payer updates. The exact value at risk is all router-held residue, including dust left by prior swaps, permit flows, or partial WETH conversions.
- Invariant to test: Each hop must consume the exact token and direction implied by the user-supplied path and bitmap. The concrete assertion should cover all router-held residue, including dust left by prior swaps, permit flows, or partial WETH conversions.
- Expected Immunefi impact: High direct user loss through settlement against the wrong pool or wrong token leg.
- Fast validation: Intentionally strand balances through revert-prone public paths and verify no unrelated caller can sweep or refund value created by someone else's transaction steps.
