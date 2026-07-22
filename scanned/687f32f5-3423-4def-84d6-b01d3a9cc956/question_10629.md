Q10629: WETH-native double counting in permit helpers when a weighted liquidity add uses cursor bounds that hug the active bin

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/base/SelfPermit.sol::*` with permit payloads, allowance races, and stale approvals through `selfPermit*` while a weighted liquidity add uses cursor bounds that hug the active bin, so that public payment helpers treat existing native ETH and WETH balances as if they belong to the same user step along `public permit helper -> token permit -> allowance consumed by later router payment flow`, corrupting router allowance, payer identity, and whether permit execution order can make the router spend beyond what the user intended in the same transaction? The caller supplies both permit material and call ordering, so any stale-allowance or wrong-owner assumption is a public exploit surface. Use `msg.value` plus router-held native or WETH residue to see whether a later path receives value twice or from the wrong payer.

Target
- File/function: metric-periphery/contracts/base/SelfPermit.sol::{selfPermit,selfPermitIfNecessary,selfPermitAllowed,selfPermitAllowedIfNecessary}
- Entrypoint: metric-periphery/contracts/base/SelfPermit.sol::*
- Attacker controls: permit payloads, allowance races, and stale approvals through `selfPermit*`
- Exploit idea: Reach `public permit helper -> token permit -> allowance consumed by later router payment flow` in a live public flow and show that use `msg.value` plus router-held native or weth residue to see whether a later path receives value twice or from the wrong payer. The exact value at risk is router allowance, payer identity, and whether permit execution order can make the router spend beyond what the user intended in the same transaction.
- Invariant to test: Native ETH, WETH deposits, and ERC20 pull settlement must remain attributable to one exact public payment obligation. The concrete assertion should cover router allowance, payer identity, and whether permit execution order can make the router spend beyond what the user intended in the same transaction.
- Expected Immunefi impact: High direct loss or stranded value above contest thresholds.
- Fast validation: Compose permit plus swap plus sweep multicalls and assert the allowance consumed is exactly the one the current caller intended to grant in that transaction.
