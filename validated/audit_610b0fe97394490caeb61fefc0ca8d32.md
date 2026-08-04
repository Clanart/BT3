## Title
Unchecked ERC20 `transfer` return value lets `IntentGatewayV2.withdraw()` finalize a settlement while the beneficiary receives less than the escrowed amount (down to zero) - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

## Summary
The Tron variant of the Intent Gateway settles cross-chain fills, cancellations, and dust sweeps by calling ERC-20 `transfer`/`transferFrom` through raw low-level `.call(...)`, checking only that the call itself did not revert. It never inspects the ABI-decoded boolean return value. Any ERC-20 token that signals failure by returning `false` (rather than reverting) — a common, standards-compliant pattern for insufficient-balance, paused, or blacklist conditions — will pass this check as `success = true`. The gateway then unconditionally decrements the escrow ledger and finalizes the order (`_filled[commitment] = beneficiary`, `EscrowReleased`/`EscrowRefunded` emitted), even though zero tokens actually moved. This mirrors the external report's core defect: state is finalized as if full payment happened while the real transferred amount can be far less than expected, down to nothing.

## Finding Description
`withdraw()` in `evm/tron/contracts/apps/IntentGatewayV2.sol` is the single settlement path reached from `onAccept()` for both `RedeemEscrow` (pay the solver) and `RefundEscrow` (refund the user), as well as from local cancel paths: [1](#0-0) 

```solidity
function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
    address beneficiary = address(uint160(uint256(body.beneficiary)));
    _filled[body.commitment] = beneficiary;

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
        unchecked { ++i; }
    }
    ...
    if (isRefund) {
        emit EscrowRefunded({commitment: body.commitment});
    } else {
        emit EscrowReleased({commitment: body.commitment});
    }
}
```

The same pattern (bare `.call` + boolean `success` on call-level only) is used in the dust-sweep path a few lines above: [2](#0-1) 

Two problems combine here:

1. **`success` only reflects whether the external call reverted, not whether `transfer` returned `true`.** Per ERC-20, a non-reverting `transfer` that fails is expected to return `false`. The code decodes no return data at all — `(bool success,) = token.call(...)` discards the returndata bytes — so a token that behaves exactly per spec and returns `false` on failure is indistinguishable here from a successful transfer.

2. **The contract already imports and even declares `using SafeERC20 for IERC20;`** (`evm/tron/contracts/apps/IntentGatewayV2.sol:38-39,56`), and the parallel EVM implementation (`evm/src/apps/intentsv2/IntentsBase.sol:390-420`) correctly uses `IERC20(token).safeTransfer(beneficiary, amount)`, which reverts on a `false` return. The Tron contract deviates from this safe pattern specifically in `withdraw()` and the dust sweep, using raw `.call` instead.

Because the escrow debit (`_orders[body.commitment][token] -= amount`) and order finalization (`_filled[...] = beneficiary`, terminal event) happen unconditionally after the (silently failed) transfer, this exactly reproduces the external report's broken invariant: **the beneficiary's real payout can be strictly less than the accounted/expected amount — including zero — while the system state and event log claim full settlement.** The commitment becomes permanently `_filled`, which is Intent Gateway's one-time settlement marker (`_filled[commitment] != address(0)` blocks re-fill/re-cancel elsewhere, e.g. `evm/src/apps/intentsv2/IntentsBase.sol:44` `Filled()` check), so there is no retry path — the escrowed value is effectively lost to the legitimate beneficiary.

## Impact Explanation
This is a fund-loss / false-settlement-acceptance bug: a legitimate refund (`RefundEscrow`, user gets escrow back) or redemption (`RedeemEscrow`, solver gets escrow) can silently deliver zero tokens while the order is irreversibly marked settled and the corresponding `EscrowReleased`/`EscrowRefunded` event fires. This satisfies the bounty's accepted impact of "stealing or loss of funds" — the escrowed asset is permanently locked/lost relative to the accounting state, with no code path to retry or reclaim it once `_filled[commitment]` is set.

## Likelihood Explanation
Triggering this does not require a malicious relayer, prover, or governance actor — it only requires that one of the configured input/output tokens used in an order is a standards-compliant ERC-20 that returns `false` instead of reverting under some condition (fee-on-transfer tokens with insufficient internal balance, pausable/blacklist tokens, or tokens with non-reverting failure semantics). Since token addresses in `Order.inputs`/`Order.output.assets` are attacker (order-placer or solver) supplied at the application layer, an attacker who wants to grief a counterparty, or who controls/deploys a token used in a route, can reliably produce this condition. This is a real code-pattern defect present today in the shipped Tron contract, independent of any external actor compromise.

## Recommendation
In `evm/tron/contracts/apps/IntentGatewayV2.sol`, replace every raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` / `transferFrom` pattern in `withdraw()` and the dust-sweep function with `IERC20(token).safeTransfer(...)` / `safeTransferFrom(...)` from the already-imported `SafeERC20` library (already used as `using SafeERC20 for IERC20;` at line 56 but not applied here). This ensures a `false` return value reverts the whole settlement, so `_orders`/`_filled` state can never diverge from the tokens actually delivered.

## Proof of Concept
1. Deploy (or use) an ERC-20 token `T` that returns `false` from `transfer()` when the caller's balance check fails, rather than reverting (this is valid, spec-compliant ERC-20 behavior; many older/non-OpenZeppelin tokens including some deployed on Tron-style chains behave this way).
2. Place a cross-chain order with `T` as an input token, so it is escrowed in `_orders[commitment][T]`.
3. Drain the gateway's `T` balance below the escrowed amount through any legitimate concurrent settlement of another order sharing token `T` (or simply have the gateway's `T` balance be insufficient due to prior partial sweeps/fee handling).
4. Trigger `RefundEscrow` (via `cancelOrder` from destination, deadline expiry) or `RedeemEscrow` (via a solver fill) for the order from step 2, which routes to `onAccept()` → `withdraw()`.
5. Inside `withdraw()`, `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))` returns `(true, abi.encode(false))` because the low-level call succeeds but the ERC-20 call returns `false`. `success` is `true`, so `if (!success) revert TransferFailed();` does not trigger.
6. `_orders[commitment][T] -= amount` and `_filled[commitment] = beneficiary` execute; `EscrowRefunded`/`EscrowReleased` is emitted.
7. Observe: beneficiary's `T` balance did not increase, yet the order is permanently marked settled (`_filled[commitment] != address(0)`) with no further recovery path — the escrowed `T` value is lost.

### Citations

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
