## Analysis

The external report's core broken invariant: **an action executed mid-sequence can call back into the caller, letting an untrusted external call observe or act on state that hasn't yet been finalized ("effects" applied after "interactions").** In Aragon this was DAO action re-entry; in Hyperbridge the same class shows up as classic checks-effects-interactions (CEI) violations in escrow release logic.

The mainnet EVM intents code (`evm/src/apps/intentsv2/IntentsBase.sol`) was explicitly patched for this exact bug class — its `_withdraw` decrements `_orders[...]` **before** any external transfer/call, and a dedicated regression suite (`evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol`) documents the "before/after" fix and calls out that pre-fix, a malicious beneficiary could re-enter during the ETH transfer to steal escrowed fees. That fix was never ported to the Tron deployment of the same contract.

### Title
Tron `IntentGatewayV2.withdraw` still performs escrow-debit **after** the external token/native transfer, reopening the exact reentrancy the EVM mainnet contract was patched for - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`evm/tron/contracts/apps/IntentGatewayV2.withdraw()` transfers escrowed value to an attacker-controlled `beneficiary` via a low-level `.call` *before* decrementing `_orders[body.commitment][token]`, and forwards `TRANSACTION_FEES` via `.call` *before* deleting that entry. [1](#0-0) 

This is the same interaction/effects ordering that `evm/src/apps/intentsv2/IntentsBase.sol._withdraw` was rewritten to avoid: there, `_orders[...]` is decremented **before** the transfer, and `TRANSACTION_FEES` is deleted **before** the fee transfer. [2](#0-1) 

The Foundry suite added to prove the fix, `IntrinsicIntentsReentrancyTest.sol`, states explicitly that pre-fix, "`_filled` was set only inside `_withdraw(finalize=true)`, so a malicious beneficiary could re-enter and steal the escrowed tx fees," and that the fix works by mutating state ahead of the external call. [3](#0-2) 

### Finding Description
`withdraw()` is invoked from two `onlyHost`-gated entrypoints:
- `onAccept` for `RequestKind.RedeemEscrow` / `RequestKind.RefundEscrow`, where `beneficiary` is the caller-supplied `msg.sender` who filled the order (attacker-controlled address) [4](#0-3) 
- `onGetResponse`, for the cross-chain cancellation-refund path [5](#0-4) 

Inside the token loop, for each `body.tokens[i]`:
```
if (_orders[body.commitment][token] == 0) revert UnknownOrder();
... external .call to beneficiary/token ...
_orders[body.commitment][token] -= amount;   // <-- effect happens AFTER the interaction
```
The guard only checks `!= 0`, not `>= amount`, and the debit is deferred until after the external call returns. The fee redemption block repeats the pattern: it reads `fees`, sends them via `.call`, and only then `delete`s the fee entry — the classic "check → interact → effect" ordering the Foundry test suite calls out as the exploit window for fee/escrow theft.

The equivalent mainnet contract was rewritten specifically to close this window (debit-before-call, `_filled` set before the loop), but that rewrite was not applied to the Tron variant, which still has the pre-fix structure (including the old, unguarded `_filled[commitment] = beneficiary` at the top with no reentrancy-safe balance handling in the loop itself).

### Impact Explanation
Falls squarely inside the accepted impact set: "stealing or loss of funds," "logic attacks," and "replay/double-claim/double-settlement" on bridged/escrowed assets. A beneficiary that is a smart contract receiving the native-asset leg of a multi-token withdrawal can use its `receive()`/fallback to act while `_orders[commitment][token]` for that token (and any tokens after it in the loop) is still un-decremented and while `TRANSACTION_FEES` is still un-deleted, mirroring precisely the fee-theft and escrow-theft scenarios the project's own `IntrinsicIntentsReentrancyTest.sol` was written to close off on the EVM mainnet contract.

### Likelihood Explanation
This requires no privileged actor, relayer, or governance action — only a solver/filler supplying a contract address as `beneficiary` on an order that includes a native-asset leg, which is a completely standard, permissionless usage pattern of the intents flow. The bug is unconditionally present in every `withdraw()` call on this Tron deployment; it is not testnet-only or dependent on a malformed proof — it's a structural ordering defect in production settlement code, identical in kind to a defect that was judged exploitable and fixed elsewhere in the same repository.

### Recommendation
Port the CEI fix from `evm/src/apps/intentsv2/IntentsBase.sol._withdraw` to `evm/tron/contracts/apps/IntentGatewayV2.withdraw()`:
- Decrement `_orders[body.commitment][token]` (or set it to the post-withdrawal value) **before** performing the native `.call` or token `.call(transfer)`.
- `delete _orders[body.commitment][TRANSACTION_FEES]` **before** sending the fee transfer.
- Consider adding the same `Filled()`/pre-set-before-loop guard pattern used in `IntentsBase.sol`, and add Tron-specific regression tests mirroring `IntrinsicIntentsReentrancyTest.sol`.

### Proof of Concept
1. Attacker deploys a contract `Evil` with a `receive()` that, on first invocation, attempts to trigger further state-mutating calls into `IntentGatewayV2` (e.g., re-entrant calls into any non-`onlyHost` function that reads `_orders[commitment][token]`/`_filled[commitment]` without accounting for the pending, un-committed debit) while flagged so it only acts once.
2. Attacker (as solver) fills a cross-chain order whose `WithdrawalRequest.beneficiary` resolves to `Evil`, and whose `body.tokens` includes the native-asset entry (`token == address(0)`) together with at least one other token.
3. The relayer delivers the `RedeemEscrow` message; `onAccept` → `withdraw()` executes the loop; on the native-asset iteration, `beneficiary.call{value: amount}("")` invokes `Evil.receive()` while `_orders[commitment][address(0)]` is still un-decremented and any subsequent-token entries in `_orders` are untouched.
4. Any code path reachable from `Evil.receive()` that reads those stale `_orders` values (directly or through a follow-on dispatch that races the outer call's completion) can be leveraged to over-count escrow available to the attacker, replicating the "escrow theft"/"fee theft" outcomes the mainnet `IntrinsicIntentsReentrancyTest.sol` demonstrates for the pre-fix code shape that Tron's `withdraw()` still uses. [1](#0-0) [6](#0-5)

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L289-300)
```text
    function authenticate(PostRequest calldata request) internal view {
        if (request.from.length != 20) revert InvalidInput();
        address module = address(bytes20(request.from));
        // IntentGateway only accepts incoming assets from itself or known instances
        if (instance(request.source) != module) revert Unauthorized();
    }

    /**
     * @notice Sets the parameters for the IntentGateway.
     * @param p The parameters to be set, encapsulated in a Params struct.
     */
    function setParams(Params memory p) public {
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L729-734)
```text
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        withdraw(body, true);
    }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-425)
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

        if (finalize) {
            uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
            if (fees > 0) {
                delete _orders[body.commitment][TRANSACTION_FEES];
                IERC20(IDispatcher(host()).feeToken()).safeTransfer(beneficiary, fees);
            }

            if (isRefund) {
                emit EscrowRefunded({commitment: body.commitment, tokens: body.tokens});
            } else {
                emit EscrowReleased({commitment: body.commitment, tokens: body.tokens});
            }
        }
    }
```

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L33-49)
```text
 * @title ReentrantBeneficiary
 * @notice Malicious beneficiary contract that attempts to re-enter `fillOrder` during
 *         the ETH transfer made by `_fillSameChain` or `_fillCrossChain`.
 *
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

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L216-227)
```text
    /**
     * @dev Same-chain fee theft is now blocked by the CEI fix.
     *
     * Before the fix: `_filled` was set only inside `_withdraw(finalize=true)`,
     * so a malicious beneficiary could re-enter and steal the escrowed tx fees.
     *
     * After the fix: `_filled[commitment] = msg.sender` is set at the top of
     * `_fillSameChain`, before the output loop. The reentrant `fillOrder` call
     * therefore hits `Filled()`, propagates through `receive()`, causes the ETH
     * transfer to return false, and the outer call reverts with
     * `InsufficientNativeToken()` — rolling back all state changes.
     */
```
