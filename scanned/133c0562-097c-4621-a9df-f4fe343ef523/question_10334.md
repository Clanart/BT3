Q10334: quote-execution divergence in token sweep and ETH refund when an exact-output path recurses through a thin intermediate pool

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/base/PeripheryPayments.sol::{sweepToken,refundETH}` with `owner`, `salt`, and weighted-share vectors through the liquidity adder while an exact-output path recurses through a thin intermediate pool, so that a public quote surface returns a result that predictably causes a loss-making live execution under nearly the same state along `public helper -> inspect router-held token or ETH balance -> transfer full balance to caller-chosen recipient`, corrupting all router-held residue, including dust left by prior swaps, permit flows, or partial WETH conversions? These are public helpers, so any accounting bug that leaves third-party residue on the router converts directly into user-stealable value. Obtain a live quote, shift the state through a tiny public action, and execute before the consumer notices the divergence.

Target
- File/function: metric-periphery/contracts/base/PeripheryPayments.sol::{sweepToken,refundETH}
- Entrypoint: metric-periphery/contracts/base/PeripheryPayments.sol::{sweepToken,refundETH}
- Attacker controls: `owner`, `salt`, and weighted-share vectors through the liquidity adder
- Exploit idea: Reach `public helper -> inspect router-held token or ETH balance -> transfer full balance to caller-chosen recipient` in a live public flow and show that obtain a live quote, shift the state through a tiny public action, and execute before the consumer notices the divergence. The exact value at risk is all router-held residue, including dust left by prior swaps, permit flows, or partial WETH conversions.
- Invariant to test: A quote helper intended for live routing must not diverge from the live path in a way that predictably exceeds the contest loss thresholds. The concrete assertion should cover all router-held residue, including dust left by prior swaps, permit flows, or partial WETH conversions.
- Expected Immunefi impact: Medium deterministic loss-making execution by integrators or users who trust the quote path.
- Fast validation: Intentionally strand balances through revert-prone public paths and verify no unrelated caller can sweep or refund value created by someone else's transaction steps.
