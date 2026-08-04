## Analog Found: Same-Chain Cancel Underflow Locks Escrowed Funds When Protocol Fees Are Enabled

### Title
Same-chain `cancelOrder` refunds the pre-fee order amount instead of the actual escrowed balance, causing a guaranteed underflow revert that permanently locks user funds - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
`IntentGatewayV2.cancelOrder`'s same-chain branch builds the refund `WithdrawalRequest` from the raw `order.inputs` (the pre-protocol-fee amounts) instead of the amount actually tracked in `_orders[commitment][token]` (the post-fee, reduced amount that was escrowed at `placeOrder`). `withdraw()` only guards against a fully-zeroed balance (`if (_orders[...][token] == 0) revert UnknownOrder();`) and never checks that the amount being paid out does not exceed the escrowed balance, exactly the missing-check pattern described in the external report. Because Solidity 0.8's checked arithmetic reverts on underflow, `_orders[commitment][token] -= amount` always reverts whenever `protocolFeeBps > 0`, making same-chain cancellation permanently impossible for any fee-bearing order and locking the user's escrow forever if no solver ever fills it.

### Finding Description
At order placement, the escrow ledger is credited with the **reduced** (post-protocol-fee) amount: [1](#0-0) [2](#0-1) 

But the same-chain cancellation path builds the refund request from the **original**, unreduced `order.inputs`: [3](#0-2) 

`withdraw()` then executes the transfer and decrement without ever validating `amount <= _orders[commitment][token]`: [4](#0-3) 

When `protocolFeeBps > 0`, `order.inputs[i].amount` (used as the refund amount) is strictly greater than `_orders[commitment][token]` (the actual escrow, which already excludes the protocol fee). The checked subtraction `_orders[body.commitment][token] -= amount` therefore always underflows and reverts, but only *after* the token/native transfer statement executes in code order — the whole transaction is nonetheless rolled back atomically. The net effect is that `cancelOrder`'s same-chain branch can never succeed for a fee-bearing order.

Contrast this with the equivalent EVM implementation, which correctly reads the live escrow balance rather than the raw order amount before refunding: [5](#0-4) 

### Impact Explanation
Any user who places a same-chain order on a deployment with a non-zero `protocolFeeBps` (global `_params.protocolFeeBps` or a per-destination override) can never cancel that order through the same-chain path — the call always reverts. If no solver fills the order before or after its deadline, the user's escrowed tokens are permanently stuck in the contract with no recovery path (cancellation is the only user-controlled escape hatch for an unfilled order). This is a direct loss/lock of user funds triggered purely by normal fee configuration, not by any malicious peer, relayer, or governance actor.

### Likelihood Explanation
This triggers deterministically, with no attacker required, whenever `protocolFeeBps` is non-zero — the expected production configuration for revenue-generating deployments — and a same-chain order is placed and later needs to be cancelled. It requires no proof forgery, no privileged access, and no race condition; it is a straightforward broken-invariant bug in the amount used for refund vs. the amount actually escrowed.

### Recommendation
In `cancelOrder`'s same-chain branch, build the refund `WithdrawalRequest` from the live `_orders[commitment][token]` balances (as the EVM `IntrinsicIntents.sol::_cancelSameChain` does) rather than from `order.inputs`. Additionally, harden `withdraw()` itself to explicitly check `amount <= _orders[body.commitment][token]` and revert with a clear error before attempting any transfer, rather than relying on an implicit underflow revert.

### Proof of Concept
1. Deploy `IntentGatewayV2` (Tron) with `_params.protocolFeeBps > 0` (or set a non-zero `_destinationProtocolFees[destinationHash]`).
2. Call `placeOrder` for a same-chain order (`order.source == order.destination`) with a non-zero-fee token input. `_orders[commitment][token]` is credited with `reducedAmount = amount - protocolFee`.
3. Before the order is filled, call `cancelOrder(order, options)` from `order.user`.
4. The same-chain branch builds `body.tokens = order.inputs` (the full, unreduced amount) and calls `withdraw(body, true)`.
5. Inside `withdraw`, the transfer of the full `order.inputs[i].amount` is attempted, then `_orders[commitment][token] -= amount` underflows (`reducedAmount - originalAmount < 0`) and reverts the entire transaction.
6. `cancelOrder` can never succeed for this order; the escrowed tokens remain locked in the contract indefinitely if the order is never filled.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L356-358)
```text
                uint256 originalAmount = order.inputs[i].amount;
                uint256 protocolFee = (originalAmount * protocolFeeBps) / 10_000;
                uint256 reducedAmount = originalAmount - protocolFee;
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L456-457)
```text
                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;
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
