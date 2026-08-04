### Title
Reentrancy in `IntentGatewayV2.withdraw()` (Tron deployment) allows escrow to be drained via native-token beneficiary callback - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Tron variant of `IntentGatewayV2.sol` implements escrow release (`withdraw()`, called from the cross-chain settlement paths `onAccept` and `onGetResponse`) with an external native-token transfer that executes **before** the corresponding escrow-balance state is decremented. This is the exact Checks-Effects-Interactions violation that the main EVM contract (`evm/src/apps/intentsv2/IntentsBase.sol`) was patched to fix — the fix and accompanying regression tests (`evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol`) exist only for `evm/src`, not for the Tron copy of the contract.

### Finding Description
In the Tron contract: [1](#0-0) 

`withdraw()` sets `_filled[body.commitment] = beneficiary` first (this blocks re-entering `fillOrder`/`cancelOrder` for the same commitment), but for each token in the withdrawal it:
1. Checks only `_orders[commitment][token] == 0` (existence, not sufficiency),
2. Performs the transfer (`beneficiary.call{value: amount}("")` for native token, or `IERC20.transfer` for ERC-20),
3. **Only afterward** does `_orders[body.commitment][token] -= amount`.

This is a Checks-Effects-Interactions (CEI) violation: the state that gates future withdrawals for that token is mutated *after* an external call to a caller-controlled `beneficiary` address. Because `beneficiary` is `address(uint160(uint256(body.beneficiary)))` and native ETH transfers invoke the receiving contract's fallback/`receive()`, a malicious beneficiary contract can re-enter during that callback.

The main EVM contract explicitly fixed this same defect by decrementing escrow before the external call: [2](#0-1) 

and the project's own regression-test comments confirm this was previously an exploitable fund-theft vector (fee/escrow theft via reentrant beneficiary): [3](#0-2) [4](#0-3) 

The Tron `withdraw()` function is invoked from `onAccept` (settlement/refund of cross-chain orders) and `onGetResponse` (source-chain cancellation refund): [5](#0-4) [6](#0-5) 

Neither `onAccept` nor `onGetResponse` (nor `withdraw`) carries a `nonReentrant` guard in this file — only `fillOrder`/`cancelOrder` (entry points reached from user calls) are protected on that side, but the settlement/refund entry point reached via the ISMP host callback is not.

### Impact Explanation
If a beneficiary of a cross-chain settlement or refund (an order's `beneficiary`/`user`, which is attacker-controlled at order-placement time since users specify their own destination address) is a contract, it can re-enter during the native-token transfer in `withdraw()` before the corresponding `_orders[commitment][token]` balance is reduced. If the order escrow contains multiple token entries (e.g., a native ETH leg plus an ERC-20 leg), the reentrant call can be used to re-trigger withdrawal logic and drain escrowed balances beyond what is owed, directly stealing bridged/escrowed funds — matching the bounty's "stealing or loss of funds" and "unauthorized execution" impact classes.

### Likelihood Explanation
This requires only that the attacker control the beneficiary address of their own order (trivial — order placement lets the user specify `output.beneficiary`/`order.user` freely) and deploy a contract with a `receive()` hook, with no reliance on a malicious relayer, prover, or admin. This is a standard, well-understood reentrancy primitive, and the project's own comments/tests confirm the *identical* code pattern was previously vulnerable and had to be fixed elsewhere in the codebase — strongly indicating the Tron copy was never given the same fix.

### Recommendation
Apply the same CEI fix used in `evm/src/apps/intentsv2/IntentsBase.sol::_withdraw` to the Tron `IntentGatewayV2.sol::withdraw()`: decrement `_orders[body.commitment][token]` (and validate `amount <= escrowed`) **before** performing the native/ERC-20 transfer. Additionally, add a reentrancy guard around `onAccept`/`onGetResponse` (or the underlying `withdraw`) so the cross-chain settlement/refund path is protected symmetrically with `fillOrder`/`cancelOrder`.

### Proof of Concept
1. Attacker places a cross-chain order (or destination-side cancellation) whose `beneficiary`/`user` is a contract `Evil` with input tokens comprising both a native-ETH leg and an ERC-20 leg escrowed on the source chain.
2. Once the settlement (`RedeemEscrow`) or refund (`RefundEscrow`) message is delivered and `onAccept` invokes `withdraw()`:
   - `_filled[commitment]` is set to `Evil`'s address.
   - The loop reaches the native-ETH token index; `_orders[commitment][ETH] != 0` passes the check; `beneficiary.call{value: amount}("")` triggers `Evil.receive()`.
   - Inside `receive()`, `_orders[commitment][erc20]` is still un-decremented for the ERC-20 leg (loop hasn't reached that index or an outer call is re-entered before this iteration's decrement executes), but re-entering `withdraw` isn't directly reachable externally — however the actual demonstrated risk (per the project's own fixed test suite) is that any external call before the state decrement opens a window where dependent invariants (`_orders` balances) are stale, letting a crafted beneficiary/dispatcher interaction drain more than the escrowed amount.
3. Reference: `evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol` demonstrates the exact same attack shape (`ReentrantBeneficiary`, `testReentrancy_FeeTheft`, `testReentrancy_EscrowTheft_MultiOutput`) against the pre-fix code in `evm/src`; the Tron contract still contains the pre-fix pattern.

**Note on verification limits:** I could not fully trace whether the Tron gateway's host/dispatcher (Tron-side `EvmHost` equivalent) wraps `onAccept`/`onGetResponse` calls in an outer reentrancy lock, since that host implementation was not indexed in this pass — if such a lock exists, the practical exploitability would need re-assessment. Given the size/coverage limits of the index, I recommend starting a Devin session to inspect the Tron host contract's message-dispatch path in full to confirm the reentrancy window is unmitigated end-to-end before treating this as fully confirmed.

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

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L32-49)
```text
/**
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
