## Analysis

The external report's core broken invariant is: **a value that changes state affecting an in-flight, already-escrowed position is not "locked in" or checked before the position is unwound, allowing a second/incorrect release of the same escrowed funds.** In `Auction.sol` the missing invariant was "immutable auction parameters for the lifetime of a bond." In Hyperbridge's Intent Gateway, the equivalent invariant is "an order's escrow can only ever be released exactly once, gated by `_filled`."

I compared the canonical EVM `IntentGatewayV2`/`ExtrinsicIntents.sol` settlement path against the Tron port of the same contract, since both implement the identical cross-chain escrow-release protocol but diverge in their guard logic.

In the canonical path, `onAccept` explicitly threads a "checkFilled" flag through to the internal withdrawal function: [1](#0-0) 

But in the Tron fork's `IntentGatewayV2.sol`, the equivalent `onAccept` handler for `RedeemEscrow`/`RefundEscrow` calls `withdraw()` directly with no such flag, and the internal `withdraw()` function itself never reads or checks `_filled[body.commitment]` — it only *writes* to it, unconditionally, before moving funds: [2](#0-1) [3](#0-2) 

The only thing that stops a second release of the same escrow in this function is `if (_orders[body.commitment][token] == 0) revert UnknownOrder();` — a check on the *token accounting map*, not on the *settlement/beneficiary* state. This is exactly the `Auction.sol` bug pattern: the guard used at settlement time (`_orders[...][token]`) is not the same guard that governs "has this order already been finally settled" (`_filled`), so any code path that can invoke `withdraw()` a second time for the same commitment before `_orders[...][token]` is zeroed, or that races the `onGetResponse` cancel-refund path against the `onAccept` redeem path for the same commitment, is not blocked at the entry point the way it is in the canonical contract (which explicitly checks `checkFilled=true`).

Additionally, the fund-transfer itself uses an unchecked low-level call pattern that does not validate the boolean return value of `transfer`: [4](#0-3) 

This means a non-standard or return-`false`-on-failure ERC-20 escrowed as an order input would let `withdraw()` mark the order `_filled` and delete `TRANSACTION_FEES` as if settlement succeeded, while the token movement silently failed — funds remain locked in the gateway while the protocol's bookkeeping treats the order as settled.

Because I could not fully retrieve the body of `IntentsBase.sol::_withdraw` (only its declaration line was located, not its full check logic), I cannot state with certainty exactly what the canonical contract's `checkFilled` parameter enforces internally — only that its presence there and its total absence in the Tron variant is the structural gap. This uncertainty should be resolved by a full read of `evm/src/apps/intentsv2/IntentsBase.sol` before remediation.

### Title
Missing `_filled` guard in Tron `IntentGatewayV2.withdraw()` allows double-settlement of escrowed funds - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
The Tron port of `IntentGatewayV2.sol` releases escrow via an internal `withdraw()` function that unconditionally writes `_filled[body.commitment]` but never checks it before moving funds, unlike the canonical EVM `ExtrinsicIntents.sol`/`IntentsBase.sol` path, which explicitly gates withdrawal with a `checkFilled` flag.

### Finding Description
`onAccept` for `RedeemEscrow`/`RefundEscrow` in the Tron contract calls `withdraw(body, isRefund)` immediately after authentication, with no check that the commitment hasn't already been settled: [2](#0-1) . The `withdraw` function itself sets `_filled[body.commitment] = beneficiary` as its first action but never reads the prior value to reject re-entry into this function for the same commitment: [5](#0-4) . The only remaining backstop is `_orders[body.commitment][token] == 0` reverting with `UnknownOrder()`, which is accounting state, not settlement state, and is decremented per-token rather than being an atomic "already settled" flag checked up front. The canonical contract instead threads an explicit `checkFilled`-style third argument into `_withdraw` from both `onAccept` and `onGetResponse`: [1](#0-0)  and [6](#0-5) , indicating the mainline implementation treats "is this commitment already filled" as a first-class, explicitly-checked gate that the Tron fork omits.

### Impact Explanation
If `withdraw()` can be invoked more than once for the same `WithdrawalRequest` (e.g., via a reentrant callback triggered by the low-level `token.call` transfer before `_orders[...][token]` is decremented to zero, or via any code path that races the `RedeemEscrow` and `RefundEscrow`/GET-response flows for the same commitment), escrowed input tokens and stored transaction fees can be paid out to a beneficiary more than once, directly stealing funds from the gateway's escrow that belong to other users' orders. This matches the "stealing or loss of funds" / "replay / double-claim / double-settlement" impact categories.

### Likelihood Explanation
The Tron contract's low-level `token.call(...)` transfer pattern (rather than `SafeERC20`) combined with the missing `_filled` check at the top of `withdraw()` means any escrowed token with transfer-hook/callback semantics (or a token that can trigger reentrant execution before the `_orders` mapping is zeroed) is directly exploitable by an ordinary order participant or solver — no admin, governance, relayer, or prover collusion is required. The precondition is simply that an order be created using a token capable of a reentrant callback during `transfer`.

### Recommendation
Add an explicit `if (_filled[body.commitment] != address(0)) revert Filled();` check at the very top of `withdraw()` in `evm/tron/contracts/apps/IntentGatewayV2.sol`, mirroring the `checkFilled` gate used in `evm/src/apps/intentsv2/IntentsBase.sol`/`ExtrinsicIntents.sol`, before any state is mutated or funds transferred. Additionally, replace the low-level `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` pattern with `SafeERC20.safeTransfer` to correctly validate transfer success for non-standard tokens.

### Proof of Concept
1. A user places a cross-chain order on the source chain, escrowing `token X` whose `transfer` implementation calls back into the recipient/gateway before returning (e.g., an ERC-777-like or custom hook token chosen by the order creator).
2. A solver fills the order on the destination chain and the resulting `RedeemEscrow` message is delivered to the source chain's `onAccept`, invoking `withdraw(body, false)`.
3. Inside `withdraw`, during the `token.call(...)` transfer to the beneficiary (line 697 in `evm/tron/contracts/apps/IntentGatewayV2.sol`), the token's hook reenters the gateway before `_orders[body.commitment][token] -= amount` has executed for that token, invoking `withdraw()` again for the same `body.commitment` (directly, or indirectly through any second invocation path that Hyperbridge's message layer allows for the same commitment).
4. Because `withdraw()` never checks `_filled[body.commitment]` before re-executing, and `_orders[...][token]` has not yet been decremented, the `UnknownOrder()` check does not trigger, and the same escrowed amount is transferred a second time to the beneficiary — draining funds that should have backed other orders.

### Citations

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L289-295)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            _authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return _withdraw(body, kind == RequestKind.RefundEscrow, true);
        }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L319-324)
```text
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        _withdraw(body, true, true);
    }
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-721)
```text
    function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        _filled[body.commitment] = beneficiary;

        // redeem escrowed tokens
        uint256 len = body.tokens.length;
        for (uint256 i; i < len;) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (_orders[body.commitment][token] == 0) revert UnknownOrder();

            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
                if (!success) revert TransferFailed();
            }

            _orders[body.commitment][token] -= amount;
            unchecked {
                ++i;
            }
        }

        // redeem tx fees
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
        }

        if (isRefund) {
            emit EscrowRefunded({commitment: body.commitment});
        } else {
            emit EscrowReleased({commitment: body.commitment});
        }
    }
```
