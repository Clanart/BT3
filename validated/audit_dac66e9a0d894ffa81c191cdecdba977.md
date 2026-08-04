## Analysis

The external report's core pattern — a state-mutating function that lacks a guard against being invoked over an already-settled/legitimate state, letting an unprivileged actor manufacture a conflicting settlement — has a direct analog in Hyperbridge's Intent Gateway escrow-release path.

### Title
Missing "already-filled" guard in `_cancelFromDest` allows any caller to force a duplicate escrow settlement, causing the legitimate solver to lose their rightful redemption - (File: `evm/src/apps/intentsv2/ExtrinsicIntents.sol`)

### Summary
`_cancelFromDest` — reachable by **anyone** once `order.deadline` has passed — unconditionally overwrites `_filled[commitment]` and dispatches a `RefundEscrow` message to the source chain, without ever checking whether the order was already legitimately filled by a solver via `_fillCrossChain`. Because the corresponding `RedeemEscrow` message from a real fill and the attacker-triggered `RefundEscrow` message are two independently relayed cross-chain ISMP messages, whichever lands first on the source chain wins the escrow via `_withdraw`, which zeroes `_orders[commitment][token]` after the first successful claim. If the `RefundEscrow` lands first, the solver's later, legitimate `RedeemEscrow` reverts with `UnknownOrder()` — the solver already paid out real output tokens on the destination chain but is denied reimbursement, while the escrowed input tokens are instead routed to `order.user`.

### Finding Description
`_fillCrossChain` (evm/src/apps/intentsv2/ExtrinsicIntents.sol:89-171) sets `_filled[commitment] = msg.sender` on the destination chain, delivers output tokens to the beneficiary, and dispatches a `RedeemEscrow` `PostRequest` to the source chain's gateway. [1](#0-0) 

`_cancelFromDest` (evm/src/apps/intentsv2/ExtrinsicIntents.sol:240-267) is the destination-chain cancellation path. Before the deadline only `order.user` may call it, but "From the first destination block after the deadline, anyone may call" (per the docs). It never checks the current value of `_filled[commitment]` — it just stomps it and dispatches `RefundEscrow`: [2](#0-1) 

On the source chain, `onAccept` authenticates both `RedeemEscrow` and `RefundEscrow` identically (both just need to originate from the registered gateway instance) and both funnel into the same `_withdraw`: [3](#0-2) 

`_withdraw` only guards against *replay of the same message type* via `escrowed == 0 → revert UnknownOrder()` — it has no concept of "this commitment was already correctly redeemed by a solver, so a refund is invalid": [4](#0-3) 

Because `RedeemEscrow` and `RefundEscrow` are two independent cross-chain messages relayed and proven separately (different dispatch times, different relayers, different proof/finality latencies), whichever message is delivered and executed first on the source chain consumes `_orders[commitment][token]` and wins. There is no invariant anywhere in the contract that says "a `RefundEscrow` can only be dispatched if the destination order was never filled" — the only check is the deadline-based caller-authorization, which does not verify fill status at all.

### Impact Explanation
This breaks a core Hyperbridge intent-settlement invariant: escrowed funds must move exactly once and only to the rightful beneficiary. A solver who did honest, real work (delivered output tokens on the destination chain) can be denied their input-token reimbursement, while the escrow is instead paid out a second way to `order.user` — who receives back their original tokens *in addition to* having already received the solver's output tokens on the destination chain. This is a direct "wrong beneficiary / duplicate settlement" fund-loss condition against solvers, and it requires no relayer collusion, no admin/governance action, and no compromised keys — any ordinary user can trigger `_cancelFromDest` once the deadline has passed.

### Likelihood Explanation
The attack window is real and controllable: a solver filling near the order deadline (which is allowed, since `_fillCrossChain`'s deadline check only requires `order.deadline >= block.number` at fill time) leaves a race window where the `RedeemEscrow` message is still in flight (awaiting proof/relaying/challenge-period) while the deadline has already elapsed on the destination chain. Any address — not necessarily the user, not necessarily malicious in intent, but any party who wants to force a duplicate refund — can call the destination cancel path during this window and race their `RefundEscrow` ahead of the legitimate `RedeemEscrow`. No special relayer or prover trust is needed; a normal user paying a slightly higher relayer fee for the cancel dispatch can realistically win the race.

### Recommendation
Add an explicit fill-status guard in `_cancelFromDest` (and mirror it in `_cancelSameChain`/any other cancellation path) that rejects cancellation once the order has already been filled on that chain:
```solidity
function _cancelFromDest(Order calldata order, CancelOptions calldata options, bytes32 commitment) internal {
    if (_filled[commitment] != address(0)) revert AlreadyFilled();
    if (order.deadline >= _blockNumber()) {
        if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();
    }
    _filled[commitment] = address(uint160(uint256(order.user)));
    ...
}
```
Additionally, consider making `_withdraw`'s idempotency check commitment-scoped rather than per-token-balance-scoped, so a `RefundEscrow` arriving after a `RedeemEscrow` (or vice versa) for the *same commitment* is rejected outright regardless of message ordering, rather than relying on incidental balance-zeroing.

### Proof of Concept
1. User places a cross-chain order with `deadline = D`.
2. Solver calls `fillOrder` at block `D` (permitted since `deadline >= block.number`), delivering output tokens to the beneficiary and dispatching `RedeemEscrow` to the source chain (in flight, pending relay/proof).
3. At block `D+1`, `order.deadline < block.number`, so the authorization check in `_cancelFromDest` is bypassed for any caller. An arbitrary third party (or the user themselves) calls `cancelOrder` on the destination chain, invoking `_cancelFromDest`, which overwrites `_filled[commitment]` and dispatches `RefundEscrow` to the source chain with a more aggressive relayer fee.
4. `RefundEscrow` lands on the source chain first, `_withdraw` releases the escrowed input tokens to `order.user` and zeroes `_orders[commitment][token]`.
5. The solver's earlier `RedeemEscrow` message later arrives at the source chain's `onAccept`, calls `_withdraw`, finds `escrowed == 0`, and reverts with `UnknownOrder()` — the solver never receives the escrowed input tokens despite having already paid out the real output tokens on the destination chain. [2](#0-1) [4](#0-3)

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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L240-259)
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
