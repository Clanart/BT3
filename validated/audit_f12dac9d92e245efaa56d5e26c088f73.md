## Analysis

The external report's core invariant break: **the ledger records an amount larger than what was actually received/owed, while later logic (subtraction/redemption) assumes the ledger equals reality — creating insolvency/bad-debt.**

Searching Hyperbridge's intent-settlement escrow code for the same pattern (fee applied on one path, but a different — unadjusted — amount recorded as the escrowed/owed balance) surfaces a direct analog in the **Tron deployment** of the Intent Gateway.

### The EVM (mainland) reference implementation does it correctly

`evm/src/apps/IntentGatewayV2.sol::placeOrder` first transfers tokens and **measures the actual amount received** (protecting against fee-on-transfer tokens), and only afterward computes the protocol-fee-reduced amount and commitment from that real, received value: [1](#0-0) 

This is exactly the behavior validated by the `FeeOnTransferToken` tests: [2](#0-1) 

### The Tron port breaks this invariant

`evm/tron/contracts/apps/IntentGatewayV2.sol::placeOrder` computes `reducedInputs` (and the order `commitment`) **before** any tokens are transferred, using the caller-declared `order.inputs[i].amount`: [3](#0-2) 

Then, in the non-predispatch (direct transfer) branch, it calls `safeTransferFrom` with the declared amount and credits escrow with the **pre-computed, unadjusted** `reducedInputs[i].amount` — with no check of the actual balance the gateway received: [4](#0-3) 

Contrast this with the predispatch branch of the same file, which *does* verify actual received balance and emits `DustCollected` for any shortfall/overage: [5](#0-4) 

### Title
Escrow ledger overstates actual token balance for fee-on-transfer tokens on direct-transfer path — ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
In the Tron `IntentGatewayV2.placeOrder`, the non-predispatch (direct `safeTransferFrom`) branch credits `_orders[commitment][token]` with the fee-reduced amount computed from the user-declared `order.inputs[i].amount`, without verifying that the gateway actually received that amount. For any ERC-20/TRC-20 token that takes a transfer fee (deflationary/fee-on-transfer tokens), the contract's actual token balance will be less than the sum of what its internal escrow ledger claims is owed across all orders using that token — an exact analog of the debt-vault bug ("the contract's debt is inconsistent with the total sum … the bias increases over time").

### Finding Description
`placeOrder` computes `reducedInputs[i].amount = originalAmount - protocolFee` using the raw, caller-supplied `order.inputs[i].amount` *before* any transfer occurs, and derives `commitment` from that value: [6](#0-5) 

It then executes the transfer and unconditionally credits the ledger with that pre-computed value: [4](#0-3) 

There is no `balanceOf` before/after check in this branch (unlike the EVM mainline contract and unlike the Tron file's own predispatch branch a few lines above). If the input token deducts a transfer fee, the gateway's real token balance increases by less than `order.inputs[i].amount`, yet `_orders[commitment][token]` is credited as if the full (fee-reduced-only) amount arrived. The escrow ledger is now globally overstated relative to the contract's actual token holdings for that token.

### Impact Explanation
Because `_orders[commitment][token]` (the accounting ledger) no longer matches the real custody balance, downstream consumers of that ledger — `fillOrder`/`_withdraw` (paying fillers) and `cancelOrder`/`_cancelSameChain` (refunding users) — will eventually attempt to pay out more than the contract actually holds for that token pool. Since the shortfall is silent (no revert, no event), it accumulates order over order. Eventually a legitimate filler or a cancelling user's withdrawal will fail (DoS/fund lock) because the real balance can't cover the ledger's claim, or — more critically — because the shortfall is drawn from the shared token balance rather than per-order isolated funds, an attacker who places many fee-on-transfer-token orders can cause later, unrelated orders on the same token to be under-collateralized, effectively socializing losses across all users of that token and enabling a race to redeem before the shortfall is discovered (fund loss for whoever redeems last).

### Likelihood Explanation
Triggerable by any unprivileged user calling the public `placeOrder` entrypoint with a fee-on-transfer token as an input — no relayer, prover, admin, or governance action needed. Likelihood depends on a fee-on-transfer token being configured/accepted as a valid intent input on the Tron deployment; if such tokens are permitted (as they clearly are handled deliberately elsewhere in this same contract's predispatch branch, and are explicitly tested for in the EVM sibling contract), the bug is trivially and repeatedly triggerable.

### Recommendation
Mirror the EVM mainline contract's pattern in the Tron non-predispatch branch: snapshot `balanceOf(address(this))` before `safeTransferFrom`, compute the actual received delta after the transfer, and only then compute `reducedInputs`/`commitment`/escrow credit from that real received amount (as already done in the file's own predispatch branch).

### Proof of Concept
1. Deploy Tron `IntentGatewayV2` and register a token that charges a 1% transfer fee (e.g., analogous to `FeeOnTransferToken` used in the EVM test suite: [7](#0-6) ).
2. Call `placeOrder` with `order.inputs[0] = { token: feeToken, amount: 1000e18 }` via the non-predispatch path.
3. Observe: `feeToken.balanceOf(gateway)` increases by only `990e18` (post 1% fee), but `_orders[commitment][feeToken]` is credited with `reducedInputs[0].amount` derived from `1000e18` (minus only the protocol fee), i.e. ~`999.7e18` — a shortfall of ~`9.7e18` tokens not backed by any real balance.
4. Repeat across multiple orders on the same token; the aggregate ledger claim on `feeToken` grows to exceed the gateway's actual `feeToken` balance, so a subsequent `fillOrder`/`cancelOrder` withdrawal for an unrelated, earlier, fully-solvent order can revert or be short-paid once the pooled balance is exhausted by other orders' phantom escrow.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L282-298)
```text
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L342-379)
```text
        // Calculate reduced inputs (after protocol fees) for commitment and escrow
        uint256 inputsLen = order.inputs.length;
        // Use destination-specific protocol fee, fallback to source chain fee if zero
        bytes32 destinationHash = keccak256(order.destination);
        uint256 protocolFeeBps = _destinationProtocolFees[destinationHash];
        if (protocolFeeBps == 0) {
            protocolFeeBps = _params.protocolFeeBps;
        }
        TokenInfo[] memory reducedInputs;
        bytes32 commitment;

        if (protocolFeeBps > 0) {
            reducedInputs = new TokenInfo[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                uint256 originalAmount = order.inputs[i].amount;
                uint256 protocolFee = (originalAmount * protocolFeeBps) / 10_000;
                uint256 reducedAmount = originalAmount - protocolFee;
                address token = address(uint160(uint256(order.inputs[i].token)));

                // Emit DustCollected for protocol fee if non-zero
                if (protocolFee > 0) emit DustCollected(token, protocolFee);

                reducedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: reducedAmount});
                unchecked {
                    ++i;
                }
            }

            // Temporarily swap inputs to calculate commitment with reduced amounts
            TokenInfo[] memory originalInputs = order.inputs;
            order.inputs = reducedInputs;
            commitment = keccak256(abi.encode(order));
            order.inputs = originalInputs;
        } else {
            // No protocol fees, use order.inputs directly
            reducedInputs = order.inputs;
            commitment = keccak256(abi.encode(order));
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L421-440)
```text
                } else {
                    balance = IERC20(token).balanceOf(dispatcher);
                    if (balance < requiredAmount) revert InvalidInput();
                    transferCalls[i] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                }

                uint256 dust = balance - requiredAmount;
                if (dust > 0) emit DustCollected(token, dust);

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }
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
