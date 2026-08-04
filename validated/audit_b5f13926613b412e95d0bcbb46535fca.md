## Analysis

**Core broken invariant from the report:** ERC20 `transfer`/`transferFrom` can return `false` on failure instead of reverting; code that doesn't check the returned boolean will treat a failed transfer as successful.

**Local analog found:** the Tron `IntentGatewayV2` contract uses a raw low-level `.call()` to invoke `IERC20.transfer` and only checks the call's `success` (i.e. that the callee didn't revert), never decoding/verifying the ERC20 return value itself. This is used in the escrow `withdraw()` path and the `SweepDust` handler — both are bridge-custody/intent-settlement code paths that permanently mutate escrow accounting and mark orders as filled/refunded.

### Title
Unchecked ERC20 `transfer` return value in escrow withdrawal permanently marks intents settled while funds remain stuck - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2.withdraw()` and the `SweepDust` branch of `onAccept()` release escrowed tokens to a beneficiary using a raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` and only check that the low-level call did not revert (`success`), never checking the boolean value the ERC20 `transfer` function itself returns.

### Finding Description
In `withdraw()`: [1](#0-0) 
the token branch does:
```solidity
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
```
`success` here only reflects whether the external call reverted, not whether the token's own `transfer` logic returned `true`. Any non-fully-compliant ERC20 (or a token that intentionally returns `false` rather than reverting on failure, e.g. insufficient balance edge cases, blacklists, paused states) will make this call return normally with `success = true` and an ABI-encoded `false` payload that is silently ignored.

Immediately after this unchecked transfer, the function commits state that treats the transfer as having happened: [2](#0-1) 
`_filled[body.commitment] = beneficiary;` is set unconditionally at function entry, and `_orders[body.commitment][token] -= amount;` decrements the escrow ledger regardless of whether the beneficiary actually received the tokens. The same unchecked pattern is repeated for transaction fees: [3](#0-2) 

The identical unchecked pattern also exists in the `SweepDust` handler reachable from `onAccept()`: [4](#0-3) 

Both `withdraw()` and the dust sweep are reached via `onAccept()`, which is driven by cross-chain ISMP messages (`RedeemEscrow`/`RefundEscrow`/`SweepDust`) after Hyperbridge already accepted the proof: [5](#0-4) 

No guard anywhere in this call chain decodes the ABI return data of the `transfer` call to confirm it equals `true`; the existing check is only "call did not revert."

### Impact Explanation
Once escrow is decremented and `_filled` is set, the order is permanently considered settled/refunded (see `EscrowRefunded`/`EscrowReleased` events). If the underlying input/output token silently returns `false` instead of reverting on the actual transfer, the intended beneficiary receives nothing, but:
- The escrow balance for that commitment is reduced to zero, so the tokens can never be re-claimed through the normal withdraw path.
- `_filled[commitment]` is already set, so no retry/refund path can run again for that commitment (`Filled()` guards elsewhere reject re-processing).

This is a direct loss/lock of bridged/escrowed funds for whichever order used a non-standard token — exactly the false-settlement/fund-loss class called out in the bounty scope (bridged assets, order escrow must move exactly once to the rightful beneficiary).

### Likelihood Explanation
This requires only that an order's input/output token be a non-fully-EIP-20-compliant ERC20 (returns `false` rather than reverting on failure) — a token property, not a malicious peer/relayer/prover assumption. The intent gateway's order flow lets solvers/users select arbitrary ERC20 tokens as `order.inputs`/`order.output.assets`, so the trigger condition (a token whose `transfer` can return `false`) is fully within reach of a permissionless order — no privileged actor needed.

### Recommendation
Replace the raw `.call` + `success`-only check with `SafeERC20.safeTransfer` (already imported and used elsewhere in the same contract, e.g. `safeTransferFrom` calls), which decodes and validates the return value (or the absence of one) per EIP-20/EIP-165-style conventions, in `withdraw()`, the fee-redemption branch, and the `SweepDust` handler.

### Proof of Concept
1. Create/select a token `T` whose `transfer(to, amount)` returns `false` on a specific failure condition instead of reverting (e.g. `to` is blacklisted, or contract-defined failure code), and use `T` as an order's escrowed input token in `IntentGatewayV2` (Tron variant).
2. Trigger the failure condition for the beneficiary (e.g. blacklist the beneficiary in `T`, or otherwise make the internal transfer fail).
3. Cause `onAccept` to be invoked with `RequestKind.RedeemEscrow`/`RefundEscrow`, driving `withdraw(body, ...)`.
4. `token.call(...)` returns `success = true` with encoded `false`; the check `if (!success) revert TransferFailed();` passes.
5. `_orders[body.commitment][token] -= amount;` executes, zeroing escrow, and `_filled[body.commitment] = beneficiary;` marks the order permanently settled, while the beneficiary's `T` balance is unchanged — the tokens are now unrecoverable through this contract.

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L661-672)
```text
                if (token == address(0)) {
                    (bool sent,) = req.beneficiary.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, req.beneficiary, amount));
                    if (!success) revert TransferFailed();
                }
                unchecked {
                    ++i;
                }
                emit DustSwept(token, amount, req.beneficiary);
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L707-714)
```text
        // redeem tx fees
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
        }
```
