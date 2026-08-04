## Title
Unchecked ERC-20 `transfer` return-value in Tron `IntentGatewayV2.withdraw`/`sweepDust` can finalize escrow release without moving funds - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron build of `IntentGatewayV2` settles escrow (`withdraw`) and sweeps protocol dust (`onAccept`/`SweepDust`) using a raw low-level `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` and only checks the boolean `success` returned by `.call` — i.e. whether the call reverted. It never decodes and validates the ERC-20 `transfer` function's own returned `bool`. A token that returns `false` on failure instead of reverting (the same "BAT-style" token class cited in the original CurveAMO_V3 report) will make `success == true` while the actual transfer silently fails. The gateway nonetheless finalizes settlement state (`_filled[...] = beneficiary`, decrements `_orders[...]`, emits `EscrowReleased`/`EscrowRefunded`/`DustSwept`) as if the funds moved.

### Finding Description
`withdraw()` in the Tron `IntentGatewayV2` releases escrowed ERC-20 tokens with: [1](#0-0) 

```solidity
function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
    address beneficiary = address(uint160(uint256(body.beneficiary)));
    _filled[body.commitment] = beneficiary;
    ...
    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
    if (!success) revert TransferFailed();

    _orders[body.commitment][token] -= amount;
    ...
}
```

`success` here is *only* the low-level-call success flag: it is `true` whenever the target contract does not revert, even if it returns `abi.encode(false)`. ERC-20 implementations that follow the (non-standard but real) pattern of returning `false` on failure rather than reverting will pass this check even though no tokens were transferred. The function still marks the commitment as finalized (`_filled[body.commitment] = beneficiary`), decrements escrow accounting (`_orders[body.commitment][token] -= amount`), and emits `EscrowReleased`/`EscrowRefunded`, permanently closing out the order.

The same pattern occurs in the dust-sweep path within `onAccept`: [2](#0-1) 

and in the fee-token release for accumulated transaction fees: [3](#0-2) 

This is a direct local analog of the CurveAMO_V3 report: a `transfer()` call whose return value is checked incompletely (only for revert, not for the boolean success payload), letting the contract "return true despite... failure to transfer tokens."

By contrast, the canonical EVM `IntentsBase._withdraw`/`_sweepDust` use OpenZeppelin's `SafeERC20.safeTransfer`, which does decode and enforce the ERC-20 return value: [4](#0-3) [5](#0-4) 

confirming that the Tron variant's raw-call pattern is the outlier and the vulnerable path.

### Impact Explanation
`_filled[commitment]` finalization and `_orders[...]` escrow-balance decrement happen unconditionally once `success` (call-didn't-revert) is true, regardless of whether the ERC-20 `transfer` actually moved value. If a supported/whitelisted collateral or fill token on the Tron deployment is one that signals failure via a `false` return (rather than reverting) — e.g., during a paused state, blacklist hit, or any other non-reverting failure branch — the beneficiary (user refund recipient or solver being paid) never receives their tokens, yet:
- The order is irreversibly marked filled/refunded (`_filled`), preventing any retry or alternate settlement path (there is no other code path to re-attempt payout for that commitment).
- The escrow accounting is decremented as if funds left, corrupting the contract's internal ledger.
- Fees and dust sweeps suffer identically, letting protocol-owned funds become permanently stuck while being recorded as swept.

This is a direct fund-loss/fund-lock condition reachable through the entrypoint's normal execution flow (`onAccept` from an authenticated cross-chain settlement message, or `onGetResponse` for cancellations) — it requires no malicious relayer, admin, or governance action, only a token whose `transfer` can return `false`.

### Likelihood Explanation
Likelihood depends on whether any token registered/used with the Tron `IntentGatewayV2` deployment can return `false` rather than revert on transfer failure. This is a well-known real-world ERC-20 behavior class (the same class the original CurveAMO_V3 report calls out, e.g. BAT), and the gateway accepts arbitrary user-specified `token` addresses in `order.inputs`/`order.output.assets`, so the condition is triggerable whenever such a token is used, without requiring any privileged actor.

### Recommendation
Replace the raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` pattern in `withdraw()`, the fee-release branch, and `onAccept`'s `SweepDust` handling with OpenZeppelin's `SafeERC20.safeTransfer`, exactly as already done in the canonical `IntentsBase.sol`/`ExtrinsicIntents.sol`/`IntrinsicIntents.sol` EVM contracts. This decodes and enforces the ERC-20 return value (and also handles tokens that return no data at all), ensuring `TransferFailed` is raised whenever the transfer did not actually succeed, before `_filled`/`_orders` state is finalized.

### Proof of Concept
1. Deploy (or register as a supported input/output token) an ERC-20 whose `transfer` returns `false` on failure instead of reverting (e.g. a mock `NonRevertingFailToken` that returns `false` when `balanceOf(address(this)) < amount`, matching the real-world BAT-style behavior).
2. Drain/underfund the gateway's balance of that token relative to an order's escrowed `amount` (e.g., via a prior partial sweep, rounding edge case, or simply by pre-configuring the mock to always return `false`).
3. Trigger settlement: have Hyperbridge deliver a `RedeemEscrow`/`RefundEscrow` `onAccept` call (or a `GetResponse` for cancellation) that invokes `withdraw(body, isRefund)` for that commitment/token.
4. Observe: `token.call(...)` returns `success == true` (no revert) even though the underlying `transfer` returned `false` and the beneficiary's balance is unchanged.
5. Observe: `_filled[body.commitment]` is set, `_orders[body.commitment][token]` is decremented, and `EscrowReleased`/`EscrowRefunded` is emitted — the order is permanently finalized with no tokens delivered and no retry path available, resulting in a stuck/lost fund state.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L652-673)
```text
        } else if (kind == RequestKind.SweepDust) {
            SweepDust memory req = abi.decode(incoming.request.body[1:], (SweepDust));

            uint256 outputsLen = req.outputs.length;
            for (uint256 i; i < outputsLen;) {
                TokenInfo memory info = req.outputs[i];
                address token = address(uint160(uint256(info.token)));
                uint256 amount = info.amount;

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L404-409)
```text
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L586-591)
```text
            if (token == address(0)) {
                (bool sent,) = req.beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(req.beneficiary, amount);
            }
```
