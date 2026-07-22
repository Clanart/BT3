Q10677: probe-pay race in permit helpers when a quoter result is consumed after a small state-moving transaction but before the user notices

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/base/SelfPermit.sol::*` with permit payloads, allowance races, and stale approvals through `selfPermit*` while a quoter result is consumed after a small state-moving transaction but before the user notices, so that the liquidity-adder probe phase measures one state but the paid phase executes against another reachable state along `public permit helper -> token permit -> allowance consumed by later router payment flow`, corrupting router allowance, payer identity, and whether permit execution order can make the router spend beyond what the user intended in the same transaction? The caller supplies both permit material and call ordering, so any stale-allowance or wrong-owner assumption is a public exploit surface. Move the pool publicly between probe and payment so the scaled shares no longer correspond to the probed token requirements.

Target
- File/function: metric-periphery/contracts/base/SelfPermit.sol::{selfPermit,selfPermitIfNecessary,selfPermitAllowed,selfPermitAllowedIfNecessary}
- Entrypoint: metric-periphery/contracts/base/SelfPermit.sol::*
- Attacker controls: permit payloads, allowance races, and stale approvals through `selfPermit*`
- Exploit idea: Reach `public permit helper -> token permit -> allowance consumed by later router payment flow` in a live public flow and show that move the pool publicly between probe and payment so the scaled shares no longer correspond to the probed token requirements. The exact value at risk is router allowance, payer identity, and whether permit execution order can make the router spend beyond what the user intended in the same transaction.
- Invariant to test: Weighted liquidity add must either revalidate the probed assumptions or revert; it must never silently mint under a stale quote. The concrete assertion should cover router allowance, payer identity, and whether permit execution order can make the router spend beyond what the user intended in the same transaction.
- Expected Immunefi impact: Medium/High LP-principal loss or broken liquidity add functionality.
- Fast validation: Compose permit plus swap plus sweep multicalls and assert the allowance consumed is exactly the one the current caller intended to grant in that transaction.
