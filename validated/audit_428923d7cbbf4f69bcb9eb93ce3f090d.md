## Analysis

Reducing the external report to its core broken invariant: **an untrusted/derived length or amount value is used directly in a sensitive operation (memcpy / hash) without ever being checked against the authoritative record that should bound it.** In `fd_bank.c`, `payload_sz` (or related fields) is trusted and fed straight into `fd_blake3_append` instead of being validated against the real transaction bounds.

The Hyperbridge analog is in `IntentGatewayV2.withdraw()` in `evm/tron/contracts/apps/IntentGatewayV2.sol`. The authoritative escrow ledger `_orders[commitment][token]` records the **protocol-fee-reduced** amount that was actually locked at `placeOrder()` time: [1](#0-0) 

but the amount used to both pay out and decrement escrow in `withdraw()` comes from `body.tokens[i].amount`, which `cancelOrder()` populates from the **original, pre-fee** `order.inputs`, not from the reduced amount that was actually escrowed: [2](#0-1) [3](#0-2) 

`withdraw()` never validates `amount <= _orders[commitment][token]`; it only checks that the entry is non-zero, then performs the external transfer with the untrusted `amount`, and only afterwards attempts to decrement the ledger: [4](#0-3) 

### Title
Unvalidated withdrawal amount in `IntentGatewayV2.withdraw()` causes escrow ledger underflow, permanently locking order refunds - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`withdraw()` uses the caller-supplied `body.tokens[i].amount` for both the outbound token/ETH transfer and the escrow-ledger decrement, but only checks that the ledger entry is non-zero — never that it is `>= amount`. Whenever an order was placed with a non-zero protocol fee, `_orders[commitment][token]` holds the fee-reduced amount, while every cancellation/refund path (`cancelOrder`, same-chain and cross-chain `RefundEscrow`) rebuilds the `WithdrawalRequest` from the original, un-reduced `order.inputs`. The mismatch guarantees `_orders[commitment][token] -= amount` underflows and reverts (Solidity 0.8 checked math), so the whole transaction reverts and refunds/cancellations for fee-bearing orders can never complete — the user's escrowed principal is permanently stuck in the contract.

### Finding Description
1. At `placeOrder()`, when `protocolFeeBps > 0`, the amount actually escrowed under `_orders[commitment][token]` is `reducedInputs[i].amount = originalAmount - protocolFee` [5](#0-4) [6](#0-5) .
2. Every path that later builds a `WithdrawalRequest` for that same order — same-chain cancel, cross-chain `RefundEscrow` dispatch — uses `order.inputs` (the full, pre-fee amount), not the reduced amount that was actually locked [7](#0-6) [8](#0-7) .
3. `withdraw()` takes this un-reduced `amount` at face value: it checks only `_orders[body.commitment][token] == 0` (existence), then performs the external transfer of the *full* amount, and only afterward tries `_orders[body.commitment][token] -= amount` [9](#0-8) .
4. Because the ledger holds strictly less than the amount being subtracted (by exactly the protocol fee), the subtraction underflows. Solidity ≥0.8 checked arithmetic reverts the entire call, rolling back the preceding transfer too — so no single call can ever complete a cancel/refund on a fee-bearing order.

This is the same class of defect as the report's core primitive: a value that must be bounded by an authoritative record (`payload_sz` vs. real txn size / `_orders[commitment][token]` vs. requested `amount`) is instead trusted and used directly in a critical operation (`fd_blake3_append` / external transfer + ledger update) with no check tying the two together.

### Impact Explanation
Any order placed while a non-zero protocol fee is configured (`_params.protocolFeeBps` or a destination-specific override in `_destinationProtocolFees`) can never be cancelled or refunded through `cancelOrder()`'s same-chain path, nor via the cross-chain `RefundEscrow` flow — every attempt reverts. The user's escrowed principal remains permanently locked in the `IntentGatewayV2` contract with no recovery path through the contract's own logic, meeting the "stealing or loss of funds" bar (funds are irrecoverably lost to the user, effectively donated to the contract's pooled balance). This is not a peer/relayer/admin-dependent condition — the order owner is an ordinary, unprivileged user calling a public entrypoint (`cancelOrder`).

### Likelihood Explanation
This triggers deterministically, with no attacker required, on the ordinary happy-path of order cancellation/refund whenever `protocolFeeBps > 0` is configured (which is the expected production configuration for a fee-generating bridge). It requires no malicious peer, relayer, or governance action — only a normal user attempting to reclaim escrowed funds after placing a fee-bearing order that is later cancelled or times out on the destination.

### Recommendation
In `cancelOrder()`, build the `WithdrawalRequest.tokens` amounts from the same fee-reduced values that were actually escrowed (mirror the `reducedInputs` computation from `placeOrder`, or store/derive the reduced amount as part of the order state), and in `withdraw()` explicitly bound-check `amount <= _orders[commitment][token]` before performing the external transfer (checks-effects-interactions: update the ledger before the external call, not after).

### Proof of Concept
1. Governance sets `_params.protocolFeeBps` (or a `_destinationProtocolFees[dest]`) to a non-zero value.
2. A user calls `placeOrder()` with `order.inputs = [{token: T, amount: A}]`. `_orders[commitment][T]` is set to `A - fee` (see `placeOrder`, lines 356‑364 & 444‑463).
3. The user calls `cancelOrder(order, options)` on the same chain as `order.source == order.destination`, before the order is filled.
4. `cancelOrder` builds `WithdrawalRequest{ commitment, tokens: order.inputs, beneficiary: order.user }` — i.e. `amount = A` (full, not `A - fee`) — and calls `withdraw(body, true)`.
5. Inside `withdraw()`, `_orders[commitment][T] == (A - fee)` is non-zero so it passes the guard; the contract attempts `token.call(transfer, beneficiary, A)` then `_orders[commitment][T] -= A`, which underflows `(A - fee) - A` and reverts.
6. The whole `cancelOrder` transaction reverts. The user has no other on-chain path in this contract to reclaim the `A - fee` that was escrowed; the funds are stuck.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L353-378)
```text
        if (protocolFeeBps > 0) {
            reducedInputs = new TokenInfo[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                uint256 originalAmount = order.inputs[i].amount;
                uint256 protocolFee = (originalAmount * protocolFeeBps) / 10_000;
                uint256 reducedAmount = originalAmount - protocolFee;
                address token = address(uint160(uint256(order.inputs[i].token)));

                // Emit DustCollected for protocol fee if non-zero
                if (protocolFee > 0) emit DustCollected(token, protocolFee);

                reducedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: reducedAmount});
                unchecked {
                    ++i;
                }
            }

            // Temporarily swap inputs to calculate commitment with reduced amounts
            TokenInfo[] memory originalInputs = order.inputs;
            order.inputs = reducedInputs;
            commitment = keccak256(abi.encode(order));
            order.inputs = originalInputs;
        } else {
            // No protocol fees, use order.inputs directly
            reducedInputs = order.inputs;
            commitment = keccak256(abi.encode(order));
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L444-463)
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

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L519-530)
```text
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L578-591)
```text
        } else if (currentChain == orderDest) {
            // destination chain: dispatch RefundEscrow request to source chain
            // If order hasn't expired, only owner can cancel
            if (order.deadline >= block.number) {
                if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();
            }

            // Mark as cancelled locally to prevent fills
            _filled[commitment] = address(uint160(uint256(order.user)));

            bytes memory body = bytes.concat(
                bytes1(uint8(RequestKind.RefundEscrow)),
                abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}))
            );
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
