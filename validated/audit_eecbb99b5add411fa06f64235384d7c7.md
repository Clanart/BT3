## Title
Excess native `msg.value` sent to `EvmHost.dispatch()` is silently trapped in the host contract instead of refunded to the caller - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.dispatch(DispatchPost memory post)` accepts `msg.value` for automatic ETH→feeToken conversion, but it forwards the *entire* `msg.value` to `swapETHForExactTokens`, and any leftover ETH refunded by the router lands back in `EvmHost` itself — never in the caller's wallet. This is the same broken invariant as the reported `newOfferETH` bug ("excessive `msg.value` is accepted and the difference is lost"), except here the loss is worse: it isn't even a self-inflicted user mistake handled elsewhere, because the sibling contract `IntentGatewayV2` (which performs the identical swap pattern) explicitly refunds the dust while `EvmHost` does not.

### Finding Description
In `EvmHost.dispatch()`:
```solidity
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
    ...
}
``` [1](#0-0) 

Standard `UniswapV2Router02.swapETHForExactTokens` only consumes `amountsIn` (derived from `post.fee`) and refunds `msg.value - amountsIn` via `TransferHelper.safeTransferETH(msg.sender, ...)`. Because `EvmHost` itself is the direct caller of the router (not a delegatecall), that dust refund is paid to `address(this)` (`EvmHost`), not to the original transaction sender (`_msgSender()`). `EvmHost.dispatch()` never re-forwards this residual ETH to the caller — there is no `msgValue -= amounts[0]` bookkeeping or trailing "refund unspent native token" step, unlike the equivalent flow in `IntentGatewayV2.placeOrder`, which explicitly tracks and refunds unspent `msgValue` to the caller:
```solidity
uint256[] memory amounts = IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msgValue}(
    order.fees, path, address(this), block.timestamp
);
msgValue -= amounts[0];
...
if (msgValue > 0) {
    (bool sent,) = msg.sender.call{value: msgValue}("");
    if (!sent) revert InsufficientNativeToken();
}
``` [2](#0-1) [3](#0-2) 

`EvmHost.dispatch()` has no such refund path. Any application or end user calling `dispatch{value: X}(post)` with `X` greater than the ETH needed to swap for `post.fee` (e.g. sending a safety margin because the exact swap output at execution time is unknown ahead of the transaction, exactly the "software performing trading contains an error" scenario cited in the seed report) permanently loses the difference — it accumulates as stray ETH balance on the `EvmHost` contract itself, with no visible sweep/withdraw function for that ETH found in the reviewed sections of `EvmHost.sol`.

### Impact Explanation
This is a direct, unprivileged loss of user funds through a public payable entrypoint (`dispatch()`), matching the "stealing or loss of funds" impact category. Because `EvmHost.dispatch()` is the primary function every application on a chain uses to send cross-chain POST requests through Hyperbridge (as documented in `docs/content/developers/evm/messaging/post-requests.mdx`, which shows `dispatch{value: msg.value}(post)` as the canonical usage pattern), overpayment is a routine and expected occurrence rather than an edge case — callers frequently cannot predict the exact AMM-quoted ETH cost for a target fee-token amount ahead of time and pad `msg.value` for safety, which is precisely the pattern that gets silently confiscated.

### Likelihood Explanation
High. Any dApp or user calling `dispatch()` with native token payment and even slightly more ETH than the AMM needs to produce `post.fee` worth of fee token will lose the excess with no user error beyond normal safety-margin behavior (unlike the "requires a mistake" framing of the original report, this requires no mistake at all — the pattern is baked into the recommended integration flow).

### Recommendation
Track the router's actual ETH consumption and refund any unspent `msg.value` back to `_msgSender()`, mirroring the pattern already implemented in `IntentGatewayV2`:
```diff
 function dispatch(DispatchPost memory post) external payable notFrozen returns (bytes32 commitment) {
     if (msg.value > 0) {
         address[] memory path = new address[](2);
         address uniswapV2 = _hostParams.uniswapV2;
         path[0] = IUniswapV2Router02(uniswapV2).WETH();
         path[1] = feeToken();
-        IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
+        uint256[] memory amounts = IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
             post.fee, path, address(this), block.timestamp
         );
+        uint256 refund = msg.value - amounts[0];
+        if (refund > 0) {
+            (bool sent,) = _msgSender().call{value: refund}("");
+            require(sent, "refund failed");
+        }
     } else if (post.fee > 0) {
         IERC20(feeToken()).safeTransferFrom(_msgSender(), address(this), post.fee);
     }
```

### Proof of Concept
1. Caller invokes `EvmHost.dispatch{value: 1 ether}(post)` where `post.fee` only requires `0.1 ether` worth of ETH to swap for the fee token via the configured Uniswap V2 router.
2. `swapETHForExactTokens{value: 1 ether}(post.fee, path, address(this), block.timestamp)` swaps only `~0.1 ether`, and the router refunds the remaining `~0.9 ether` to `msg.sender` of the swap call, which is `EvmHost` (`address(this)` in `dispatch()`), not the original caller.
3. `dispatch()` returns normally with the commitment hash; the `0.9 ether` residual balance now sits in `EvmHost`'s ETH balance permanently, with no code path in `dispatch()` (or, per the reviewed sections, elsewhere in `EvmHost.sol`) to return it to the original caller.
4. Compare against `IntentGatewayV2SameChainTest.sol::testPlaceOrder_FeeSwap_RefundsExcessNativeToken`, which demonstrates that the sibling contract deliberately protects against exactly this scenario via explicit `msgValue` tracking and refund — confirming that `EvmHost.dispatch()` is missing the equivalent safeguard. [4](#0-3)

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

**File:** evm/src/apps/IntentGatewayV2.sol (L345-360)
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
