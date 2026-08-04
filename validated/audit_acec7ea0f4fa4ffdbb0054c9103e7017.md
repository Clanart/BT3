## Analysis

The Derby bug's core pattern: an emergency/settlement function performs a **critical state transition** (`blacklistProtocol` marking a protocol unusable) **atomically bundled with an external value-transfer call** (`withdrawFromProtocol`). If the transfer reverts for any reason, the whole transaction — including the state transition that was supposed to be unconditionally safe — reverts too, permanently blocking the intended safety action.

The Hyperbridge analog is `IntentsBase._withdraw` (and its Tron-variant twin `IntentGatewayV2.withdraw`), which is the single settlement path for both `RedeemEscrow` and `RefundEscrow` — i.e., both order fills and order cancellations/refunds. [1](#0-0) 

### Title
Escrow settlement (`_withdraw`) atomically couples the `_filled` finality marker with token transfer, permanently locking escrow when a transfer reverts - (File: evm/src/apps/intentsv2/IntentsBase.sol)

### Summary
`_withdraw` (called from `onAccept` for `RedeemEscrow`/`RefundEscrow`, from `onGetResponse` for source-side cancels, and from `_cancelSameChain` for same-chain cancels) sets `_filled[commitment] = beneficiary` and then, in the same atomic call, loops over `body.tokens` performing `beneficiary.call{value}` for native assets or `IERC20.safeTransfer` for ERC-20s. Any single transfer failure (a paused/blacklisted token, a token that reverts on transfer to a specific address, a malicious ERC-777-style hook, etc.) reverts the entire call — rolling back `_filled` along with it. This is structurally identical to `Vault.blacklistProtocol`: a mandatory external transfer embedded inside what should be an unconditional finality/safety update. [2](#0-1) 

### Finding Description
`IntentGatewayV2.cancelOrder` for same-chain orders calls `withdraw(body, true)` directly and synchronously with `_filled[commitment] = beneficiary`: [3](#0-2) 

For cross-chain orders, the destination-side cancel path (`_cancelFromDest`) marks `_filled[commitment]` first, then dispatches a `RefundEscrow` POST to the source chain; the source chain's `onAccept` receives it and calls `_withdraw`, which re-couples the *source-side* `_filled` marker with the actual token release: [4](#0-3) [5](#0-4) 

The `order.inputs` token list is fully attacker/user-controlled at order placement time — any user can escrow an ERC-20 whose `transfer`/`transferFrom` can be made to revert for a chosen recipient (blacklist-capable stablecoins, pausable tokens, or a bespoke malicious token deployed by the order's own user). Once such a token is escrowed:

- **Same-chain**: the order's own creator can trap their own escrow (self-inflicted, low severity by itself) — but more importantly, for multi-input orders, if *any one* of several escrowed tokens is made non-transferable, the *entire* refund/settlement for **all** tokens in that order reverts, since all transfers happen in the same atomic loop before `_filled` is committed.
- **Cross-chain**: once Hyperbridge delivers the `RefundEscrow`/`RedeemEscrow` POST request and `onAccept` calls `_withdraw`, if the transfer reverts, the ISMP message delivery itself reverts. This is far more damaging than a same-chain revert: a reverted `onAccept` typically leaves the request undelivered/retryable, so the commitment is stuck in limbo — never marked `_filled`, escrow never released, and every future delivery attempt (by any relayer) fails identically, permanently freezing that escrow. There is no guarded, state-first-then-try-transfer path (unlike, e.g., `EvmHost.withdraw` where the beneficiary is governance-controlled) — here `beneficiary`/`order.inputs` are attacker/user-supplied at order placement.

### Impact Explanation
This matches the "fund loss/lock" bridge pivot: escrowed order inputs can become permanently unrecoverable because the finality marker (`_filled`) can never be committed independently of a transfer that an attacker (the order's own user, or an adversarial token issuer colluding with them) can force to permanently fail. Since `_filled` gates both future fills and future cancel attempts, a stuck commitment can never be settled through any code path — same-chain, cancel-from-source, or cancel-from-dest all funnel through the same coupled `_withdraw`/`withdraw` function.

### Likelihood Explanation
Likelihood is Medium: it requires either (a) escrowing a token with an admin-revocable transfer capability (real-world blacklist-capable stablecoins qualify) where the token's own governance later blacklists the escrow contract or beneficiary, or (b) a user deliberately escrowing a custom malicious token designed to revert on transfer to grief protocol operators/relayers by consuming their delivery gas repeatedly. Both require no privileged access — any unprivileged order placer chooses `order.inputs`.

### Recommendation
Decouple the finality state update from the token disbursement: commit `_filled[commitment] = beneficiary` unconditionally first (already partially done), then attempt each transfer in a way that failures are recorded per-token (e.g., credit a per-user, per-token "pending withdrawal" balance instead of reverting) rather than reverting the entire settlement/message-delivery call. This mirrors the Derby recommendation of splitting the "mark as settled" step from the "attempt withdrawal" step, with a separate retry entrypoint for stuck transfers.

### Proof of Concept
1. User places a same-chain order with `order.inputs` containing a custom ERC-20 `EvilToken` that reverts `transfer` when the recipient equals the order's own `order.user` address (attacker controls the token contract).
2. User calls `cancelOrder`, hitting `_cancelSameChain` → `withdraw(body, true)`.
3. `_filled[commitment] = beneficiary` executes, then the loop hits `EvilToken.transfer` and reverts `TransferFailed()`; the whole transaction, including `_filled`, rolls back.
4. The order can never be cancelled or filled again with this input token — repeat with a cross-chain order where destination-side cancel already committed `_filled` on the destination, so the source-side escrow is now permanently orphaned once the corresponding `onAccept`/`_withdraw` call on the source chain reverts on every relayer retry. [6](#0-5)

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L391-410)
```text
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L519-530)
```text
        if (isSameChain) {
            // Same-chain: validate locally and refund immediately
            // only owner can cancel
            if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();

            // Verify we're on the correct chain
            if (orderSource != currentChain) revert WrongChain();

            WithdrawalRequest memory body =
                WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user});

            withdraw(body, true);
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
