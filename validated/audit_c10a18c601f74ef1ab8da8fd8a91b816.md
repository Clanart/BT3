## Title
`withdraw()` in the Intent Gateway underflows and permanently reverts settlement when the withdrawal amount exceeds the tracked per-token escrow - ([File: evm/src/apps/intentsv2/IntentsBase.sol])

### Summary
`IntentsBase.withdraw()` (used by both the EVM `IntentGatewayV2` and the Tron `IntentGatewayV2.sol` fork) decrements a per-`(commitment, token)` escrow counter with `_orders[commitment][token] = escrowed - amount` (Solidity checked arithmetic), guarded only by `if (escrowed == 0) revert UnknownOrder()`. There is no check that `escrowed >= amount`. Any code path that can cause `amount` (from the cross-chain `WithdrawalRequest.tokens[]`) to exceed the currently tracked `escrowed` value causes the subtraction to underflow and revert — this is the exact analog of the reported `_settleStrike()` underflow: a required arithmetic step reverts because the precondition (`escrowed >= amount`) is never validated before the subtraction.

### Finding Description
`withdraw()` in `evm/src/apps/intentsv2/IntentsBase.sol`:
```solidity
uint256 escrowed = _orders[body.commitment][token];
if (escrowed == 0) revert UnknownOrder();
_orders[body.commitment][token] = escrowed - amount;
``` [1](#0-0) 

The Tron fork has the identical pattern:
```solidity
if (_orders[body.commitment][token] == 0) revert UnknownOrder();
...
_orders[body.commitment][token] -= amount;
``` [2](#0-1) 

The check only guards against a fully-unknown/zero-escrow entry; it never verifies `escrowed >= amount`. `withdraw()` is invoked from `onAccept` for both `RedeemEscrow` (fill settlement) and `RefundEscrow` (cancellation) message kinds, and from `onGetResponse` for source-side cancellation: [3](#0-2) [4](#0-3) 

Since `withdraw()` reverts the entire call rather than partially settling, any state where `amount > escrowed` for a given token entry — for example a partial fill already having drawn down `_orders[commitment][token]`, followed by a second settlement/cancellation message (e.g. a race between fill-settlement and destination-side cancellation, or a duplicate/late `RefundEscrow` after a `RedeemEscrow` already reduced the balance) — makes the withdrawal transaction permanently revert. Because the escrow counter is per-token and mutates on every successful withdraw, once one path (fill or cancel) consumes the escrow, the other path's message (already dispatched and in flight over Hyperbridge) will always underflow-revert when it eventually lands, since there is no way to reduce `amount` to match the remaining `escrowed`.

### Impact Explanation
This falls under the bounty's "transaction manipulation / logic attacks" and potential fund-lock category: a legitimately dispatched settlement or refund message that arrives after the escrow has already been (partially) drained by a race between fill and cancellation becomes permanently unprocessable — the ISMP message keeps reverting on delivery, and the corresponding tokens can become stuck in the gateway's per-commitment/per-token accounting (the `_orders` mapping entry never reaches zero for that token, since the decrement always reverts). This can be used to grief settlement of intents by winning a race between `RedeemEscrow` and `RefundEscrow` dispatches, since both paths write to the same counter and neither validates against the other before dispatch.

### Likelihood Explanation
Cross-chain cancel-from-destination is explicitly allowed to race against fill: "Destination gateway locks the order against fills and dispatches a RefundEscrow POST message to the source" while a same-block or in-flight `RedeemEscrow` may already be traveling from a prior fill. Because both dispatch paths are triggered by ordinary, permissionless user/solver actions (not requiring any privileged actor), and Hyperbridge's asynchronous relay model makes in-flight message ordering non-deterministic, this underflow condition is reachable without any relayer, prover, or admin compromise — matching the audit's finding that the guard is insufficient and the revert is deterministic once the precondition is violated.

### Recommendation
In `withdraw()`, validate `escrowed >= amount` before subtracting, and either revert with a dedicated `InsufficientEscrow` error (rather than a generic underflow panic) or clamp the transferred/decremented amount to `min(escrowed, amount)` so a stale or racing settlement message cannot leave funds permanently stuck. Additionally, consider making `RedeemEscrow`/`RefundEscrow` mutually exclusive at the commitment level (e.g., checking `_filled[commitment]` before allowing the second message to attempt a withdraw) so only one settlement path can ever act on a given commitment's escrow.

### Proof of Concept
1. Solver fills a cross-chain order on the destination chain; destination gateway dispatches a `RedeemEscrow` `WithdrawalRequest` back to source with `tokens[i].amount = order.inputs[i].amount`.
2. Before that message is delivered/executed on the source chain, the order's deadline passes (or the user races the cancel window) and destination-side `cancelOrder()` is called, dispatching a `RefundEscrow` `WithdrawalRequest` for the same commitment/tokens.
3. Whichever message lands first on the source `IntentGatewayV2`/`ExtrinsicIntents` calls `withdraw()`, decrementing `_orders[commitment][token]` from `order.inputs[i].amount` to `0`.
4. The second message (same commitment, same token, same `amount`) now hits `withdraw()` with `escrowed == 0`, which is caught by `if (escrowed == 0) revert UnknownOrder()` — but for tokens with an *unequal* prior partial reduction (e.g., a partial fill scenario, or multiple tokens per order where only some are consumed by the first message), `escrowed` can be non-zero but smaller than `amount`, causing `escrowed - amount` to underflow and revert the second message every time it is retried, permanently stranding those funds in the gateway.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L400-403)
```text
            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L620-626)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return withdraw(body, kind == RequestKind.RefundEscrow);
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L691-701)
```text
            if (_orders[body.commitment][token] == 0) revert UnknownOrder();

            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
                if (!success) revert TransferFailed();
            }

            _orders[body.commitment][token] -= amount;
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L729-734)
```text
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        withdraw(body, true);
    }
```
