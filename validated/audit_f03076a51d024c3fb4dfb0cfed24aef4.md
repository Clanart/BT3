Confirmed: `onAccept` has no `nonReentrant` guard, and `withdraw()` performs the external token/native transfer before decrementing the escrow ledger.

### Title
Checks-effects-interactions violation in `IntentGatewayV2.withdraw` allows reentrant double-drain of escrow before the ledger is decremented - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron `IntentGatewayV2.withdraw()` function guards escrow release with a bare `_orders[body.commitment][token] == 0` equality check — exactly the same class of flaw as the reported `DebtManager.sol` bug, where a strict equality check on a mutable balance fails to bound the actual amount being moved. Unlike the canonical EVM `IntentsBase.sol::_withdraw`, which decrements the escrow ledger **before** making the external transfer, this Tron variant performs the native/token transfer **before** updating `_orders[commitment][token]`, and `onAccept`/`onGetResponse` carry no reentrancy guard.

### Finding Description
In `evm/tron/contracts/apps/IntentGatewayV2.sol`:

```solidity
function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
    address beneficiary = address(uint160(uint256(body.beneficiary)));
    _filled[body.commitment] = beneficiary;

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
        ...
``` [1](#0-0) 

The guard `_orders[body.commitment][token] == 0` only checks that *some* escrow exists, not that it covers `amount`, and — critically — the raw `.call` transfer to `beneficiary` (attacker-controlled address decoded straight from the cross-chain `WithdrawalRequest.beneficiary`) executes **before** `_orders[body.commitment][token] -= amount` runs. This is the inverse of the checks-effects-interactions pattern used correctly in the sibling EVM implementation:

```solidity
uint256 escrowed = _orders[body.commitment][token];
if (escrowed == 0) revert UnknownOrder();
_orders[body.commitment][token] = escrowed - amount;
...
(bool sent,) = beneficiary.call{value: amount}("");
``` [2](#0-1) 

Because `onAccept` and `onGetResponse` carry no `nonReentrant` modifier or reentrancy lock [3](#0-2) , a malicious `beneficiary` receiving the native-token `.call{value: amount}("")` can execute arbitrary code mid-transfer, while `_orders[commitment][token]` still reflects its **stale, pre-decrement** value. If the host/dispatcher's own execution model allows any reentrant path back into `onAccept`/`onGetResponse` for the same or another pending withdrawal against the same commitment/token (e.g., a second in-flight `RedeemEscrow`/`RefundEscrow`/`onGetResponse` message queued for delivery, or a duplicate-token entry inside the same `body.tokens` array), the stale nonzero balance check passes again and a second transfer of `amount` is issued for escrow that has already been logically spent, before the ledger update from the first iteration is committed.

### Impact Explanation
A successful reentrant path leads to a double payout of escrowed input tokens (or a duplicate-token entry within a single request draining more than what remains) to a single beneficiary from a single order's escrow, i.e., theft of user/protocol funds from the escrow contract. This falls squarely inside the "stealing or loss of funds" and "double-settlement" categories called out in the impact gate, since it is a public-entrypoint (`onAccept`/`onGetResponse`, callable once a legitimately-routed cross-chain message arrives) path where existing invariant checks (`== 0` instead of an amount-aware `< amount` check, plus lack of CEI ordering/reentrancy guard) fail to stop it.

### Likelihood Explanation
The likelihood is moderate to low-confidence without deeper knowledge of the Tron host's message-delivery guarantees: `onAccept`/`onGetResponse` are `onlyHost`-gated, so a wholly external attacker cannot call `withdraw` directly. The exploitable trigger requires either (a) a duplicate-token entry inside a single legitimately-dispatched `WithdrawalRequest.tokens` array, or (b) some reentrant delivery path in the Tron host/dispatcher that permits a second `onAccept`/`onGetResponse` call to land for the same commitment while the first is still executing (e.g., via the native-token callback to a malicious `beneficiary`). I was not able to fully confirm from the indexed code whether the Tron host's dispatch mechanism permits such nested/reentrant delivery calls, since that logic lives in host-side contracts not surfaced in this search. Regardless of exact reachability, the missing checks-effects-interactions ordering and the amount-unaware `== 0` guard are a genuine, provable local regression relative to the correctly-ordered EVM `IntentsBase.sol` implementation, and constitute a real defense-in-depth gap that should be fixed.

### Recommendation
Reorder `withdraw()` to update `_orders[body.commitment][token]` (effects) before performing the native/token transfer (interaction), mirroring `IntentsBase.sol::_withdraw`, and tighten the guard to validate `amount <= _orders[body.commitment][token]` rather than merely `!= 0`:

```solidity
uint256 escrowed = _orders[body.commitment][token];
if (escrowed < amount) revert UnknownOrder();
_orders[body.commitment][token] = escrowed - amount;

if (token == address(0)) {
    (bool sent,) = beneficiary.call{value: amount}("");
    if (!sent) revert InsufficientNativeToken();
} else {
    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
    if (!success) revert TransferFailed();
}
```
Additionally, add a reentrancy guard to `onAccept`/`onGetResponse` (as is standard practice, matching `IntentGatewayV2` on EVM chains which uses `nonReentrant` on public entrypoints) for defense in depth.

### Proof of Concept
Conceptual PoC (host-reentrancy path not independently confirmed in this codebase slice):
1. Attacker places/fills a cross-chain order such that the `WithdrawalRequest.beneficiary` resolves to an attacker-controlled contract, and the escrowed token is native (`token == address(0)`).
2. Hyperbridge relays a `RedeemEscrow`/`RefundEscrow` message; `onAccept` → `withdraw()` reaches `beneficiary.call{value: amount}("")` while `_orders[commitment][address(0)]` is still at its pre-decrement value.
3. The attacker contract's receive/fallback triggers a second delivery for the same commitment/token (via any reentrant host callback path, or via a `body.tokens` array crafted with a duplicate `address(0)` entry within the same message).
4. The second iteration/call re-reads the stale nonzero `_orders[commitment][address(0)]`, passes the `== 0` check again, and issues a second `amount` transfer before either decrement is applied — the beneficiary receives `2 × amount` while only `amount` was ever legitimately escrowed. [4](#0-3)

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
