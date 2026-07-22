Q9860: probe-pay race in WETH unwrap helper when the router already holds leftover WETH or ERC20 from an earlier step in the same transaction

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/base/PeripheryPayments.sol::unwrapWETH9` with `msg.value` plus WETH input/output paths with partial native balance already on the router while the router already holds leftover WETH or ERC20 from an earlier step in the same transaction, so that the liquidity-adder probe phase measures one state but the paid phase executes against another reachable state along `unwrapWETH9 -> check router WETH balance -> withdraw WETH -> native ETH transfer to recipient`, corrupting router-held WETH, native ETH sent, and whether leftover balances from earlier public steps are isolated to the right user? This helper is public and balance-based, so any earlier step that strands WETH on the router can turn into a direct theft path if attribution is weak. Move the pool publicly between probe and payment so the scaled shares no longer correspond to the probed token requirements.

Target
- File/function: metric-periphery/contracts/base/PeripheryPayments.sol::unwrapWETH9
- Entrypoint: metric-periphery/contracts/base/PeripheryPayments.sol::unwrapWETH9
- Attacker controls: `msg.value` plus WETH input/output paths with partial native balance already on the router
- Exploit idea: Reach `unwrapWETH9 -> check router WETH balance -> withdraw WETH -> native ETH transfer to recipient` in a live public flow and show that move the pool publicly between probe and payment so the scaled shares no longer correspond to the probed token requirements. The exact value at risk is router-held WETH, native ETH sent, and whether leftover balances from earlier public steps are isolated to the right user.
- Invariant to test: Weighted liquidity add must either revalidate the probed assumptions or revert; it must never silently mint under a stale quote. The concrete assertion should cover router-held WETH, native ETH sent, and whether leftover balances from earlier public steps are isolated to the right user.
- Expected Immunefi impact: Medium/High LP-principal loss or broken liquidity add functionality.
- Fast validation: Leave controlled WETH residue on the router across different public call sequences and assert only the rightful caller can unwrap or claim it.
