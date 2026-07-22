Q10763: extension-data propagation bug in permit helpers when the first hop pays from the external caller while later hops pay from router-held balances

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/base/SelfPermit.sol::*` with `zeroForOneBitMap`, `amountInMaximum`, and `amountOutMinimum` around exact-output recursion edges while the first hop pays from the external caller while later hops pay from router-held balances, so that per-hop or per-liquidity extension payloads are delivered to a different step than the caller intended along `public permit helper -> token permit -> allowance consumed by later router payment flow`, corrupting router allowance, payer identity, and whether permit execution order can make the router spend beyond what the user intended in the same transaction? The caller supplies both permit material and call ordering, so any stale-allowance or wrong-owner assumption is a public exploit surface. Mix different extension payloads across hops or liquidity calls and see whether the router/adder forwards them to the wrong protection boundary.

Target
- File/function: metric-periphery/contracts/base/SelfPermit.sol::{selfPermit,selfPermitIfNecessary,selfPermitAllowed,selfPermitAllowedIfNecessary}
- Entrypoint: metric-periphery/contracts/base/SelfPermit.sol::*
- Attacker controls: `zeroForOneBitMap`, `amountInMaximum`, and `amountOutMinimum` around exact-output recursion edges
- Exploit idea: Reach `public permit helper -> token permit -> allowance consumed by later router payment flow` in a live public flow and show that mix different extension payloads across hops or liquidity calls and see whether the router/adder forwards them to the wrong protection boundary. The exact value at risk is router allowance, payer identity, and whether permit execution order can make the router spend beyond what the user intended in the same transaction.
- Invariant to test: Each public step must deliver the exact extension payload intended for that step and no other. The concrete assertion should cover router allowance, payer identity, and whether permit execution order can make the router spend beyond what the user intended in the same transaction.
- Expected Immunefi impact: High if a guard or accounting extension can be bypassed through wrong payload routing.
- Fast validation: Compose permit plus swap plus sweep multicalls and assert the allowance consumed is exactly the one the current caller intended to grant in that transaction.
