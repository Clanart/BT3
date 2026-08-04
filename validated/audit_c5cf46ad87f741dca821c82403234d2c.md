## Finding

### Title
Escrow accounting uses pre-transfer "expected" input amount instead of actual received balance in `IntentGatewayV2.placeOrder` (Tron) - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Tron variant of `IntentGatewayV2.placeOrder()` credits the escrow ledger `_orders[commitment][token]` with the pre-computed "expected" (fee-reduced) input amount instead of the amount actually received by the contract. For any input token whose `transferFrom` delivers less than the requested amount (fee-on-transfer / deflationary / rebasing ERC-20s), the contract's internal escrow bookkeeping becomes inflated relative to its real token balance.

### Finding Description
In the non-predispatch escrow path of `placeOrder`, the contract does: [1](#0-0) 

```solidity
} else {
    for (uint256 i; i < inputsLen;) {
        if (order.inputs[i].amount == 0) revert InvalidInput();
        address token = address(uint160(uint256(order.inputs[i].token)));
        if (token == address(0)) {
            ...
        } else {
            IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
        }
        // Store reduced amount (after protocol fees) in escrow
        _orders[commitment][token] += reducedInputs[i].amount;
        ...
    }
}
```

`reducedInputs[i].amount` is derived purely from `order.inputs[i].amount` (the caller-specified, pre-transfer value) minus the protocol fee — it is never reconciled against the token balance actually received by `safeTransferFrom`.

This is the exact same "expected-amount" pattern flagged in the external report: the contract computes what it *expects* to receive/hold and acts on that number, without ever verifying that the real balance matches.

Contrast this with the sibling implementation in `evm/src/apps/IntentGatewayV2.sol`, which was hardened against exactly this class of bug by measuring the actual balance delta: [2](#0-1) 

```solidity
uint256 balBefore = IERC20(token).balanceOf(address(this));
IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
order.inputs[i].amount = IERC20(token).balanceOf(address(this)) - balBefore;
```

and the Tron predispatch branch, which also correctly checks actual balance before crediting escrow: [3](#0-2) 

The plain (non-predispatch) branch — which is the default path used by ordinary `placeOrder` calls — was not given this same protection.

Later, when the order is filled and settled, `withdraw()` pays out the amount recorded at placement time (via the commitment-bound `reducedInputs`/`order.inputs`, propagated through `WithdrawalRequest.tokens`), not the amount actually custodied: [4](#0-3) 

```solidity
function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
    ...
    uint256 amount = body.tokens[i].amount;
    if (_orders[body.commitment][token] == 0) revert UnknownOrder();
    ...
    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
    if (!success) revert TransferFailed();
    _orders[body.commitment][token] -= amount;
    ...
}
```

The check is only `_orders[commitment][token] == 0` (existence), never a comparison against real contract balance.

### Impact Explanation
Because `_orders[commitment][token]` is a shared per-token ledger (not a segregated vault per commitment), an inflated escrow credit for one order silently overstates the contract's claimed liabilities for that token pool. When any order using a deflationary/fee-on-transfer token is placed, the contract will have credited more tokens to `_orders` than it physically holds. Two outcomes follow:
- The affected order's own `withdraw()`/redemption reverts (funds locked), matching the original report's "locking up tokens" impact, or
- If the same token is shared by other unrelated orders' escrow (a very likely case for any actively used input token), the deficit is silently paid out of other users' escrowed balances during their `withdraw()` calls — i.e., other users lose funds they legitimately deposited, since the contract's real balance can't cover all recorded `_orders` entries. This is a direct loss-of-funds condition for third parties, reachable by any ordinary user calling `placeOrder` with a deflationary token as input — no privileged actor, relayer, or malicious peer required.

### Likelihood Explanation
Any unprivileged user can trigger this by placing an order whose input token is fee-on-transfer/deflationary (a widely available token class). No cooperation from relayers, provers, or governance is needed — the attacker only needs to call the public `placeOrder` entrypoint with such a token, and later cause/allow the order to be filled or cancelled so `withdraw()` executes with the inflated recorded amount.

### Recommendation
Mirror the fix already present in `evm/src/apps/IntentGatewayV2.sol`: measure the actual token balance delta (`balanceOf` before/after `safeTransferFrom`) in the plain escrow branch of the Tron `placeOrder`, and derive `reducedInputs[i].amount` (and the escrow credit) from the actually-received amount rather than the caller-specified `order.inputs[i].amount`. Alternatively, reject placement outright if `balanceOf` delta does not equal the requested amount.

### Proof of Concept
1. Deploy/attacker uses a fee-on-transfer ERC-20 `FOT` (e.g., 5% fee on transfer) as an order input token on the Tron `IntentGatewayV2`.
2. Call `placeOrder` with `order.inputs[0] = {token: FOT, amount: 1000}` and `protocolFeeBps = 0` for simplicity.
   - `safeTransferFrom(user, address(this), 1000)` executes; contract's actual `FOT` balance increases by only 950 (5% fee burned/redirected).
   - `reducedInputs[0].amount = 1000` (no protocol fee) is credited: `_orders[commitment][FOT] += 1000`.
3. Any subsequent `withdraw()` call for this commitment (via fill/`RedeemEscrow` or cancel/refund) attempts `token.call(transfer(beneficiary, 1000))`, but the contract only holds 950 of `FOT` from this order (plus whatever balance is pooled from other unrelated orders using the same token).
   - If no other `FOT` escrow exists in the contract, the transfer fails and `TransferFailed()` reverts, permanently locking the order's funds.
   - If other orders also escrow `FOT`, this withdrawal silently consumes 50 tokens from that shared pool, causing another user's later `withdraw()` to revert or receive insufficient funds — a direct fund-loss/insolvency condition for a third party. [1](#0-0) [4](#0-3)

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L412-435)
```text
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
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L444-462)
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

**File:** evm/src/apps/IntentGatewayV2.sol (L288-292)
```text
                } else {
                    uint256 balBefore = IERC20(token).balanceOf(address(this));
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                    order.inputs[i].amount = IERC20(token).balanceOf(address(this)) - balBefore;
                }
```
