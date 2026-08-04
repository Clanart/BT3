## Finding

### Title
Excess native-token payment in `EvmHost.dispatch()` is silently donated to the Host instead of refunded to the caller - ([File: evm/src/core/EvmHost.sol])

### Summary
The Frax report describes a value (`minVeFXSForMaxBoost`) that is derived from a live, atomically-manipulable AMM state and used to compute a payout without accounting for the fact that the spot price can move within the same transaction, letting the caller capture value they didn't earn. Hyperbridge's `EvmHost.dispatch()` has the mirror-image defect: it derives the *native-token cost* of a fixed fee-token amount from the live Uniswap V2 pool at call time, and any native token the caller supplied beyond what the pool actually consumes is not returned to the caller — it is refunded by the Uniswap router to `msg.sender` of the swap call, which is the `EvmHost` contract itself, not the original payer.

### Finding Description
`EvmHost.dispatch(DispatchPost)` accepts native token payment and swaps it for the exact `post.fee` amount of `feeToken` via the host-configured Uniswap V2 router: [1](#0-0) 

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
    }
    ...
```

`swapETHForExactTokens` computes `amounts[0]` (the ETH actually required) from the pool's **live reserves at call time** — the same class of "spot price at execution" value the Frax report flags as manipulable. Uniswap V2 Router02's implementation of this function refunds `msg.value - amounts[0]` to `msg.sender` of the router call. Because `EvmHost` calls the router directly (not via `delegatecall`), `msg.sender` from the router's perspective is `address(EvmHost)`, not the original transaction sender or `post.payer`. The refunded ETH lands in the Host contract with no code path visible in `EvmHost.sol` that forwards it back to the caller or credits it to `post.payer`.

The same pattern repeats in `dispatch(DispatchGet)` and `fundRequest()`: [2](#0-1) [3](#0-2) 

Every one of these functions calls `swapETHForExactTokens{value: msg.value}(...)` with no `amountOutMin`/refund-forwarding logic, so any `msg.value` supplied above the pool's current-block requirement for `post.fee`/`get.fee`/`amount` is trapped in the Host.

Callers are expected to size `msg.value` using `HyperApp.quote()`, which itself reads the same live spot price: [4](#0-3) 

Between the `quote()` read (or any client-side estimate) and the `dispatch()` transaction landing on-chain, the pool price can move — either naturally or via a deliberate manipulation (a large swap through the same Uniswap V2 pair immediately before the victim's `dispatch()` call lands, then reversed after). A price move that makes the pool cheaper for the swap causes `amounts[0] < msg.value`, and the difference is refunded to the Host contract, not the caller — a permanent, protocol-level loss of the caller's funds with no recovery mechanism.

### Impact Explanation
This is a genuine, on-chain, protocol-level loss-of-funds bug: native tokens a user legitimately sends to pay a Hyperbridge dispatch fee can be partially captured by the `EvmHost` contract itself whenever the live AMM price at execution differs from the price assumed when `msg.value` was sized — which is guaranteed to happen under any adversarial or even just normal price movement, and can be amplified by an attacker sandwiching the pool immediately before a victim's `dispatch()`/`fundRequest()` call. Unlike a generic slippage complaint, there is no slippage bound (`amountOutMin`) and no refund-forwarding logic at all in `EvmHost`, so 100% of the "spot price moved" delta is siphoned to the contract rather than returned to the rightful payer. This matches the required impact class: loss of user funds caused by an unguarded, execution-time spot-price dependency, exactly the invariant broken in the Frax report.

### Likelihood Explanation
Any unprivileged user or app calling `dispatch()`/`fundRequest()` with native-token payment is affected whenever their supplied `msg.value` exceeds the current-block cost — a common and unavoidable situation given price volatility and the delay between quoting and transaction inclusion. An attacker can deterministically trigger and maximize the loss for a targeted large dispatch by trading against the same Uniswap V2 pair pool in the block immediately preceding the victim's transaction, cheapening the WETH→feeToken leg, then reversing the trade afterward, all without needing relayer, prover, or governance privileges.

### Recommendation
- **Short term:** After `swapETHForExactTokens`, forward any leftover `msg.value` (i.e., `msg.value - amounts[0]`) back to `_msgSender()` (or `post.payer`/`get.payer`) instead of letting it sit in `EvmHost`. Apply the same fix to `dispatch(DispatchGet)` and `fundRequest()`.
- **Long term:** Add an `amountOutMin`/max native-in bound derived from a caller-supplied slippage tolerance, so `dispatch()` reverts rather than silently overcharging when the pool price has moved beyond an acceptable band — consistent with the guard pattern already used elsewhere in the repo (e.g., the `referencePrice`/`maxDeviationBps` guard in `sdk/packages/simplex`).

### Proof of Concept
1. Victim calls `HyperApp.quote(post)` to estimate native cost for `post.fee`, gets `X` wei, and submits `dispatch{value: X}(post)`.
2. Attacker, in the block before the victim's transaction lands, swaps a large amount of WETH into the feeToken side of the same Uniswap V2 pair the Host uses, cheapening `WETH → feeToken` at the pool.
3. Victim's `dispatch()` executes `swapETHForExactTokens{value: X}(post.fee, [WETH, feeToken], address(this), block.timestamp)`. Because the pool is now cheaper, `amounts[0] < X`.
4. The Uniswap router refunds `X - amounts[0]` ETH to `msg.sender`, i.e., `EvmHost`, not the victim. `EvmHost.sol` has no logic to forward this refund to the victim.
5. Attacker reverses their swap in a later block, restoring the pool, having caused the victim to permanently lose `X - amounts[0]` wei to the Host contract. [1](#0-0)

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

**File:** sdk/packages/core/contracts/apps/HyperApp.sol (L73-80)
```text
    function quote(DispatchPost memory request) public returns (uint256) {
        address _host = host();
        address _uniswap = IDispatcher(_host).uniswapV2Router();
        address[] memory path = new address[](2);
        path[0] = IUniswapV2Router02(_uniswap).WETH();
        path[1] = IDispatcher(_host).feeToken();
        return IUniswapV2Router02(_uniswap).getAmountsIn(request.fee, path)[0];
    }
```
