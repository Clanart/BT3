Q9767: permit-order confusion in WETH unwrap helper when the first hop pays from the external caller while later hops pay from router-held balances

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/base/PeripheryPayments.sol::unwrapWETH9` with extensionData arrays that differ by hop or by liquidity operation while the first hop pays from the external caller while later hops pay from router-held balances, so that permit execution order lets the router spend a different allowance than the caller intended for the current swap along `unwrapWETH9 -> check router WETH balance -> withdraw WETH -> native ETH transfer to recipient`, corrupting router-held WETH, native ETH sent, and whether leftover balances from earlier public steps are isolated to the right user? This helper is public and balance-based, so any earlier step that strands WETH on the router can turn into a direct theft path if attribution is weak. Mix permit helpers with multicall and swap steps so allowance state differs from what the final payment path assumes.

Target
- File/function: metric-periphery/contracts/base/PeripheryPayments.sol::unwrapWETH9
- Entrypoint: metric-periphery/contracts/base/PeripheryPayments.sol::unwrapWETH9
- Attacker controls: extensionData arrays that differ by hop or by liquidity operation
- Exploit idea: Reach `unwrapWETH9 -> check router WETH balance -> withdraw WETH -> native ETH transfer to recipient` in a live public flow and show that mix permit helpers with multicall and swap steps so allowance state differs from what the final payment path assumes. The exact value at risk is router-held WETH, native ETH sent, and whether leftover balances from earlier public steps are isolated to the right user.
- Invariant to test: The router must only spend the allowance the current caller intentionally granted for the current transaction path. The concrete assertion should cover router-held WETH, native ETH sent, and whether leftover balances from earlier public steps are isolated to the right user.
- Expected Immunefi impact: High direct loss if a caller can be induced to spend more than the swap they authorized.
- Fast validation: Leave controlled WETH residue on the router across different public call sequences and assert only the rightful caller can unwrap or claim it.
