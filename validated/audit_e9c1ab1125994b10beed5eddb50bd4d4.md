Found the exact analog. Line 353-361 of `IntentGatewayV2.sol` calls the `swapETHForExactTokens` wrapper (`UniV4UniswapV2Wrapper.sol:74`), which silently truncates `order.fees` to `uint128` before using it as the swap-out amount, while the gateway itself credits the full untruncated `uint256 order.fees` value into escrow.

### Title
Truncation of `order.fees` to `uint128` in the ETH→fee-token swap path lets an attacker escrow a fee obligation far larger than the tokens actually purchased - (File: `evm/src/apps/IntentGatewayV2.sol`, `evm/src/utils/uniswapv2/UniV4UniswapV2Wrapper.sol`)

### Summary
`placeOrder`'s native-fee path passes the full `uint256 order.fees` to `IUniswapV2Router02(uniswapV2).swapETHForExactTokens(order.fees, ...)` [1](#0-0)  which for native-fee orders resolves to `UniV4UniswapV2Wrapper.swapETHForExactTokens`, whose first action encodes the requested amount as `uint128(amountOut)` [2](#0-1) . If `order.fees` exceeds `type(uint128).max`, the value silently wraps instead of reverting (unlike the VTVL `_baseVestedAmount` bug, which reverted on overflow — here the analogous narrow-width truncation instead corrupts the traded amount without any revert at all). The gateway nonetheless records the full, untruncated `order.fees` into escrow: `_orders[commitment][TRANSACTION_FEES] = order.fees;` [3](#0-2) .

### Finding Description
`placeOrder` lets the caller set an arbitrary `order.fees` (a `uint256`) as part of the user-supplied `Order` struct. When the order pays fees in native token (`msgValue > 0`), the gateway swaps ETH for the fee token via the router, requesting exactly `order.fees` tokens out [4](#0-3) .

The router used for this integration is `UniV4UniswapV2Wrapper`, which implements `swapETHForExactTokens` but immediately casts the requested amount to `uint128` when building the Uniswap V4 swap params: `abi.encode(poolKey, true, uint128(amountOut), uint128(msg.value), bytes(""))` [5](#0-4) . This cast does not revert on truncation in Solidity — it silently wraps modulo 2^128 — so a `fees` value like `2**128 + 1` becomes `1` in the actual swap request. The wrapper still returns `amounts[1] = amountOut` (the untruncated `uint256` value) to the caller [6](#0-5) , so `IntentGatewayV2` has no way to detect the mismatch between what was actually swapped and what was requested.

Back in `IntentGatewayV2.placeOrder`, the code unconditionally sets `_orders[commitment][TRANSACTION_FEES] = order.fees` using the original, full-size value [3](#0-2) , regardless of what was actually acquired via the swap. Later, on settlement/withdrawal, `_withdraw` unconditionally forwards this recorded fee balance to the beneficiary out of the contract's general token balance: `fees = _orders[body.commitment][TRANSACTION_FEES]; ... IERC20(...feeToken()).safeTransfer(beneficiary, fees);` [7](#0-6) . Because `_orders[...][TRANSACTION_FEES]` is a shared, ungated ledger drawn from the gateway's aggregate fee-token balance (not a token specifically escrowed for this order), any order whose recorded fee exceeds what was actually purchased is paid out of fees deposited by *other* users' orders — a cross-order fund drain, not merely wasted own funds.

### Impact Explanation
This breaks the "bridged assets ... must move exactly once and only to the rightful beneficiary and amount" invariant. An attacker can place an order with `order.fees` deliberately set just above a multiple of `2**128` so the wrapper only actually swaps/receives a negligible amount of fee token, while the gateway credits the order with the full, enormous nominal fee amount. When the order settles, `_withdraw` pays out that inflated `TRANSACTION_FEES` balance from the shared fee-token pool, draining fee-token balance contributed by unrelated orders/users to the attacker-controlled beneficiary.

### Likelihood Explanation
The only precondition is calling the public, unprivileged `placeOrder` entrypoint with `order.fees` set above `type(uint128).max` and `msg.value > 0` (native-fee path). No relayer, prover, admin, or governance action is required — a single attacker-controlled transaction triggers the corrupted accounting.

### Recommendation
- Reject `order.fees > type(uint128).max` (or any value the swap wrapper cannot represent) before invoking the swap, or
- Have `IntentGatewayV2` credit `_orders[commitment][TRANSACTION_FEES]` with the *actual* fee-token amount received from the swap (measured via balance delta, as is already done for `order.inputs`) rather than the caller-supplied `order.fees`, and
- Make `UniV4UniswapV2Wrapper.swapETHForExactTokens`/`swapExactTokensForETH` explicitly revert if `amountOut`/`amountIn` exceed `type(uint128).max` instead of silently truncating.

### Proof of Concept
1. Attacker calls `IntentGatewayV2.placeOrder` with `order.fees = 2**128 + 1` and `msg.value` set to a small amount of ETH (just enough to cover the actual, truncated swap of `1` unit of fee token).
2. `placeOrder` invokes `IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msgValue}(order.fees, ...)` → resolves to `UniV4UniswapV2Wrapper.swapETHForExactTokens(2**128+1, ...)`.
3. Inside the wrapper, `uint128(amountOut)` truncates `2**128+1` to `1`; the router swap only acquires `1` unit of fee token, and `amounts[1]` returned is still `amountOut` (the full `2**128+1` value) per line 100 of the wrapper.
4. Back in `placeOrder`, `_orders[commitment][TRANSACTION_FEES] = order.fees` records `2**128+1` regardless of the negligible amount actually purchased [3](#0-2) .
5. On settlement, `_withdraw` transfers `fees = 2**128+1` fee-token units to the attacker's beneficiary from the gateway's pooled fee-token balance [7](#0-6) , which in practice will drain whatever fee-token balance the contract holds (contributed by other users' orders), well beyond what the attacker paid in.

Note: I was not able to fully trace every downstream caller of `TRANSACTION_FEES` withdrawal across all settlement paths (e.g. cross-chain vs same-chain finalize) within the available index; a Devin session with full repo access is recommended to confirm all code paths that read `_orders[...][TRANSACTION_FEES]` and verify there is no additional balance check that would catch the shortfall before payout.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L345-356)
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
```

**File:** evm/src/apps/IntentGatewayV2.sol (L361-361)
```text
            _orders[commitment][TRANSACTION_FEES] = order.fees;
```

**File:** evm/src/utils/uniswapv2/UniV4UniswapV2Wrapper.sol (L66-76)
```text
    function swapETHForExactTokens(uint256 amountOut, address[] calldata path, address recipient, uint256 deadline)
        external
        payable
        returns (uint256[] memory amounts)
    {
        PoolKey memory poolKey = _createPoolKey(path[1]);

        bytes[] memory params = new bytes[](3);
        params[0] = abi.encode(poolKey, true, uint128(amountOut), uint128(msg.value), bytes(""));
        params[1] = abi.encode(poolKey.currency0, uint256(0), false);
        params[2] = abi.encode(poolKey.currency1, recipient, amountOut);
```

**File:** evm/src/utils/uniswapv2/UniV4UniswapV2Wrapper.sol (L98-101)
```text
        amounts = new uint256[](2);
        amounts[0] = msg.value - refundETH;
        amounts[1] = amountOut;
    }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L412-417)
```text
        if (finalize) {
            uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
            if (fees > 0) {
                delete _orders[body.commitment][TRANSACTION_FEES];
                IERC20(IDispatcher(host()).feeToken()).safeTransfer(beneficiary, fees);
            }
```
