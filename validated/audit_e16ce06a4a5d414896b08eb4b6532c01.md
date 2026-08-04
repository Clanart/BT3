### Title
Reentrancy through native-token output transfer in `_fillSameChain` lets an order creator drain escrow twice via `cancelOrder` mid-fill - ([File: evm/src/apps/intentsv2/IntrinsicIntents.sol])

### Summary
`_fillSameChain` marks an order as filled, then makes an unguarded external call (`beneficiary.call{value: ...}`) to a beneficiary address that is fully controlled by the order's own creator, *before* the escrowed input tokens for that fill are decremented. The escrow decrement only happens later inside `_withdraw`. Because `_cancelSameChain` never checks the in-progress fill state before refunding "remaining" escrow, an attacker who is both the order's `user` and its `output.beneficiary` can reenter `cancelOrder` from the receive hook while the escrow ledger still reflects the pre-fill amount, draining the same escrow that is also about to be paid out to the honest solver.

### Finding Description
`_fillSameChain` sets the "filled" marker unconditionally up front: [1](#0-0) 

For native-token outputs, it then makes a raw external call to `beneficiary` — an address chosen entirely by whoever created the order — while the corresponding escrow entry `_orders[commitment][token]` has **not yet been decremented**: [2](#0-1) 

The escrow amount for the just-transferred output is only read/decremented afterwards, and the actual escrow debit happens inside `_withdraw`, called once at the very end of the function: [3](#0-2) [4](#0-3) 

Meanwhile, `_cancelSameChain` — reachable by the order's `user` at any time — only checks that escrow is non-zero; it never checks whether a fill is currently mid-flight (e.g. via `_filled[commitment]` or a reentrancy lock): [5](#0-4) 

Attack sequence:
1. Attacker creates a same-chain order with `order.user = attacker` and `order.output.beneficiary = attacker's malicious contract`, requesting a native-token output.
2. An honest solver calls the public fill entrypoint (wrapping `_fillSameChain`) to fill the order.
3. Inside the loop, `beneficiary.call{value: beneficiaryTotal}("")` sends ETH to the attacker's contract. At this point `_orders[commitment][inputToken]` still holds the **full, undecremented** escrow for the order (decrement only happens later in `_withdraw`).
4. The attacker's `receive()`/fallback reenters `cancelOrder` → `_cancelSameChain`, which sees `hasEscrow = true` with the full original escrow amount and immediately refunds the full escrowed input tokens to the attacker via `_withdraw(body, true, true)`.
5. Control returns to the outer `_fillSameChain` call, which continues and calls `_withdraw(body, false, isFullyFilled)` for the solver's leg. Since escrow was already zeroed by the reentrant cancel, per-token amounts read as `0` and are silently skipped (`if (amount == 0) continue;`) rather than reverting — the outer call completes "successfully."
6. Net result: the honest solver already sent output tokens to the attacker's beneficiary contract in step 3, and the attacker also collected the full escrowed input tokens via the reentrant cancel in step 4. The solver receives nothing back.

This breaks the invariant that "bridged assets, order escrow, refunds... must move exactly once and only to the rightful beneficiary and amount." The escrow is paid out to two different recipients (the solver's expected input payment is diverted to the attacker as a "refund") for a single fill event, financed entirely by the solver's real token transfer.

### Impact Explanation
This is a direct, unprivileged fund-theft primitive against solvers filling same-chain intents: escrowed input tokens are released twice for the same commitment (once effectively to the solver's expected leg being nulled out, once refunded to the malicious order creator), while the solver's real output-token payment is not compensated. No relayer, prover, or admin cooperation is required — only a malicious order creator and a standard solver interaction, exactly the "unauthorized transaction / logic attack / double-settlement" class the bounty targets.

### Likelihood Explanation
Requires only: (a) the attacker to place a same-chain order with `output.beneficiary` set to a contract they control, (b) an unmodified public `fillOrder`/`cancelOrder` entrypoint lacking a reentrancy guard around this internal flow, and (c) any solver executing a native-token fill. All actions are performed via standard, permissionless public entrypoints (`placeOrder`, `fillOrder`, `cancelOrder`), matching the "public-entrypoint, unprivileged attacker" requirement. The severity is gated on whether the external `fillOrder`/`cancelOrder` wrapper functions (in the concrete `IntentGatewayV2` contract, not reviewed in this pass) add a `nonReentrant` guard; this was not confirmed within the available code and should be verified directly against `evm/src/apps/IntentGatewayV2.sol` before triage.

### Recommendation
- Apply checks-effects-interactions strictly in `_fillSameChain`: decrement `_orders[commitment][token]` (and any other escrow bookkeeping) *before* making the native-token `.call` to `beneficiary`.
- Add a reentrancy guard (e.g. OpenZeppelin `ReentrancyGuard`) shared across all external entrypoints that touch `_filled`, `_orders`, and `_partialFills` for a given commitment (`fillOrder`, `cancelOrder`, and their cross-chain equivalents).
- Have `_cancelSameChain` treat "a fill is in progress" as a first-class guarded state (not just relying on post-hoc `_filled` deletion), so a fill cannot be cancelled out from under itself mid-execution.

### Proof of Concept
Conceptual Foundry PoC outline (cannot be fully executed without the concrete `IntentGatewayV2` external entrypoints, which were not available in this review pass):
```solidity
contract MaliciousBeneficiary {
    IntentGatewayLike gateway;
    Order order;
    bool reentered;

    receive() external payable {
        if (!reentered) {
            reentered = true;
            gateway.cancelOrder(order); // drains full escrow to attacker (order.user)
        }
    }
}
```
1. Attacker deploys `MaliciousBeneficiary`, sets it as `order.output.beneficiary`, sets `order.user = attacker`, and calls `placeOrder` with a native-token input escrowed.
2. Honest solver calls `fillOrder(order, options)` with a native-token output matching `order.output.assets`.
3. During the output transfer in `_fillSameChain`, `MaliciousBeneficiary.receive()` reenters `cancelOrder`, refunding the full (still undecremented) escrow to the attacker.
4. Assert: attacker's balance increases by both the solver's output payment and the full escrowed input refund; solver's expected input token payment is zero.

### Citations

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L54-61)
```text
    function _fillSameChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;

        uint256 msgValue = msg.value;
        address beneficiary = address(uint160(uint256(order.output.beneficiary)));
        bool isFullyFilled = true;
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L99-111)
```text
            uint256 beneficiaryTotal = fillAmount + beneficiaryShare;

            if (token == address(0)) {
                if (msgValue < beneficiaryTotal + protocolShare) revert InsufficientNativeToken();
                msgValue -= (beneficiaryTotal + protocolShare);
                (bool sent,) = beneficiary.call{value: beneficiaryTotal}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransferFrom(msg.sender, beneficiary, beneficiaryTotal);
                if (protocolShare > 0) {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), protocolShare);
                }
            }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L116-134)
```text
            uint256 escrowedAmount;
            if (amountFilled == totalRequired) {
                escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
            } else {
                escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
            }
            escrowedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: escrowedAmount});
            outputFills[i] = TokenInfo({token: outputToken, amount: fillAmount});
        }

        // Orders carrying output calldata must be filled completely in a single fill.
        // The attached call is only executed on a full fill, so a partial fill would
        // leave the intended side effect unexecuted while releasing proportional escrow.
        if (order.output.call.length > 0 && !isFullyFilled) revert PartialFillNotAllowed();

        WithdrawalRequest memory body = WithdrawalRequest({
            commitment: commitment, tokens: escrowedInputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
        });
        _withdraw(body, false, isFullyFilled);
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L161-187)
```text
    function _cancelSameChain(Order calldata order, bytes32 commitment) internal {
        if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();

        address hostAddr = host();
        bytes32 currentChain = keccak256(IDispatcher(hostAddr).host());
        bytes32 orderSource = keccak256(order.source);
        if (orderSource != currentChain) revert WrongChain();

        uint256 inputsLen = order.inputs.length;
        TokenInfo[] memory remainingTokens = new TokenInfo[](inputsLen);
        bool hasEscrow = false;
        for (uint256 i; i < inputsLen;) {
            address token = address(uint160(uint256(order.inputs[i].token)));
            uint256 escrowed = _orders[commitment][token];
            if (escrowed > 0) hasEscrow = true;
            remainingTokens[i] = TokenInfo({token: order.inputs[i].token, amount: escrowed});
            unchecked {
                ++i;
            }
        }
        if (!hasEscrow) revert UnknownOrder();

        WithdrawalRequest memory body =
            WithdrawalRequest({commitment: commitment, tokens: remainingTokens, beneficiary: order.user});

        _withdraw(body, true, true);
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
