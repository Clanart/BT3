### Title
Missing `_filled` finalization guard in `IntentsBase._withdraw` allows re-finalization of an already-settled order on the source chain - ([File: evm/src/apps/intentsv2/IntentsBase.sol])

### Summary
`IntentGatewayV2`/`ExtrinsicIntents` enforces "an order can be finalized only once" via the `_filled[commitment] != address(0)` check everywhere a user can directly trigger settlement (`fillOrder`, `cancelOrder`). However, the actual state-mutating finalization primitive, `IntentsBase._withdraw`, which is reached from the cross-chain settlement callbacks (`onAccept` for `RedeemEscrow`/`RefundEscrow`, and `onGetResponse` for source-initiated cancellation), never checks whether `_filled[commitment]` is already set before overwriting it and re-emitting finalization events. This is structurally the same defect class as the seed report: a state-closing condition ("this order is already finalized") is enforced on some code paths but omitted on another path that reaches the same state transition, so the guard silently no-ops for one class of caller.

### Finding Description
`fillOrder` and `cancelOrder` both gate entry with:
```solidity
if (_filled[commitment] != address(0)) revert Filled();
``` [1](#0-0) [2](#0-1) 

But the three code paths that ultimately call `_withdraw(..., finalize: true)` — the cross-chain settlement handlers — do not perform this check on the source chain's own `_filled` mapping before finalizing:

- `onAccept` for `RedeemEscrow`/`RefundEscrow` calls `_withdraw` directly with no `_filled[commitment]` precondition: [3](#0-2) 

- `onGetResponse` (source-initiated cancel) likewise calls `_withdraw` unconditionally once the destination non-membership proof is verified: [4](#0-3) 

- `_withdraw` itself unconditionally overwrites the finalization marker whenever `finalize` is true, with no read-then-check of the prior value: [5](#0-4) 

This mirrors the Munchables `_farmPlots` bug precisely: the closing invariant ("plot invalid once funds unlocked" / "order already finalized") is validated in one call path (`_getNumPlots(landlord) < _toiler.plotId` / `_filled[commitment] != address(0)`) but the code path that actually performs the state-changing action (`_farmPlots` continuing to farm plot 0 / `_withdraw` finalizing settlement) omits the equivalent check.

In the current escrow-accounting design, `_orders[commitment][token]` decrementing to zero happens to provide a secondary backstop against re-draining the exact same token amount a second time (`escrowed == 0` reverts with `UnknownOrder`). But this backstop is incidental, not the intended guard, and it does not protect:
- The `_filled[commitment]` beneficiary record itself, which downstream consumers rely on as the authoritative "who received this order" value (the SDK's `isOrderFilled`/`isOrderRefunded`, and the `_calculateCommitmentSlotHash`-based cross-chain non-membership proof used by `_cancelFromSource` on other chains).
- The `EscrowReleased`/`EscrowRefunded` events, which can be re-emitted for a commitment that was already finalized, corrupting off-chain indexer state that keys off these events as one-time settlement signals.
- Any `WithdrawalRequest` whose token list does not span (or fully drain) every escrowed token for the commitment — the per-token check only reverts once that specific token's balance reaches zero, so a second finalization call with a different or partial token/fee-storage layout can silently succeed and overwrite `_filled[commitment]` to a different beneficiary without reverting.

### Impact Explanation
This falls under "false state acceptance" / "logic attack on settlement finality" from the Hyperbridge impact gate: the on-chain record of who legitimately owns a settled order (`_filled[commitment]`) can be overwritten a second time through the cross-chain settlement callback surface, silently, with no defensive revert at the point that matters (unlike the sibling user-facing entrypoints `fillOrder`/`cancelOrder`, which correctly guard this). Because `_filled` also backs the non-membership proof other chains use to decide whether escrow is refundable (`_calculateCommitmentSlotHash` / `FILLED_SLOT_BIG_ENDIAN_BYTES`), a stale or re-overwritten value can propagate incorrect settlement state cross-chain.

### Likelihood Explanation
Exploitability depends on reaching `_withdraw` twice with `finalize=true` for the same commitment before the per-token escrow depletion trips `UnknownOrder`. This is bounded by the ISMP host's own duplicate-request-receipt protection for a single dispatched `PostRequest` (`requestReceipts` check in `HandlerV2.handlePostRequests`), so a literal replay of the identical message is blocked at the host layer. The realistic trigger is two *distinct* legitimately-authenticated `WithdrawalRequest` messages for the same order commitment (e.g., a `RedeemEscrow` from a destination-side fill racing a `RefundEscrow`/GET-based cancellation for the same commitment) landing on the source chain — a scenario the protocol's deadline/height-ordering rules are designed to prevent, but which is enforced only by that timing logic rather than by an explicit finalization guard in `_withdraw` itself. This is a defense-in-depth gap: I could not fully verify a standalone unprivileged exploit path within the available context (this would require deeper review of cross-chain height/deadline interaction and of `_orders` accounting for partial-token `WithdrawalRequest`s), so likelihood is assessed as depending on interaction with the deadline-ordering logic in `_cancelFromSource`/`_fillCrossChain` rather than as an immediately, independently exploitable primitive.

### Recommendation
Add the same guard used in `fillOrder`/`cancelOrder` directly inside `_withdraw` (or immediately before each call site) when `finalize` is true:
```solidity
if (finalize) {
    if (_filled[body.commitment] != address(0)) revert Filled();
    _filled[body.commitment] = beneficiary;
}
```
This closes the gap between the "already finalized" invariant enforced on the direct user entrypoints and the cross-chain settlement callback entrypoints, matching the recommended fix pattern in the seed report (make the invalidation check unconditionally authoritative rather than an artifact of only some call paths).

### Proof of Concept
Not independently reproduced end-to-end; the finding is based on direct code review of the call graph:
1. `ExtrinsicIntents.onAccept` → `_withdraw(body, isRefund, true)` with no `_filled` precheck: [3](#0-2) 
2. `ExtrinsicIntents.onGetResponse` → `_withdraw(body, true, true)` with no `_filled` precheck: [4](#0-3) 
3. `IntentsBase._withdraw` unconditionally sets `_filled[body.commitment] = beneficiary` under `finalize`, contrasted with `IntentGatewayV2.fillOrder`/`cancelOrder`, which correctly gate on `_filled[commitment] != address(0)` before any state change: [6](#0-5)  vs [1](#0-0) 

A concrete Foundry PoC driving two authenticated `WithdrawalRequest`s (one `RedeemEscrow`, one `RefundEscrow`) for the same commitment through `onAccept` to demonstrate the second call succeeding and overwriting `_filled[commitment]` was not built in this review; this would be the next step to fully confirm exploitability versus the deadline/height-ordering mitigations in `_fillCrossChain`/`_cancelFromSource`.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L421-426)
```text
        bool isSameChain = orderSource == orderDest;

        if (isSameChain && orderSource != currentChain) revert WrongChain();
        if (!isSameChain && orderDest != currentChain) revert WrongChain();

        if (_filled[commitment] != address(0)) revert Filled();
```

**File:** evm/src/apps/IntentGatewayV2.sol (L470-474)
```text
    function cancelOrder(Order calldata order, CancelOptions calldata options) public payable nonReentrant {
        bytes32 commitment = keccak256(abi.encode(order));

        if (_filled[commitment] != address(0)) revert Filled();

```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L289-295)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            _authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return _withdraw(body, kind == RequestKind.RefundEscrow, true);
        }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L319-324)
```text
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        _withdraw(body, true, true);
    }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-425)
```text
    function _withdraw(WithdrawalRequest memory body, bool isRefund, bool finalize) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        if (finalize) _filled[body.commitment] = beneficiary;

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
        }

        if (finalize) {
            uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
            if (fees > 0) {
                delete _orders[body.commitment][TRANSACTION_FEES];
                IERC20(IDispatcher(host()).feeToken()).safeTransfer(beneficiary, fees);
            }

            if (isRefund) {
                emit EscrowRefunded({commitment: body.commitment, tokens: body.tokens});
            } else {
                emit EscrowReleased({commitment: body.commitment, tokens: body.tokens});
            }
        }
    }
```
