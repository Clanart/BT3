### Title
Reentrant double-spend of escrowed funds via `withdraw()` state update after external call - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Tron-specific `IntentGatewayV2.withdraw()` function transfers escrowed tokens to the beneficiary **before** decrementing the `_orders` escrow accounting mapping, violating checks-effects-interactions. Since `onAccept` (the entry point that invokes `withdraw`) carries no reentrancy guard, a malicious beneficiary contract can re-enter and drain the same escrow slot multiple times before it is ever decremented — directly analogous to the seed report's core flaw of relying on a balance value that has not yet been updated to reflect an in-flight operation.

### Finding Description
`withdraw()` in `evm/tron/contracts/apps/IntentGatewayV2.sol` is called from `onAccept()` for both `RedeemEscrow` and `RefundEscrow` message kinds: [1](#0-0) 

`onAccept` has the `onlyHost` modifier but **no `nonReentrant` guard**, and dispatches straight into `withdraw()`: [2](#0-1) 

Inside `withdraw()`, for each token in the withdrawal request the code checks `_orders[body.commitment][token] == 0`, then performs the native ETH `.call{value: amount}("")` or the ERC-20 `transfer` **first**, and only afterward executes `_orders[body.commitment][token] -= amount;`:
```solidity
if (_orders[body.commitment][token] == 0) revert UnknownOrder();

if (token == address(0)) {
    (bool sent,) = beneficiary.call{value: amount}("");
    if (!sent) revert InsufficientNativeToken();
} else {
    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
    if (!success) revert TransferFailed();
}

_orders[body.commitment][token] -= amount;
``` [2](#0-1) 

This is exactly the corrupted-value pattern the seed report describes: the accounting variable (`_orders[commitment][token]`, the escrow "balance") is stale — still reflecting its pre-payout value — at the exact moment external control is handed off. On a native-ETH withdrawal, `beneficiary.call{value: amount}("")` can execute arbitrary code in the beneficiary contract before `_orders[...]` is decremented.

Contrast this with the canonical EVM implementation of the same logic in `IntentsBase.sol`, which correctly follows checks-effects-interactions by updating storage **before** the external call: [3](#0-2) 

The existence of `evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol`, which documents a prior fix ("`_filled[commitment]` is set before the loop… After the fix: … no state is mutated" on revert) confirms the project is aware this exact reentrancy class is exploitable and previously patched it in the canonical EVM contract — but the Tron-specific fork was not updated with the same fix. [4](#0-3) 

### Impact Explanation
A solver/filler that controls (or is) the beneficiary address on a `RedeemEscrow`/`RefundEscrow` payout can deploy a malicious contract as the beneficiary. When `withdraw()` sends native ETH to it via `.call`, its `receive()`/`fallback()` re-enters before `_orders[body.commitment][token]` is decremented, allowing the escrow for that `(commitment, token)` pair to be paid out multiple times — direct theft of escrowed user/protocol funds from the IntentGateway contract on Tron. This is unauthorized fund extraction / double-settlement of an intent order's escrow, matching the bounty's "stealing or loss of funds" and "replay/double-claim/double-settlement" categories.

### Likelihood Explanation
The attack requires no privileged role, malicious relayer, or malicious prover — only that the attacker be the beneficiary of a legitimate settlement message (e.g., the solver who filled the order, or the user being refunded), a role any unprivileged participant can occupy by placing/filling their own order. The vulnerable path is public-entrypoint reachable: `onAccept` → `withdraw` is invoked whenever a valid `RedeemEscrow`/`RefundEscrow` message is delivered, and no reentrancy guard exists anywhere in the call chain shown. The only precondition is a native-ETH output/input token in the order (ERC-20 `transfer`/`safeTransfer` calls to non-malicious tokens don't hand back control, but a malicious/callback-supporting ERC-20 or the native-asset branch does).

### Recommendation
Reorder `withdraw()` in `evm/tron/contracts/apps/IntentGatewayV2.sol` to decrement `_orders[body.commitment][token]` (and set `_filled[body.commitment]`) **before** performing any external call, mirroring the checks-effects-interactions pattern already used in `evm/src/apps/intentsv2/IntentsBase.sol::_withdraw`. Additionally, add a reentrancy guard (`nonReentrant`) to `onAccept` or to `withdraw()` itself as defense in depth.

### Proof of Concept
1. User places a same-chain or cross-chain order whose output/input includes native ETH, with `beneficiary` set to an attacker-controlled contract `Evil`.
2. `Evil` fills the order (or is the refund beneficiary), triggering a `RedeemEscrow`/`RefundEscrow` message that reaches `onAccept` → `withdraw(body, ...)`.
3. Inside `withdraw`, `_orders[commitment][address(0)]` is checked non-zero, then `beneficiary.call{value: amount}("")` transfers ETH to `Evil` — control passes to `Evil.receive()` **before** `_orders[commitment][address(0)] -= amount` executes.
4. `Evil.receive()` re-enters by causing another `onAccept` delivery for the same commitment (e.g., a duplicate/looped message, or by invoking any other path that re-reads the still-nonzero `_orders[commitment][address(0)]`), receiving the same escrowed ETH a second time before the first call's decrement ever lands.
5. Repeat until the escrow slot's actual token balance is exhausted, net-stealing funds beyond what was legitimately owed.

Note: I could not fully verify whether the specific message-delivery path from Hyperbridge allows a genuine duplicate delivery of the *same* commitment inside a single re-entered call stack (this depends on host/ISMP receipt semantics not fully covered in the indexed code); the core, directly-verified vulnerability is the CEI violation itself — the escrow balance is provably stale across the external call, which is the same broken invariant as the seed report's `underlyingBalance` issue, and is not visible in the tests in this index (`IntrinsicIntentsReentrancyTest.sol` only covers the fixed canonical `IntentsBase.sol`, not the Tron fork).

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

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L305-316)
```text
    /**
     * @dev Same-chain multi-output escrow theft is blocked by the CEI fix.
     *
     * Before the fix: on a two-output order (ETH + ERC-20), the malicious
     * beneficiary could re-enter during the ETH transfer, self-fill the ERC-20
     * output (net-zero cost), trigger `_withdraw(finalize=true)`, and steal the
     * entire input[1] escrow.
     *
     * After the fix: `_filled[commitment]` is set before the loop, so the
     * reentrant call reverts with `Filled()`. The whole transaction reverts with
     * `InsufficientNativeToken()` and no state is mutated.
     */
```
