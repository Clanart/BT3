### Title
Cancel/refund path releases the gross escrowed amount while escrow only holds the fee-reduced amount, causing a permanent underflow revert that locks user principal - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2.sol` credits escrow with the **protocol-fee-reduced** input amount at order placement, but the cancel/refund flow later tries to release the **gross** (pre-fee) amount from that same escrow slot. Because Solidity 0.8 arithmetic reverts on underflow, this mismatch causes `withdraw()` to always panic for any order that had a nonzero protocol fee, permanently locking the user's escrowed principal — the same "subtract-an-adjusted-value-and-underflow" root cause as the Morpho `_calculateMaxBorrowCollateral` bug, just applied to intent escrow accounting instead of a borrow limit.

### Finding Description
At order placement, the contract explicitly stores the **reduced** amount (net of protocol fees) as the escrow balance: [1](#0-0) [2](#0-1) 

Both branches explicitly comment "Store reduced amount (after protocol fees) in escrow" and write `reducedInputs[i].amount` (net of fee) into `_orders[commitment][token]`, while the user/solver actually transferred the full `order.inputs[i].amount` (gross) into the contract.

When an order is later cancelled on the source chain and proven unfilled on the destination, the refund `WithdrawalRequest` is built directly from the **gross** `order.inputs` array, not the reduced amounts: [3](#0-2) 

That request eventually reaches `withdraw()`, which subtracts the requested (gross) `amount` from the stored (net, fee-reduced) escrow balance: [4](#0-3) 

`_orders[body.commitment][token] -= amount;` at line 701 is ordinary checked arithmetic (not wrapped in `unchecked`). Whenever a nonzero protocol fee was applied at placement, `_orders[commitment][token]` (net) is strictly less than `body.tokens[i].amount` (gross, taken from `order.inputs`), so this subtraction underflows and the entire transaction reverts with a Solidity arithmetic panic — exactly the underflow-revert pattern described in the Morpho report, just triggered by a fee-reduced balance rather than a leverage-reduced borrow limit.

### Impact Explanation
This is a direct, unconditional loss/lock-of-funds bug that fits the Impact Gate:
- Every order placed with `order.fees` / protocol fee > 0 that is later cancelled (deadline passed, or owner-initiated on the destination side) will have its refund path call `withdraw()` and always revert.
- Because `withdraw()` reverts, `_filled[body.commitment]` is never persisted (state changes roll back), so the failure is deterministic and repeatable — the escrow can never be drained through this code path.
- The user's principal (net of protocol fee, which was already collected/escrowed) becomes permanently stuck in the contract with no functioning recovery path, since the same broken accounting is used both for `RefundEscrow` on the source chain and the destination-chain cancellation flow that marks the order `_filled` before dispatching the refund request.
- This requires no malicious relayer, prover, or governance actor — it is a pure unprivileged-user flow (place order with a fee → cancel it) triggering guaranteed fund lock, matching "stealing or loss of funds" / "logic attacks" in the required impact list.

### Likelihood Explanation
High. The bug triggers deterministically any time `order.fees` (or the destination protocol fee) is nonzero and the order is cancelled/refunded — no race condition, no attacker-controlled proof forgery, and no reliance on a compromised actor. Any ordinary user cancelling a fee-bearing order hits this path.

### Recommendation
Build the refund/cancel `WithdrawalRequest.tokens` from the same reduced (post-fee) amounts that were actually credited to `_orders[commitment][token]` at placement (i.e. `reducedInputs`, not `order.inputs`), or alternatively have `withdraw()` release `min(amount, _orders[commitment][token])` and clamp/floor to zero instead of doing an unchecked-assumption subtraction. Add a regression test that places an order with `fees > 0`, cancels it, and asserts the refund succeeds and returns exactly the escrowed (net) amount.

### Proof of Concept
1. Solver/user calls `placeOrder` with `order.fees > 0` (or a destination chain that has a nonzero `DestinationProtocolFeeUpdated` fee configured). `_orders[commitment][token]` is credited with `reducedInputs[i].amount < order.inputs[i].amount`.
2. Before the order is filled, the deadline passes (or the user cancels the order on the destination chain per the branch at lines 578–591).
3. The cancellation path constructs `WithdrawalRequest{ tokens: order.inputs, ... }` using the gross amounts.
4. `withdraw()` executes `_orders[commitment][token] -= amount` where `amount == order.inputs[i].amount` (gross) but the stored balance is only `reducedInputs[i].amount` (net) — the subtraction underflows and the transaction panics/reverts.
5. Every subsequent retry hits the identical underflow, so the escrowed principal is permanently locked in the contract with no way to release it via `withdraw()`.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L433-440)
```text

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L445-462)
```text
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
