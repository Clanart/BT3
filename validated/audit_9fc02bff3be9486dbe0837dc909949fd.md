## Title
Escrow accounting trusts *requested* transfer amount instead of *actually received* balance for ERC-20 inputs, enabling insolvency for fee-on-transfer/deflationary tokens — ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

## Summary
This mirrors the LIDO bug class exactly: a contract increases an internal accounting variable by the amount it *requested* to move, rather than the amount it actually received/holds, and later pays out against that inflated accounting figure.

## Finding Description
In the Tron `IntentGatewayV2.placeOrder()`, for non-predispatch ERC-20 inputs, tokens are pulled via `safeTransferFrom(msg.sender, address(this), order.inputs[i].amount)` and the escrow ledger is credited with `reducedInputs[i].amount`, which is derived from `order.inputs[i].amount` (the user-specified/requested amount minus protocol fee) — never from the gateway's actual post-transfer balance delta: [1](#0-0) 

If `token` charges a transfer fee (deflationary/rebasing/fee-on-transfer token), the gateway physically receives less than `order.inputs[i].amount`, yet `_orders[commitment][token]` is credited with the full (fee-reduced-only-by-protocol-fee) requested amount. This is the same broken invariant as the PufferVault report: crediting internal accounting by the *requested* value while the actual custody balance is smaller than what accounting claims.

By contrast, the canonical EVM `IntentGatewayV2.sol` in this same repo already fixed this exact defect by measuring the actual balance delta before crediting escrow: [2](#0-1) 
and the repo's own test suite explicitly documents and verifies this fix for the EVM contract (`testPlaceOrder_FeeOnTransferToken_EscrowMatchesReceived`, `testPlaceAndFill_FeeOnTransferToken_RoundTrip`): [3](#0-2) 

The Tron variant of the same contract does not carry this fix over — it still uses the pre-transfer requested amount for both the actual token pull and (indirectly, via `reducedInputs`) the escrow credit, with no balance-before/after measurement.

## Impact Explanation
Because `_orders[commitment][token]` becomes larger than the token balance the gateway actually custodies, later settlement (`withdraw()` on fill/refund) will pay out against phantom balance: [4](#0-3) 
`_orders[body.commitment][token] -= amount` only checks that the entry is non-zero, not that the contract's real token balance covers it. Since `_orders` accounting is shared across all orders in the same token, an inflated entry for one order can allow that order's beneficiary to drain tokens that are actually the escrowed balance of *other, unrelated* orders in the same token — a fund-loss/insolvency condition matching "Protocol insolvency" and "Permanent freezing of funds" impact classes, without requiring any relayer, prover, or admin compromise. Any unprivileged user placing an order with a fee-on-transfer or deflationary ERC-20 as input triggers the shortfall; any solver filling any order in that token can then be paid out of the resulting shortfall pool.

## Likelihood Explanation
Requires only: (1) a fee-on-transfer/deflationary token being accepted as an order input (no allowlist check evident in the placeOrder path shown), and (2) a normal, permissionless `placeOrder` call. No malicious relayer, prover, or governance actor is needed — this is a straightforward unprivileged-user-triggerable accounting bug, directly analogous to a bug the EVM sibling contract already had to patch.

## Recommendation
Port the fix already present in `evm/src/apps/IntentGatewayV2.sol` to the Tron variant: measure `balanceOf(address(this))` before and after each `safeTransferFrom` (and the predispatch dispatcher hop) and use the actual received delta — not the requested `order.inputs[i].amount` — to compute `reducedInputs` and to credit `_orders[commitment][token]`.

## Proof of Concept
1. Deploy a fee-on-transfer ERC-20 (e.g., 1% fee burned on transfer, as in the repo's own `FeeOnTransferToken` test helper) and register it as a valid input token on the Tron `IntentGatewayV2`.
2. User calls `placeOrder` with `inputs[0].amount = 1000e18` of this token, approving the gateway for `1000e18`.
3. `safeTransferFrom` pulls `1000e18` from the user but the gateway's actual balance only increases by `990e18` (1% burned).
4. `_orders[commitment][token]` is nonetheless credited with `reducedInputs[0].amount` derived from `1000e18` (minus protocol fee only), i.e. accounting overstates the gateway's real balance in that token by `10e18`.
5. A solver fills/cancels this order (or any other order sharing that token) and `withdraw()`/refund logic transfers out against the inflated `_orders` entry, eventually reverting or succeeding at the expense of another order's real escrowed balance in the same token — reproducing the exact "accounting says more than is actually held" condition demonstrated in the referenced `testPlaceOrder_FeeOnTransferToken_EscrowMatchesReceived` test, but observing that the Tron contract lacks the balance-diff guard that test protects on the EVM side.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L444-462)
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
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-705)
```text
    function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        _filled[body.commitment] = beneficiary;

        // redeem escrowed tokens
        uint256 len = body.tokens.length;
        for (uint256 i; i < len;) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (_orders[body.commitment][token] == 0) revert UnknownOrder();

            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
                if (!success) revert TransferFailed();
            }

            _orders[body.commitment][token] -= amount;
            unchecked {
                ++i;
            }
        }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L281-298)
```text
        } else {
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                if (token == address(0)) {
                    if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
                    msgValue -= order.inputs[i].amount;
                } else {
                    uint256 balBefore = IERC20(token).balanceOf(address(this));
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                    order.inputs[i].amount = IERC20(token).balanceOf(address(this)) - balBefore;
                }

                unchecked {
                    ++i;
                }
            }
        }
```

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L2256-2308)
```text
    /// @notice Escrow correctly reflects actual received amount for fee-on-transfer tokens.
    function testPlaceOrder_FeeOnTransferToken_EscrowMatchesReceived() public {
        // Deploy a 1% fee-on-transfer token
        FeeOnTransferToken fot = new FeeOnTransferToken(100); // 1% = 100 bps
        fot.mint(user, 10000 * 1e18);

        uint256 inputAmount = 1000 * 1e18;
        uint256 expectedReceived = inputAmount - (inputAmount * 100) / 10000; // 990

        TokenInfo[] memory inputs = new TokenInfo[](1);
        inputs[0] = TokenInfo({token: bytes32(uint256(uint160(address(fot)))), amount: inputAmount});

        TokenInfo[] memory outputAssets = new TokenInfo[](1);
        outputAssets[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 900 * 1e18});

        PaymentInfo memory output =
            PaymentInfo({beneficiary: bytes32(uint256(uint160(user))), assets: outputAssets, call: ""});

        Order memory order = Order({
            user: bytes32(0),
            source: "",
            destination: host.host(),
            deadline: block.number + 100,
            nonce: 0,
            fees: 0,
            session: address(0),
            predispatch: DispatchInfo({assets: new TokenInfo[](0), call: ""}),
            inputs: inputs,
            output: output
        });

        vm.startPrank(user);
        fot.approve(address(intentGateway), inputAmount);
        intentGateway.placeOrder(order, bytes32(0));
        vm.stopPrank();

        // Gateway should hold only what it actually received
        assertEq(fot.balanceOf(address(intentGateway)), expectedReceived, "Gateway balance should match received amount");

        // Reconstruct the order as placeOrder would have mutated it
        order.user = bytes32(uint256(uint160(user)));
        order.source = host.host();
        order.nonce = 0;
        order.inputs[0].amount = expectedReceived;
        bytes32 commitment = keccak256(abi.encode(order));

        // Escrow should match actual received, not the user-specified amount
        assertEq(
            intentGateway._orders(commitment, address(fot)),
            expectedReceived,
            "Escrow should equal actual received amount"
        );
    }
```
