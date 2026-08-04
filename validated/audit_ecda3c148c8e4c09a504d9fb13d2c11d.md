### Title
Escrow accounting trusts nominal input amount instead of actual received balance in Tron IntentGatewayV2, breaking custody invariant for fee-on-transfer/deflationary tokens - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The external report's core broken invariant is: a value (the IV) that must be identical across two dependent code paths is instead independently (re)computed in each path, and nothing forces the two computed values to be equal to what was actually used/produced. The local Hyperbridge analog is in `IntentGatewayV2.placeOrder` on the Tron contract, where the escrow amount credited to `_orders[commitment][token]` is derived purely from the user-declared `order.inputs[i].amount` (reduced only by the protocol fee), instead of from the token balance the gateway actually received via `safeTransferFrom`. The canonical EVM contract already fixed this exact class of bug by measuring pre/post balances and mutating `order.inputs` to the real received amount before computing the commitment and escrow credit; the Tron contract does not carry that fix.

### Finding Description
In `evm/src/apps/IntentGatewayV2.sol` (main EVM contract), `placeOrder` explicitly guards against a mismatch between declared and actual custody: [1](#0-0) 
It snapshots balances before/after `safeTransferFrom` (or the predispatch sweep) and overwrites `order.inputs[i].amount` with the **actually received** amount before the commitment hash and the fee-reduction math are computed: [2](#0-1) 

The Tron contract (`evm/tron/contracts/apps/IntentGatewayV2.sol`) performs the transfer and the fee-reduction/commitment computation entirely from the caller-declared `order.inputs[i].amount`, never checking or reconciling against the gateway's real balance change: [3](#0-2) [4](#0-3) 

`_orders[commitment][token]` is incremented by `reducedInputs[i].amount`, which is computed as `order.inputs[i].amount - protocolFee` — i.e., derived from the nominal amount, not from `IERC20(token).balanceOf(address(this))` deltas. If `token` charges a transfer fee, rebases, or otherwise delivers less than `amount` to the gateway (as demonstrated by the `FeeOnTransferToken` test harness already present for the main EVM contract), the escrow ledger records more tokens than the contract actually custodies for that order.

### Impact Explanation
This breaks the "bridged assets... must move exactly once and only to the rightful beneficiary and amount" invariant. Because `_orders[commitment][token]` is a per-commitment, per-token accounting entry shared against a single pooled token balance across all outstanding orders, over-crediting one order's escrow means that when that order is later filled (`withdraw()`/`RedeemEscrow`) or refunded/cancelled, the contract will attempt to pay out the inflated `_orders[...]` amount. Since the gateway never actually held that amount for this token, satisfying the payout can only be done by consuming balance that belongs to other users' still-escrowed orders in the same token — i.e., loss of funds / unauthorized draining of unrelated users' escrow, not merely a self-inflicted shortfall for the ordering user. This matches the bounty's "stealing or loss of funds" and "logic attacks" categories.

### Likelihood Explanation
Exploitation requires only a normal, unprivileged user to place an order using any ERC-20 (or Tron TRC-20) token that takes a transfer fee, has a deflationary/rebasing mechanic, or otherwise delivers less than the nominal `amount` on `transferFrom` — no malicious relayer, prover, or governance actor is needed, and the flow uses the public `placeOrder` entrypoint. The existence of a dedicated `FeeOnTransferToken` test contract and multiple regression tests (`testPlaceOrder_FeeOnTransferToken_EscrowMatchesReceived`, `testPlaceOrder_FeeOnTransferToken_WithProtocolFee`, `testPlaceAndFill_FeeOnTransferToken_RoundTrip`) on the main EVM contract confirms this is a recognized, previously-fixed bug class in this exact contract family — the Tron variant simply lacks the corresponding fix. [5](#0-4) 

I was not able to fully trace every downstream consumer of `_orders[commitment][token]` (e.g., `withdraw()`/`RedeemEscrow`, `_cancelSameChain`) inside the Tron file within the available context to confirm there is no independent balance check at payout time; that should be verified directly in the file before treating this as fully proven.

### Recommendation
Port the same fix used in `evm/src/apps/IntentGatewayV2.sol`: measure `balanceOf(address(this))` before and after each `safeTransferFrom` (and after the predispatch sweep), overwrite `order.inputs[i].amount` with the actual delta, and compute both the protocol-fee reduction and the order commitment from that reconciled amount before crediting `_orders[commitment][token]`.

### Proof of Concept
1. Deploy a TRC-20 token with a 1% transfer fee (mirroring `FeeOnTransferToken` in the EVM test suite) on the Tron deployment.
2. User calls `placeOrder` with `inputs[0] = {token: feeToken, amount: 1000}`. `safeTransferFrom(user, gateway, 1000)` executes but the gateway's balance only increases by 990 due to the token's fee.
3. `placeOrder` computes `reducedInputs[0].amount = 1000 - protocolFee` (based on the nominal 1000, not the 990 actually received) and sets `_orders[commitment][feeToken] = reducedInputs[0].amount`.
4. The gateway now records escrow greater than its real token balance for this order. When this order is filled/withdrawn (transferring `_orders[commitment][feeToken]` to the solver) alongside other orders sharing the same token, the aggregate payouts can exceed the gateway's actual `feeToken` balance, forcing later legitimate withdrawals/cancellations for unrelated orders to fail or be under-paid — the shortfall is effectively stolen from other users' escrow. [4](#0-3)

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L196-331)
```text
        uint256 inputsLen = order.inputs.length;

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

        // Phase 2: Compute protocol fees and commitment from actual received amounts.
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
                if (originalAmount == 0) revert InvalidInput();
                uint256 protocolFee = (originalAmount * protocolFeeBps) / 10_000;
                uint256 reducedAmount = originalAmount - protocolFee;
                address token = address(uint160(uint256(order.inputs[i].token)));

                if (protocolFee > 0) emit DustCollected(token, protocolFee);

                reducedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: reducedAmount});
                unchecked {
                    ++i;
                }
            }

            order.inputs = reducedInputs;
            commitment = keccak256(abi.encode(order));
        } else {
            reducedInputs = order.inputs;
            commitment = keccak256(abi.encode(order));
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L332-379)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable {
        // Validate that order has inputs
        if (order.inputs.length == 0) revert InvalidInput();

        address hostAddr = host();
        // fill out the order preludes
        order.user = bytes32(uint256(uint160(msg.sender)));
        order.source = IDispatcher(hostAddr).host();
        order.nonce = _nonce++;

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L440-463)
```text
            }

            // Execute transfer calls from call dispatcher
            ICallDispatcher(dispatcher).dispatch(abi.encode(transferCalls));
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
