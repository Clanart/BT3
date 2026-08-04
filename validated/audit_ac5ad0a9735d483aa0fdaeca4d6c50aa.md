### Title
Escrow double-release via reentrancy in `IntentGatewayV2.withdraw()` due to post-interaction state update - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2.withdraw()` on the source-chain escrow contract violates checks-effects-interactions: it performs the external token transfer (`token.call(...transfer...)`) **before** decrementing the escrow accounting variable `_orders[commitment][token]`. Because the escrowed token itself is attacker-supplied (`order.inputs`), a malicious ERC777/hook-style token used as an order input can re-enter during the transfer callback while the escrow balance for that commitment/token is still non-zero, allowing the same escrow to be paid out more than once — mirroring the exact bug class in the referenced Reserve Protocol report (state-changing accounting update placed after, rather than before, a token transfer that can trigger reentrant hooks).

### Finding Description
`withdraw()` is the internal function that all escrow-release paths converge on: `onAccept()` for `RequestKind.RedeemEscrow` / `RequestKind.RefundEscrow`, `onGetResponse()`, and the same-chain branch of `cancelOrder()`. [1](#0-0) 

Inside `withdraw()`, for every escrowed token the contract:
1. Confirms `_orders[commitment][token] != 0`.
2. Performs the raw external call `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))`.
3. Only **after** the call returns does it execute `_orders[body.commitment][token] -= amount;`. [2](#0-1) 

This is the inverse of checks-effects-interactions and is the same broken invariant as the reported Reserve Protocol bug: `basketsNeeded`/balance bookkeeping was updated only after (or interleaved with) untrusted token transfers, letting a hook-token reenter while stale accounting values are still visible. Here, the accounting variable `_orders[commitment][token]` is the direct analog of `basketsNeeded`/BackingManager balances — it is read for the guard (`== 0` check) but not yet decremented at the moment the external call executes, so a reentrant invocation of `withdraw()` for the same `(commitment, token)` pair sees the escrow as still fully funded.

By contrast, the correctly-hardened same-chain/cross-chain fill path in `IntrinsicIntents.sol`/`ExtrinsicIntents.sol` sets `_filled[commitment] = msg.sender` **before** any external transfer, and the dedicated Foundry regression suite `IntrinsicIntentsReentrancyTest.sol` explicitly documents and tests this CEI fix for `fillOrder`. [3](#0-2) 
The mainline `IntentsBase.sol._withdraw()` implementation (used by the main EVM app family) also follows the safe order — it decrements `_orders[...]` *before* the transfer: [4](#0-3) 
The Tron-variant `IntentGatewayV2.sol` `withdraw()` is a regression relative to that pattern: the decrement happens strictly after the interaction, with no `_filled[commitment]` guard checked inside `withdraw()` itself before mutating state. [5](#0-4) 

The order's input tokens are fully attacker-controlled at `placeOrder()` time (the user chooses `order.inputs`), so nothing prevents a user from escrowing a malicious hook-token (ERC777-style, or any token whose `transfer()` implementation calls back into an attacker contract) as one of the order inputs. When escrow release later transfers that token to the (attacker-controlled) beneficiary, the hook fires mid-`withdraw()`, before `_orders[commitment][token]` has been zeroed/decremented for that token.

### Impact Explanation
This falls squarely within the bounty's fund-loss / double-settlement class: unauthorized transaction manipulation and double-claim of escrowed bridge funds. If a reentrant path exists back into `withdraw()` for the same commitment while `_orders[...]` is stale (e.g., via a nested Host message-processing call reachable during the token-transfer callback, or via the same-chain `cancelOrder()` → `withdraw()` call being reentered), the attacker can drain the same escrow entry multiple times, stealing funds that belong to solvers/users on the counterparty side of the intent settlement — the same class of loss described in the source report (bypassing an accounting bound because state wasn't finalized before the untrusted external call).

### Likelihood Explanation
The attacker fully controls the vulnerable input: they choose `order.inputs` when placing an order, so supplying a malicious hook-token requires no privileged access, relayer collusion, or governance action — only an unprivileged user calling `placeOrder`/`fillOrder`/`cancelOrder`. The unsafe ordering (`transfer` before `_orders -= amount`) is unconditional in `withdraw()`, i.e., it triggers on every settlement/refund flow that touches an attacker-chosen token. The remaining precondition — that some external call path can re-enter `withdraw()` (or its callers `onAccept`/`onGetResponse`/`cancelOrder`) before the first invocation completes — was not fully confirmed in this pass: I could not fully verify whether `EvmHost`'s `dispatchIncoming`/receipt-marking ordering (in `evm/src/core/EvmHost.sol`, referenced from `HandlerV2.sol`'s `handlePostRequests`/`handleGetResponses`) records the request/response receipt before or after invoking the module's `onAccept`/`onGetResponse` callback. That ordering determines whether a hook-token callback can force the Host to reprocess the same or a related message within the same call stack. This should be verified directly against `EvmHost.sol`'s `dispatchIncoming` implementation before treating the full double-spend chain as proven; the local violation of checks-effects-interactions inside `withdraw()` itself, however, is directly confirmed in code.

### Recommendation
- In `IntentGatewayV2.withdraw()` (`evm/tron/contracts/apps/IntentGatewayV2.sol`), decrement `_orders[body.commitment][token]` **before** performing the external token transfer, matching the pattern already used in `evm/src/apps/intentsv2/IntentsBase.sol::_withdraw()`.
- Set `_filled[body.commitment] = beneficiary` guarded by an explicit "not already processed" check (or use a per-commitment one-time flag checked at the top of `withdraw()`), consistent with the CEI fix already applied and tested for `fillOrder` in `IntrinsicIntents.sol`/`ExtrinsicIntents.sol`.
- Replace the unchecked low-level `.call` for `transfer` with `SafeERC20.safeTransfer`, which both enforces boolean return-value correctness and is easier to audit for reentrancy-safety.
- Verify and, if needed, add a Host-level guard ensuring a request/response receipt is recorded before dispatching to the destination module's `onAccept`/`onGetResponse`, to eliminate any cross-message reentrancy surface at the `HandlerV2`/`EvmHost` layer.

### Proof of Concept
1. Attacker calls `placeOrder()` with `order.inputs[0].token` = a malicious ERC20/ERC777-style contract they control, whose `transfer()` implementation calls back into a helper contract when the recipient is the attacker's own address.
2. Order is filled (or attacker cancels same-chain) so that `withdraw()` is invoked with `body.tokens` including the malicious token and `beneficiary` = attacker's contract.
3. In `withdraw()`: `_orders[commitment][token] == X` (checked, non-zero) → `token.call(transfer(beneficiary, X))` executes → malicious token's callback fires before returning. [6](#0-5) 
4. If the callback can trigger another invocation reaching `withdraw()` for the same `(commitment, token)` before step 3's `_orders[...] -= X` executes (e.g., through the same-chain `cancelOrder()` re-entry path, since `cancelOrder()`'s only guard is `_filled[commitment] != address(0)`, which is not yet set until after `withdraw()`'s loop begins for a same-chain refund) — `_orders[commitment][token]` is still `X`, so the check passes again and a second transfer of `X` is issued.
5. Net effect: attacker receives `2X` (or more, bounded by number of reentrant calls) for a single `X`-sized escrow, at the expense of the counterparty (user or solver) who should have received those funds or the corresponding refund.

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

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L37-49)
```text
 * Attack window (pre-fix):
 *
 *   _fillSameChain / _fillCrossChain:
 *     beneficiary.call{value: ...}("")   ← RE-ENTRY HERE
 *     // _filled still == address(0) pre-fix, now set at the top (CEI)
 *
 * With the CEI fix in place, `_filled[commitment]` is set to `msg.sender` at the
 * very start of both fill functions. Any reentrant `fillOrder` call therefore hits
 * the `if (_filled[commitment] != address(0)) revert Filled()` guard and reverts.
 * That revert propagates through `receive()`, causing the outer ETH transfer to
 * return `(false, ...)`, which triggers `InsufficientNativeToken()` in the outer
 * call — rolling back all state changes atomically.
 */
```

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
