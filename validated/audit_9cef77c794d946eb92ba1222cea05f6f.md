Based on my investigation, I found a concrete local analog of the reported bug class (missing uniqueness/consistency validation on a keyed collection that lets divergent duplicate entries corrupt accounting logic).

### Title
`IntentGatewayV2.placeOrder` (Tron variant) accepts duplicate input/output token entries, allowing escrow under-accounting and mismatched fill settlement - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The external report's core invariant break is: a batch of keyed entries (`nftId`) is accepted without verifying that a discriminating field (`isFungible`) stays consistent for that key, corrupting downstream logic that assumes one semantic per key. The same broken invariant class exists in Hyperbridge's `IntentGatewayV2` deployed for Tron: `placeOrder` accepts an `order.inputs`/`order.output.assets` array with duplicate `token` entries without any uniqueness check, whereas the canonical EVM `IntentGatewayV2.sol` was hardened against exactly this with explicit duplicate-token rejection and regression tests.

### Finding Description
In the canonical EVM gateway (`evm/src/apps/IntentGatewayV2.sol`), `placeOrder` explicitly guards against duplicate output tokens using transient-storage dedup [1](#0-0)  and against duplicate input tokens via an explicit `_orders[commitment][token] != 0` check before crediting escrow [2](#0-1) . These were added as regression fixes, documented by tests explicitly titled "Regression test for: same-chain partial fills over-release repeated input escrow" and "prematurely finalize repeated output legs" [3](#0-2) [4](#0-3) .

The Tron deployment of the same protocol, `evm/tron/contracts/apps/IntentGatewayV2.sol`, implements `placeOrder` with the identical escrow-crediting pattern — a `mapping(bytes32 => mapping(address => uint256)) public _orders` keyed by `[commitment][token]` — but has **no** duplicate-token check anywhere in `placeOrder` [5](#0-4) . When a user submits an `order.inputs` array containing the same token address twice (analogous to two tier entries sharing `nftId`), each iteration of the escrow-crediting loop does `_orders[commitment][token] += reducedInputs[i].amount;` [6](#0-5) , additively merging two logically distinct order legs into one storage bucket — exactly the "same nftId, divergent semantics" collision from the reference report, except here the collision is between two `TokenInfo` legs that should be tracked independently but get merged under one key.

This corrupted state then flows into `withdraw()`, which iterates `body.tokens` (which mirrors `order.inputs`) and decrements the same merged bucket once per array entry [7](#0-6) . Because the check is `if (_orders[body.commitment][token] == 0) revert UnknownOrder();` rather than a strict equality/consistency check against the expected sub-amount, a merged bucket lets `withdraw` be called with an amount for one duplicate leg that decrements shared state without validating that the amount matches what was actually escrowed for that specific leg — the same class of "the system checks existence but not consistency" flaw described in the original report about tier creation checking `existingNFTAddr` but not `isFungible`.

### Impact Explanation
This directly maps to the bounty's accepted categories: unauthorized transaction/execution and transaction/logic manipulation of bridge custody (escrowed order funds), and potential double-accounting of settlement legs. A user can place a cross-chain intent order with duplicate input tokens whose per-leg amounts diverge from what the merged bucket implies, then have the solver/settlement path release funds inconsistent with what was actually escrowed, or allow one leg's withdrawal to zero out and free the entire merged escrow while the other logical leg is never paid out correctly — a fund-accounting corruption in the custody path that the same-chain EVM version was specifically patched to prevent.

### Likelihood Explanation
This is reachable by any unprivileged user calling the public `placeOrder` entrypoint on the Tron IntentGateway deployment with a crafted `Order.inputs` array — no relayer, prover, or admin cooperation is required. The EVM sibling contract received a dedicated patch and regression tests for this exact scenario, confirming the maintainers recognize it as a real, exploitable defect class; the Tron contract's lack of the same guard is a straightforward omission rather than a design difference (the storage layout, `_orders` mapping, and settlement flow are otherwise identical).

### Recommendation
Port the same duplicate-token rejection logic from `evm/src/apps/IntentGatewayV2.sol` (lines 165–179 for outputs, 333–343 for inputs) into `evm/tron/contracts/apps/IntentGatewayV2.sol`'s `placeOrder`, rejecting any order whose `inputs` or `output.assets` array contains a repeated `token` value before crediting or committing escrow.

### Proof of Concept
1. Construct an `Order` with `order.inputs = [{token: USDC, amount: 1000}, {token: USDC, amount: 500}]`.
2. Call `IntentGatewayV2.placeOrder(order, graffiti)` on the Tron contract — it succeeds (no duplicate check), and `_orders[commitment][USDC]` becomes `1000 + 500 = 1500` after fee reduction, merging two legs into one bucket [8](#0-7) .
3. On fill/settlement, `withdraw()` is invoked once per `body.tokens` entry containing the same `USDC` address with two different `amount` values from `order.inputs`; the existence check passes for both entries even though the second decrement operates on state already reduced by the first, allowing an amount mismatch between what was escrowed per logical leg and what gets paid out, and permitting the merged bucket to be drained by a withdrawal that doesn't correspond one-to-one with either original leg [9](#0-8) .

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L165-179)
```text
        // Reject duplicate output tokens 
        uint256 outputsLen_ = order.output.assets.length;
        for (uint256 i; i < outputsLen_;) {
            bytes32 token = order.output.assets[i].token;
            assembly ("memory-safe") {
                if tload(token) {
                    mstore(0, 0xb4fa3fb3) // InvalidInput.selector
                    revert(0x1c, 0x04)
                }
                tstore(token, 1)
            }
            unchecked {
                ++i;
            }
        }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L333-343)
```text
        // Phase 3: Credit escrow.
        for (uint256 i; i < inputsLen;) {
            address token = address(uint160(uint256(order.inputs[i].token)));
            // Reject duplicate input tokens
            if (_orders[commitment][token] != 0) revert InvalidInput();
            _orders[commitment][token] = reducedInputs[i].amount;

            unchecked {
                ++i;
            }
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

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L2054-2064)
```text
    /// @notice Placing an order with duplicate output tokens must revert.
    /// Regression test for: same-chain partial fills prematurely finalize repeated output legs.
    function testRevert_PlaceOrder_DuplicateOutputTokens() public {
        TokenInfo[] memory inputs = new TokenInfo[](2);
        inputs[0] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: 1000 * 1e6});
        inputs[1] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 500 * 1e18});

        // Two output legs both requesting DAI — shares one _partialFills bucket
        TokenInfo[] memory outputAssets = new TokenInfo[](2);
        outputAssets[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 400 * 1e18});
        outputAssets[1] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 600 * 1e18});
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L332-463)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable {
        // Validate that order has inputs
        if (order.inputs.length == 0) revert InvalidInput();

        address hostAddr = host();
        // fill out the order preludes
        order.user = bytes32(uint256(uint160(msg.sender)));
        order.source = IDispatcher(hostAddr).host();
        order.nonce = _nonce++;

        // Calculate reduced inputs (after protocol fees) for commitment and escrow
        uint256 inputsLen = order.inputs.length;
        // Use destination-specific protocol fee, fallback to source chain fee if zero
        bytes32 destinationHash = keccak256(order.destination);
        uint256 protocolFeeBps = _destinationProtocolFees[destinationHash];
        if (protocolFeeBps == 0) {
            protocolFeeBps = _params.protocolFeeBps;
        }
        TokenInfo[] memory reducedInputs;
        bytes32 commitment;

        if (protocolFeeBps > 0) {
            reducedInputs = new TokenInfo[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                uint256 originalAmount = order.inputs[i].amount;
                uint256 protocolFee = (originalAmount * protocolFeeBps) / 10_000;
                uint256 reducedAmount = originalAmount - protocolFee;
                address token = address(uint160(uint256(order.inputs[i].token)));

                // Emit DustCollected for protocol fee if non-zero
                if (protocolFee > 0) emit DustCollected(token, protocolFee);

                reducedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: reducedAmount});
                unchecked {
                    ++i;
                }
            }

            // Temporarily swap inputs to calculate commitment with reduced amounts
            TokenInfo[] memory originalInputs = order.inputs;
            order.inputs = reducedInputs;
            commitment = keccak256(abi.encode(order));
            order.inputs = originalInputs;
        } else {
            // No protocol fees, use order.inputs directly
            reducedInputs = order.inputs;
            commitment = keccak256(abi.encode(order));
        }

        // escrow tokens
        uint256 msgValue = msg.value;
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

            // Execute transfer calls from call dispatcher
            ICallDispatcher(dispatcher).dispatch(abi.encode(transferCalls));
        } else {
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                if (token == address(0)) {
                    // native token
                    if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
                    msgValue -= order.inputs[i].amount;
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                }

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
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
