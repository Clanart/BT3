Q9824: WETH-native double counting in WETH unwrap helper when the router already holds leftover WETH or ERC20 from an earlier step in the same transaction

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/base/PeripheryPayments.sol::unwrapWETH9` with quote or depth reads taken immediately before the user executes the live trade while the router already holds leftover WETH or ERC20 from an earlier step in the same transaction, so that public payment helpers treat existing native ETH and WETH balances as if they belong to the same user step along `unwrapWETH9 -> check router WETH balance -> withdraw WETH -> native ETH transfer to recipient`, corrupting router-held WETH, native ETH sent, and whether leftover balances from earlier public steps are isolated to the right user? This helper is public and balance-based, so any earlier step that strands WETH on the router can turn into a direct theft path if attribution is weak. Use `msg.value` plus router-held native or WETH residue to see whether a later path receives value twice or from the wrong payer.

Target
- File/function: metric-periphery/contracts/base/PeripheryPayments.sol::unwrapWETH9
- Entrypoint: metric-periphery/contracts/base/PeripheryPayments.sol::unwrapWETH9
- Attacker controls: quote or depth reads taken immediately before the user executes the live trade
- Exploit idea: Reach `unwrapWETH9 -> check router WETH balance -> withdraw WETH -> native ETH transfer to recipient` in a live public flow and show that use `msg.value` plus router-held native or weth residue to see whether a later path receives value twice or from the wrong payer. The exact value at risk is router-held WETH, native ETH sent, and whether leftover balances from earlier public steps are isolated to the right user.
- Invariant to test: Native ETH, WETH deposits, and ERC20 pull settlement must remain attributable to one exact public payment obligation. The concrete assertion should cover router-held WETH, native ETH sent, and whether leftover balances from earlier public steps are isolated to the right user.
- Expected Immunefi impact: High direct loss or stranded value above contest thresholds.
- Fast validation: Leave controlled WETH residue on the router across different public call sequences and assert only the rightful caller can unwrap or claim it.
