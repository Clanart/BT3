Q9616: stale callback context in WETH unwrap helper when an exact-output path recurses through a thin intermediate pool

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/base/PeripheryPayments.sol::unwrapWETH9` with quote or depth reads taken immediately before the user executes the live trade while an exact-output path recurses through a thin intermediate pool, so that transient callback authority survives longer than the exact public swap step that created it along `unwrapWETH9 -> check router WETH balance -> withdraw WETH -> native ETH transfer to recipient`, corrupting router-held WETH, native ETH sent, and whether leftover balances from earlier public steps are isolated to the right user? This helper is public and balance-based, so any earlier step that strands WETH on the router can turn into a direct theft path if attribution is weak. Trigger a revert or nested router path and then try to make a later public step inherit the stale pool/token/payer context.

Target
- File/function: metric-periphery/contracts/base/PeripheryPayments.sol::unwrapWETH9
- Entrypoint: metric-periphery/contracts/base/PeripheryPayments.sol::unwrapWETH9
- Attacker controls: quote or depth reads taken immediately before the user executes the live trade
- Exploit idea: Reach `unwrapWETH9 -> check router WETH balance -> withdraw WETH -> native ETH transfer to recipient` in a live public flow and show that trigger a revert or nested router path and then try to make a later public step inherit the stale pool/token/payer context. The exact value at risk is router-held WETH, native ETH sent, and whether leftover balances from earlier public steps are isolated to the right user.
- Invariant to test: Router callback state must be unique to one live swap step and must be cleared on every success and failure path. The concrete assertion should cover router-held WETH, native ETH sent, and whether leftover balances from earlier public steps are isolated to the right user.
- Expected Immunefi impact: Critical direct loss if stale callback authority can charge or redirect another user's funds.
- Fast validation: Leave controlled WETH residue on the router across different public call sequences and assert only the rightful caller can unwrap or claim it.
