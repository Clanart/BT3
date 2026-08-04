## Analysis Summary

The core broken invariant in the original report is: **an irreversible local action is taken (bonding tokens) before the corresponding completion check is guaranteed to pass, and once that check fails there is no alternate path back to a refundable state** — the bonder is stuck and can even be punished (burned) for it.

I traced the closest structural analog in Hyperbridge's `IntentGatewayV2` cross-chain order-cancellation flow, where a similarly irreversible local state transition is committed on the destination chain *before* the corresponding cross-chain settlement is guaranteed to complete, and the only fallback route is permanently foreclosed by that same local transition.

### Title
Destination-side order cancellation permanently locks escrow and forecloses the only fallback recovery path if the `RefundEscrow` message never completes - (File: evm/src/apps/intentsv2/ExtrinsicIntents.sol / evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
### Finding Description
`_cancelFromDest` marks the order as terminated locally — `_filled[commitment] = address(uint160(uint256(order.user)))` — immediately, before the cross-chain `RefundEscrow` POST message that actually releases the source-chain escrow is confirmed delivered: [1](#0-0) 

This message is dispatched with `timeout: 0`, i.e. it can never time out: [2](#0-1) 

Docs confirm this is deliberate protocol behavior for `timeout: 0` — "Messages will never expire": [3](#0-2) [4](#0-3) 

The only alternate recovery route for a stuck cross-chain order is the source-side cancellation path, `_cancelFromSource`, which issues a `DispatchGet` to read the destination's `_filled` storage slot and only refunds if that slot is provably empty; if the queried slot is non-empty it reverts permanently with `Filled()`: [5](#0-4) [6](#0-5) 

But `_cancelFromDest` already wrote a non-zero value into that exact `_filled` slot as its very first action — regardless of whether the `RefundEscrow` message it dispatches ever successfully executes `onAccept`/`withdraw` on the source chain. This mirrors the Auction.sol pattern precisely: `bondForRebalance()` commits an irreversible state change (the bond / here, the `_filled` mark) before the corresponding completion condition (`newRatio >= minIbRatio` / here, delivery+acceptance of `RefundEscrow`) is verified to hold, and once that condition fails there is no alternate path to reclaim funds — `settleAuction()` always reverts just as `_cancelFromSource`'s `onGetResponse` will always revert with `Filled()` for that commitment from that point forward, since the slot is permanently non-empty.

### Impact Explanation
If the `RefundEscrow` POST request is dispatched but its `onAccept` execution on the source chain does not successfully complete (e.g., decode/authentication failure, gateway instance not yet registered for that chain via `_instance()`, or any other deterministic revert condition in `onAccept`/`_withdraw` unrelated to relayer honesty), the source-chain escrow can never be released:
- The user cannot retry `_cancelFromDest` (blocked by `Filled()` in `cancelOrder`'s top-level check since `_filled[commitment] != address(0)`).
- The user cannot fall back to `_cancelFromSource` because the destination storage slot it queries is now permanently non-empty, so `onGetResponse` reverts with `Filled()` forever.
- Because the request was dispatched with `timeout: 0`, it never times out, so no timeout-based refund/rollback of the escrow-locking state is ever possible either.

The escrowed input tokens on the source chain are permanently locked with no recovery mechanism — a direct loss/lock of user funds, matching "stealing or loss of funds" and "logic attacks" in the bounty's impact gate.

### Likelihood Explanation
This requires no malicious peer, relayer, or prover — it is triggered purely by the deterministic ordering of state commits in `_cancelFromDest` versus the guaranteed success of the corresponding cross-chain message execution, exactly like the original Auction.sol bug where the irrecoverable state was set before the completion check was proven to hold. I was not able to fully confirm from the indexed code every deterministic condition under which `onAccept`'s `RefundEscrow` branch could revert on the source chain (e.g., the full body of `_authenticate`/instance registration edge cases), since parts of `IntentsBase.sol`/`ExtrinsicIntents.sol` beyond what I could inspect may contain additional guards. This should be verified directly against the full source before treating likelihood as fully proven.

### Recommendation
Do not write `_filled[commitment] = user` on the destination chain until receipt-confirmation semantics guarantee eventual delivery, or add a fallback recovery entrypoint (analogous to a request-timeout callback) that can un-mark `_filled` and re-enable `_cancelFromSource`/`_cancelFromDest` retry if the `RefundEscrow` message is provably never going to complete. Alternatively, dispatch `RefundEscrow` with a non-zero timeout and implement `onPostRequestTimeout` to revert the local `_filled` mark, restoring the order to a cancellable state — mirroring the Auction fix of moving the irrecoverable check earlier in the flow so failure can never leave funds permanently stuck.

### Proof of Concept
1. User calls `cancelOrder` from the destination chain after the deadline (or as order owner before it) → `_cancelFromDest` executes, sets `_filled[commitment] = user`, and dispatches `RefundEscrow` with `timeout: 0`. [7](#0-6) 
2. Assume the `RefundEscrow` message is delivered to the source chain (via a legitimate relayer with a valid proof) but `onAccept` reverts deterministically for any reason unrelated to proof validity (e.g., misconfigured/unregistered gateway instance, decode mismatch).
3. Source-chain escrow for `commitment` remains locked in `_orders`.
4. User attempts `_cancelFromSource` as the only remaining recovery path; the dispatched `DispatchGet` reads the destination `_filled[commitment]` slot, finds it non-empty (set in step 1), and `onGetResponse` reverts with `Filled()` permanently. [6](#0-5) 
5. Because `timeout: 0` means the `RefundEscrow` request can never time out, there is no timeout callback to reset state either. The escrow is permanently locked.

### Citations

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L188-223)
```text
    function _cancelFromSource(Order calldata order, CancelOptions calldata options, bytes32 commitment) internal {
        if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();

        if (options.height <= order.deadline) revert NotExpired();

        uint256 inputsLen = order.inputs.length;
        for (uint256 i; i < inputsLen;) {
            if (_orders[commitment][address(uint160(uint256(order.inputs[i].token)))] == 0) revert UnknownOrder();

            unchecked {
                ++i;
            }
        }

        bytes memory context =
            abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}));

        bytes[] memory keys = new bytes[](1);
        keys[0] = bytes.concat(abi.encodePacked(_instance(order.destination)), _calculateCommitmentSlotHash(commitment));
        DispatchGet memory request = DispatchGet({
            dest: order.destination,
            keys: keys,
            timeout: 0,
            height: options.height,
            fee: options.relayerFee,
            context: context,
            payer: msg.sender
        });

        address hostAddr = host();
        if (msg.value > 0) {
            IDispatcher(hostAddr).dispatch{value: msg.value}(request);
        } else {
            dispatchWithFeeToken(request);
        }
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L240-267)
```text
    function _cancelFromDest(Order calldata order, CancelOptions calldata options, bytes32 commitment) internal {
        if (order.deadline >= _blockNumber()) {
            if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();
        }

        _filled[commitment] = address(uint160(uint256(order.user)));

        bytes memory body = bytes.concat(
            bytes1(uint8(RequestKind.RefundEscrow)),
            abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}))
        );

        DispatchPost memory request = DispatchPost({
            dest: order.source,
            to: abi.encodePacked(_instance(order.source)),
            body: body,
            timeout: 0,
            fee: options.relayerFee,
            payer: msg.sender
        });

        address hostAddr = host();
        if (msg.value > 0) {
            IDispatcher(hostAddr).dispatch{value: msg.value}(request);
        } else {
            dispatchWithFeeToken(request);
        }
    }
```

**File:** docs/content/developers/network/collator.mdx (L1-1)
```text
---
```

**File:** docs/content/developers/evm/messaging/post-requests.mdx (L44-44)
```text
| `timeout` | Time in seconds for message validity eg 3600 for a timeout of 1 hour, or 0 for no timeout. ie Messages will never expire. If the timeout is set to a non-zero value, messages that have exceeded this timeout will be rejected on the destination and require user action (timeout message) to revert changes. |
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L729-735)
```text
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        withdraw(body, true);
    }
}
```
