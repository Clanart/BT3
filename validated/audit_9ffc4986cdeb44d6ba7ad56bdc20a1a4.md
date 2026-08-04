### Title
Excess native-token payment on `dispatch()`/`fundRequest()` is refunded to the `EvmHost` contract instead of the caller, permanently trapping user funds - (File: `evm/src/core/EvmHost.sol`)

### Summary
The external report's core broken invariant is: a swap-based payment path silently mis-handles the leftover/self-referential leg of a token conversion, causing user value to be lost rather than correctly settled. The direct Hyperbridge analog is in `EvmHost.dispatch(DispatchPost)`, `EvmHost.dispatch(DispatchGet)`, and `EvmHost.fundRequest()`, which convert user-supplied native token into `feeToken()` via `swapETHForExactTokens`, but forward the caller's *entire* `msg.value` to the router while designating `address(this)` (the host contract) as both the token recipient and, implicitly, the refund recipient for any unused ETH.

### Finding Description
In all three payable entrypoints, when `msg.value > 0` the host performs: [1](#0-0) 

`IUniswapV2Router02.swapETHForExactTokens{value: msg.value}(post.fee, path, address(this), block.timestamp)` is an *exact-output* swap: the router consumes only as much ETH as needed to produce `post.fee` units of `feeToken()`, and refunds any unspent ETH. Per the standard `UniswapV2Router02` implementation, that refund is sent back to `msg.sender` of the router call — which, because `EvmHost` itself calls the router, is `EvmHost`, not the original transaction sender (`_msgSender()`). The user-facing SDK confirms this pattern is exploited/relied upon by normal usage: quotes intentionally overpay with a buffer (e.g. `protocolFeeInNative * 101n / 100n` in `TokenGateway.quoteNative`) expecting the excess native value to come back: [2](#0-1) 

and the docs explicitly promise a refund of unused native value on the intent-gateway placement flow ("unused native is refunded"): [3](#0-2) 

The identical `swapETHForExactTokens{value: msg.value}(..., address(this), ...)` pattern recurs in `dispatch(DispatchGet)` and `fundRequest()`: [4](#0-3) [5](#0-4) 

Because the recipient argument (`address(this)`) only controls where the *output* `feeToken` goes, not where leftover ETH goes, any ETH sent above the exact amount needed to buy `post.fee`/`get.fee`/`amount` worth of fee token is absorbed by the `EvmHost` contract's own balance rather than returned to `_msgSender()`. There is no compensating logic elsewhere in `dispatch()`/`fundRequest()` that computes the actual ETH consumed and refunds the difference to the caller.

### Impact Explanation
This is a genuine, unprivileged, permissionless loss-of-funds bug matching the bounty's "stealing or loss of funds" category:
- Any caller of the public `dispatch(DispatchPost)`, `dispatch(DispatchGet)`, or `fundRequest()` functions who sends `msg.value` greater than what is strictly required to purchase the requested `fee`/`amount` of `feeToken` permanently loses the difference — it becomes stranded in the `EvmHost` contract's native balance (or is only recoverable by governance/admin via a host-management withdrawal path, not by the user).
- This is not a hypothetical: the SDK itself deliberately overpays by design (1–2% buffers, gas-price volatility margins) on the assumption that excess native value is refunded to the sender, as documented for the intent-gateway flow. Every dispatch made through this pattern with any buffer above the exact swap requirement leaks value from the caller to the host contract.
- Existing guards (`notFrozen` modifier, `feeToken()` transferFrom paths) do not address this because the vulnerable branch is only the `msg.value > 0` swap branch; there is no validation that all of `msg.value` was consumed, nor any explicit refund-to-caller step.

### Likelihood Explanation
High likelihood of triggering under normal operation, not just adversarial conditions: since `swapETHForExactTokens` almost always leaves some dust or a larger remainder (gas-price/slippage buffers baked into SDK quoting, or a user simply sending a round `value`), essentially every native-token-funded dispatch or fee-top-up call will produce unspent ETH that is misdirected. No malicious relayer, prover, or admin is required — a single ordinary user calling `dispatch()`/`fundRequest()` with any overestimate of the native cost loses funds by default.

### Recommendation
Track the ETH balance of `EvmHost` (or read the router's return value) before and after the `swapETHForExactTokens` call, and explicitly forward any residual `msg.value` back to `_msgSender()` with a low-level call, e.g.:
```solidity
uint256 balanceBefore = address(this).balance - msg.value;
IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(post.fee, path, address(this), block.timestamp);
uint256 refund = address(this).balance - balanceBefore;
if (refund > 0) {
    (bool ok, ) = _msgSender().call{value: refund}("");
    require(ok, "refund failed");
}
```
Apply the same fix uniformly to `dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()`.

### Proof of Concept
1. Attacker/user calls `EvmHost.dispatch(DispatchPost)` with `post.fee = 100` (feeToken units) and `msg.value` set generously higher than the exact ETH needed to buy 100 feeToken units (e.g. 20% headroom, matching normal SDK buffer behavior).
2. `swapETHForExactTokens{value: msg.value}(100, [WETH, feeToken], address(this), deadline)` executes: the router consumes only the ETH required to net exactly 100 feeToken, sends 100 feeToken to `address(this)` (`EvmHost`), and refunds the unspent ETH to `msg.sender` of the router call, which is `EvmHost` itself.
3. `EvmHost`'s native balance increases by the unspent ETH; the original caller who funded the transaction receives nothing back.
4. Repeating this for every dispatch call across all users constitutes a systematic, permissionless leak of native-token value from callers into the host contract, recoverable only via the privileged `withdraw()`/host-management path — never returned to the payer.

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

**File:** sdk/packages/sdk/src/protocols/tokenGateway.ts (L207-212)
```typescript
		// Get protocol fee from source chain by calling quoteNative
		// This returns the cost in native tokens for dispatching the request
		const protocolFeeInNative = await this.source.quoteNative(postRequest, relayerFee)

		// Add 1% buffer to the protocol fee
		const protocolFeeWithBuffer = (protocolFeeInNative * 101n) / 100n
```

**File:** docs/content/developers/evm/intent-gateway/placing-orders.mdx (L275-282)
```text
#### Native token

The placement transaction carries `nativeValue` extra wei, which the gateway swaps into the fee token through its configured router (unused native is refunded). Check the balance now; the placement step adds `nativeValue` to the transaction:

```typescript title="check-native-fee.ts" lineNumbers
const nativeBalance = await sourceChain.client.getBalance({ address: account.address })
if (nativeBalance < nativeValue) throw new Error("Insufficient native balance for the solver fee")
```
```
