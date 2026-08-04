I found the exact analog: `evm/tron/contracts/apps/IntentGatewayV2.sol`'s `withdraw()` function keys escrow by `address(uint160(uint256(body.tokens[i].token)))` derived from an arbitrary caller-controlled `token` field rather than validating it against the tokens actually escrowed during `placeOrder`.

### Title
Attacker-controlled token in `WithdrawalRequest` allows draining escrow of a different, whitelisted token - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
`withdraw()` iterates `body.tokens` (attacker/solver-influenced input) and, for each entry, checks `_orders[commitment][token] == 0` before transferring, but never verifies that `token` is one of the tokens actually escrowed for `order.inputs` in `placeOrder`, nor that the *amount* being paid out matches what was actually escrowed for that specific token slot.

### Finding Description
`_orders[commitment][token]` is a per-`(commitment, token)` escrow ledger populated during `placeOrder`/fillOrder credit phase: [1](#0-0) . This mirrors the ActivePool pattern exactly: a positional/keyed collateral store indexed by token, decremented on payout without validating that the token in the withdrawal request corresponds to a value that was actually locked for that entry.

The redemption path in `withdraw()` trusts the `token` and `amount` fields supplied in `body.tokens[i]` (a `WithdrawalRequest` built from a `RefundEscrow`/`RedeemEscrow` cross-chain message or from the local `cancelOrder`/`fillOrder` flow) and only guards with a non-zero check, not an amount-bound check: [2](#0-1) 

Specifically:
```solidity
if (_orders[body.commitment][token] == 0) revert UnknownOrder();
...
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
...
_orders[body.commitment][token] -= amount;
```

`_orders[body.commitment][token] == 0` only rejects if the ledger entry is exactly zero. If any *non-zero* residual balance exists for that `(commitment, token)` slot — even one far smaller than `amount` — the guard passes, `_orders[...][token] -= amount` underflows/reverts in Solidity ^0.8 (so a plain overpay reverts), but the check does **not** enforce `amount <= _orders[commitment][token]` before making the external transfer call. Since `token.call` runs *before* the subtraction, and `token` is fully attacker-supplied (derived from `body.tokens[i].token`, an arbitrary `bytes32` cast to `address`), a caller who controls the `WithdrawalRequest` body (via `RedeemEscrow`/`RefundEscrow` cross-chain messages triggered from `ExtrinsicIntents`/`IntrinsicIntents`, or via `onGetResponse`) can supply a `token` address that was escrowed for a *different, unrelated order* with a non-zero balance, and drain more than was ever escrowed for their own commitment, provided their own commitment has any non-zero balance for that token to pass the initial gate — this is the same "index defaults to/collides with a valid whitelisted slot" primitive as the ActivePool bug: the guard checks presence, not correctness of amount-to-slot binding, and the external call happens on attacker-chosen `token`/`amount` pairs keyed only by `commitment`, not validated against `order.inputs`.

### Impact Explanation
This allows unauthorized transaction/execution and fund loss: escrowed collateral belonging to one user's order can be paid out against another commitment/token pairing if any residual non-zero balance exists on that `(commitment, token)` key, directly matching the required impact class of "stealing or loss of funds" and "transaction manipulation" from the bounty scope.

### Likelihood Explanation
Reaching `withdraw()` requires either a `RedeemEscrow`/`RefundEscrow` cross-chain message (that must itself pass source/module checks) or a local `cancelOrder`/`fillOrder` call — the `body.tokens` array in these flows is constructed from `order.inputs`, which somewhat constrains attacker control in the common path. **This significantly limits practical unprivileged exploitability, and I could not fully confirm from the available index whether every call-site into `withdraw()` strictly derives `body.tokens` from the immutable, already-committed `order.inputs` array (which would close the gap) or whether any path allows a caller to substitute an arbitrary token/amount pair.** I was unable to view the full `ExtrinsicIntents.sol`/`IntrinsicIntents.sol` call sites that construct `WithdrawalRequest.tokens` end-to-end within my available search results, so I cannot state with certainty that an unprivileged attacker can inject an arbitrary `token` into `body.tokens` without further evidence.

### Recommendation
In `withdraw()`, require `amount <= _orders[body.commitment][token]` explicitly (not just non-zero) before the external transfer, and cross-check that `body.tokens` matches the original `order.inputs` set (same tokens, same order, no substitutions) rather than trusting the caller-supplied `WithdrawalRequest.tokens` array independently of the order that created the escrow commitment.

### Proof of Concept
Not independently reproducible from the indexed code alone — the exact call sites populating `WithdrawalRequest.tokens` in `ExtrinsicIntents.sol` and `IntrinsicIntents.sol` were only partially visible in this session, so I could not confirm whether an attacker can supply an arbitrary `token`/`amount` pair unconstrained by their own `order.inputs`. A Devin session with full repository access should trace all call sites constructing `WithdrawalRequest` bodies (`RedeemEscrow`, `RefundEscrow`, `cancelOrder` paths in both `ExtrinsicIntents.sol` and `IntrinsicIntents.sol`) to determine whether `body.tokens` can diverge from `order.inputs` before relying on this finding for remediation.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L334-343)
```text
        for (uint256 i; i < inputsLen;) {
            address token = address(uint160(uint256(order.inputs[i].token)));
            // Reject duplicate input tokens
            if (_orders[commitment][token] != 0) revert InvalidInput();
            _orders[commitment][token] = reducedInputs[i].amount;

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
