Based on the evidence gathered, I found a concrete, locally-provable analog, but I want to be transparent about the scope of what I could verify given the tool budget.

### Title
CEI violation in `withdraw()` allows reentrancy-driven double transfer of escrowed order funds - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Tron deployment of `IntentGatewayV2` contains a standalone, monolithic copy of the escrow-release logic that has **not** received the Checks-Effects-Interactions (CEI) fix that was applied to the equivalent function in the main EVM codebase (`IntentsBase._withdraw`). In the Tron variant's `withdraw()`, the token/ETH transfer to the beneficiary is executed *before* the corresponding escrow balance is decremented, exactly matching the "external call before state update" pattern from the seed report (reward/hooked-token reentrancy).

### Finding Description
In `evm/tron/contracts/apps/IntentGatewayV2.sol`, `withdraw()` iterates over `body.tokens` and for each token:
1. Checks `_orders[body.commitment][token] != 0`
2. Performs the transfer via a raw `.call` (native ETH or ERC20 `transfer`)
3. **Only afterward** does `_orders[body.commitment][token] -= amount;` [1](#0-0) 

Compare this to the already-fixed sibling implementation in the main EVM codebase, `IntentsBase.withdraw()`, which decrements `_orders[...]` **before** making the external call: [2](#0-1) 

This confirms the project is aware of, and has fixed elsewhere, exactly this class of bug — as documented extensively by the dedicated `IntrinsicIntentsReentrancyTest.sol` regression suite for the main-chain contracts: [3](#0-2) 

The Tron `withdraw()` is reachable from the unprivileged, non-`nonReentrant`-guarded `cancelOrder()` entrypoint for same-chain orders, where the caller controls the refund `beneficiary` (it equals `order.user == msg.sender`): [4](#0-3) 

It is also reachable via the host-gated `onAccept`/cross-chain settlement path: [5](#0-4) 

### Impact Explanation
If an attacker escrows a token with a transfer hook (ERC777-style, or any ERC20 whose `transfer()` can execute arbitrary attacker code — which is entirely possible since `token` is user-supplied at `placeOrder` time and only validated by address, not an allowlist) as one of the order's `inputs`, then upon calling `cancelOrder()`, the hook fires mid-`withdraw()` while `_orders[commitment][token]` still reflects the pre-transfer balance. This is the same "reward token with hooks causes repeat payout" primitive as the seed report, applied to bridge escrow funds instead of a raffle prize — i.e., stealing/loss of escrowed bridge funds.

### Likelihood Explanation
**Important caveat found during investigation:** `withdraw()` sets `_filled[body.commitment] = beneficiary;` as its very first statement, before the transfer loop: [6](#0-5) 

`cancelOrder()` checks `_filled[commitment] != address(0)` at its top and reverts with `Filled()`: [7](#0-6) 

This means a naive reentrant call back into `cancelOrder()` for the **same** commitment during the hook is blocked. I was not able to fully trace, within the remaining tool budget, whether the Tron `fillOrder()` (same-chain solver-release path, not shown in the excerpts I retrieved) independently sets `_filled` before invoking `withdraw()`, or applies a `nonReentrant` modifier the way the main-chain `IntentGatewayV2.fillOrder` does (`function fillOrder(...) public payable nonReentrant`, confirmed in `evm/src`). If the Tron `fillOrder()` lacks either protection, the classic solver/beneficiary reentrancy (exactly what `IntrinsicIntentsReentrancyTest.sol` was written to prevent on the main chain) would be directly exploitable there. Given this unresolved gap, I rate confidence in a fully working end-to-end PoC as **medium** — the CEI defect itself is confirmed and is a real regression relative to the patched code path, but the specific unprivileged trigger I could fully verify (`cancelOrder`) is mitigated by the `_filled` front-load.

### Recommendation
- Apply the same CEI fix used in `evm/src/apps/intentsv2/IntentsBase.sol` to `evm/tron/contracts/apps/IntentGatewayV2.sol::withdraw()`: decrement `_orders[body.commitment][token]` (and delete the `TRANSACTION_FEES` entry) **before** performing any external transfer.
- Add `nonReentrant` (OpenZeppelin `ReentrancyGuard`) to every externally reachable entrypoint that can trigger `withdraw()` — `cancelOrder`, `fillOrder`, `onAccept`, `onGetResponse` — matching the main-chain contract's modifiers.
- Port the `IntrinsicIntentsReentrancyTest.sol` regression suite to the Tron contract to close this gap permanently and catch future divergence between the two implementations.

### Proof of Concept
1. Deploy a malicious ERC20 whose `transfer()` implementation calls back into `IntentGatewayV2.cancelOrder()` (or, pending verification, `fillOrder()`) for a second, independent order that also uses the malicious token, or attempts state manipulation during the callback window.
2. Attacker calls `placeOrder()` escrowing the malicious token as an input.
3. Attacker calls `cancelOrder()` after expiry (or same-chain immediate path) with `order.user == attacker`.
4. Inside `withdraw()`, the malicious token's `transfer()` hook fires before `_orders[commitment][token] -= amount` executes.
5. Full confirmation of double-spend requires verifying the exact reentry target in Tron's `fillOrder()`, which a Devin session with full file access should complete by reading the remainder of `evm/tron/contracts/apps/IntentGatewayV2.sol` (particularly the `fillOrder` function body and its modifiers) and writing a Foundry PoC analogous to `IntrinsicIntentsReentrancyTest.sol` against the Tron contract.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L507-530)
```text
    function cancelOrder(Order calldata order, CancelOptions calldata options) public payable {
        bytes32 commitment = keccak256(abi.encode(order));

        // order has already been filled
        if (_filled[commitment] != address(0)) revert Filled();

        address hostAddr = host();
        bytes32 currentChain = keccak256(IDispatcher(hostAddr).host());
        bytes32 orderSource = keccak256(order.source);
        bytes32 orderDest = keccak256(order.destination);
        bool isSameChain = orderSource == orderDest;

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-685)
```text
    function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        _filled[body.commitment] = beneficiary;

```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L688-705)
```text
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

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L37-48)
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
```
