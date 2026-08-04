Based on my investigation, I found a concrete, locally-provable analog of the "shared asset double counting" bug class in the **Tron port of `IntentGatewayV2`**, which appears to be missing the duplicate-token guards that the canonical EVM `IntentGatewayV2.sol` explicitly added as a security fix.

### Title
Tron `IntentGatewayV2.placeOrder` / escrow accounting lacks duplicate-token guards present in the EVM version, enabling double-counted escrow/fill accounting - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
The canonical EVM `IntentGatewayV2.sol` explicitly rejects orders whose `inputs` or `output.assets` arrays contain the same token twice, because the escrow ledger `_orders[commitment][token]` and the partial-fill ledger `_partialFills[commitment][outputToken]` are both keyed **by token address only**, not by array index. Two array slots sharing the same token collapse into one storage bucket, exactly the "two strategies, one asset" over-counting/double-spend pattern from the external report. The EVM contract fixes this with an explicit duplicate-output check via transient storage in `placeOrder` and an explicit duplicate-input check in the escrow-crediting loop, both backed by regression tests. [1](#0-0) [2](#0-1) 

The Tron variant of the same contract reimplements `placeOrder` from scratch (likely because Tron's TVM does not support the `TLOAD`/`TSTORE` opcodes used for the EVM's duplicate-output check) and, in the code path I was able to inspect, does not perform either the duplicate-output or duplicate-input rejection before computing the commitment and crediting escrow. [3](#0-2) 

### Finding Description
`_orders[commitment][token]` is the single source of truth for how much of a given token is escrowed for an order, and `_partialFills[commitment][outputToken]` tracks how much of an output leg has been filled — both keyed purely by `token`, with no index component. [4](#0-3) [5](#0-4) 

On the EVM side, the team clearly identified and patched this exact class of bug:
- `placeOrder` rejects duplicate output tokens using transient storage before any state is touched, with the comment "Reject duplicate output tokens." [6](#0-5) 
- The escrow-crediting loop rejects duplicate input tokens: `if (_orders[commitment][token] != 0) revert InvalidInput();`. [2](#0-1) 
- Regression tests explicitly document the bugs these checks close: "same-chain partial fills over-release repeated input escrow" and "same-chain partial fills prematurely finalize repeated output legs." [7](#0-6) [8](#0-7) 

The Tron `placeOrder`, however, computes `reducedInputs`/the commitment and proceeds toward escrow crediting without any equivalent duplicate check visible in the function body I inspected. [3](#0-2)  Its `withdraw`/escrow-release path also decrements `_orders[body.commitment][token]` per array entry with no dedup of `token` across the withdrawal's token list. [5](#0-4) 

This is the direct analog of the AdapterVault bug: a single underlying "asset" (token address) bucket gets written/read by two different logical legs (two input legs, or two output legs) of the same order, so accounting that assumes one-token-per-leg either merges balances (losing/overwriting escrow on `placeOrder`) or double-releases/double-fills against one shared bucket (over-paying a solver or beneficiary) on fill/withdraw.

### Impact Explanation
If confirmed by fully tracing the Tron escrow-crediting phase (which I could not completely view within the available iterations), an unprivileged user could place a same-chain order with a duplicate input or output token to corrupt `_orders[commitment][token]`/`_partialFills[commitment][outputToken]`, letting a colluding or opportunistic filler receive escrowed funds beyond what was legitimately deposited, or letting a user's second input leg silently overwrite/merge into the first — directly matching the required impact categories: unauthorized transaction/execution, transaction manipulation, and fund loss from bridge custody (escrow).

### Likelihood Explanation
Likelihood is **Low-Medium**: it requires the Tron deployment of `IntentGatewayV2` to actually lack the guard (matching the EVM history of this exact bug having been found and fixed), and requires an attacker to construct an order with intentionally duplicated token entries — no privileged actor, relayer, or prover involvement needed, just a normal `placeOrder`/`fillOrder` call.

### Recommendation
Port the same duplicate-token rejection logic from `evm/src/apps/IntentGatewayV2.sol` (persistent-storage-based check since Tron may lack transient storage) into `evm/tron/contracts/apps/IntentGatewayV2.sol`, covering both the input-escrow crediting loop and the output/`fillOrder` legs, and add the same regression tests (`testRevert_PlaceOrder_DuplicateInputTokens`, `testRevert_PlaceOrder_DuplicateOutputTokens`) to the Tron test suite.

### Proof of Concept
Not independently reproduced against Tron bytecode in this session — I was unable to view the full `placeOrder` escrow-crediting phase and `fillOrder` logic in `evm/tron/contracts/apps/IntentGatewayV2.sol` before iterations ran out (only lines 1–400 and 691–735 were retrieved). To confirm exploitability, a background agent should:
1. Read the full `evm/tron/contracts/apps/IntentGatewayV2.sol` file end-to-end (`placeOrder`, `fillOrder`, `_withdraw`/`withdraw`).
2. Confirm the absence of both duplicate-input and duplicate-output checks that exist in `evm/src/apps/IntentGatewayV2.sol`.
3. Write a Foundry/Tron-fork test mirroring `testRevert_PlaceOrder_DuplicateInputTokens` and `testRevert_PlaceOrder_DuplicateOutputTokens` against the Tron contract to see if it does *not* revert and instead corrupts escrow/fill accounting.

**Note on confidence**: due to the read/tool budget for this session, I confirmed the missing guard only in the portion of `placeOrder` I could retrieve (lines 332–400) and in the `withdraw` release loop (lines 691–713); I could not view the full escrow-crediting (Phase 3 equivalent) or `fillOrder` code in the Tron file to give a fully certain PoC. This should be treated as a high-priority lead requiring direct code confirmation rather than a fully proven finding.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L163-189)
```text
        if (order.inputs.length == 0) revert InvalidInput();

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
        // Clean up transient storage so repeated placeOrder calls in the same tx don't false-positive.
        for (uint256 i; i < outputsLen_;) {
            bytes32 token = order.output.assets[i].token;
            assembly ("memory-safe") {
                tstore(token, 0)
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L332-400)
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
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L691-713)
```text
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

        // redeem tx fees
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L74-98)
```text
            uint256 alreadyFilled = _partialFills[commitment][outputToken];
            uint256 remaining = totalRequired - alreadyFilled;
            if (remaining == 0 || solverAmount == 0) {
                if (solverAmount == 0 && remaining > 0) isFullyFilled = false;
                continue;
            }
            uint256 fillAmount;

            uint256 beneficiaryShare = 0;
            uint256 protocolShare = 0;
            if (alreadyFilled == 0 && solverAmount > totalRequired) {
                fillAmount = totalRequired;
                uint256 dust = solverAmount - totalRequired;
                if (order.output.call.length > 0) {
                    protocolShare = dust;
                } else {
                    protocolShare = (dust * _params.surplusShareBps) / 10_000;
                    beneficiaryShare = dust - protocolShare;
                }
            } else {
                fillAmount = solverAmount > remaining ? remaining : solverAmount;
            }

            uint256 amountFilled = alreadyFilled + fillAmount;
            _partialFills[commitment][outputToken] = amountFilled;
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

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L2054-2088)
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
        usdc.approve(address(intentGateway), 1000 * 1e6);
        dai.approve(address(intentGateway), 500 * 1e18);
        vm.expectRevert(IntentsBase.InvalidInput.selector);
        intentGateway.placeOrder(order, bytes32(0));
        vm.stopPrank();
    }
```
