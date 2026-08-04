### Title
`_cancelFromDest` can pay out escrow to the wrong beneficiary because it never checks if the order was already filled - ([File: evm/src/apps/intentsv2/ExtrinsicIntents.sol])

### Summary
The external report's root cause is a state-transition function (`emergencyRevoke`) that mutates one piece of accounting state (deletes the vesting schedule) while never checking/reconciling it against a related piece of state (`categoryUsed`), letting funds be misrouted/locked. The local analog is `_cancelFromDest` in `ExtrinsicIntents.sol`, which unconditionally overwrites the `_filled` finalization marker and dispatches a fund-moving `RefundEscrow` message without ever checking whether the order was already filled by a solver (which independently dispatches a `RedeemEscrow` message against the exact same escrow balance).

### Finding Description
`_cancelFromDest` is reachable by any address once the order deadline has passed: [1](#0-0) 

It sets `_filled[commitment] = order.user` and dispatches a `RefundEscrow` POST to the source chain — with **no check** of the existing `_filled[commitment]` value before overwriting it.

Compare this to `_fillCrossChain`, which a solver calls (potentially just before the deadline) to fill the order on the destination chain: it sets `_filled[commitment] = msg.sender` and asynchronously dispatches a `RedeemEscrow` message to the source chain to pay itself from the same escrow: [2](#0-1) [3](#0-2) 

Both `RedeemEscrow` and `RefundEscrow` messages are independently-relayed ISMP POST requests targeting the same source-chain escrow. On the source chain, `onAccept` routes either message to the same `_withdraw` function, which unconditionally decrements `_orders[commitment][token]` and pays whichever beneficiary that specific message carries: [4](#0-3) [5](#0-4) 

Whichever message is delivered/relayed first wins the escrow; the second one to arrive reverts with `UnknownOrder` once `escrowed == 0` (`IntentsBase.sol:400-401`). There is no ordering guarantee between the two independently-dispatched cross-chain messages, and no on-chain check in `_cancelFromDest` (or anywhere in `ExtrinsicIntents.sol`/`IntentsBase.sol`) that the order's `_filled` state already reflects a solver fill before triggering a refund dispatch.

This is the direct structural analog of the report: `emergencyRevoke` deletes `vestingSchedules[beneficiary]` without checking/decrementing `categoryUsed`, so a later legitimate operation (`createVestingSchedule`) fails/misallocates. Here, `_cancelFromDest` overwrites `_filled` and races a fund-moving message against the same escrow the solver already relies on for compensation, without checking the actual escrow/fill state first.

### Impact Explanation
If an attacker (or even an honest but slow relayer scenario) calls the destination-chain cancel path after `order.deadline` has elapsed — which is fully permissionless once the deadline passes — and their `RefundEscrow` message reaches the source chain before the legitimate `RedeemEscrow` message dispatched earlier by the solver's fill, the escrowed input tokens are paid to `order.user` instead of the solver. The solver has already delivered the output tokens to the beneficiary on the destination chain (in `_fillCrossChain`) but its own `RedeemEscrow` message then reverts with `UnknownOrder` once it arrives, permanently losing its compensation. This is unauthorized/incorrect fund movement to the wrong beneficiary and fund loss for the rightful party (the solver) — squarely within the bounty's "stealing or loss of funds" / "unauthorized transaction or execution" / "transaction manipulation" categories, and requires no malicious relayer, prover, or admin — just ordinary message-relay timing that any unprivileged caller can influence (e.g., by paying a higher relayer fee on the cancel dispatch to get it relayed first).

### Likelihood Explanation
The race window is realistic: a solver's fill can legitimately occur very close to `order.deadline` (there's no requirement the fill happen far in advance), and the corresponding `RedeemEscrow` relay is asynchronous and can be delayed. Meanwhile `_cancelFromDest` is callable by *any* address as soon as `_blockNumber() > order.deadline`, with no dependency on relayer identity, key compromise, or governance. An attacker only needs to notice a fill near the deadline and race a cancel dispatch with a higher fee to get their `RefundEscrow` message relayed first.

### Recommendation
`_cancelFromDest` should check `_filled[commitment] == address(0)` before proceeding, mirroring the check that a "no-op if already finalized" state machine requires — analogous to updating `categoryUsed` before allowing a category-affecting action. Concretely:
```solidity
function _cancelFromDest(Order calldata order, CancelOptions calldata options, bytes32 commitment) internal {
    if (_filled[commitment] != address(0)) revert Filled();
    ...
}
```
This closes the race by making the destination-side fill state authoritative and rejecting a cancel dispatch once a solver has already filled the order, regardless of message delivery order on the source chain.

### Proof of Concept
1. Solver calls the destination-chain fill entrypoint for `order` just before `order.deadline`, triggering `_fillCrossChain`: `_filled[commitment] = solver`, output tokens sent to beneficiary, and a `RedeemEscrow` message dispatched to the source chain with a modest relayer fee (`evm/src/apps/intentsv2/ExtrinsicIntents.sol:89-171`).
2. `_blockNumber()` advances past `order.deadline`.
3. Attacker (any address) calls the destination-chain cancel entrypoint, which reaches `_cancelFromDest`: overwrites `_filled[commitment] = order.user` and dispatches a `RefundEscrow` message to the source chain with a higher relayer fee to be prioritized (`ExtrinsicIntents.sol:240-267`).
4. The `RefundEscrow` message is relayed/accepted first on the source chain: `onAccept` → `_withdraw(body, true, true)` pays `order.user` and zeroes `_orders[commitment][token]` (`ExtrinsicIntents.sol:289-295`, `IntentsBase.sol:390-410`).
5. The solver's earlier `RedeemEscrow` message later arrives on the source chain; `_withdraw` reverts with `UnknownOrder` because `escrowed == 0` (`IntentsBase.sol:400-401`).
6. Result: the solver paid out the order's output tokens on the destination chain but never received its escrowed compensation on the source chain — funds went to the original user instead of the rightful solver, with no re-entrancy, malicious relayer, or admin action required.

### Citations

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L89-96)
```text
    function _fillCrossChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;

        uint256 msgValue = msg.value;
        address beneficiary = address(uint160(uint256(order.output.beneficiary)));
        TokenInfo[] memory outputFills = new TokenInfo[](outputsLen);
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L139-163)
```text
        address hostAddr = host();
        bytes memory body = bytes.concat(
            bytes1(uint8(RequestKind.RedeemEscrow)),
            abi.encode(
                WithdrawalRequest({
                    commitment: commitment, tokens: order.inputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
                })
            )
        );
        DispatchPost memory request = DispatchPost({
            dest: order.source,
            to: abi.encodePacked(_instance(order.source)),
            body: body,
            timeout: 0,
            fee: options.relayerFee,
            payer: msg.sender
        });

        if (options.nativeDispatchFee > 0 && msgValue >= options.nativeDispatchFee) {
            IDispatcher(hostAddr).dispatch{value: options.nativeDispatchFee}(request);
            msgValue -= options.nativeDispatchFee;
        } else {
            dispatchWithFeeToken(request);
        }

```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L240-245)
```text
    function _cancelFromDest(Order calldata order, CancelOptions calldata options, bytes32 commitment) internal {
        if (order.deadline >= _blockNumber()) {
            if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();
        }

        _filled[commitment] = address(uint160(uint256(order.user)));
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-410)
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
```
