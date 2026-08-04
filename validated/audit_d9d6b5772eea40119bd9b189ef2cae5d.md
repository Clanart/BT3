Based on my research, the closest local analog to the "empty collection breaks a critical path" bug class is in the IntentGateway's same-chain fill logic, where an order with zero output assets skips the entire fill loop but still finalizes the order.

### Title
Order with empty `output.assets` finalizes and permanently locks user escrow without releasing funds - (`evm/src/apps/intentsv2/IntrinsicIntents.sol`)

### Summary
### Finding Description
The external report's core primitive is: a zero-length collection (property with no items) bypasses the logic that is supposed to populate/validate state before a downstream operation runs, causing the system to enter a state that cannot be corrected through the normal path (division by zero on mint).

In `IntrinsicIntents::_fillSameChain`, `outputsLen` is derived from `order.output.assets.length` [1](#0-0) . The loop that computes `escrowedInputs`, releases beneficiary funds, and determines `isFullyFilled` iterates `for (uint256 i; i < outputsLen; i++)` [2](#0-1) . `isFullyFilled` is initialized to `true` before the loop [3](#0-2) , so if `outputsLen == 0` the loop body never executes, `escrowedInputs` stays a zero-length array, and `isFullyFilled` remains `true`.

After the loop, because `order.output.call.length == 0` (no attached calldata for such a degenerate order), the `PartialFillNotAllowed` guard does not trigger [4](#0-3) . The code then calls `_withdraw(body, false, isFullyFilled)` with `body.tokens = escrowedInputs` (empty) and `finalize = true` [5](#0-4) . Inside `_withdraw`, `finalize == true` immediately sets `_filled[body.commitment] = beneficiary` before iterating the (empty) `body.tokens` array, so no escrow is actually transferred [6](#0-5) . The user's originally escrowed `order.inputs` tokens (tracked separately in `_orders[commitment][token]`) are never touched, yet the order commitment is now permanently marked as filled.

I was unable to confirm within my available tool budget whether `placeOrder` explicitly rejects `output.assets.length == 0` — I found an existing check and test that reject `inputs.length == 0` (`testPlaceOrderInvalidInput`) [7](#0-6)  and duplicate-token checks for both inputs and outputs [8](#0-7) , but I did not locate an equivalent "outputs must be non-empty" guard in the `placeOrder`/`ExtrinsicIntents.sol` validation path during this session. This is the key uncertainty in this analog.

### Impact Explanation
If an order with an empty `output.assets` array can be placed and later filled, any address can call `fillOrder`/the same-chain fill path against it, causing `_filled[commitment]` to be set to the caller while the user's escrowed input tokens remain locked in `_orders[commitment][token]` with no code path left to release or refund them (cancellation requires `!_filled` per the cancel flow, and the fill flow that would normally release tokens already ran with an empty token list). This is a permanent loss/lock of user-escrowed funds, matching the bounty's "loss of funds" impact category.

### Likelihood Explanation
This requires only an unprivileged order to exist with zero output assets and any unprivileged caller invoking the fill function — no relayer, prover, or admin involved. Likelihood is contingent entirely on whether `placeOrder` validation currently permits `output.assets.length == 0`; I could not verify this in the given tool budget, so this should be confirmed against `ExtrinsicIntents.sol`/`IntentGatewayV2.sol`'s `placeOrder` validation before treating this as confirmed-exploitable.

### Recommendation
In the order validation path (wherever `inputs.length == 0` is currently rejected), add an equivalent check that reverts with `InvalidInput()` when `order.output.assets.length == 0`. Additionally, harden `_fillSameChain`/`_fillCrossChain` defensively: do not allow `isFullyFilled` (initialized `true`) to reach the finalize branch when `outputsLen == 0`, e.g. explicitly revert if `outputsLen == 0` before the loop.

### Proof of Concept
Conceptual PoC (pending confirmation that `placeOrder` allows empty outputs):
1. User calls `placeOrder` with `order.inputs = [TokenInfo(USDC, 1000e6)]` and `order.output.assets = []` (empty array), `order.output.call = ""`.
2. If accepted, `_orders[commitment][USDC] = 1000e6` is escrowed.
3. Any caller invokes the same-chain fill path with `options.outputs = []`.
4. In `_fillSameChain`, `outputsLen = 0` → loop skipped → `escrowedInputs = []`, `isFullyFilled = true`.
5. `_withdraw(body={tokens: [], commitment, beneficiary: msg.sender}, false, true)` sets `_filled[commitment] = msg.sender` without transferring any of the escrowed 1000e6 USDC.
6. The user's USDC is now unrecoverable: the order is finalized (`_filled` set) so it cannot be re-filled or refunded via the normal cancel path, yet no token was ever released from escrow.

### Citations

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L54-67)
```text
    function _fillSameChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;

        uint256 msgValue = msg.value;
        address beneficiary = address(uint160(uint256(order.output.beneficiary)));
        bool isFullyFilled = true;

        TokenInfo[] memory escrowedInputs = new TokenInfo[](outputsLen);
        TokenInfo[] memory outputFills = new TokenInfo[](outputsLen);

        for (uint256 i; i < outputsLen; i++) {
            bytes32 outputToken = order.output.assets[i].token;
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L126-129)
```text
        // Orders carrying output calldata must be filled completely in a single fill.
        // The attached call is only executed on a full fill, so a partial fill would
        // leave the intended side effect unexecuted while releasing proportional escrow.
        if (order.output.call.length > 0 && !isFullyFilled) revert PartialFillNotAllowed();
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L131-134)
```text
        WithdrawalRequest memory body = WithdrawalRequest({
            commitment: commitment, tokens: escrowedInputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
        });
        _withdraw(body, false, isFullyFilled);
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

**File:** evm/tests/foundry/IntentGatewayV2Test.sol (L2042-2068)
```text
    function testPlaceOrderInvalidInput() public {
        TokenInfo[] memory inputs = new TokenInfo[](0); // Empty inputs

        TokenInfo[] memory outputAssets = new TokenInfo[](1);
        outputAssets[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 1000 * 1e18});

        PaymentInfo memory output =
            PaymentInfo({beneficiary: bytes32(uint256(uint160(user))), assets: outputAssets, call: ""});

        Order memory order = Order({
            user: bytes32(uint256(uint160(user))),
            source: host.host(),
            destination: host.host(),
            deadline: block.number + 1000,
            nonce: 0,
            fees: 0,
            session: address(0),
            predispatch: DispatchInfo({assets: new TokenInfo[](0), call: ""}),
            inputs: inputs,
            output: output
        });

        vm.startPrank(user);
        vm.expectRevert(IntentsBase.InvalidInput.selector);
        intentGateway.placeOrder(order, bytes32(0));
        vm.stopPrank();
    }
```

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L1931-1964)
```text
    /// @notice Placing an order with duplicate input tokens must revert.
    /// Regression test for: same-chain partial fills over-release repeated input escrow.
    function testRevert_PlaceOrder_DuplicateInputTokens() public {
        // Two input legs both using USDC — this previously merged into one escrow bucket
        TokenInfo[] memory inputs = new TokenInfo[](2);
        inputs[0] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: 1200 * 1e6});
        inputs[1] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: 1000 * 1e6});

        TokenInfo[] memory outputAssets = new TokenInfo[](2);
        outputAssets[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 500 * 1e18});
        outputAssets[1] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 1000 * 1e18});

        PaymentInfo memory output =
            PaymentInfo({beneficiary: bytes32(uint256(uint160(user))), assets: outputAssets, call: ""});

        Order memory order = Order({
            user: bytes32(0),
            source: "",
            destination: host.host(),
            deadline: block.number + 100,
            nonce: 0,
            fees: 0,
            session: address(0),
            predispatch: DispatchInfo({assets: new TokenInfo[](0), call: ""}),
            inputs: inputs,
            output: output
        });

        vm.startPrank(user);
        usdc.approve(address(intentGateway), 2200 * 1e6);
        vm.expectRevert(IntentsBase.InvalidInput.selector);
        intentGateway.placeOrder(order, bytes32(0));
        vm.stopPrank();
    }
```
