Q9889: owner-salt misattribution in WETH unwrap helper when an exact-output path recurses through a thin intermediate pool

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/base/PeripheryPayments.sol::unwrapWETH9` with `multicall` ordering that mixes permit, swap, unwrap, sweep, and refund steps while an exact-output path recurses through a thin intermediate pool, so that the public liquidity-adder flow mints or burns value into a different owner/salt identity than the payer intended along `unwrapWETH9 -> check router WETH balance -> withdraw WETH -> native ETH transfer to recipient`, corrupting router-held WETH, native ETH sent, and whether leftover balances from earlier public steps are isolated to the right user? This helper is public and balance-based, so any earlier step that strands WETH on the router can turn into a direct theft path if attribution is weak. Stress owner/payer separation and multicall ordering until callback payment and position ownership stop matching.

Target
- File/function: metric-periphery/contracts/base/PeripheryPayments.sol::unwrapWETH9
- Entrypoint: metric-periphery/contracts/base/PeripheryPayments.sol::unwrapWETH9
- Attacker controls: `multicall` ordering that mixes permit, swap, unwrap, sweep, and refund steps
- Exploit idea: Reach `unwrapWETH9 -> check router WETH balance -> withdraw WETH -> native ETH transfer to recipient` in a live public flow and show that stress owner/payer separation and multicall ordering until callback payment and position ownership stop matching. The exact value at risk is router-held WETH, native ETH sent, and whether leftover balances from earlier public steps are isolated to the right user.
- Invariant to test: Every paid liquidity action must mint value only into the exact owner/salt position encoded in the public request. The concrete assertion should cover router-held WETH, native ETH sent, and whether leftover balances from earlier public steps are isolated to the right user.
- Expected Immunefi impact: High direct loss if user-paid tokens can be minted into another position key.
- Fast validation: Leave controlled WETH residue on the router across different public call sequences and assert only the rightful caller can unwrap or claim it.
