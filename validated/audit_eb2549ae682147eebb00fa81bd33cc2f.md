### Title
Escrow over-release in `IntrinsicIntents::_fillSameChain` due to un-differentiated `_orders[commitment][token]` lookup - (File: evm/src/apps/intentsv2/IntrinsicIntents.sol)

### Summary
### Finding Description
`_orders` is a mapping keyed only by `(commitment, token address)` [1](#0-0) . It is not keyed by the *index* of the input leg within `order.inputs[]`. Nothing in the visible order-validation logic (`_validateParams`, `placeOrder`) forbids an order from containing multiple `TokenInfo` entries in `order.inputs[]` that reference the same token address for different output legs (e.g. `inputs[0].token == inputs[1].token == USDC`, escrowing separate amounts for two different output pairs).

In `IntrinsicIntents::_fillSameChain`, when an individual output leg `i` reaches a full fill (`amountFilled == totalRequired`), the code releases escrow like this:

```solidity
if (amountFilled == totalRequired) {
    escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
``` [2](#0-1) 

Instead of computing the escrow amount belonging specifically to leg `i` (proportional/allocated share), it reads the **entire current balance** stored in `_orders[commitment][token]`. If a second, still-unfilled output leg `j` shares the same input token, that leg's escrow is aggregated into the very same `_orders[commitment][token]` bucket (since the mapping has no per-index dimension). The solver who fully fills only leg `i` therefore receives leg `j`'s escrow as well.

This is a direct analog of the `RiskOracle::_processUpdate` bug: a value that should be scoped by a full composite key (here, order index/leg) is instead read from a mapping keyed by only part of the relevant dimensions (commitment + token, omitting the leg), causing state belonging to one logical bucket to leak into another.

### Impact Explanation
This directly matches the bounty's "unauthorized transaction/execution" and "stealing or loss of funds" classes:
- A solver can fully satisfy one small output leg of a multi-leg order and receive the *combined* escrow of all legs sharing that input token, while other output legs remain unfilled (`isFullyFilled = false`, order stays open).
- Subsequent solvers or the user's cancel path (`_cancelSameChain`) then find `_orders[commitment][token]` already drained via `_withdraw`'s `escrowed == 0 → revert UnknownOrder()` check [3](#0-2) , permanently locking the user out of receiving their remaining output and preventing the true remaining escrow from ever being paid out or refunded.
- Net effect: user funds meant for one output leg are misappropriated by a solver settling a different, unrelated leg — funds move to the wrong beneficiary and in the wrong amount, with no mechanism for the affected user to recover them.

### Likelihood Explanation
No privileged actor, relayer, or prover is required — this is exploitable by any unprivileged solver calling the public `fillOrder` entrypoint with an order the attacker themselves (or the user, unwittingly) constructs with two output legs backed by the same input token. Since order construction/signing/placement does not appear to reject repeated input token addresses across legs, an attacker can deliberately place (or induce a user to place) such an order and then call `fillOrder` to trigger the over-release on the first small leg.

### Recommendation
Track escrow release per output-leg index (or track a per-leg-allocated escrow amount at `placeOrder` time, e.g. `_orders[commitment][i]` or `_orders[commitment][token][i]`) rather than reading the raw token-keyed running balance. On a full fill of leg `i`, only the amount originally allocated to `order.inputs[i]` should be released — not the entire remaining balance of `_orders[commitment][token]`. Alternatively, reject orders at placement time where two input legs share the same token address unless the escrow accounting explicitly supports pro-rata allocation per corresponding output leg.

### Proof of Concept
1. User places an order via `IntentGatewayV2.placeOrder` with:
   - `order.inputs = [ {token: USDC, amount: 600}, {token: USDC, amount: 400} ]`
   - `order.output.assets = [ {token: DAI, amount: 600}, {token: WETH, amount: 1} ]`
   
   Both USDC legs escrow into the same `_orders[commitment][USDC]` bucket, totalling 1000 USDC.
2. Solver calls `fillOrder` providing only `outputs[0] = {DAI, 600}` and `outputs[1] = {WETH, 0}` (or an amount too small to fill leg 1).
   - Leg 0 (`i=0`) reaches `amountFilled == totalRequired` (600 DAI fully filled).
   - `escrowedAmount = _orders[commitment][USDC]` reads the full 1000 USDC (both legs' escrow), not just the 600 USDC allocated to leg 0.
   - `_withdraw` releases the full 1000 USDC to the solver and decrements `_orders[commitment][USDC]` to 0.
3. `isFullyFilled` is `false` (leg 1/WETH unfilled), so the order stays open, but `_orders[commitment][USDC]` is now 0.
4. Any later solver attempting to complete leg 1, or the user attempting `_cancelSameChain`, hits `escrowed == 0 → revert UnknownOrder()` in `_withdraw` — the second leg's rightful escrow has already been paid out to the first solver and cannot be recovered.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L136-140)
```text
    /**
     * @dev Maps (commitment, token address) to the escrowed amount for that token.
     * Decremented as tokens are released via fills or refunds.
     */
    mapping(bytes32 => mapping(address => uint256)) public _orders;
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L399-401)
```text

            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L116-122)
```text
            uint256 escrowedAmount;
            if (amountFilled == totalRequired) {
                escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
            } else {
                escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
            }
            escrowedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: escrowedAmount});
```
