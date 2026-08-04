### Title
Native-token overpayment on `EvmHost.dispatch()`/`fundRequest()` is refunded by the router to the Host, not to the paying user, permanently locking the excess ETH - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()` accept `msg.value` and swap it for an exact amount of fee token via `IUniswapV2Router02.swapETHForExactTokens{value: msg.value}(...)`. This is the direct Hyperbridge analog of the Cork Protocol AMM issue: a caller supplies a "desired" amount (`msg.value`) to satisfy a required amount (`post.fee`/`get.fee`/`amount`), the AMM/router only consumes what's needed, and the difference is never accounted for by the protocol.

### Finding Description
In `EvmHost.sol`: [1](#0-0) 
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
```
The same pattern repeats in `dispatch(DispatchGet)` and `fundRequest()`. [2](#0-1) [3](#0-2) 

Uniswap V2's canonical `swapETHForExactTokens` implementation computes `amounts[0] = getAmountsIn(amountOut, path)`, requires `amounts[0] <= msg.value`, performs the swap, and — critically — refunds any unused portion (`msg.value - amounts[0]`) back to `msg.sender`. Here, `msg.sender` as seen by the router is `EvmHost` itself (the Host is the caller of the router), not the end user who called `dispatch()`/`fundRequest()`. The return value of `swapETHForExactTokens` (the `amounts` array) is never captured or checked, and no logic exists to forward the refunded ETH to `_msgSender()`.

This is exactly the Cork Protocol bug class: a "desired" input (`msg.value`) vs. a "required" output (`post.fee`) mismatch handled by an AMM, where the unspent remainder is not tracked or returned — except here the remainder is native ETH that becomes permanently stuck in the Host contract rather than a few wei of a token.

Compounding the issue: the Host contract has no `receive()`/`fallback()` guard logic tying incoming ETH to a purpose, and no visible mechanism in `IHostManager.withdraw`/`WithdrawParams` (governance-only, restricted to `_hostParams.hostManager`) that specifically targets "leftover swap-refund ETH" as distinct from protocol-owned funds — meaning any accumulated user overpayment is indistinguishable from host revenue and is not returned to the original payer.

### Impact Explanation
Any user or integrating contract that overestimates the native-token amount needed to cover `post.fee`/`get.fee`/`fundRequest(amount)` — which is expected behavior since exact fee-token pricing fluctuates with the pool's spot price between the moment of fee estimation off-chain and the transaction's on-chain execution — loses the ETH difference into the Host contract with no path to recovery. Because `dispatch`/`fundRequest` are public, permissionless, and called on every single cross-chain message with native-token payment, this is not an edge case: it's the default off-chain quoting flow described in the docs (`quoteNative()` estimates a native amount, and any block-to-block price movement or slippage rounding causes at least some residual `msg.value` to remain unspent, up to whatever slack the caller provisions for slippage). Funds are lost to the depositing account and effectively captured by the Host, which qualifies as unauthorized loss/locking of user funds under the Hyperbridge bounty scope ("stealing or loss of funds").

### Likelihood Explanation
High. This occurs on the default, unprivileged, most common code path (dispatching a paid ISMP message with native token) with no attacker required — it's triggered by normal usage any time the caller doesn't supply the *exact* minimal `msg.value`, which is the practical norm since front-end/SDK quoting (`quoteNative`) computes an estimate before the transaction lands and typically must add slippage buffer to avoid reverts, guaranteeing overpayment on essentially every dispatch.

### Recommendation
Capture and check the `amounts` array returned by `swapETHForExactTokens` in `dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()`, and refund `msg.value - amounts[0]` directly to `_msgSender()` (not `address(this)`), mirroring the pattern already used correctly in `IntentGatewayV2.sol` where `msgValue -= amounts[0]` is tracked and the remaining native balance is properly accounted for. Add a low-level `.call{value: refund}("")` with success check, consistent with existing ETH-refund patterns elsewhere in the codebase (e.g., `UniV4UniswapV2Wrapper.sol`'s `refundETH` handling).

### Proof of Concept
1. Host's `uniswapV2` pool WETH/feeToken price at block N implies `post.fee` (e.g. 100 feeToken) requires 1.00 ETH.
2. Off-chain SDK calls `quoteNative()` and, to protect against price movement between quoting and execution, the caller supplies `msg.value = 1.02 ETH` (2% buffer) to `dispatch(post)`.
3. `EvmHost.dispatch` calls `swapETHForExactTokens{value: 1.02 ether}(100e18, path, address(this), deadline)`.
4. The Uniswap V2 router computes `amounts[0] = 1.00 ether` is sufficient, performs the swap, and refunds `0.02 ether` — to `EvmHost` (since `EvmHost` is `msg.sender` from the router's perspective), landing in the Host's ETH balance.
5. `EvmHost.dispatch` returns without ever reading `amounts` or forwarding the `0.02 ether` refund to the original caller.
6. The 0.02 ETH is now stranded in `EvmHost`, with no code path that returns it to the depositor; it is only reachable via governance-restricted `withdraw()`, which sends it to an arbitrary beneficiary chosen by `_hostParams.hostManager`, not the user who paid it. [1](#0-0) [4](#0-3)

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L465-479)
```text
        if (order.fees > 0) {
            // escrow fees
            address feeToken = IDispatcher(hostAddr).feeToken();
            if (msgValue > 0) {
                address uniswapV2 = IDispatcher(hostAddr).uniswapV2Router();
                address WETH = IUniswapV2Router02(uniswapV2).WETH();
                address[] memory path = new address[](2);
                path[0] = WETH;
                path[1] = IDispatcher(hostAddr).feeToken();
                IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msgValue}(
                    order.fees, path, address(this), block.timestamp
                );
            } else {
                IERC20(feeToken).safeTransferFrom(msg.sender, address(this), order.fees);
            }
```
