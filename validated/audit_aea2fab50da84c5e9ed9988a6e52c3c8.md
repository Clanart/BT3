## Finding

### Title
Solver fund loss via unguarded `_cancelFromDest` racing `_fillCrossChain` on cross-chain escrow release - (File: `evm/src/apps/intentsv2/ExtrinsicIntents.sol`)

### Summary
The Papr report's core broken invariant is: a cheap, unprivileged front-run mutates a balance that a legitimate transaction later subtracts a **fixed** amount from, with no re-validation against the party's actual current entitlement, causing the legitimate transaction to fail or the wrong party to be paid. The same broken invariant exists in `IntentGatewayV2`'s cross-chain settlement path: `_cancelFromDest` never checks whether the order has already been filled before dispatching a `RefundEscrow` message, so it can race the legitimate `RedeemEscrow` message dispatched by `_fillCrossChain` for the exact same escrow.

### Finding Description
`_fillCrossChain` ( [1](#0-0) ) lets a solver deliver output tokens to the beneficiary and sets `_filled[commitment] = msg.sender`, then dispatches a `RedeemEscrow` `PostRequest` back to the source chain so the solver can claim the escrowed input tokens.

`_cancelFromDest` ( [2](#0-1) ) only guards *who* can call it (creator before deadline, anyone after deadline). It never checks `_filled[commitment]` before proceeding — it unconditionally overwrites `_filled[commitment]` and dispatches a `RefundEscrow` message to the source chain for the same `order.inputs`/commitment:

```solidity
function _cancelFromDest(Order calldata order, CancelOptions calldata options, bytes32 commitment) internal {
    if (order.deadline >= _blockNumber()) {
        if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();
    }
    _filled[commitment] = address(uint160(uint256(order.user)));
    ...
```

Both `RedeemEscrow` and `RefundEscrow` land on the source chain and are handled identically by `onAccept` → `_withdraw` ( [3](#0-2) ), which subtracts a fixed `amount` (the full `order.inputs[i].amount`, since cross-chain fills are all-or-nothing) from `_orders[commitment][token]`:

```solidity
uint256 escrowed = _orders[body.commitment][token];
if (escrowed == 0) revert UnknownOrder();
_orders[body.commitment][token] = escrowed - amount;
``` [4](#0-3) 

Whichever of the two messages (`RedeemEscrow` from the solver's fill, `RefundEscrow` from a cancellation) is delivered to the source chain first zeroes the escrow and marks `_filled[commitment]` there; the second reverts with `UnknownOrder`. Because `_cancelFromDest` never checks the destination-side `_filled[commitment]` before firing, an attacker (or the order's own user, who after `order.deadline` has passed is not even required to be the creator — "anyone" may call it) can dispatch a `RefundEscrow` for an order that a solver has *already filled* on the destination chain. If that `RefundEscrow` message reaches the source chain before the solver's `RedeemEscrow`, the escrow is refunded to the user and the solver's own `RedeemEscrow` message permanently reverts on delivery (`UnknownOrder`) — the solver has already paid out the output tokens on the destination chain and gets nothing back on the source chain.

This mirrors the Papr bug class exactly: a legitimate settlement transaction (`RedeemEscrow`) subtracts a fixed amount from mutable shared state (`_orders[commitment][token]`) with no re-check against current entitlement, and a cheap unprivileged action (`_cancelFromDest`, gated by nothing but the deadline) can mutate that state first and cause the legitimate claim to permanently fail — except here the consequence is not just a DoS/retry but genuine fund loss for the solver.

### Impact Explanation
A solver who correctly fills a cross-chain order (delivering real output tokens to the beneficiary) can lose the entire escrowed reimbursement if a racing `RefundEscrow` (triggerable by anyone once the fill/cancel race straddles `order.deadline`) lands first on the source chain. This is unauthorized reallocation of escrowed funds away from the rightful beneficiary (the filling solver) — a direct fund-loss scenario, not merely a griefing/DoS as in the original Papr report.

### Likelihood Explanation
`_cancelFromDest` requires no special privilege once `order.deadline < block.number` — "anyone" can call it per the code's own comment. Solvers naturally fill orders near their deadline (to maximize the priced window), so the race window between a solver's fill (and its outbound `RedeemEscrow` dispatch/relay) and a third party's post-deadline cancel call is realistic and cheap to trigger; it requires no compromised relayer, prover, or admin — only ordinary message-delivery timing on Hyperbridge, which an attacker can influence by simply submitting `_cancelFromDest` as soon as the deadline passes and paying for its relay to be prioritized.

### Recommendation
`_cancelFromDest` should check `_filled[commitment] == address(0)` (i.e., the order has not already been filled/cancelled on the destination chain) before proceeding, mirroring the guard implicitly relied upon by `_cancelFromSource`'s GET-proof check. Additionally, `_withdraw`/`onAccept` should treat a second, contradictory settlement message (`RefundEscrow` after `RedeemEscrow` or vice versa) for the same commitment as a hard authorization failure distinguishable from ordinary double-spend prevention, so that the choice of which message wins is deterministic and not simply "whichever the relayer delivers first."

### Proof of Concept
1. Alice places a cross-chain order with `deadline = D`.
2. Near block `D`, Solver Bob calls `fillOrder` → `_fillCrossChain`: transfers output tokens to Alice's beneficiary address, sets `_filled[commitment] = Bob` on the destination chain, and dispatches `RedeemEscrow{commitment, tokens: order.inputs, beneficiary: Bob}` to the source chain.
3. As soon as block height passes `D`, attacker Carol (or Alice herself) calls `cancelOrder` → `_cancelFromDest` for the same order. Since the function never checks `_filled[commitment]`, it succeeds, overwrites `_filled[commitment] = Alice`, and dispatches `RefundEscrow{commitment, tokens: order.inputs, beneficiary: Alice}` to the source chain.
4. If Carol's `RefundEscrow` relay lands on the source chain before Bob's `RedeemEscrow` (e.g., Carol pays a higher relayer fee or simply wins the race), `_withdraw` zeroes `_orders[commitment][token]` and pays Alice.
5. Bob's `RedeemEscrow` is later delivered and reverts in `_withdraw` with `UnknownOrder` because `escrowed == 0`.
6. Result: Bob permanently loses the output tokens he already delivered to Alice on the destination chain, with no reimbursement — funds diverted to the wrong party (Alice) instead of the rightful solver (Bob).

### Citations

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L89-95)
```text
    function _fillCrossChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;

        uint256 msgValue = msg.value;
        address beneficiary = address(uint160(uint256(order.output.beneficiary)));
```

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L400-403)
```text
            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
```
