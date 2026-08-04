### Title
`IntentGatewayV2.placeOrder()` (Tron variant) accepts zero-amount escrow inputs via the predispatch path, creating phantom orders that trap solver funds - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron variant of `IntentGatewayV2.placeOrder()` validates that input amounts are nonzero only on the direct-transfer path, but omits this check entirely on the predispatch (call-dispatcher) path. This lets a user place an order whose escrowed input amount is `0`, which is later exposed to solvers as a normal fillable order. A solver who fills it delivers real output tokens on the destination chain, then the `RedeemEscrow` withdrawal on the source chain reverts because the corresponding escrow entry is `0`, permanently trapping the solver's already-delivered assets — the same “accept a zero deposit and lock/corrupt the pool” invariant break as the `DAOfiV1Pair.deposit()` report.

### Finding Description
In the non-predispatch branch of `placeOrder`, a nonzero-amount guard is present: [1](#0-0) 

But the predispatch branch, which funds inputs indirectly through the `CallDispatcher`, has no equivalent check on `order.inputs[i].amount` (the `requiredAmount`): [2](#0-1) 

Because `requiredAmount` can be `0`, the guard `if (balance < requiredAmount) revert InvalidInput();` is trivially satisfied by any `balance >= 0`, so the branch never reverts even when nothing was actually escrowed for that token. The escrow map is then credited with the (also zero, if `reducedInputs[i].amount` is zero) amount: [3](#0-2) 

The resulting order is emitted through the normal `OrderPlaced` event and looks like any other order to solvers/indexers/SDKs, with a valid commitment. When a solver later fills it on the destination chain and the settlement message reaches `withdraw()`, the zero-value escrow entry causes the redemption to revert: [4](#0-3) 

Specifically `if (_orders[body.commitment][token] == 0) revert UnknownOrder();` fires because the escrow was never actually funded for that token, even though the order was accepted at placement. This mirrors the reported bug's root cause exactly: the deposit/escrow entrypoint doesn't enforce a nonzero, backed amount, so a caller can create a "phantom" record that later corrupts a downstream invariant (here, "an accepted order always has redeemable escrow").

### Impact Explanation
A solver who observes and fills the phantom order sends real output tokens to the beneficiary on the destination chain (per normal `fillOrder` flow) before the `RedeemEscrow` message is even dispatched back to source. Since the source-side `withdraw()` reverts with `UnknownOrder()` for the zero-escrow token, the solver's `RedeemEscrow` settlement can never complete for that leg, and the tokens the solver already delivered on the destination side are not compensated by the (nonexistent) source escrow. This is unauthorized/asymmetric fund loss for an honest, unprivileged actor (the solver), triggered purely by a malformed-but-accepted user order — not by a malicious relayer, prover, or admin.

### Likelihood Explanation
The path requires only an unprivileged, ordinary user calling the public `placeOrder()` entrypoint with `predispatch.call.length > 0`, `predispatch.assets.length > 0`, and `order.inputs[i].amount = 0`. No collusion, front-running, leaked keys, or governance/admin action is needed; it is directly reachable by any EOA that wants to grief solvers.

### Recommendation
Add the same nonzero-amount validation used in the direct-transfer branch to the predispatch branch of `placeOrder()` in `evm/tron/contracts/apps/IntentGatewayV2.sol`:
- Require `order.inputs[i].amount > 0` before or during the escrow-crediting loop (mirroring the check already present in the non-predispatch branch and in `evm/src/apps/IntentGatewayV2.sol`'s Phase 1 checks at lines 210/233/283).
- Additionally verify `reducedInputs[i].amount > 0` after protocol-fee reduction so fee rounding cannot zero out an otherwise-valid amount.

### Proof of Concept
1. Attacker calls `placeOrder(order, graffiti)` on the Tron `IntentGatewayV2` with:
   - `order.predispatch.call` set to any no-op/self-call, `order.predispatch.assets` set to a trivial nonzero entry (satisfies the `>0` length checks),
   - `order.inputs[0].amount = 0` for the token the attacker wants to weaponize.
2. `placeOrder` proceeds through the predispatch branch; `requiredAmount = 0` passes the `balance < requiredAmount` check unconditionally, `_orders[commitment][token] += reducedInputs[0].amount` credits `0`, and `OrderPlaced` is emitted looking like a normal order.
3. A solver calls `fillOrder` on the destination chain, delivering real output assets to the beneficiary, and the destination dispatches `RedeemEscrow` back to source.
4. On source, `onAccept` → `withdraw()` executes `if (_orders[body.commitment][token] == 0) revert UnknownOrder();` at [5](#0-4) , reverting the redemption — the solver's already-delivered output tokens are not reimbursed by any source-side escrow.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L410-440)
```text
            // Transfer tokens from call dispatcher back to IntentGateway
            Call[] memory transferCalls = new Call[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 requiredAmount = order.inputs[i].amount;
                uint256 balance;

                if (token == address(0)) {
                    balance = address(dispatcher).balance;
                    if (balance < requiredAmount) revert InsufficientNativeToken();
                    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
                } else {
                    balance = IERC20(token).balanceOf(dispatcher);
                    if (balance < requiredAmount) revert InvalidInput();
                    transferCalls[i] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                }

                uint256 dust = balance - requiredAmount;
                if (dust > 0) emit DustCollected(token, dust);

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L444-454)
```text
        } else {
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                if (token == address(0)) {
                    // native token
                    if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
                    msgValue -= order.inputs[i].amount;
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
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
