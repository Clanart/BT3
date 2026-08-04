### Title
Mistakenly sent native ETH is silently locked/absorbed when placing an ERC20-only order or overpaying the fee swap in `placeOrder()` - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`placeOrder()` is `payable` but the escrow logic only consumes `msg.value` when an order input token is `address(0)` (native) or when fees are paid via native-to-feeToken swap. If a caller sends ETH alongside an order whose inputs are exclusively ERC20 and `order.fees == 0`, or sends more ETH than the Uniswap swap actually needs for the fee, the leftover native value is never tracked, never refunded to the sender, and never escrowed under the order's commitment. This mirrors the LineLib.sol "mistakenly sent eth could be locked" pattern: ERC20 and ETH accounting paths diverge, and the ETH branch silently drops excess value.

### Finding Description
In `placeOrder()` [1](#0-0) , `msgValue` is initialized from `msg.value` and only decremented inside the escrow loop when a given `order.inputs[i].token` is `address(0)`: [2](#0-1) 

If every input is ERC20 (the `else` branch calls `safeTransferFrom` and never touches `msgValue`), any ETH sent with the call is completely unaccounted for. The only remaining consumer of `msgValue` is the fee block: [3](#0-2) 

This block only runs `if (order.fees > 0)`. If `order.fees == 0`, leftover `msgValue` is never spent, never escrowed in `_orders[commitment][address(0)]`, and never refunded — the ETH balance simply accumulates in the contract with zero on-chain accounting tying it back to the depositor.

Even when `order.fees > 0` and `msgValue > 0`, the call is `IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msgValue}(order.fees, path, address(this), block.timestamp)`. Uniswap's `swapETHForExactTokens` refunds any unused ETH to `msg.sender` of that call — which is `IntentGatewayV2` itself (since the router is called directly by the contract, not forwarded from the original caller). So if the user supplies more native value than `order.fees` actually costs, the excess is refunded to the IntentGatewayV2 contract, not back to the user who overpaid. There is no code path that forwards this refunded ETH to the original order placer.

The only mechanism referencing "dust" or sweeping is the `RequestKind.SweepDust` cross-chain governance request [4](#0-3) , which is a privileged, host-authenticated path for protocol-level dust cleanup — not a user-initiated refund mechanism. It does not return the mistakenly sent ETH to the sender; at best it moves the stuck value to a protocol-controlled destination, which from the depositor's perspective is still a total loss of their mistakenly sent ETH.

### Impact Explanation
Any unprivileged user who calls `placeOrder()` with an ERC20-only order (or a native-fee overpayment) and attaches native value they did not intend to lock will have that ETH become permanently stuck in the `IntentGatewayV2` contract with no commitment, no escrow record, and no user-triggerable withdrawal path. This is a direct loss/lock of user funds through the contract's normal, unprivileged public entry point — matching the "stealing or loss of funds" impact category. It requires no malicious peer, relayer, or admin.

### Likelihood Explanation
The trigger is trivial: any wallet/integration mistake that attaches `msg.value` to an ERC20-only `placeOrder()` call, or supplies a generous ETH amount for the fee swap expecting excess to be refunded to them (a very natural expectation given Uniswap's normal refund-to-caller semantics), reproduces the bug. No special preconditions, timing, or privileged roles are needed — only that the caller (understandably) assumes unspent `msg.value` is returned to them.

### Recommendation
- At the end of `placeOrder()`, if `msgValue > 0` after all escrow/fee logic executes, either revert (`if (msgValue > 0) revert UnexpectedNativeToken();`) to force callers to send exact value, or explicitly refund the remainder to `msg.sender` via a low-level call.
- For the fee-swap branch, after `swapETHForExactTokens`, check `address(this).balance` change (or use the router's return value) and forward any refunded ETH to `msg.sender` instead of leaving it on the contract.
- Add a symmetry check similar to the LineLib.sol fix: in the ERC20-only path, explicitly reject non-zero `msg.value` unless it is expected to be consumed by native inputs/fees.

### Proof of Concept
1. User calls `placeOrder(order, graffiti)` with `order.inputs = [{token: USDC, amount: 100e6}]`, `order.fees = 0`, `order.predispatch` empty, but sends `msg.value = 1 ether` by mistake (e.g. wallet default gas/value confusion).
2. Execution enters the `else` branch at [2](#0-1) : `token != address(0)`, so `safeTransferFrom` pulls the USDC, and `msgValue` is left untouched at `1 ether`.
3. `order.fees == 0`, so the block at [3](#0-2)  is skipped entirely.
4. `placeOrder()` returns successfully; the contract's ETH balance increases by 1 ether with no entry in `_orders[commitment][...]` referencing it and no event emitted for it.
5. The user has no function to reclaim this ETH — `cancelOrder()` only operates on `order.inputs` (ERC20 in this scenario), never on stray native balance. The 1 ether is locked in the contract indefinitely (or later swept away from the user entirely via the governance-only `SweepDust` request).

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L66-77)
```text
    enum RequestKind {
        /// @dev Identifies a request for redeeming an escrow.
        RedeemEscrow,
        /// @dev Identifies a request for recording new contract deployments
        NewDeployment,
        /// @dev Identifies a request for updating parameters.
        UpdateParams,
        /// @dev Identifies a request for sweeping accumulated dust
        SweepDust,
        /// @dev Identifies a request for refunding an escrow (cancellation from destination chain)
        RefundEscrow
    }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L332-340)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable {
        // Validate that order has inputs
        if (order.inputs.length == 0) revert InvalidInput();

        address hostAddr = host();
        // fill out the order preludes
        order.user = bytes32(uint256(uint160(msg.sender)));
        order.source = IDispatcher(hostAddr).host();
        order.nonce = _nonce++;
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L444-463)
```text
        } else {
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                if (token == address(0)) {
                    // native token
                    if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
                    msgValue -= order.inputs[i].amount;
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                }

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L465-482)
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

            _orders[commitment][TRANSACTION_FEES] = order.fees;
        }
```
