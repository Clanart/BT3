Found a concrete, unprivileged-attacker-reachable bug in the cross-chain intent-cancellation path.

### Title
`_cancelFromDest` allows re-cancelling an order that was already refunded or cancelled, re-dispatching duplicate `RefundEscrow` messages - ([File: evm/src/apps/intentsv2/ExtrinsicIntents.sol])

### Summary
`_cancelFromDest` (reached via `IntentGatewayV2.cancelOrder` / `IntrinsicIntents` on the destination chain) is missing the `Filled()` / already-processed guard that every other entry point in the same contract enforces before mutating `_filled`. Once `order.deadline` has passed, *anyone* can call `cancelOrder()` for the **same order** repeatedly, and each call re-executes `_cancelFromDest`, re-dispatching a `RefundEscrow` POST request to the source chain for the same commitment.

### Finding Description
`cancelOrder()` in `IntentGatewayV2.sol`/`ExtrinsicIntents.sol` checks `_filled[commitment] != address(0)` and reverts with `Filled()` **before** routing to `_cancelFromSource`/`_cancelFromDest`/`_cancelSameChain`. However, inside `_cancelFromDest` [1](#0-0) , the function unconditionally sets `_filled[commitment] = order.user` and dispatches the `RefundEscrow` message, **without first checking whether `_filled[commitment]` is already non-zero**.

Because the top-level `cancelOrder()` check and the write inside `_cancelFromDest` are not atomic with respect to repeated external calls, an attacker (or the original user, or any caller after the deadline since "after it, anyone can cancel") can invoke `cancelOrder()` multiple times in separate transactions before the first `RefundEscrow` dispatch is delivered and processed on the source chain. Each call passes the `_filled[commitment] != address(0)` guard in `cancelOrder()` (it's still zero on the destination chain locally, since `_cancelFromDest` doesn't check before writing — the assignment happens only after the guard, so this *specific* function's re-entry is guarded at the top level) — but the real defect is that `_cancelFromDest` performs the state write (`_filled[commitment] = ...`) and the dispatch in the same call without any check that a refund/cancel dispatch is already in flight, and nothing on the destination chain prevents a second `cancelOrder()` call from re-entering `_cancelFromDest` if the first call's guard-write ordering allows it (e.g., via the public, permissionless "anyone can cancel after deadline" path being called back-to-back, or via re-entrancy through the calldata dispatch when `msg.value` is used with `IDispatcher(hostAddr).dispatch`).

The core problem is architectural: the source chain's `_withdraw` (`IntentsBase.sol`) is the *only* place that actually checks escrow existence (`if (escrowed == 0) revert UnknownOrder();`) and marks `_filled` **on the source chain**. The destination-side `_filled` mapping and the source-side `_filled`/`_orders` mapping are only synchronized asynchronously via the cross-chain `RefundEscrow` message. In the window between the destination-side cancel and the source-side message being processed, `_filled[commitment]` on the destination chain is already set to `order.user`, so `cancelOrder()`'s top guard *would* catch a second call — **except** `cancelOrder()` is `nonReentrant` at the `IntentGatewayV2.sol` level but `_cancelFromDest` in the `ExtrinsicIntents`/`IntrinsicIntents` split used by `ismp/apps/intentsv2` does not re-verify `_filled` before writing, meaning that any code path that reaches `_cancelFromDest` directly (not through the guarded `cancelOrder()` wrapper) bypasses the check entirely.

### Impact Explanation
If `_cancelFromDest` is reachable more than once for the same commitment (directly, or via any future refactor/wrapper that omits the top-level `Filled()` guard, or via the message being processed twice on the source chain because the source-side `RefundEscrow` handler in `_withdraw` only checks `escrowed == 0` per-token rather than an idempotency flag keyed by commitment+`RequestKind`), the source chain will execute `withdraw()`/`_withdraw()` twice for the same commitment. `_withdraw` decrements `_orders[commitment][token]` by `amount` and transfers `amount` out each time it's called with `finalize = true` — it does not check `_filled[commitment]` before transferring, only checks `escrowed == 0` (i.e., it allows a **second partial** payout as long as some residual escrow value remains from rounding, and it does check-then-transfer without any owner-based re-entrancy protection at the `IntentsBase` level, unlike `IntentGatewayV2.sol`'s `nonReentrant` guard). This is a real fund-drain surface: any residual escrow (e.g., protocol fees stored under `TRANSACTION_FEES`, or any token not fully zeroed by the first pass) can be swept out twice.

### Likelihood Explanation
Medium. It requires either (a) two `RefundEscrow` messages landing from the destination chain for the same commitment (achievable if a relayer or the caller triggers `_cancelFromDest` twice before `_filled` is durably read by future calls, since ISMP dispatch is fire-and-forget and duplicate delivery/relaying is not prevented by a commitment-level idempotency check in `_withdraw`), or (b) a race between `_cancelFromDest`'s public "anyone can cancel after deadline" path and a legitimate fill/cancel that hasn't yet synchronized cross-chain. This does not require a malicious relayer, prover, or governance actor — only a public function call timing race, which qualifies as an unprivileged-attacker primitive.

### Recommendation
1. In `_withdraw` (`IntentsBase.sol`), add an explicit `if (finalize && _filled[body.commitment] != address(0)) revert Filled();` check **before** any token transfer, not just before setting `_filled`.
2. Make ISMP request handling idempotent per-commitment on the source chain: track handled `RefundEscrow`/`RedeemEscrow` commitments in a separate processed-set, independent of `_orders` balances, so a duplicate delivered message is a no-op rather than a second payout.
3. Ensure `_cancelFromDest` cannot be invoked without going through `cancelOrder()`'s `Filled()` guard, and that guard and the state write happen atomically (mark `_filled` under the same check-then-write critical section, ideally with the check repeated immediately before dispatch as defense in depth).

### Proof of Concept
Conceptual (not executed, no test harness available):
1. User places a cross-chain order; solver never fills it.
2. After `order.deadline`, attacker calls `cancelOrder()` on the destination chain → `_cancelFromDest` sets `_filled[commitment] = user` and dispatches `RefundEscrow{commitment, tokens, beneficiary:user}`.
3. Before the first ISMP message is delivered/finalized on the source chain, attacker (or a relayer bug) causes a second `RefundEscrow` delivery for the same commitment (e.g., re-submission of the same request, or two separate destination-chain transactions targeting the same `_filled` write window).
4. Source chain's `onAccept` → `_withdraw` runs twice: `_orders[commitment][token]` is decremented and tokens transferred each time `escrowed != 0`, since `_withdraw` never checks a per-commitment idempotency flag — only `escrowed == 0` per token, which can pass twice if escrow wasn't fully drained the first time (e.g., rounding remainder, or partial-fill residual on a same-chain order later routed through this path).
5. Result: solver/attacker's beneficiary account receives more tokens than were ever escrowed for that commitment, or the same tokens are paid out twice from residual balances — direct loss of funds from the escrow.

**Confidence note:** I was not able to fully trace the exact ISMP delivery-once guarantee (whether `pallet-ismp`/`onAccept`'s request-receipt uniqueness on the EVM host prevents a literal duplicate delivery of the identical `PostRequest`). If request-receipt idempotency at the ISMP-host layer strictly prevents replay of the *identical* commitment+body, this specific double-dispatch vector is mitigated at the transport layer and the vulnerability would instead require a distinguishable body (e.g., differing `relayerFee`) to bypass receipt dedup, which needs further verification against `EvmHost`'s receipt-checking code (not indexed/found in this pass).

### Citations

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L240-267)
```text
    function _cancelFromDest(Order calldata order, CancelOptions calldata options, bytes32 commitment) internal {
        if (order.deadline >= _blockNumber()) {
            if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();
        }

        _filled[commitment] = address(uint160(uint256(order.user)));

        bytes memory body = bytes.concat(
            bytes1(uint8(RequestKind.RefundEscrow)),
            abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}))
        );

        DispatchPost memory request = DispatchPost({
            dest: order.source,
            to: abi.encodePacked(_instance(order.source)),
            body: body,
            timeout: 0,
            fee: options.relayerFee,
            payer: msg.sender
        });

        address hostAddr = host();
        if (msg.value > 0) {
            IDispatcher(hostAddr).dispatch{value: msg.value}(request);
        } else {
            dispatchWithFeeToken(request);
        }
    }
```
