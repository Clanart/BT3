## Analysis

The Rubicon bug's core primitive: a withdrawal function computes the payout amount from an **untrusted/external value** and transfers it to the caller/beneficiary without first verifying that value against the contract's actual custodied balance — the existence check ("has some balance") is conflated with a sufficiency check ("has exactly this much").

The closest local analog is the escrow `withdraw()` path in the Tron variant of `IntentGatewayV2`, which is missing a replay/duplicate-processing guard that the canonical EVM version of the same contract explicitly has.

### Title
Missing "already-settled" guard in `IntentGatewayV2.withdraw` allows double-settlement of escrowed order funds - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`withdraw()` is the internal function that pays out escrowed order funds for both `RedeemEscrow`/`RefundEscrow` cross-chain messages (`onAccept`) and cancel-verification `onGetResponse` callbacks. Unlike the canonical EVM `IntentGatewayV2` (`evm/src/apps/IntentGatewayV2.sol`), which explicitly checks `if (_filled[commitment] != address(0)) revert Filled();` before `fillOrder`/`cancelOrder` proceed, the Tron variant's `onAccept` and `onGetResponse` call `withdraw()` unconditionally, and `withdraw()` itself only unconditionally overwrites `_filled[commitment]` without ever checking its prior state.

### Finding Description [1](#0-0) 
`onAccept` dispatches directly into `withdraw(body, isRefund)` for both `RedeemEscrow` and `RefundEscrow` requests with no prior check that `body.commitment` has not already been settled. [2](#0-1) 
Inside `withdraw()`:
- Line 684 unconditionally sets `_filled[body.commitment] = beneficiary;` — it never checks whether this commitment was already marked filled by a prior call.
- Line 691, `if (_orders[body.commitment][token] == 0) revert UnknownOrder();`, is only an **existence** check, not a sufficiency/exact-match check against the transferred `amount` (`body.tokens[i].amount`), which is taken directly from the decoded message body.
- Lines 693-699 perform the actual token/native transfer to `beneficiary` **before** the escrow ledger is decremented at line 701. [3](#0-2) 
`onGetResponse` similarly calls `withdraw(body, true)` with no check that the commitment hasn't already been redeemed by a prior `RedeemEscrow`/`RefundEscrow` message.

By contrast, the canonical EVM implementation guards every entry into the settlement path with an explicit "already filled" check: [4](#0-3) [5](#0-4) 

This divergence means that on the Tron deployment, if the escrow for a given `commitment` is not fully exhausted by the first authenticated settlement message (e.g., a per-token `amount` in the message body is less than the full `_orders[commitment][token]` balance, which is legal since the per-token check only requires non-zero, not equality), a second legitimately-authenticated message for the same `commitment` (a duplicate delivery, or a `RefundEscrow` arriving after a `RedeemEscrow` already settled part of the escrow, or vice versa) will pass the same `_orders[...] == 0` check again and pay out additional funds to a (possibly different) beneficiary — because `_filled` was never checked before being blindly overwritten.

### Impact Explanation
This is a double-settlement / duplicate-claim vulnerability on escrowed bridge funds: the same order commitment's escrow can be paid out more than once, or paid to two different beneficiaries (the filler via `RedeemEscrow` and the original user via `RefundEscrow`), draining protocol-held escrow beyond what was legitimately owed. This matches the bounty's explicit "double-claim/double-settlement" and "stealing or loss of funds" categories, and requires no malicious relayer, prover, or admin — only that the module receives two legitimately-authenticated messages referencing the same commitment, which the code has no logic to prevent.

### Likelihood Explanation
Medium: it requires a partial (non-exhausting) escrow settlement — plausible whenever the message body's `amount` doesn't exactly zero out `_orders[commitment][token]` — combined with a second authenticated message for the same commitment reaching `onAccept`/`onGetResponse`. Given that the sibling canonical EVM contract found it necessary to add an explicit `_filled` pre-check to every entry point, its absence here on the Tron variant is a genuine regression/gap, not a hypothetical concern.

### Recommendation
Add an explicit `if (_filled[body.commitment] != address(0)) revert Filled();` check at the top of `withdraw()` (or in `onAccept`/`onGetResponse` before invoking it), mirroring the guard already present in `evm/src/apps/IntentGatewayV2.sol`. Additionally, replace the `_orders[...] == 0` existence check with a strict sufficiency check (`amount <= _orders[commitment][token]`) performed **before** the external transfer, and decrement the escrow ledger prior to the external call (checks-effects-interactions).

### Proof of Concept
1. User places an order on the source chain via `placeOrder`, escrowing `100` units of `tokenA` under `commitment`, `_orders[commitment][tokenA] = 100`.
2. A legitimate authenticated `RedeemEscrow` message arrives via `onAccept` with `body.tokens[0].amount = 40` (e.g., a partial fill/settlement scheme, or a message crafted by the legitimate solver-fill flow on the destination instance). `withdraw()` passes the `== 0` check, transfers `40` to the filler, decrements `_orders[commitment][tokenA]` to `60`. `_filled[commitment]` is now set to the filler.
3. A second legitimate authenticated message for the same `commitment` — e.g., a `RefundEscrow` triggered by `cancelOrder`'s destination-chain path (which does not check whether the order was already filled before dispatching) or a duplicate/redelivered `RedeemEscrow` — arrives at `onAccept`. `_orders[commitment][tokenA]` is still `60` (non-zero), so the `UnknownOrder` check passes again, and `withdraw()` pays out another `60` units, this time possibly to a different beneficiary (`body.beneficiary` from the second message), overwriting `_filled[commitment]`.
4. Total paid out: `100` units correctly escrowed, but delivered across two separate, non-idempotent settlement calls with no cross-check — enabling duplicate/double settlement of the same escrow to two different parties, or twice to a colluding party if messages can be legitimately re-triggered (e.g., via the `_cancelFromDest`/`_cancelFromSource` race that is not visible in this file but is structurally unguarded here).

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L620-626)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return withdraw(body, kind == RequestKind.RefundEscrow);
        }
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L729-734)
```text
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        withdraw(body, true);
    }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L426-426)
```text
        if (_filled[commitment] != address(0)) revert Filled();
```

**File:** evm/src/apps/IntentGatewayV2.sol (L473-473)
```text
        if (_filled[commitment] != address(0)) revert Filled();
```
