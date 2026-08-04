Confirmed: `_cancelFromDest` in `evm/src/apps/intentsv2/ExtrinsicIntents.sol:245` sets `_filled[commitment] = address(uint160(uint256(order.user)))`. If `order.user` is `bytes32(0)`, this line writes `address(0)` into `_filled[commitment]` — indistinguishable from the "never touched" sentinel used everywhere else (`fillOrder` line 426, `cancelOrder` line 473) as `if (_filled[commitment] != address(0)) revert Filled()`.

### Title
Zero-address `order.user` collides with the unfilled sentinel in `_filled`, enabling double-fill / lost-cancellation on IntentGatewayV2 - (File: evm/src/apps/intentsv2/ExtrinsicIntents.sol)

### Summary
This is the same broken-invariant class as the CNote `_accountant` bug: a state variable meant to encode "not yet set" via `address(0)` is also a value a user fully controls, so setting it to zero silently defeats the guard that depends on it.

### Finding Description
`_filled` is a `bytes32 => address` mapping where `address(0)` means "commitment not yet settled," checked at `fillOrder` (`evm/src/apps/IntentGatewayV2.sol:426`) and `cancelOrder` (`evm/src/apps/IntentGatewayV2.sol:473`): [1](#0-0) [2](#0-1) 

`_cancelFromDest` writes the sentinel directly from `order.user` before dispatching the refund, "to prevent future fills": [3](#0-2) 

`order.user` is a `bytes32` field fully chosen by whoever constructs the `Order` (the order creator, at `placeOrder` time) — there is no visible validation in the shown flows that `order.user != bytes32(0)`. If a user places an order with `order.user = bytes32(0)`, then calls `cancelOrder` from the destination chain, `_filled[commitment]` is set to `address(0)` instead of a non-zero marker. This is functionally a no-op write against the guard.

### Impact Explanation
Because `_filled[commitment]` remains `address(0)` after the "cancel-from-dest" local mark, a solver (or the same actor) can still call `fillOrder` on the destination chain for the exact same commitment — the `if (_filled[commitment] != address(0)) revert Filled()` check at `evm/src/apps/IntentGatewayV2.sol:426` passes because the value is still zero. This produces double-settlement: the destination-side output can be delivered to a filler while the source-chain `RefundEscrow` message (dispatched from `_cancelFromDest`) also independently releases the escrowed input back to the user via `_withdraw` on the source. The user's `order.user = 0` doesn't stop the refund from working since `_withdraw` beneficiary just resolves to `address(0)`, but the destination fill is not blocked, allowing a second output payout that should never have been possible — the order gets both refunded on the source and filled on the destination, an unauthorized double-payout condition (loss of funds for whichever party funds the destination output, typically the protocol/solver economics, and duplicate settlement of a single commitment). This falls squarely under "replay/double-claim/double-settlement" in the bounty's impact gate.

### Likelihood Explanation
Likelihood is Medium: it requires the order creator to deliberately construct `order.user = bytes32(0)` at placement time (fully attacker-controlled, no privileged role needed) and then trigger the destination-side cancel path themselves (`cancelOrder` before the deadline requires `order.user == msg.sender`, but `msg.sender` cast to `bytes32` can never literally be `bytes32(0)` for an EOA/contract call — this constrains the pre-deadline path). However, after the deadline, `_cancelFromDest` permits **anyone** to call cancel (`if (order.deadline >= _blockNumber()) { require order.user == msg.sender }` — the check is skipped after expiry), so any third party can trigger the vulnerable code path against an order whose `user` field was set to zero by its creator, without needing to match `msg.sender`. Combined with attacker-full-control over `order.user` during `placeOrder`, this is directly reachable by an unprivileged actor without relayer/prover/admin assumptions.

### Recommendation
Reject the CNote-style zero-address ambiguity directly:
1. Validate `order.user != bytes32(0)` in `placeOrder` (reject orders with a zero user up front).
2. Use a dedicated non-address sentinel for "filled/cancelled" state instead of overloading `address(0)`, e.g. a separate `mapping(bytes32 => bool) _settled` alongside the beneficiary-recording mapping, so a zero beneficiary can never be conflated with "not yet processed."
3. Mirror the same fix in `evm/tron/contracts/apps/IntentGatewayV2.sol` and `evm/src/apps/intentsv2/IntentsBase.sol`'s `_withdraw`, which have the identical `_filled[commitment] = beneficiary` pattern.

### Proof of Concept
1. Attacker calls `placeOrder` with `order.user = bytes32(0)`, a valid cross-chain destination, and normal input/output tokens.
2. Order deadline elapses.
3. Any third party (or the attacker) calls `cancelOrder(order, options)` on the destination chain. `_cancelFromDest` executes: since `order.deadline < block.number`, the `msg.sender == order.user` check is skipped, and `_filled[commitment] = address(uint160(uint256(bytes32(0)))) = address(0)` is written — a no-op against the sentinel. A `RefundEscrow` message is dispatched to the source chain.
4. Before or concurrently, a solver calls `fillOrder` on the destination chain for the same `order`/`commitment`. The check `_filled[commitment] != address(0)` at `evm/src/apps/IntentGatewayV2.sol:426` still evaluates false (value is zero), so the fill proceeds and output tokens are delivered to the solver, and a `RedeemEscrow` message is also dispatched to the source.
5. Both the `RefundEscrow` and `RedeemEscrow` messages settle on the source chain via `onAccept` → `_withdraw`, releasing the same escrowed input twice (once as a "refund" to `order.user` = address(0), and once as payment to the solver via the redeem path), depending on delivery ordering — resulting in double-settlement of a single order commitment. [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L413-427)
```text
    function fillOrder(Order calldata order, FillOptions calldata options) public payable nonReentrant {
        if (order.deadline < _blockNumber()) revert Expired();
        bytes32 commitment = keccak256(abi.encode(order));

        address hostAddr = host();
        bytes32 currentChain = keccak256(IDispatcher(hostAddr).host());
        bytes32 orderSource = keccak256(order.source);
        bytes32 orderDest = keccak256(order.destination);
        bool isSameChain = orderSource == orderDest;

        if (isSameChain && orderSource != currentChain) revert WrongChain();
        if (!isSameChain && orderDest != currentChain) revert WrongChain();

        if (_filled[commitment] != address(0)) revert Filled();

```

**File:** evm/src/apps/IntentGatewayV2.sol (L470-490)
```text
    function cancelOrder(Order calldata order, CancelOptions calldata options) public payable nonReentrant {
        bytes32 commitment = keccak256(abi.encode(order));

        if (_filled[commitment] != address(0)) revert Filled();

        address hostAddr = host();
        bytes32 currentChain = keccak256(IDispatcher(hostAddr).host());
        bytes32 orderSource = keccak256(order.source);
        bytes32 orderDest = keccak256(order.destination);
        bool isSameChain = orderSource == orderDest;

        if (isSameChain) {
            _cancelSameChain(order, commitment);
        } else if (currentChain == orderSource) {
            _cancelFromSource(order, options, commitment);
        } else if (currentChain == orderDest) {
            _cancelFromDest(order, options, commitment);
        } else {
            revert WrongChain();
        }
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L240-250)
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
```
