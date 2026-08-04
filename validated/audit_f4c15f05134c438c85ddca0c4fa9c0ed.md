### Title
Excess native token sent to `EvmHost.dispatch()`/`fundRequest()` for fee swaps is permanently trapped in the host instead of being refunded to the caller - ([File: evm/src/core/EvmHost.sol])

### Summary
`EvmHost.dispatch(DispatchPost)`, `EvmHost.dispatch(DispatchGet)`, and `EvmHost.fundRequest()` are public, unrestricted, `payable` entry points that let any caller pay the protocol fee in native token. When `msg.value > 0`, each function forwards the *entire* `msg.value` to `swapETHForExactTokens{value: msg.value}(fee, path, address(this), block.timestamp)` on the configured Uniswap V2 router. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`UniswapV2Router02.swapETHForExactTokens` only consumes `amounts[0]` (the exact ETH needed to buy `fee`/`amount` of the fee token) and refunds any unspent `msg.value - amounts[0]` dust ETH back to `msg.sender` — which, in this call, is `EvmHost` itself, not the original caller (`_msgSender()`). `EvmHost` accepts arbitrary ETH via its `receive()`.

Unlike `IntentGatewayV2.placeOrder`, which explicitly captures the router's returned `amounts[0]`, subtracts it from `msgValue`, and forwards the leftover back to `msg.sender` with `msg.sender.call{value: msgValue}("")` (see the "Refund any unspent native tokens" pattern used consistently across `IntentGatewayV2`, `ExtrinsicIntents`, and `IntrinsicIntents`), `EvmHost.dispatch()` and `fundRequest()` never inspect the swap's return value or the leftover ETH — there is no post-swap refund logic at all. [4](#0-3) [5](#0-4) 

This is the same broken invariant as the reported bug (unused native tokens sent for a swap/settlement are not returned to the payer): a payable function accepts `msg.value`, performs a partial spend via an external swap, and any unspent remainder is silently absorbed by the receiving contract instead of being routed back to the rightful payer. Because `EvmHost.dispatch()` is a public entrypoint used both directly by third-party integrators and internally by apps like `ExtrinsicIntents` (`IDispatcher(hostAddr).dispatch{value: options.nativeDispatchFee}(request)`, where `nativeDispatchFee` is a caller/solver-supplied value, not necessarily exactly equal to the real swap cost), any overestimation of the required native fee results in the excess being permanently retained by `EvmHost`.

### Impact Explanation
Funds are lost by the depositor/caller: excess native currency paid on `dispatch()`/`fundRequest()` calls becomes stuck host balance rather than returning to the payer, matching the bounty's "stealing or loss of funds" category. Since Uniswap prices fluctuate between quoting and execution, a caller sending a small buffer of native tokens above the exact fee cost (a normal, expected pattern, as documented and tested for the wrapper-level `IntentGatewayV2.placeOrder`) will lose that buffer whenever they instead go through the raw `EvmHost` fee-payment entrypoints. There is no way for the specific payer to reclaim these funds; only governance-triggered `IHostManager.withdraw` (via a cross-chain `HostManager.onAccept` message) can move funds out of `EvmHost`, and that path sends funds to whatever beneficiary governance specifies, not automatically to the original overpaying caller.

### Likelihood Explanation
High likelihood of occurrence in practice for any caller (application, solver, or user) that pays the native-token dispatch fee with a safety margin instead of a value computed to the wei — a pattern actively used elsewhere in this same codebase (`ExtrinsicIntents._fillCrossChain` passes an arbitrary `options.nativeDispatchFee`). No malicious peer, relayer, or governance actor is required — a normal unprivileged caller triggers the loss simply by calling a public, unrestricted function with `msg.value` slightly greater than the exact required swap input.

### Recommendation
In `EvmHost.dispatch(DispatchPost)`, `EvmHost.dispatch(DispatchGet)`, and `EvmHost.fundRequest()`, capture the `amounts` array returned by `swapETHForExactTokens` and, if `msg.value > amounts[0]`, forward the difference back to `_msgSender()` with a low-level `call`, mirroring the refund pattern already implemented in `IntentGatewayV2.placeOrder` and `ExtrinsicIntents._fillCrossChain`.

### Proof of Concept
1. Configure `EvmHost` with a live Uniswap V2 router and fee token (as in existing fork tests).
2. Compute the exact fee-token cost `amounts[0]` for a target `post.fee` via `getAmountsIn`.
3. Call `EvmHost.dispatch{value: amounts[0] + X}(post)` for some `X > 0` (simulating normal slippage buffer).
4. Observe: `EvmHost`'s ETH balance increases by `X`; the caller's balance decreases by `amounts[0] + X` with no refund event or transfer back — contrasted directly with `IntentGatewayV2SameChainTest.testPlaceOrder_RefundsExcessNativeToken` / `testPlaceOrder_FeeSwap_RefundsExcessNativeToken`, which assert the analogous overpayment IS refunded at the app layer. [6](#0-5) [7](#0-6)

### Citations

**File:** evm/src/core/EvmHost.sol (L921-932)
```text
    function dispatch(DispatchPost memory post) external payable notFrozen returns (bytes32 commitment) {
        if (msg.value > 0) {
            address[] memory path = new address[](2);
            address uniswapV2 = _hostParams.uniswapV2;
            path[0] = IUniswapV2Router02(uniswapV2).WETH();
            path[1] = feeToken();
            IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
                post.fee, path, address(this), block.timestamp
            );
        } else if (post.fee > 0) {
            IERC20(feeToken()).safeTransferFrom(_msgSender(), address(this), post.fee);
        }
```

**File:** evm/src/core/EvmHost.sol (L974-985)
```text
    function dispatch(DispatchGet memory get) external payable notFrozen returns (bytes32 commitment) {
        if (msg.value > 0) {
            address[] memory path = new address[](2);
            address uniswapV2 = _hostParams.uniswapV2;
            path[0] = IUniswapV2Router02(uniswapV2).WETH();
            path[1] = feeToken();
            IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
                get.fee, path, address(this), block.timestamp
            );
        } else if (get.fee > 0) {
            IERC20(feeToken()).safeTransferFrom(_msgSender(), address(this), get.fee);
        }
```

**File:** evm/src/core/EvmHost.sol (L1031-1042)
```text
    function fundRequest(bytes32 commitment, uint256 amount) external payable notFrozen {
        if (msg.value > 0) {
            address[] memory path = new address[](2);
            address uniswapV2 = _hostParams.uniswapV2;
            path[0] = IUniswapV2Router02(uniswapV2).WETH();
            path[1] = feeToken();
            IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
                amount, path, address(this), block.timestamp
            );
        } else {
            IERC20(feeToken()).safeTransferFrom(_msgSender(), address(this), amount);
        }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L345-368)
```text
        if (order.fees > 0) {
            address feeToken = IDispatcher(hostAddr).feeToken();
            if (msgValue > 0) {
                address uniswapV2 = IDispatcher(hostAddr).uniswapV2Router();
                address WETH = IUniswapV2Router02(uniswapV2).WETH();
                address[] memory path = new address[](2);
                path[0] = WETH;
                path[1] = IDispatcher(hostAddr).feeToken();
                uint256[] memory amounts = IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msgValue}(
                    order.fees, path, address(this), block.timestamp
                );
                msgValue -= amounts[0];
            } else {
                IERC20(feeToken).safeTransferFrom(msg.sender, address(this), order.fees);
            }

            _orders[commitment][TRANSACTION_FEES] = order.fees;
        }

        // Refund any unspent native tokens to the user.
        if (msgValue > 0) {
            (bool sent,) = msg.sender.call{value: msgValue}("");
            if (!sent) revert InsufficientNativeToken();
        }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L157-168)
```text
        if (options.nativeDispatchFee > 0 && msgValue >= options.nativeDispatchFee) {
            IDispatcher(hostAddr).dispatch{value: options.nativeDispatchFee}(request);
            msgValue -= options.nativeDispatchFee;
        } else {
            dispatchWithFeeToken(request);
        }

        // Refund any unspent native tokens to the solver.
        if (msgValue > 0) {
            (bool sent,) = msg.sender.call{value: msgValue}("");
            if (!sent) revert InsufficientNativeToken();
        }
```

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L2128-2163)
```text
    /// @notice Excess msg.value beyond native input legs is refunded to the user.
    function testPlaceOrder_RefundsExcessNativeToken() public {
        uint256 inputAmount = 1 ether;
        uint256 overpayment = 0.5 ether;

        TokenInfo[] memory inputs = new TokenInfo[](1);
        inputs[0] = TokenInfo({token: bytes32(0), amount: inputAmount}); // native ETH

        TokenInfo[] memory outputAssets = new TokenInfo[](1);
        outputAssets[0] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: 1000 * 1e6});

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

        uint256 userBalBefore = user.balance;

        vm.prank(user);
        intentGateway.placeOrder{value: inputAmount + overpayment}(order, bytes32(0));

        // User should only have spent inputAmount, overpayment refunded
        assertEq(user.balance, userBalBefore - inputAmount, "Overpayment should be refunded");
        assertEq(address(intentGateway).balance, inputAmount, "Gateway should only hold escrowed amount");
    }
```

**File:** evm/tests/foundry/IntentGatewayV2Test.sol (L3460-3499)
```text
    /// @notice placeOrder with fee swap refunds unused ETH after swapETHForExactTokens.
    function testPlaceOrder_FeeSwap_RefundsExcessNativeToken() public {
        uint256 inputAmount = 1000 * 1e6;
        uint256 feeAmount = 1 * 1e18; // 1 DAI worth of fees

        TokenInfo[] memory inputs = new TokenInfo[](1);
        inputs[0] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: inputAmount});

        TokenInfo[] memory outputAssets = new TokenInfo[](1);
        outputAssets[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 1000 * 1e18});

        PaymentInfo memory output =
            PaymentInfo({beneficiary: bytes32(uint256(uint160(user))), assets: outputAssets, call: ""});

        Order memory order = Order({
            user: bytes32(0),
            source: "",
            destination: host.host(),
            deadline: block.number + 1000,
            nonce: 0,
            fees: feeAmount,
            session: address(0),
            predispatch: DispatchInfo({assets: new TokenInfo[](0), call: ""}),
            inputs: inputs,
            output: output
        });

        uint256 userEthBefore = user.balance;

        vm.startPrank(user);
        usdc.approve(address(intentGateway), inputAmount);
        // Send 5 ETH for a fee swap that should cost much less
        intentGateway.placeOrder{value: 5 ether}(order, bytes32(0));
        vm.stopPrank();

        // User should get back most of the 5 ETH — the swap only needed a tiny fraction
        uint256 ethSpent = userEthBefore - user.balance;
        assertTrue(ethSpent < 1 ether, "User should have been refunded most of the 5 ETH");
        assertTrue(ethSpent > 0, "User should have spent some ETH on the fee swap");
    }
```
