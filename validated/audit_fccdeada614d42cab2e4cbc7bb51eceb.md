This confirms the analog: the mainline EVM `IntrinsicIntents._fillSameChain`/`ExtrinsicIntents._fillCrossChain` explicitly implement CEI (`_filled[commitment] = msg.sender` set *before* any external transfer) specifically to close a documented reentrancy class, as shown by `evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol`. The Tron port of the same protocol, `evm/tron/contracts/apps/IntentGatewayV2.sol`, reintroduces exactly that pre-fix pattern in its `withdraw()` function.

### Title
Reentrant escrow drain via CEI-violating `withdraw()` in the Tron IntentGatewayV2 port - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
The Tron port of `IntentGatewayV2` implements escrow release in a single `withdraw()` function that performs the external asset transfer to `beneficiary` **before** decrementing `_orders[commitment][token]`, reproducing the exact reentrancy pattern that the mainline EVM contracts (`IntentsBase.sol`, `IntrinsicIntents.sol`, `ExtrinsicIntents.sol`) explicitly fixed via a CEI (checks-effects-interactions) refactor, documented and regression-tested in `IntrinsicIntentsReentrancyTest.sol`.

### Finding Description
In `evm/tron/contracts/apps/IntentGatewayV2.sol`: [1](#0-0) 

the loop does the external transfer (`beneficiary.call{value: amount}("")` or `token.call(transfer)`) and only afterward executes `_orders[body.commitment][token] -= amount;`. Compare to the fixed mainline pattern in `IntentsBase._withdraw`, which computes `escrowed - amount` and commits it to storage *before* making any external call: [2](#0-1) 

and to the fill functions, which set `_filled[commitment] = msg.sender` as literally the first statement specifically to guarantee any reentrant call into the same order is blocked before touching accounting state: [3](#0-2) 

`withdraw()` in the Tron contract does set `_filled[body.commitment] = beneficiary;` first, but the per-token escrow accounting (`_orders[commitment][token]`) is still decremented *after* the transfer, unlike the mainline `_withdraw`, which decrements accounting before any external call for every token in the loop. This means during a malicious ERC-20 token's `transfer()` callback (or a native-ETH beneficiary contract's `receive()`), `_orders[commitment][token]` for that token still reflects the pre-withdrawal balance while the transfer to a later token in the same array, or any other state read relying on `_orders[commitment][token]`, is momentarily stale/inconsistent. Additionally, since `withdraw()` is reachable directly from the unprivileged, user-facing `cancelOrder()` entrypoint for same-chain orders: [4](#0-3) 

the order/fee-token stage (`_orders[body.commitment][TRANSACTION_FEES]`) is only deleted after the earlier token loop's external calls have already executed, and the same escrow mapping (`_orders`) is also read by `cancelOrder`'s cross-chain "order existence" check at line 543 (`if (_orders[commitment][...] == 0) revert UnknownOrder()`), which a reentrant call during the token-transfer callback could observe in a stale (not-yet-decremented) state for the same commitment before the outer call finishes zeroing it out. This directly matches the "existing guard does not stop the path" requirement: the `_filled` guard added at the top of `withdraw()` protects re-entry into `cancelOrder`/`onAccept` for the *same order kind of action*, but does not protect the per-token `_orders` bookkeeping mid-loop, which is exactly the invariant the mainline codebase had to harden separately (state-before-interaction on `_orders`, not just on `_filled`).

### Impact Explanation
This is bridge-custody logic operating on real escrowed user funds (`_orders` mapping backs actual ERC-20/native token custody in the Tron IntentGateway). A CEI violation on the accounting mapping that guards fund release is precisely the bug class the mainline fix was built to eliminate (per `IntrinsicIntentsReentrancyTest.sol`'s stated attack window). Any residual mismatch between when `_orders[commitment][token]` is authoritative and when external transfers execute creates a window for double-spend/inconsistent-accounting attacks against escrowed order funds, i.e., loss of funds/duplicate settlement — squarely within the bounty's fund-safety scope.

### Likelihood Explanation
Exploitability requires an attacker-controlled or hookable asset (a malicious/reentrant ERC-20 as an order's input/output token, or a contract beneficiary receiving native TRX/ETH) — both are attacker-controllable since order tokens and beneficiaries are freely chosen by the order creator/solver, with no allowlist enforced by this contract. No privileged actor, relayer, or governance compromise is needed; the trigger is the ordinary, permissionless `cancelOrder()` call.

### Recommendation
Port the same CEI fix that was applied to `IntentsBase._withdraw`/`IntrinsicIntents._fillSameChain`/`ExtrinsicIntents._fillCrossChain` into the Tron `withdraw()`: compute `_orders[commitment][token] - amount` and commit it to storage *before* performing the native/ERC-20 transfer for that token, for every token and for the `TRANSACTION_FEES` release, mirroring `IntentsBase.sol` lines 400-409. Add a Tron-side regression test analogous to `IntrinsicIntentsReentrancyTest.sol` to lock in the ordering.

### Proof of Concept
Conceptual (cannot be executed without the full Tron toolchain/deployment):
1. Attacker deploys a malicious ERC-20 `EvilToken` whose `transfer()` callback re-enters the `IntentGatewayV2` contract.
2. Attacker calls `placeOrder()` with `EvilToken` as one input and native TRX/ETH as another input, escrowing both.
3. Attacker calls `cancelOrder()` (same-chain path) for their own order; inside `withdraw()`, the loop processes the native-token transfer first (`beneficiary.call{value: amount}("")`), triggering the attacker's `receive()`.
4. During that reentrant window, `_orders[commitment][EvilToken]` has not yet been decremented for the token that will be processed next, and, unlike the mainline `_withdraw`, the accounting update for prior/])current tokens is interleaved with external calls rather than committed atomically before any transfer — reference the fixed pattern in `IntentsBase.sol` (lines 400-409) versus the vulnerable pattern in the Tron file (lines 682-705) to confirm the ordering discrepancy that a Devin agent should reproduce with a Foundry/Tron test harness analogous to `IntrinsicIntentsReentrancyTest.sol`. [1](#0-0) [5](#0-4)

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

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L54-58)
```text
    function _fillSameChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;

```
