### Title
Same-chain fillers can capture escrowed Hyperbridge relayer fees intended for cross-chain relaying - ([File: evm/src/apps/intentsv2/IntrinsicIntents.sol])

### Summary
`placeOrder` allows a user to escrow non-zero `order.fees` (documented as "Hyperbridge relayer fees") regardless of whether the order is same-chain or cross-chain [1](#0-0) . When a same-chain order is fully filled, `_fillSameChain` calls `_withdraw` with `finalize = isFullyFilled` and `body.beneficiary = msg.sender` (the filler) [2](#0-1) . `_withdraw`'s `finalize` branch unconditionally forwards any accumulated `_orders[commitment][TRANSACTION_FEES]` to `beneficiary` with no check that the fill actually required a cross-chain relayer [3](#0-2) .

### Finding Description
`fillOrder` routes to `_fillSameChain` whenever `order.source == order.destination` [4](#0-3) . Nothing in `placeOrder` or `fillOrder` prevents `order.fees` from being non-zero for such a same-chain order — the fee token is collected into `_orders[commitment][TRANSACTION_FEES]` unconditionally whenever `order.fees > 0` [5](#0-4) .

On a full same-chain fill, `_withdraw` is invoked with `finalize = true` and `beneficiary = msg.sender` (the filler/solver, not the relayer) [2](#0-1) . The `finalize` branch of `_withdraw` pays out the entire `TRANSACTION_FEES` balance to that beneficiary with no check on whether a cross-chain relayer was actually used: [3](#0-2) . Since same-chain fills involve no Hyperbridge relayer at all, any escrowed relayer fee ends up paid to the same-chain filler instead of being refunded or otherwise never being escrowed for a same-chain order in the first place.

### Impact Explanation
Any filler of a same-chain order that happens to carry non-zero `order.fees` receives those relayer fees for free, even though no relaying service was ever rendered. This corrupts the relayer-fee accounting invariant that `TRANSACTION_FEES` should only be disbursed to whoever actually performed cross-chain relaying — funds move to the wrong beneficiary (the same-chain solver) instead of being refunded to the order's user or withheld.

### Likelihood Explanation
Exploitation only requires an order to exist with `order.source == order.destination` and `order.fees > 0`; the contract does not reject this combination at `placeOrder` time. Any unprivileged filler can then call `fillOrder`/`_fillSameChain` on such an order to redirect the escrowed fee to itself.

### Recommendation
- Reject `order.fees > 0` in `placeOrder` when `order.source == order.destination` (same-chain orders), or
- In `_fillSameChain`, do not forward `TRANSACTION_FEES` to the filler; instead refund it to `order.user` (or leave it unused/refundable) since no relayer service is rendered for same-chain fills, and only allow the fee-forwarding branch of `_withdraw` to pay a genuine relayer beneficiary in the cross-chain (`ExtrinsicIntents`) path.

### Proof of Concept
1. User calls `placeOrder` for an order with `source == destination` (same-chain) and `fees = X > 0`; `X` fee tokens get escrowed under `_orders[commitment][TRANSACTION_FEES]` [1](#0-0) .
2. Attacker (any solver) calls `fillOrder` and fully satisfies the output assets; `fillOrder` routes to `_fillSameChain` since `orderSource == orderDest` [6](#0-5) .
3. `_fillSameChain` calls `_withdraw(body, false, true)` with `body.beneficiary = msg.sender` [2](#0-1) .
4. `_withdraw`'s finalize branch pays the full `X` fee-token balance to `msg.sender` (the attacker/filler) [3](#0-2) .
5. Assert: `_orders[commitment][TRANSACTION_FEES]` is zeroed and the fee token balance of the filler increased by `X`, despite no cross-chain relayer ever being invoked.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L345-362)
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
```

**File:** evm/src/apps/IntentGatewayV2.sol (L419-446)
```text
        bytes32 orderSource = keccak256(order.source);
        bytes32 orderDest = keccak256(order.destination);
        bool isSameChain = orderSource == orderDest;

        if (isSameChain && orderSource != currentChain) revert WrongChain();
        if (!isSameChain && orderDest != currentChain) revert WrongChain();

        if (_filled[commitment] != address(0)) revert Filled();

        if (_params.solverSelection) {
            bytes32 storedSelectionHash;
            assembly {
                storedSelectionHash := tload(commitment)
            }

            bytes32 expectedSelectionHash = keccak256(abi.encode(msg.sender, order.session));
            if (storedSelectionHash != expectedSelectionHash) revert Unauthorized();
        }

        uint256 outputsLen = order.output.assets.length;
        if (options.outputs.length != outputsLen) revert InvalidInput();
        if (order.inputs.length != outputsLen) revert InvalidInput();

        if (isSameChain) {
            _fillSameChain(order, options, commitment);
        } else {
            _fillCrossChain(order, options, commitment);
        }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L131-134)
```text
        WithdrawalRequest memory body = WithdrawalRequest({
            commitment: commitment, tokens: escrowedInputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
        });
        _withdraw(body, false, isFullyFilled);
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
