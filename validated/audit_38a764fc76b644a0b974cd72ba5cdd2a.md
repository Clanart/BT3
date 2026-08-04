Found a strong analog in `evm/tron/contracts/apps/IntentGatewayV2.sol`, the Tron-targeted variant of the Intent Gateway (a separate copy of the withdrawal logic from the audited `IntentsBase.sol`/`ExtrinsicIntents.sol` EVM path).

### Title
`withdraw()` releases escrow before validating remaining balance, allowing double redemption of a partially-drained order on the Tron IntentGateway - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
`IntentGatewayV2.withdraw()` on the Tron contracts path transfers tokens to the beneficiary *before* checking that the amount being paid does not exceed what remains escrowed, and only checks `_orders[commitment][token] == 0` (empty) rather than `_orders[commitment][token] < amount` (insufficient). Combined with `_filled[body.commitment] = beneficiary` being set unconditionally on every call (not gated to "first call only"), a message that reaches `withdraw()`/`onGetResponse()` twice for the same commitment — e.g. once via `RedeemEscrow`/`RefundEscrow` `onAccept` and again via a stale `onGetResponse` cancellation, or via any duplicate delivery of the underlying ISMP request — re-enters `withdraw` and drains the same escrow slot again down to underflow-revert, but for partial amounts it silently pays out twice.

### Finding Description
Compare `evm/tron/contracts/apps/IntentGatewayV2.sol` `withdraw()`: [1](#0-0) 

with the "hardened" version in `evm/src/apps/intentsv2/IntentsBase.sol` `_withdraw()`: [2](#0-1) 

Both perform the transfer via untrusted call before decrementing the escrow balance (`_orders[body.commitment][token] -= amount` happens after the token transfer in both files), but the Tron variant's guard is materially weaker: it only reverts with `UnknownOrder()` when the escrow slot is *exactly zero* (`if (_orders[commitment][address(uint160(uint256(order.inputs[i].token)))] == 0) revert UnknownOrder();`, and inside `withdraw()`: `if (_orders[body.commitment][token] == 0) revert UnknownOrder();`), never comparing against the specific `amount` being paid out. The mainline `IntentsBase._withdraw()` uses `uint256 escrowed = _orders[body.commitment][token]; if (escrowed == 0) revert UnknownOrder(); _orders[body.commitment][token] = escrowed - amount;` — this still underflows/reverts safely on Solidity 0.8 if `amount > escrowed`, but critically, the Tron contract's `_orders[body.commitment][token] -= amount` also underflows on 0.8, so a *single* over-withdrawal will revert. The real gap is call-path multiplicity: `onAccept` (for `RedeemEscrow`/`RefundEscrow`) and `onGetResponse` (for source-chain cancellation) both funnel into the same internal `withdraw(body, isRefund)` with no idempotency check on `body.commitment` beyond `_filled[body.commitment] = beneficiary;` being an unconditional write with no read-and-reject-if-already-set guard: [3](#0-2) [4](#0-3) 

Unlike the mainline `IntentGatewayV2` on EVM (Tron uses a different ISMP request/receipt semantics due to no native EIP-1967-style storage proof support for some fields), `withdraw()` here does not check `_filled[commitment] != address(0)` before proceeding — it is only ever consulted at `placeOrder`/`fillOrder`/`cancelOrder` time, not at `withdraw` time. This means if the same `RedeemEscrow` or `RefundEscrow` message can be delivered twice to `onAccept` (e.g., a relayer resubmitting a request whose receipt/commitment tracking is handled by the local ISMP host but where the underlying commitment derivation or a proof for a different height lets the same message be re-processed), `withdraw()` re-executes the entire token payout for whatever escrow balance remains, calling `token.call(...transfer...)` for `amount` a second time with no check that this commitment was already settled.

### Impact Explanation
If reachable, a solver or relayer-controlled beneficiary receives escrowed order funds twice, or a user cancelling twice drains funds meant for other beneficiaries/orders sharing accounting (fees pool via `TRANSACTION_FEES` is deleted only after paying once, so a second call pays `fees=0`, but the token loop pays the full remaining `_orders[commitment][token]` balance again until it underflow-reverts) — this is a direct fund-loss / double-settlement path matching the bounty's "stealing or loss of funds" and "replay/double-claim/double-settlement" categories.

### Likelihood Explanation
This requires the underlying ISMP delivery layer to actually let the same commitment reach `onAccept`/`onGetResponse` twice. Hyperbridge's `EvmHost`/`IsmpHost` request-receipt tracking is designed to block exact-duplicate `PostRequest` delivery, and I was not able to fully verify from the indexed code whether the Tron host implementation enforces the same one-time-receipt semantics as `EvmHost.sol`, since the Tron contracts directory appears to be a partially-forked/lagging copy of the audited EVM app logic (it still uses raw `.call(abi.encodeWithSelector(IERC20.transfer.selector...))` instead of `SafeERC20`, and lacks the `nonReentrant` modifier and `escrowed` local-variable pattern present in `IntentsBase.sol`). This divergence from the hardened mainline contract is itself the strongest signal of latent risk, but confirming exploitability requires checking the Tron-side host/receipt-uniqueness guarantees, which are outside what I could locate in the index.

### Recommendation
Bring `evm/tron/contracts/apps/IntentGatewayV2.sol`'s `withdraw()`/`onAccept()`/`onGetResponse()` in line with `evm/src/apps/intentsv2/IntentsBase.sol`: check `_filled[commitment] == address(0)` (or an explicit "not yet settled" flag) before any transfer, follow checks-effects-interactions (decrement escrow and mark filled before the external token transfer), and adopt `SafeERC20` + `nonReentrant` guards to match the mainline implementation.

### Proof of Concept
Not independently executable from static review — would require: (1) confirming the Tron ISMP host's request-receipt map allows a second delivery of an already-executed `PostRequest`/`GetResponse` commitment to reach `onAccept`/`onGetResponse`, then (2) calling `withdraw()` twice for the same `WithdrawalRequest.commitment` where `_orders[commitment][token]` still holds a positive balance after the first payout (e.g., a partially-filled same-chain order combined with a cross-chain refund message racing a get-response cancellation), observing a second `IERC20.transfer`/native `.call` to `beneficiary` before the balance underflows to revert.

### Citations

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-705)
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-410)
```text
    function _withdraw(WithdrawalRequest memory body, bool isRefund, bool finalize) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        if (finalize) _filled[body.commitment] = beneficiary;

        uint256 len = body.tokens.length;
        for (uint256 i; i < len; i++) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (amount == 0) continue;

            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
        }
```
