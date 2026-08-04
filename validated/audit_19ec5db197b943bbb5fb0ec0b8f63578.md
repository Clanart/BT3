### Title
Excess native-token payment on `EvmHost.dispatch()`/`fundRequest()` is refunded to the Host contract instead of the payer, permanently stranding user ETH - (File: evm/src/core/EvmHost.sol)

### Summary
`EvmHost.dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()` accept `msg.value` and forward it to `IUniswapV2Router02.swapETHForExactTokens{value: msg.value}(fee, path, address(this), ...)`. The standard UniswapV2 router refunds any unused ETH (`msg.value - amountIn`) to its immediate caller. Because `EvmHost` itself calls the router, the refund lands on `EvmHost`, not on the original `msg.sender`/`payer` who overpaid. Unlike `IntentGatewayV2.placeOrder`, which explicitly performs the same fee-swap pattern and then refunds `msgValue` leftover to `msg.sender` (evm/src/apps/IntentGatewayV2.sol:345-368), `EvmHost.dispatch`/`fundRequest` have no such refund step.

### Finding Description
In `EvmHost.sol`: [1](#0-0) [2](#0-1) [3](#0-2) 

All three functions do:
```
if (msg.value > 0) {
    ...
    IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
        post.fee, path, address(this), block.timestamp
    );
}
```
There is no `msg.value -= amounts[0]` accounting and no subsequent `msg.sender.call{value: refund}("")` — the exact refund pattern that `IntentGatewayV2.placeOrder` and `ExtrinsicIntents` apply for their own native-fee swaps: [4](#0-3) [5](#0-4) 

Per the standard UniswapV2Router02 `swapETHForExactTokens` implementation (see ABI reference used by the SDK), the router computes `amounts[0]` = ETH actually needed for `post.fee`/`amount`, performs the swap, and refunds `msg.value - amounts[0]` back to `msg.sender` of the call — which is `EvmHost`, not the end user: [6](#0-5) 

Consequently, any ETH sent above the exact amount needed for the Uniswap swap is silently absorbed into `EvmHost`'s own native balance. `EvmHost` exposes no accounting entry, event, or per-caller ledger for this stray balance, and there is no user-facing withdrawal path tied to it — the same root cause as the referenced Line-of-Credit bug: `msg.value` is used for the swap without validating/handling the surplus, and the surplus is not attributed back to the payer in the contract's internal accounting.

### Impact Explanation
Any caller of `dispatch(DispatchPost)`, `dispatch(DispatchGet)`, or `fundRequest()` who supplies `msg.value` larger than what the AMM swap consumes for the requested `fee`/`amount` — which is essentially guaranteed in practice because callers must estimate a Uniswap swap's ETH cost off-chain under variable slippage/price conditions, exactly as flagged in the original report — has the surplus permanently redirected into `EvmHost`'s balance instead of being returned. This is systemic fund loss for ordinary users dispatching POST/GET requests or funding relayer fees using the "Native Token Payment" path that Hyperbridge's own documentation recommends: [7](#0-6) 

Given that `dispatch()` is a heavily-used, unprivileged, public entrypoint for cross-chain messaging fee payment, this can drain real user ETH at scale with no way for the affected users to reclaim it.

### Likelihood Explanation
Overpayment is the normal case, not an edge case: the documentation itself instructs "User must send enough native tokens to cover fees" without giving an exact quote mechanism resistant to slippage, and the SDK's `quote()` helper can only estimate, not guarantee, the exact swap input. Every dispatcher/fund-request caller using native payment is exposed unless they send the *exact* wei amount the AMM will consume at execution time, which is not deterministically knowable ahead of the transaction due to AMM price movement between quote and execution.

### Recommendation
Mirror the pattern already implemented in `IntentGatewayV2.placeOrder` (evm/src/apps/IntentGatewayV2.sol:345-368) and `ExtrinsicIntents._fillOnSource` (evm/src/apps/intentsv2/ExtrinsicIntents.sol:157-168): capture the `amounts` return value of `swapETHForExactTokens`, compute `refund = msg.value - amounts[0]`, and send `refund` back to `_msgSender()`/`payer` at the end of `dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()` in `EvmHost.sol`.

### Proof of Concept
1. User calls `EvmHost.dispatch{value: 5 ether}(post)` with `post.fee = 100e18` (feeToken units), intending only enough ETH to cover the swap for `100e18` feeToken.
2. `dispatch` calls `uniswapV2.swapETHForExactTokens{value: 5 ether}(100e18, [WETH, feeToken], address(this), block.timestamp)`.
3. The router only needs, say, `0.2 ether` to obtain `100e18` feeToken; it refunds `4.8 ether` — but to `msg.sender` of the router call, which is `EvmHost`, not the user.
4. `EvmHost`'s native balance increases by `4.8 ether`; the user's transaction returns a `commitment`, but the user never receives their `4.8 ether` back.
5. There is no function in `EvmHost.sol` or `HostManager.sol` that lets the original payer reclaim this stranded ETH; it is not tracked in `FeeMetadata`, `_requestCommitments`, or any other per-user accounting structure, matching exactly the "protocol does not refund overpayment... permanently locked" pattern from the seed report.

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

**File:** evm/src/core/EvmHost.sol (L1031-1041)
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

**File:** sdk/packages/sdk/src/abis/uniswapRouterV2.ts (L636-668)
```typescript
		inputs: [
			{
				internalType: "uint256",
				name: "amountOut",
				type: "uint256",
			},
			{
				internalType: "address[]",
				name: "path",
				type: "address[]",
			},
			{
				internalType: "address",
				name: "to",
				type: "address",
			},
			{
				internalType: "uint256",
				name: "deadline",
				type: "uint256",
			},
		],
		name: "swapETHForExactTokens",
		outputs: [
			{
				internalType: "uint256[]",
				name: "amounts",
				type: "uint256[]",
			},
		],
		stateMutability: "payable",
		type: "function",
	},
```

**File:** docs/content/developers/evm/messaging/post-requests.mdx (L162-189)
```text
### Native Token Payment

For native token payments, dispatch directly and let the Host handle the Uniswap swap:

```solidity lineNumbers title="MyApp.sol"
contract MyApp is HyperApp {
    function sendMessageWithNative(
        bytes memory message,
        bytes memory dest,
        uint64 timeout,
        address to,
        uint256 relayerFee
    ) public payable returns (bytes32) {
        DispatchPost memory post = DispatchPost({
            body: message,
            dest: dest,
            timeout: timeout,
            to: abi.encode(to),
            fee: relayerFee,
            payer: msg.sender
        });
        
        // User must send enough native tokens to cover fees
        // The Host will swap native -> feeToken via Uniswap
        return IDispatcher(host()).dispatch{value: msg.value}(post);
    }
}
```
```
