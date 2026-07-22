Q10698: owner-salt misattribution in permit helpers when the router already holds leftover WETH or ERC20 from an earlier step in the same transaction

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/base/SelfPermit.sol::*` with multi-hop paths with repeated tokens or repeated pools in `exactInput` or `exactOutput` while the router already holds leftover WETH or ERC20 from an earlier step in the same transaction, so that the public liquidity-adder flow mints or burns value into a different owner/salt identity than the payer intended along `public permit helper -> token permit -> allowance consumed by later router payment flow`, corrupting router allowance, payer identity, and whether permit execution order can make the router spend beyond what the user intended in the same transaction? The caller supplies both permit material and call ordering, so any stale-allowance or wrong-owner assumption is a public exploit surface. Stress owner/payer separation and multicall ordering until callback payment and position ownership stop matching.

Target
- File/function: metric-periphery/contracts/base/SelfPermit.sol::{selfPermit,selfPermitIfNecessary,selfPermitAllowed,selfPermitAllowedIfNecessary}
- Entrypoint: metric-periphery/contracts/base/SelfPermit.sol::*
- Attacker controls: multi-hop paths with repeated tokens or repeated pools in `exactInput` or `exactOutput`
- Exploit idea: Reach `public permit helper -> token permit -> allowance consumed by later router payment flow` in a live public flow and show that stress owner/payer separation and multicall ordering until callback payment and position ownership stop matching. The exact value at risk is router allowance, payer identity, and whether permit execution order can make the router spend beyond what the user intended in the same transaction.
- Invariant to test: Every paid liquidity action must mint value only into the exact owner/salt position encoded in the public request. The concrete assertion should cover router allowance, payer identity, and whether permit execution order can make the router spend beyond what the user intended in the same transaction.
- Expected Immunefi impact: High direct loss if user-paid tokens can be minted into another position key.
- Fast validation: Compose permit plus swap plus sweep multicalls and assert the allowance consumed is exactly the one the current caller intended to grant in that transaction.
