Q10009: stale callback context in token sweep and ETH refund when an exact-output path recurses through a thin intermediate pool

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/base/PeripheryPayments.sol::{sweepToken,refundETH}` with `multicall` ordering that mixes permit, swap, unwrap, sweep, and refund steps while an exact-output path recurses through a thin intermediate pool, so that transient callback authority survives longer than the exact public swap step that created it along `public helper -> inspect router-held token or ETH balance -> transfer full balance to caller-chosen recipient`, corrupting all router-held residue, including dust left by prior swaps, permit flows, or partial WETH conversions? These are public helpers, so any accounting bug that leaves third-party residue on the router converts directly into user-stealable value. Trigger a revert or nested router path and then try to make a later public step inherit the stale pool/token/payer context.

Target
- File/function: metric-periphery/contracts/base/PeripheryPayments.sol::{sweepToken,refundETH}
- Entrypoint: metric-periphery/contracts/base/PeripheryPayments.sol::{sweepToken,refundETH}
- Attacker controls: `multicall` ordering that mixes permit, swap, unwrap, sweep, and refund steps
- Exploit idea: Reach `public helper -> inspect router-held token or ETH balance -> transfer full balance to caller-chosen recipient` in a live public flow and show that trigger a revert or nested router path and then try to make a later public step inherit the stale pool/token/payer context. The exact value at risk is all router-held residue, including dust left by prior swaps, permit flows, or partial WETH conversions.
- Invariant to test: Router callback state must be unique to one live swap step and must be cleared on every success and failure path. The concrete assertion should cover all router-held residue, including dust left by prior swaps, permit flows, or partial WETH conversions.
- Expected Immunefi impact: Critical direct loss if stale callback authority can charge or redirect another user's funds.
- Fast validation: Intentionally strand balances through revert-prone public paths and verify no unrelated caller can sweep or refund value created by someone else's transaction steps.
