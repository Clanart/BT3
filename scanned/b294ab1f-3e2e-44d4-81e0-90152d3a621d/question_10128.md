Q10128: residue theft in token sweep and ETH refund when the first hop pays from the external caller while later hops pay from router-held balances

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/base/PeripheryPayments.sol::{sweepToken,refundETH}` with quote or depth reads taken immediately before the user executes the live trade while the first hop pays from the external caller while later hops pay from router-held balances, so that router-held ETH or ERC20 residue from one public step becomes claimable by a later caller through a helper along `public helper -> inspect router-held token or ETH balance -> transfer full balance to caller-chosen recipient`, corrupting all router-held residue, including dust left by prior swaps, permit flows, or partial WETH conversions? These are public helpers, so any accounting bug that leaves third-party residue on the router converts directly into user-stealable value. Strand value on the router through a legitimate path, then claim it through an unwrap, sweep, or refund helper.

Target
- File/function: metric-periphery/contracts/base/PeripheryPayments.sol::{sweepToken,refundETH}
- Entrypoint: metric-periphery/contracts/base/PeripheryPayments.sol::{sweepToken,refundETH}
- Attacker controls: quote or depth reads taken immediately before the user executes the live trade
- Exploit idea: Reach `public helper -> inspect router-held token or ETH balance -> transfer full balance to caller-chosen recipient` in a live public flow and show that strand value on the router through a legitimate path, then claim it through an unwrap, sweep, or refund helper. The exact value at risk is all router-held residue, including dust left by prior swaps, permit flows, or partial WETH conversions.
- Invariant to test: No public helper may transfer value that is economically attributable to a different user's earlier router step. The concrete assertion should cover all router-held residue, including dust left by prior swaps, permit flows, or partial WETH conversions.
- Expected Immunefi impact: High direct loss of user-approved token or ETH value.
- Fast validation: Intentionally strand balances through revert-prone public paths and verify no unrelated caller can sweep or refund value created by someone else's transaction steps.
