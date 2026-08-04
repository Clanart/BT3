### Title
`EvmHost.dispatch()` / `fundRequest()` accept excess native-token payment and permanently lock it in the host contract - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.dispatch(DispatchPost)`, `EvmHost.dispatch(DispatchGet)`, and `EvmHost.fundRequest()` all accept `msg.value` and swap it for an exact amount of `feeToken` via `swapETHForExactTokens{value: msg.value}(fee, path, address(this), block.timestamp)`. Any native-token overpayment beyond what the swap actually consumes is returned by the router to the caller of the swap — which is `EvmHost` itself, not the original end user. Unlike the analogous app-layer contracts (`IntentGatewayV2`, `ExtrinsicIntents`) in this same repo, which explicitly track leftover `msgValue` and refund it to `msg.sender`, none of these three `EvmHost` functions refund the leftover ETH, and no admin/rescue function exists to recover it. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`dispatch(DispatchPost)` performs:
```
if (msg.value > 0) {
    ...
    IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
        post.fee, path, address(this), block.timestamp
    );
}
``` [1](#0-0) 

The Uniswap V2 router's `swapETHForExactTokens` only spends the exact ETH required to buy `post.fee` units of `feeToken` and refunds the rest via `TransferHelper.safeTransferETH(msg.sender, ...)`. Here, `msg.sender` from the router's perspective is `EvmHost`, since `EvmHost` itself calls the router with `{value: msg.value}`. So the leftover ETH lands back on `EvmHost`'s own balance rather than being forwarded to the original caller of `dispatch()`.

This is the identical bug class from the external report: the function's payment-acceptance logic (`msg.value` used to buy an exact required amount) never checks or returns the surplus to the payer. The same pattern repeats in `dispatch(DispatchGet)` [2](#0-1)  and `fundRequest()` [3](#0-2) .

The repo demonstrates the correct fix is already known and implemented elsewhere: `IntentGatewayV2.placeOrder`/`fillOrder` and `ExtrinsicIntents` explicitly decrement a local `msgValue` accumulator by the amount actually spent (`msgValue -= amounts[0]`) and refund whatever remains to `msg.sender` at the end of the function. [4](#0-3) [5](#0-4)  `EvmHost.dispatch()`/`fundRequest()` never mirror that pattern.

Searching `EvmHost.sol` for a withdraw/rescue/sweep function that an admin could later use to recover stray native ETH turned up nothing; the only fee-related transfers in the file are relayer-fee refunds paid in `feeToken()` on timeout paths, not native-ETH recovery.

### Impact Explanation
Any user who calls `dispatch()` (directly, or indirectly through an app contract that simply forwards `msg.value`) with a native-token amount larger than what is needed to purchase `post.fee`/`get.fee`/`amount` units of `feeToken` will have the difference permanently stuck in `EvmHost`, with no way for the user or any admin function to reclaim it. Since `dispatch()` is a public, unprivileged entrypoint used by any Hyperbridge app that dispatches cross-chain messages (documented as the standard way to pay in native token per `docs/content/developers/evm/messaging/post-requests.mdx`), this is a routine, easily-triggered fund-loss path affecting ordinary users, not just an edge case. [6](#0-5) 

### Likelihood Explanation
Because slippage/price movement between quoting and executing `swapETHForExactTokens` is expected and documented (the docs even warn against using `quote()` on-chain due to sandwich risk), users/integrators routinely send extra native tokens as a safety margin. [7](#0-6)  Every such over-provisioned call to `dispatch()` or `fundRequest()` loses the surplus with no attacker action required — this happens on ordinary, honest usage, making the likelihood high.

### Recommendation
Mirror the pattern already used in `IntentGatewayV2`/`ExtrinsicIntents`: capture the amount actually spent by the swap (`swapETHForExactTokens` returns `amounts[0]`), and refund `msg.value - amounts[0]` back to `_msgSender()` at the end of `dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()`. Alternatively, add a governed sweep function scoped strictly to accidental native-ETH dust, separate from protocol-owned fee balances.

### Proof of Concept
1. Deploy/point at a live `EvmHost` with a configured `uniswapV2` router and `feeToken`.
2. Call `dispatch(DispatchPost{... fee: 1e18 ...})` with `msg.value = 2 ether` while the actual ETH cost to buy `1e18` feeToken is `0.5 ether`.
3. `swapETHForExactTokens{value: 2 ether}(1e18, path, address(this), block.timestamp)` spends `0.5 ether` and refunds `1.5 ether` back to `address(this)` (`EvmHost`), per standard Uniswap V2 router semantics.
4. `dispatch()` returns normally; `EvmHost`'s native ETH balance increases by `1.5 ether` that came from the caller.
5. Confirm no code path in `EvmHost.sol` (checked for `withdraw`/`rescue`/`sweep`) ever transfers this ETH back to the caller or to any beneficiary — it is permanently unrecoverable within the contract, contrasted with `IntentGatewayV2.placeOrder`, which under the identical overpay scenario correctly refunds the excess (see `testPlaceOrder_FeeSwap_RefundsExcessNativeToken`). [8](#0-7)

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

**File:** docs/content/developers/evm/messaging/post-requests.mdx (L181-187)
```text
            payer: msg.sender
        });
        
        // User must send enough native tokens to cover fees
        // The Host will swap native -> feeToken via Uniswap
        return IDispatcher(host()).dispatch{value: msg.value}(post);
    }
```

**File:** docs/content/developers/evm/messaging/post-requests.mdx (L236-238)
```text
<Callout type="warning" title="Estimate Fees Off-Chain">
Use the `quote()` view function from your frontend to estimate how much native token users need to send. **Do not call `quote()` in smart contract transactions.** It uses Uniswap's `getAmountsIn`, making it vulnerable to sandwich attacks. Only use it off-chain for frontend fee estimation
</Callout>
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
