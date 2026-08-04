### Title
Escrow accounting in Tron `IntentGatewayV2.placeOrder()` credits the declared amount instead of the actual tokens received, enabling insolvency/theft of pooled escrow with fee-on-transfer or rebasing tokens - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron deployment of `IntentGatewayV2` credits an order's escrow ledger (`_orders[commitment][token]`) using the user-declared `order.inputs[i].amount` (minus protocol fee), instead of measuring the tokens actually received by the contract. The EVM mainline version of the same contract was hardened against this exact class of bug by snapshotting `balanceOf` before/after every transfer and mutating `order.inputs[i].amount` to the true received amount, but the Tron variant does not carry this fix. Since all orders' escrowed token balances share one physical ERC-20 balance on the contract, an attacker can use a fee-on-transfer (or rebasing) token to inflate their own order's escrow entry beyond what was actually deposited, and later drain that inflated balance out of the shared pool — consuming principal that belongs to other users' legitimately escrowed orders.

### Finding Description
In `placeOrder()` (non-predispatch branch), the Tron contract does: [1](#0-0) 

```
IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
...
_orders[commitment][token] += reducedInputs[i].amount;
```

`reducedInputs[i].amount` is derived purely from `order.inputs[i].amount` (the caller-declared amount) minus the protocol fee — it is never checked against the contract's actual token balance delta: [2](#0-1) 

The predispatch branch has the same defect: it computes `dust = balance - requiredAmount` off the dispatcher's raw balance but still credits escrow with `reducedInputs[i].amount` (based on the declared amount), not the post-sweep balance actually received by the gateway itself: [3](#0-2) 

Contrast this with the corrected EVM mainline contract, which explicitly snapshots balances and rewrites `order.inputs[i].amount` to the actual received amount before computing the commitment/escrow, precisely to prevent this discrepancy for fee-on-transfer/deflationary tokens: [4](#0-3) 

This fix-vs-no-fix gap is also demonstrated by the dedicated regression tests added for the mainline contract (`testPlaceOrder_FeeOnTransferToken_EscrowMatchesReceived`, `testPlaceOrder_FeeOnTransferToken_Predispatch`) which assert escrow equals actual received balance: [5](#0-4) 

No equivalent tests or logic exist for the Tron contract.

When `withdraw()` later releases escrow, it transfers `body.tokens[i].amount` (which is exactly what was credited at placement time, i.e., the inflated declared amount) out of the contract's single pooled ERC-20 balance: [6](#0-5) 

Because `_orders[commitment][token]` is only ever checked for non-zero (`if (_orders[body.commitment][token] == 0) revert UnknownOrder();`), not compared to actual contract balance, and all commitments for the same `token` draw from the same physical balance, an inflated entry for one commitment can be redeemed using tokens that were actually deposited by other, unrelated orders.

### Impact Explanation
This is a direct "stealing or loss of funds" / "false state acceptance" bug matching the bounty scope: the on-chain escrow ledger records more tokens as available than the contract actually custodies for a fee-on-transfer or rebasing token. An attacker who places (and later cancels, refunds, or has filled) an order using such a token receives a payout drawn from the shared token pool that other legitimate users deposited, directly causing their subsequent withdrawals to revert (fund lock) or, if attacker acts first, to succeed at the expense of later depositors' principal — an accounting-driven insolvency exactly analogous to the reported `ZNSTreasury.stakeForDomain()` issue, but reachable by any unprivileged user calling a public entrypoint (`placeOrder`/`cancelOrder`/fill flow) with no relayer, prover, or admin compromise required.

### Likelihood Explanation
Likelihood is high for any deployment that accepts arbitrary ERC-20 tokens as order inputs (the contract does not whitelist tokens) and where a fee-on-transfer/rebasing token is used on Tron — the attacker fully controls which token to submit and needs no cooperation from other parties, satisfying "unprivileged attacker" and "public entrypoint" criteria in the impact gate.

### Recommendation
Port the same balance-diffing fix used in `evm/src/apps/IntentGatewayV2.sol` (`placeOrder`) to the Tron contract: measure `IERC20(token).balanceOf(address(this))` before and after each `safeTransferFrom`/sweep, mutate `order.inputs[i].amount` to the actual amount received, and compute `reducedInputs`/escrow credit from that measured value rather than the caller-declared value. Apply the same fix to the predispatch sweep path (measure gateway balance before/after the dispatcher-to-gateway transfer, not just the dispatcher's balance).

### Proof of Concept
1. Deploy the Tron `IntentGatewayV2` with a fee-on-transfer token registered as a valid input token (no token whitelist restricts this).
2. Attacker calls `placeOrder` declaring `order.inputs[0].amount = 1000e18` of the fee-on-transfer token (e.g., 5% transfer fee). The contract's actual received balance is `950e18`, but `_orders[commitment][token]` is credited with `reducedInputs[0].amount ≈ 1000e18` (minus protocol fee), i.e., ~50e18 more than what the contract actually holds for that order.
3. A second, unrelated legitimate user places a normal order for the same token, depositing e.g. `500e18` — the contract's total token balance is now `950e18 + 500e18 = 1450e18`, while combined escrow ledger entries sum to `~1450e18 + 50e18` (inflated).
4. Attacker cancels their own order (same-chain `cancelOrder` → `withdraw(body, true)`), which transfers the full inflated `_orders[commitment][token]` amount back to the attacker, pulling `50e18` of principal that physically came from the second user's deposit.
5. When the legitimate second user later attempts to withdraw/fill their order, the contract's token balance is short, causing their `withdraw()` call to revert (fund lock) or, depending on ordering, a subsequent attacker-controlled order to drain further legitimate balances — demonstrating the insolvency/theft path driven purely by inaccurate escrow accounting for fee-on-transfer tokens.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L353-379)
```text
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L410-440)
```text
            // Transfer tokens from call dispatcher back to IntentGateway
            Call[] memory transferCalls = new Call[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 requiredAmount = order.inputs[i].amount;
                uint256 balance;

                if (token == address(0)) {
                    balance = address(dispatcher).balance;
                    if (balance < requiredAmount) revert InsufficientNativeToken();
                    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
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

**File:** evm/src/apps/IntentGatewayV2.sol (L198-298)
```text
        // Phase 1: Transfer tokens and record actual received amounts.
        // For fee-on-transfer tokens, the gateway receives less than the requested amount.
        // We mutate order.inputs to reflect actual received so the commitment and escrow
        // are consistent with what the gateway holds.
        uint256 msgValue = msg.value;
        if (order.predispatch.call.length > 0 && order.predispatch.assets.length > 0) {
            address dispatcher = _params.dispatcher;

            uint256 assetsLen = order.predispatch.assets.length;
            for (uint256 i; i < assetsLen;) {
                address token = address(uint160(uint256(order.predispatch.assets[i].token)));
                uint256 amount = order.predispatch.assets[i].amount;
                if (amount == 0) revert InvalidInput();

                if (token == address(0)) {
                    if (amount > msgValue) revert InsufficientNativeToken();
                    msgValue -= amount;

                    (bool sent,) = dispatcher.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, dispatcher, amount);
                }

                unchecked {
                    ++i;
                }
            }

            ICallDispatcher(dispatcher).dispatch(order.predispatch.call);

            // Build sweep calls and snapshot gateway balances before the sweep.
            Call[] memory transferCalls = new Call[](inputsLen);
            uint256[] memory balancesBefore = new uint256[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 requiredAmount = order.inputs[i].amount;

                if (token == address(0)) {
                    uint256 balance = address(dispatcher).balance;
                    if (balance < requiredAmount) revert InsufficientNativeToken();
                    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
                    balancesBefore[i] = address(this).balance;
                } else {
                    uint256 balance = IERC20(token).balanceOf(dispatcher);
                    if (balance < requiredAmount) revert InvalidInput();
                    transferCalls[i] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                    balancesBefore[i] = IERC20(token).balanceOf(address(this));
                }

                unchecked {
                    ++i;
                }
            }

            ICallDispatcher(dispatcher).dispatch(abi.encode(transferCalls));

            // Measure actual received, emit dust for excess, update order.inputs.
            for (uint256 i; i < inputsLen;) {
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 received;
                if (token == address(0)) {
                    received = address(this).balance - balancesBefore[i];
                } else {
                    received = IERC20(token).balanceOf(address(this)) - balancesBefore[i];
                }

                if (received > order.inputs[i].amount) {
                    uint256 dust = received - order.inputs[i].amount;
                    emit DustCollected(token, dust);
                } else {
                    order.inputs[i].amount = received;
                }

                unchecked {
                    ++i;
                }
            }
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
