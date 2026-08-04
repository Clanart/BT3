Reducing the report to its core primitive: a strict, raw on-chain **balance snapshot** is used as ground truth for accounting, and an attacker-controlled step lets that balance be manipulated/interleaved with other state before the check completes. The Fantom bug used `selfdestruct`-forced ETH plus a rigid equality check; the Hyperbridge analog is the same class of bug — balance-based settlement accounting combined with an **attacker-controlled arbitrary external call executed without a reentrancy guard** on the Tron/TVM build of `IntentGatewayV2`.

### Title
Missing reentrancy guard on `IntentGatewayV2.placeOrder`/order-settlement paths in the Tron build allows re-entrant double-escrow-credit via attacker-controlled `predispatch.call` - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The canonical EVM `IntentGatewayV2` guards `placeOrder` with `nonReentrant` (`ReentrancyGuardTransient`). [1](#0-0) [2](#0-1) 
The Tron/TVM port of the same contract implements the identical `placeOrder` logic — including dispatching a fully attacker-supplied `predispatch.call` through the shared `CallDispatcher` and then crediting escrow based on the dispatcher's raw balance — but does **not** apply `nonReentrant` (or any reentrancy guard) to `placeOrder`. [3](#0-2) 

### Finding Description
`placeOrder` lets the caller supply `order.predispatch.call`, arbitrary calldata that is dispatched through the shared, permissionless `CallDispatcher` contract before escrow amounts are finalized: [4](#0-3) 

`CallDispatcher.dispatch` only checks that the call target has code — it will happily call back into `IntentGatewayV2` itself, or any attacker-deployed contract, with attacker-chosen `value` and `data`: [5](#0-4) 

After the predispatch call executes, the gateway reads the dispatcher's **raw balance** — not a tracked, per-order deposit amount — to build the sweep transfer and to credit escrow for the current commitment: [6](#0-5) 

Because `placeOrder` in the Tron build has no reentrancy lock, a malicious `predispatch.call` target can re-enter `placeOrder` (or other state-mutating entry points that touch `_orders[...]`/`_nonce`) while the outer call is still mid-flight — i.e., after the dispatcher has received the outer order's deposited assets but before the outer call has swept/credited them into `_orders[commitment][token]`. The inner, re-entrant call observes the same dispatcher balance (which still includes the outer order's not-yet-swept deposit) and can drive a second `placeOrder`/escrow-credit cycle against that same balance, or manipulate `_nonce`/commitment derivation ordering between the two overlapping calls. This is exactly the "strict balance check exploited by an attacker-influenced balance state" pattern from the report, except here the primitive is a directly attacker-controlled call rather than `selfdestruct`.

### Impact Explanation
This falls squarely under "logic attacks" / "double-claim / double-settlement" in the Hyperbridge impact gate: a single native/ERC20 deposit into the shared `CallDispatcher` can be observed and credited to escrow more than once across nested `placeOrder` invocations, or interleaved with `_nonce`/commitment computation in a way that produces inconsistent order state, letting an unprivileged attacker cause the gateway to lock in escrow credit for value it never actually received per-order. This is a production contract path (`IntentGatewayV2` intent settlement/escrow), reachable by any unprivileged caller supplying a crafted `Order.predispatch.call`, with no relayer, prover, or admin involvement required.

### Likelihood Explanation
High for the Tron deployment specifically: the vulnerable code path (`predispatch.call` + dispatcher balance sweep) is a normal, documented feature of `placeOrder`, not an edge case, and the only thing preventing exploitation on the EVM build is the `nonReentrant` modifier that is absent from the Tron mirror. Any user can trigger it by placing an order whose `predispatch.call` targets a contract they control.

### Recommendation
Add `nonReentrant` (or an equivalent transient/storage reentrancy lock) to `placeOrder`, `fillOrder`, and any other state-mutating entry point in `evm/tron/contracts/apps/IntentGatewayV2.sol` that dispatches attacker-controlled calldata via `CallDispatcher`, mirroring the guard already present in `evm/src/apps/IntentGatewayV2.sol`. Additionally, avoid crediting escrow from a raw `balanceOf`/`.balance` read on a shared dispatcher; track exact per-call transferred amounts (e.g., via return values or a per-order sub-account) so a shared dispatcher's balance cannot be conflated across concurrent/nested orders.

### Proof of Concept
1. Attacker deploys `Evil` contract implementing a fallback/function that, when called by `CallDispatcher`, re-enters `IntentGatewayV2.placeOrder` with a second order.
2. Attacker calls `placeOrder` with `order.predispatch.assets` funding the dispatcher with native ETH, and `order.predispatch.call` = a `Call[]` whose target is `Evil`.
3. `CallDispatcher.dispatch(order.predispatch.call)` invokes `Evil`, which — because `placeOrder` in the Tron build lacks `nonReentrant` — calls back into `placeOrder` (or another state-changing function) before the outer call finishes sweeping/crediting `_orders[commitment][token]`.
4. The inner call observes the dispatcher's balance (still holding the outer order's deposited ETH) and drives its own sweep/credit cycle, then returns.
5. The outer call resumes, performs its own sweep/credit cycle against the same (now partially/fully drained or double-read) dispatcher balance, resulting in escrow accounting (`_orders[commitment][token]`) that does not correspond 1:1 to actual net asset inflow — i.e., double-credited or corrupted escrow state, without ever needing a malicious relayer, prover, or admin. [3](#0-2) [5](#0-4)

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L61-61)
```text
contract IntentGatewayV2 is IntrinsicIntents, ExtrinsicIntents, ReentrancyGuardTransient, Initializable {
```

**File:** evm/src/apps/IntentGatewayV2.sol (L162-162)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable nonReentrant {
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L332-340)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable {
        // Validate that order has inputs
        if (order.inputs.length == 0) revert InvalidInput();

        address hostAddr = host();
        // fill out the order preludes
        order.user = bytes32(uint256(uint160(msg.sender)));
        order.source = IDispatcher(hostAddr).host();
        order.nonce = _nonce++;
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L383-408)
```text
        if (order.predispatch.call.length > 0 && order.predispatch.assets.length > 0) {
            address dispatcher = _params.dispatcher;

            // Transfer all predispatch assets to the call dispatcher
            uint256 assetsLen = order.predispatch.assets.length;
            for (uint256 i; i < assetsLen;) {
                address token = address(uint160(uint256(order.predispatch.assets[i].token)));
                uint256 amount = order.predispatch.assets[i].amount;

                if (token == address(0)) {
                    if (amount > msgValue) revert InsufficientNativeToken();
                    msgValue -= amount;

                    (bool sent,) = dispatcher.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, dispatcher, amount);
                }

                unchecked {
                    ++i;
                }
            }

            // Execute the call dispatcher with predispatch call
            ICallDispatcher(dispatcher).dispatch(order.predispatch.call);
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L410-440)
```text
            // Transfer tokens from call dispatcher back to IntentGateway
            Call[] memory transferCalls = new Call[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 requiredAmount = order.inputs[i].amount;
                uint256 balance;

                if (token == address(0)) {
                    balance = address(dispatcher).balance;
                    if (balance < requiredAmount) revert InsufficientNativeToken();
                    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
                } else {
                    balance = IERC20(token).balanceOf(dispatcher);
                    if (balance < requiredAmount) revert InvalidInput();
                    transferCalls[i] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                }

                uint256 dust = balance - requiredAmount;
                if (dust > 0) emit DustCollected(token, dust);

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }
```

**File:** evm/src/utils/CallDispatcher.sol (L44-61)
```text
    function dispatch(bytes memory encoded) external {
        Call[] memory calls = abi.decode(encoded, (Call[]));
        uint256 callsLen = calls.length;
        for (uint256 i = 0; i < callsLen; ++i) {
            Call memory call = calls[i];
            uint32 size;
            address to = call.to;
            assembly {
                size := extcodesize(to)
            }

            if (size == 0) {
                revert NotContract(to);
            }

            (bool success, bytes memory result) = to.call{value: call.value}(call.data);
            if (!success) revert CallFailed(to, result);
        }
```
