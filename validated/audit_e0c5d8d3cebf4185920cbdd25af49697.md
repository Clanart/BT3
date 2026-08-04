### Title
Overpaid native fee dust from `dispatch()`'s Uniswap fee-swap is refunded to `EvmHost` itself, not to the paying app/user - (File: evm/src/core/EvmHost.sol)

### Summary
The external report describes a "repay with ETH does not refund excess" bug: a user-specified amount larger than what is actually needed leaves the surplus permanently stuck in an intermediary contract instead of being returned to the payer. The same broken invariant exists in Hyperbridge's `EvmHost.dispatch()` native-fee path: when an app pays the ISMP dispatch fee in native ETH, `EvmHost` swaps the *entire* `msg.value` for an exact amount of fee token via Uniswap V2, and any unspent ETH from that swap is refunded by the router to `EvmHost` (the direct caller), not to the original fee-payer.

### Finding Description
`EvmHost.dispatch(DispatchPost)` and `EvmHost.dispatch(DispatchGet)` handle native-token fee payment identically: [1](#0-0) 

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
    } else if (post.fee > 0) { ... }
    ...
}
```

`swapETHForExactTokens` only pulls in the exact input amount Uniswap computes for `post.fee` output tokens; any unused portion of `msg.value` is refunded by the router — but the refund target is `msg.sender` of the router call, which is `EvmHost` (since `EvmHost` itself calls the router), not the original transaction sender or the calling app contract. `dispatch()` never captures or forwards this router refund back to the caller — there is no `refundETH`/leftover-balance accounting in either `dispatch(DispatchPost)` or `dispatch(DispatchGet)`.

This differs from the pattern used elsewhere in the same repo, where callers of `dispatch()` explicitly refund unspent `msg.value` back to the end user after invoking `IDispatcher(hostAddr).dispatch{value: ...}(request)`: [2](#0-1) 

But those callers only refund based on a locally-computed leftover (`msgValue -= options.nativeDispatchFee`), assuming the entire fee amount they forwarded was consumed. They have no way to recover the *actual* AMM refund, because that refund never reaches them — it lands inside `EvmHost`. Any app that forwards native ETH to pay dispatch fees (`WrappedHyperFungibleToken.send()`, `HyperFungibleToken.send()`, `IntentGatewayV2`/`ExtrinsicIntents` fillOrder/placeOrder native-fee paths) is exposed to this same dust-trapping behavior whenever the amount sent exceeds the AMM's exact required input for `post.fee`/`get.fee`.

For example, in `WrappedHyperFungibleToken.send()`, the *entire* leftover `msgValue` (after WETH-wrapping `params.amount`) is forwarded as the dispatch value: [3](#0-2) 

There is no mechanism in `EvmHost.sol` (confirmed by searching for withdraw/sweep/receive-native-balance patterns) that lets the fee-payer, the app, or governance recover ETH dust that accumulates in `EvmHost` from these AMM refunds.

### Impact Explanation
Every native-fee dispatch call across the fungible-token bridge apps and intent gateways that overpays even slightly relative to the exact Uniswap input requirement permanently donates that overpayment to `EvmHost`'s balance instead of returning it to the payer. Because prices move between fee quoting (`quote()`) and execution, and because callers commonly send round or generous `msg.value` amounts (as the docs explicitly recommend, e.g. `msg.value = amount + nativeFee`), this dust accrual will occur routinely, not just in edge cases — directly mirroring the WETHGateway issue's "loss of funds to an unintended party" pattern.

### Likelihood Explanation
High — this triggers on ordinary usage of the documented WETH-mode fee payment flow, requires no malicious actor, relayer, or governance action, and reproduces every time `msg.value` sent for the native-fee path exceeds the AMM's exact quoted input for `post.fee`/`get.fee`.

### Recommendation
In `EvmHost.dispatch(DispatchPost)` and `dispatch(DispatchGet)`, capture the actual amount consumed by `swapETHForExactTokens` (its return value `amounts[0]`) and refund `msg.value - amounts[0]` back to `_msgSender()` (or `post.payer`/`get`'s payer) before returning, rather than letting the router's refund settle silently into `EvmHost`'s own balance. Additionally, add a governance-only skim function to sweep any pre-existing stray native balance out of `EvmHost` to a safe treasury.

### Proof of Concept
1. Attacker/user calls `WrappedHyperFungibleToken.send{value: amount + X}(...)` with `isWeth = true`, where `X` is meant to cover the native dispatch fee but is larger than the exact ETH input Uniswap needs to produce `post.fee` fee tokens.
2. `send()` wraps `amount` into WETH and forwards the full remainder `X` as `msg.value` to `IDispatcher(_host).dispatch{value: X}(request)`.
3. Inside `EvmHost.dispatch()`, `swapETHForExactTokens{value: X}(post.fee, path, address(this), block.timestamp)` only consumes `Y < X` ETH (per Uniswap pricing) and refunds `X - Y` — to `EvmHost`, the calling contract, not to the user or `WrappedHyperFungibleToken`.
4. `dispatch()` returns without forwarding or accounting for `X - Y`; that ETH is now permanently stuck in `EvmHost`'s balance with no recovery path for the user or any sweep function for governance.

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

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol (L266-281)
```text
    function send(HyperFungibleToken.SendParams calldata params) external payable whenNotPaused {
        uint256 msgValue = msg.value;
        if (_isWeth && msgValue >= params.amount) {
            msgValue = msgValue - params.amount;
            IWETH(_underlying).deposit{value: params.amount}();
        } else {
            IERC20(_underlying).safeTransferFrom(msg.sender, address(this), params.amount);
        }

        DispatchPost memory request = _buildDispatchPost(params);
        bytes32 commitment;
        if (msgValue > 0) {
            commitment = IDispatcher(_host).dispatch{value: msgValue}(request);
        } else {
            commitment = dispatchWithFeeToken(request);
        }
```
