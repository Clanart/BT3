Based on the code I reviewed, the strongest local analog is in `IntentGatewayV2.sol`'s `cancelOrder()` destination-chain branch.

### Title
`cancelOrder`: unauthorized permissionless cancellation before expiry check ordering allows forced-cancel path on destination chain - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
Unlike the source-chain cancellation branches, which strictly require `order.user == msg.sender`, the destination-chain branch of `cancelOrder` only checks ownership *conditionally*, gated on `order.deadline >= block.number`:

```solidity
} else if (currentChain == orderDest) {
    // destination chain: dispatch RefundEscrow request to source chain
    // If order hasn't expired, only owner can cancel
    if (order.deadline >= block.number) {
        if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();
    }

    // Mark as cancelled locally to prevent fills
    _filled[commitment] = address(uint160(uint256(order.user)));
    ...
``` [1](#0-0) 

### Finding Description
Once `block.number > order.deadline`, the ownership check is skipped entirely and **any address** can call `cancelOrder` for **any other user's order** on the destination chain. This permissionlessly marks `_filled[commitment] = order.user` and dispatches a `RefundEscrow` request back to the source chain to release the user's escrowed inputs. Compare this to the `removeLiquidity` bug pattern in the external report: an unprivileged caller triggers state-changing custody logic keyed to another user's identity without that user's consent or signature. Because `order.user` is embedded in the order struct (part of the commitment) rather than derived from the caller, the destination-side branch effectively lets a third party unilaterally decide *when* another user's order is cancelled and force the refund flow to execute, taking away the order owner's exclusive control over that decision — even though the eventual funds still land back with `order.user` as beneficiary.

### Impact Explanation
While the beneficiary of the refund (`order.user`) is fixed by the order struct itself — so funds cannot be *stolen* to an attacker's address in this branch — an unauthorized third party can force cancellation/refund of a pending order that the legitimate user or solver may still be racing to fill. This can be used to grief solvers who have already committed capital or executed a fill-path against the destination escrow: a malicious actor observing the mempool near `order.deadline` can preemptively fire `cancelOrder` for someone else's order, marking `_filled[commitment]` and short-circuiting the intended fill, before the rightful owner or a solver acts. This is a logic/transaction-manipulation issue on the destination custody path (`_filled` state + `RefundEscrow` dispatch), matching the "logic attacks" / "unauthorized transaction or execution" category in the bounty scope, though impact is bounded because funds go to the correct beneficiary.

### Likelihood Explanation
Likelihood is high for any order that has passed its deadline: the check is fully absent (no signature, no `msg.sender` restriction at all) once `block.number > order.deadline`, so any address watching for expired orders can trigger this at will with no special access, matching the report's "Low impact / High likelihood" profile.

### Recommendation
Restrict `cancelOrder`'s destination-chain branch consistently with the other two branches: require `order.user == msg.sender` unconditionally, or if permissionless cancellation-after-expiry is an intended design (to unblock stuck escrows), explicitly document and design it so beneficiary/side effects cannot be reordered or front-run in ways that damage solvers' in-flight fills — e.g., require the query/refund dispatch to be a no-op if a fill receipt already exists at the moment of the call, and emit clear on-chain intent so that only the account controlling `order.user`, or an already-verified proof of non-fill, can be the trigger.

### Proof of Concept
1. User `U` creates an order with `order.destination` = chain B, `order.deadline = D`.
2. Order sits unfilled past block `D` because the solver was slow or gas conditions changed.
3. Attacker `A` (unrelated to the order) calls `cancelOrder(order, options)` on chain B directly — `order.deadline >= block.number` is now false, so the `Unauthorized()` check at [2](#0-1)  is skipped entirely.
4. `_filled[commitment]` is set to `order.user`'s address [3](#0-2) , and a `RefundEscrow` dispatch to the source chain is fired by the attacker without the user's consent, even if the user or a solver intended to still fill/settle the order through another path.

**Uncertainty note:** I could not fully trace whether a solver's fill on the destination side checks `_filled[commitment]` before or after this cancellation write in a way that could cause a race resulting in fund loss (as opposed to griefing); confirming an actual loss-of-funds path (versus pure griefing) would require reading the full fill-order function body, which I was not able to retrieve within the available iterations.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L578-600)
```text
        } else if (currentChain == orderDest) {
            // destination chain: dispatch RefundEscrow request to source chain
            // If order hasn't expired, only owner can cancel
            if (order.deadline >= block.number) {
                if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();
            }

            // Mark as cancelled locally to prevent fills
            _filled[commitment] = address(uint160(uint256(order.user)));

            bytes memory body = bytes.concat(
                bytes1(uint8(RequestKind.RefundEscrow)),
                abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}))
            );

            DispatchPost memory request = DispatchPost({
                dest: order.source,
                to: abi.encodePacked(instance(order.source)),
                body: body,
                timeout: 0,
                fee: options.relayerFee,
                payer: msg.sender
            });
```
