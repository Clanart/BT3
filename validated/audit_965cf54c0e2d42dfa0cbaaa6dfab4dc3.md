## Title
Fee-on-Transfer / Non-Standard ERC20 Escrow Over-Crediting in `IntentGatewayV2.placeOrder` — Tron Variant Trusts Declared Amount Instead of Actual Received Balance (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The external report's core flaw is: a contract trusts a declared numeric input (the RSA modulus/exponent) without validating the structural properties that make it *actually* trustworthy (bit length, oddness, exponent bound), letting malformed values pass silently into security-critical logic. The local analog in Hyperbridge's Tron intent gateway is structurally the same defect applied to token accounting: `placeOrder` credits the internal escrow ledger `_orders[commitment][token]` with the **declared** order amount rather than the **actual** ERC20 balance received by the contract, never validating that the transfer actually delivered what was claimed.

### Finding Description
In `evm/tron/contracts/apps/IntentGatewayV2.sol`, the direct-escrow branch of `placeOrder` does: [1](#0-0) 

```solidity
for (uint256 i; i < inputsLen;) {
    if (order.inputs[i].amount == 0) revert InvalidInput();
    address token = address(uint160(uint256(order.inputs[i].token)));
    if (token == address(0)) {
        if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
        msgValue -= order.inputs[i].amount;
    } else {
        IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
    }
    _orders[commitment][token] += reducedInputs[i].amount;
    ...
}
```

The call `safeTransferFrom(msg.sender, address(this), order.inputs[i].amount)` is never checked against the contract's balance delta. For any fee-on-transfer, rebasing, or otherwise non-standard ERC20 token, the amount actually received by the contract can be strictly less than `order.inputs[i].amount`. Regardless of what is actually received, the contract credits the *full declared* `reducedInputs[i].amount` into the shared per-token escrow ledger `_orders[commitment][token]`.

This is the exact same class of bug as `_validateModulus`/`_hasNonZeroExponent`: the code accepts an attacker/user-supplied numeric quantity and derives downstream trust (escrow accounting) from the *claimed* value instead of verifying the *actual* value.

Contrast this with the hardened EVM mainline version, which explicitly measures the real balance delta before crediting escrow and computing the commitment, specifically to prevent this class of issue: [2](#0-1) 

```solidity
} else {
    for (uint256 i; i < inputsLen;) {
        if (order.inputs[i].amount == 0) revert InvalidInput();
        address token = address(uint160(uint256(order.inputs[i].token)));
        if (token == address(0)) {
            if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
            msgValue -= order.inputs[i].amount;
        } else {
            uint256 balBefore = IERC20(token).balanceOf(address(this));
            IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
            order.inputs[i].amount = IERC20(token).balanceOf(address(this)) - balBefore;
        }
        ...
    }
}
```

The comment above this code in the mainline contract even documents the exact hazard being guarded against: [3](#0-2) 

The Tron contract dropped this validation, so its `_orders[commitment][token]` accounting can diverge from the real token balance actually held by the contract for that specific token across all outstanding orders (since `_orders` is a shared pool per `token`, not a segregated vault).

### Impact Explanation
Because `_orders[commitment][token]` is later used verbatim to authorize real token transfers on fill/cancel/refund (see the withdrawal path): [4](#0-3) 

an inflated escrow record for one order (created via a fee-on-transfer token) lets that order's beneficiary or refund path claim more of the token pool than the contract actually received for that order. Since the token balance is a shared, fungible reserve, satisfying this over-credited claim can be paid out of tokens that were actually contributed by *other* users' orders in the same token, causing insolvency: legitimate later orders' withdrawals or refunds fail or drain funds that don't belong to them. This is a direct "stealing or loss of funds" / accounting-manipulation impact matching the bounty's required impact classes, reachable by any unprivileged user who places an order using a fee-on-transfer ERC20 as input — no relayer, prover, or admin involvement required.

### Likelihood Explanation
Likelihood is high for any deployment where the intent gateway allowlists (or does not strictly denylist) fee-on-transfer / rebasing tokens as valid input tokens. An attacker only needs to call the public `placeOrder` entrypoint with such a token to create a discrepancy between recorded escrow and actual contract balance; no privileged role, malicious relayer, or front-running condition is required. The bug is a pure omission of a balance-delta check that the project's own EVM mainline contract demonstrates is necessary and has already fixed.

### Recommendation
Mirror the mainline `evm/src/apps/IntentGatewayV2.sol` fix in the Tron contract: measure `balanceOf(address(this))` before and after each `safeTransferFrom` call, use the actual received delta (not the declared `order.inputs[i].amount`) both for the escrow credit (`_orders[commitment][token]`) and for computing the order `commitment`, in every code path (direct transfer and predispatch/CallDispatcher paths alike).

### Proof of Concept
1. Attacker deploys or uses an existing ERC20 token `T` that charges a transfer fee (e.g., burns/redirects 5% of every transfer) and gets it accepted as a valid `order.inputs[i].token` (no on-chain check here restricts input token type).
2. Attacker calls `placeOrder` with `order.inputs[0] = {token: T, amount: 1000}`.
3. `safeTransferFrom(attacker, gateway, 1000)` executes; due to the fee, the gateway's actual `T` balance only increases by 950.
4. `_orders[commitment][T] += reducedInputs[0].amount` credits (approximately) 1000 (minus protocol fee, which is computed off the *declared* amount, not real receipt) — an escrow record exceeding the tokens actually held for this order by ~50 units.
5. Repeating this (or combining with other users' legitimate `T` orders sharing the same pooled balance) allows total claims recorded in `_orders[...][T]` to exceed the gateway's real `T` balance.
6. When solvers/users later call fill/cancel/refund flows that invoke `_withdraw` (`evm/src/apps/intentsv2/IntentsBase.sol:391-409`), transfers can be authorized against `_orders` balances that are not actually backed, causing later legitimate withdrawals for other orders in token `T` to revert (fund lock) or, depending on ordering, to be paid out of value contributed by other users (fund theft/insolvency).

### Citations

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

**File:** evm/src/apps/IntentGatewayV2.sol (L198-202)
```text
        // Phase 1: Transfer tokens and record actual received amounts.
        // For fee-on-transfer tokens, the gateway receives less than the requested amount.
        // We mutate order.inputs to reflect actual received so the commitment and escrow
        // are consistent with what the gateway holds.
        uint256 msgValue = msg.value;
```

**File:** evm/src/apps/IntentGatewayV2.sol (L281-298)
```text
        } else {
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                if (token == address(0)) {
                    if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
                    msgValue -= order.inputs[i].amount;
                } else {
                    uint256 balBefore = IERC20(token).balanceOf(address(this));
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                    order.inputs[i].amount = IERC20(token).balanceOf(address(this)) - balBefore;
                }

                unchecked {
                    ++i;
                }
            }
        }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L394-409)
```text
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
```
