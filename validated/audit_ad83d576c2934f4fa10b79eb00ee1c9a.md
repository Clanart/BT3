## Title
Escrow balance decremented after external token/ETH transfer in `withdraw` — CEI violation in the Tron IntentGatewayV2 (unlike the audited/fixed EVM equivalent) - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron deployment of `IntentGatewayV2` (`evm/tron/contracts/apps/IntentGatewayV2.sol`) contains a `withdraw()` function that sends escrowed tokens/ETH to the beneficiary via a raw external call and **only decrements the `_orders[commitment][token]` escrow accounting after that call returns**: [1](#0-0) 

This is the same bug class as the reported `emergencyWithdraw` reentrancy: state (the user/order balance) is not zeroed before funds are sent out. This directly contradicts the fix already applied to the equivalent EVM production path, `IntentsBase.sol`'s `_withdraw`, which performs the escrow decrement **before** the external call: [2](#0-1) 

The repo's own regression suite (`IntrinsicIntentsReentrancyTest.sol`) documents that a prior CEI violation in `_fillSameChain` was already found and fixed by moving `_filled[commitment] = msg.sender` to the top of the function, specifically to defeat reentrant beneficiary contracts: [3](#0-2) 

The Tron `withdraw()` function only applies half of that fix: it sets `_filled[body.commitment] = beneficiary` at the top (blocking a duplicate top-level `withdraw` call), but leaves the **per-token escrow decrement inside the loop after the external transfer**, reintroducing the exact hazard the EVM-side fix eliminated.

### Finding Description
`withdraw()` in the Tron `IntentGatewayV2.sol` is reached from `onAccept` for `RedeemEscrow`/`RefundEscrow` requests and from `onGetResponse`: [4](#0-3) [5](#0-4) 

Inside the token loop, the guard only checks that the bucket is non-zero, sends the funds, and *then* subtracts:

```
if (_orders[body.commitment][token] == 0) revert UnknownOrder();
... beneficiary.call{value: amount}("") / token.call(transfer...)
_orders[body.commitment][token] -= amount;
``` [6](#0-5) 

Compare this to the hardened pattern used elsewhere in the same codebase (`IntentsBase.sol`), which reads-and-effects the escrowed amount *before* making any external call:
```
uint256 escrowed = _orders[body.commitment][token];
if (escrowed == 0) revert UnknownOrder();
_orders[body.commitment][token] = escrowed - amount;
if (token == address(0)) { beneficiary.call{value: amount}(""); } else { safeTransfer(...); }
``` [2](#0-1) 

The Tron file is a separately maintained fork of the intents gateway logic (it does not import `IntentsBase.sol`, but reimplements `withdraw` inline), so the CEI fix applied on the primary EVM code path was not carried over here.

### Impact Explanation
If a beneficiary is a contract (native-ETH `receive()` hook) or the escrowed token has transfer-time callback semantics, an attacker-controlled beneficiary can re-enter during the `.call` in one loop iteration. Because `_orders[body.commitment][token]` for that token bucket is still un-decremented at that point in execution, any code path that can reach `withdraw()`/`onAccept` again for the same commitment while the outer call is still on the stack (e.g., a second, independently-authenticated delivery of a message touching the same order, or any future/alternate module entry point that calls into `withdraw` without first checking `_filled`) would read stale escrow state and could disburse the same bucket twice — a double-settlement of escrowed funds, i.e. loss of bridged funds from the gateway. This falls squarely under the bounty's "stealing or loss of funds" / "replay/double-claim/double-settlement" categories.

### Likelihood Explanation
Likelihood is moderate-to-low today because the top-level `_filled[commitment]` guard blocks the most obvious single-entrypoint reentrant call, and `onAccept`/`onGetResponse` are `onlyHost`-gated so an attacker cannot call `withdraw` directly. However, the vulnerable ordering is a live latent defect: it is a direct regression against a documented, previously-audited-and-fixed pattern (`_fillSameChain`'s CEI fix) that exists in the same monorepo, and any additional code path, upgrade, or interaction that touches `_orders[commitment][token]` for the same commitment before the loop's decrement completes (including composed multi-token bodies with duplicate token entries reached through a nested call) turns it into full fund loss. Given this is a bridge-custody accounting function, the standard/expected fix (decrement-before-send) should be applied regardless of whether a full external trigger for the nested re-entry is currently provable from this code snapshot alone.

### Recommendation
Apply the same Checks-Effects-Interactions fix already used in `IntentsBase.sol`: read `_orders[body.commitment][token]` into a local variable, revert on zero, write the decremented value back to storage, and only then perform the external `.call`/`transfer`. Do this for both the token loop and the transaction-fee payout in `withdraw()`: [7](#0-6) 

### Proof of Concept
Exact external trigger for nested re-entrancy could not be fully enumerated from the indexed code (the Tron host's `dispatchIncoming`/receipt-marking implementation was not retrievable in this pass — see `evm/tron` host contracts, which are outside what the index returned). The core corrupted value and violated invariant are nonetheless concretely demonstrated by the code delta between:
- Vulnerable: `_orders[body.commitment][token] -= amount;` placed **after** `beneficiary.call{value: amount}("")` / `token.call(transfer...)` in `evm/tron/contracts/apps/IntentGatewayV2.sol:687-705`.
- Fixed reference implementation: `_orders[body.commitment][token] = escrowed - amount;` placed **before** the equivalent external call in `evm/src/apps/intentsv2/IntentsBase.sol:400-409`.

Given the index's file-size limits, the Tron host contract (`onlyHost`/receipt logic and any nested dispatch) could not be fully traced in this session; a Devin session with full repository access is recommended to confirm whether any current call path allows a second `onAccept`/`onGetResponse` invocation to observe the stale (un-decremented) `_orders` bucket during the outer call, which would convert this into a directly provable double-spend.

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L729-734)
```text
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        withdraw(body, true);
    }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L400-409)
```text
            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
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
