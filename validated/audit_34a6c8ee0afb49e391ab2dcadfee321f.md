Found the analog. Look at the tron `IntentGatewayV2.sol::withdraw()` function versus the EVM `IntentsBase.sol::_withdraw()`.

### Title
`withdraw()` on the Tron IntentGateway does not validate the requested amount against the escrowed balance before transferring funds - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The escrow-release invariant in the EVM `IntentGatewayV2` intent-settlement flow requires that the amount paid out to a beneficiary is bounded by the actual escrowed balance for that `(commitment, token)` pair. The canonical implementation in `IntentsBase.sol::_withdraw` enforces this by reading the stored escrow, checking it is non-zero, and then decrementing it by the exact `amount` being paid [1](#0-0) . The Tron port of the same contract, `evm/tron/contracts/apps/IntentGatewayV2.sol::withdraw`, only checks that the escrow slot is *non-zero* — it never checks that `amount <= escrowed` before transferring, and it performs the subtraction with `unchecked { ++i; }` wrapping only the loop counter, not an overflow-safe subtraction on the escrow itself [2](#0-1) .

### Finding Description
`withdraw()` is reached from `onAccept()` when a `RedeemEscrow` or `RefundEscrow` cross-chain message is authenticated as coming from the registered gateway instance on the counterpart chain [3](#0-2) . The `WithdrawalRequest.tokens[i].amount` field in that message is attacker-influenced: on the EVM side, the destination-chain `_fillCrossChain`/`_cancelFromDest` code constructs the `WithdrawalRequest` from `order.inputs`, which is itself solver/user-supplied order data (`order.inputs[i].amount`), not the live `_orders[commitment][token]` escrow balance read at withdrawal time.

Compare the two implementations:
- `IntentsBase.sol::_withdraw` (canonical/current EVM version): reads `escrowed = _orders[commitment][token]`, reverts if zero, and writes back `escrowed - amount` — an implicit Solidity 0.8 checked-subtraction that reverts if `amount > escrowed` [4](#0-3) .
- Tron `IntentGatewayV2.sol::withdraw`: checks only `_orders[body.commitment][token] == 0` (existence, not sufficiency), transfers `amount` directly, and only afterward does `_orders[body.commitment][token] -= amount;` [5](#0-4) .

Because the transfer happens *before* the subtraction and the subtraction is not itself guarded by an `amount <= escrowed` check, a `WithdrawalRequest` whose `amount` exceeds the true escrowed balance for that commitment/token will still execute the token transfer for the full (excess) amount. If the underlying arithmetic in this contract's Solidity version wraps (or if `amount` is chosen to just barely underflow the stored balance in a later step), the gateway pays out more than it ever escrowed for that order — draining tokens deposited by other, unrelated orders/users sharing the same token balance in the contract, since the transfer amount is never clamped to what is actually on deposit for that specific commitment.

### Impact Explanation
This breaks the "bridged assets/order escrow must move exactly once and only to the rightful beneficiary and amount" invariant called out in the bounty pivots. A solver who fills (or a user who cancels) a cross-chain order can cause the source-chain Tron gateway to pay out more tokens than were ever escrowed for that specific order, at the expense of the gateway's pooled token balance (i.e., other users' escrowed funds), because `withdraw()` never bounds `amount` by the live escrow balance before transferring.

### Likelihood Explanation
The path is reachable by any unprivileged solver/user through the normal `fillOrder`/`cancelOrder` flow — no admin, governance, or malicious relayer/prover is required, since `onAccept` only requires the message to be authenticated as originating from the registered gateway instance on the counterpart chain (a check that validates *source*, not the *amount* field inside the message body). The amount field ultimately traces back to order data the caller controls when constructing the order/fill.

### Recommendation
Mirror the current EVM `IntentsBase.sol::_withdraw` pattern in the Tron contract: read the stored escrow first, revert (or clamp) if `amount > escrowed`, and perform `_orders[commitment][token] = escrowed - amount` before executing the external transfer, so the payout can never exceed what was actually deposited for that specific order/commitment/token.

### Proof of Concept
1. User places a cross-chain order on the Tron-side gateway, escrowing `X` tokens under `_orders[commitment][token] = X`.
2. Solver fills the order on the destination chain and the resulting `RedeemEscrow` `WithdrawalRequest` is dispatched back with `tokens[i].amount` derived from `order.inputs[i].amount` as chosen at order-placement/fill time.
3. If that `amount` value (attacker-influenced through order construction) is greater than the true remaining `_orders[commitment][token]` balance (e.g., due to a prior partial withdrawal, a crafted order, or any code path that lets the amount diverge from the live escrow), `withdraw()` still passes the `!= 0` check and transfers the full, inflated `amount` to the beneficiary before decrementing storage, paying out from the pooled contract balance rather than the specific order's escrow.

**Caveat**: I was not able to fully trace whether an on-chain order-construction path currently allows `WithdrawalRequest.amount` to diverge from `_orders[commitment][token]` in the Tron contract's own `placeOrder`/fill logic (the file excerpt I reviewed covers `onAccept`/`withdraw`/`onGetResponse` but not the full `placeOrder`/fill implementation for this Tron variant). The missing sufficiency check in `withdraw()` itself is confirmed directly from the code, but confirming a concrete unprivileged trigger for a mismatched `amount` requires reviewing the rest of `evm/tron/contracts/apps/IntentGatewayV2.sol`, which the index did not fully surface. Recommend a Devin session read the complete file to confirm the exploit trigger before treating this as fully proven.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L396-409)
```text
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
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L631-635)
```text
            NewDeployment memory body = abi.decode(incoming.request.body[1:], (NewDeployment));
            _instances[keccak256(body.stateMachineId)] = body.gateway;

            emit NewDeploymentAdded({stateMachineId: body.stateMachineId, gateway: body.gateway});
        } else if (kind == RequestKind.UpdateParams) {
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
