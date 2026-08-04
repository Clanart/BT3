I have enough evidence to write up the finding. The Tron variant of `IntentGatewayV2.sol` lacks the fee-on-transfer accounting fix that exists in the main EVM contract, creating a pool-insolvency vulnerability where escrow bookkeeping overstates actual token custody.

### Title
Escrow ledger overstates actual token custody for fee-on-transfer tokens in Tron `IntentGatewayV2.placeOrder`, enabling insolvency and fund loss for later depositors - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron deployment of `IntentGatewayV2.placeOrder` computes the escrowed amount (`reducedInputs[i].amount`, i.e., `order.inputs[i].amount` minus the protocol fee) *before* transferring tokens, and credits `_orders[commitment][token]` with that value unconditionally, regardless of how many tokens the gateway actually received via `safeTransferFrom`. The mainline EVM contract (`evm/src/apps/IntentGatewayV2.sol`) was hardened against this exact class of bug — it measures `balanceOf` before and after the transfer and mutates `order.inputs[i].amount` to the actually-received value before computing the commitment/escrow — but this fix was never ported to the Tron contract.

### Finding Description
In `evm/tron/contracts/apps/IntentGatewayV2.sol`, `placeOrder` computes `reducedInputs`/`commitment` from the caller-supplied `order.inputs[i].amount` at [1](#0-0) , then transfers tokens and credits escrow independent of the real transfer outcome:

```solidity
} else {
    for (uint256 i; i < inputsLen;) {
        if (order.inputs[i].amount == 0) revert InvalidInput();
        address token = address(uint160(uint256(order.inputs[i].token)));
        if (token == address(0)) {
            if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
            msgValue -= order.inputs[i].amount;
        } else {
            IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
        }
        // Store reduced amount (after protocol fees) in escrow
        _orders[commitment][token] += reducedInputs[i].amount;
        ...
``` [2](#0-1) 

For any ERC-20 with a transfer fee, `safeTransferFrom(msg.sender, address(this), amount)` moves `amount` from the user's balance but the gateway's balance increases by `amount - fee`. The code never checks `IERC20(token).balanceOf(address(this))` before/after, so `_orders[commitment][token]` is credited with `reducedInputs[i].amount` (only reduced by the protocol fee), which is strictly larger than what the contract actually holds. The predispatch branch has the identical defect at [3](#0-2) , where `balance - requiredAmount` "dust" accounting assumes `balance >= requiredAmount` even though a fee-on-transfer token guarantees `balance < requiredAmount`, meaning `dust` underflows/or the branch simply understates the deficit while crediting the full `reducedInputs[i].amount`.

Contrast with the fixed mainline EVM contract, which snapshots balances and mutates `order.inputs[i].amount` to the real received amount *before* computing `reducedInputs`/`commitment`/escrow: [4](#0-3)  and [5](#0-4) . This exact fix — and dedicated fee-on-transfer regression tests — exists in the repo's Foundry test suite: [6](#0-5) , confirming the team is aware of the bug class and patched it for the primary EVM contract but not for Tron.

The `_orders` mapping is a shared per-token virtual ledger; the actual token balance held by `address(this)` is the single pooled resource backing every open commitment for that token. Once the ledger total exceeds the real balance, `withdraw()` (`_orders[body.commitment][token] -= amount` then `token.call(transfer, beneficiary, amount)` at [7](#0-6) ) will keep succeeding for early claimants by consuming principal that nominally belongs to other still-open orders, until the contract's real balance is exhausted and a later, legitimate withdrawal reverts or receives less than its escrow entry states.

### Impact Explanation
This is a fund-loss / pool-insolvency bug reachable by any unprivileged user who places an order with a fee-on-transfer token as input — no malicious relayer, prover, or admin is required. Every `placeOrder` call using such a token silently creates a shortfall between the ledger and actual custody. As shortfalls accumulate, some legitimate order beneficiaries (`RedeemEscrow`/`RefundEscrow`/cancellation withdrawals) are unable to redeem their full escrowed amount even though the contract's internal accounting says they are entitled to it — effectively funds are diverted from later depositors to earlier ones, and ultimately some user's principal is permanently lost/locked. This matches the bounty's "stealing or loss of funds" and "false state acceptance" categories: the escrow map is accepted as true state despite not matching actual custody.

### Likelihood Explanation
Likelihood is driven purely by whether a fee-on-transfer ERC-20 is used as an order input on the Tron deployment — no privileged actor, race condition, or complex setup is needed. Given USDT on some networks and various other deflationary/tax tokens implement transfer fees, and the IntentGateway is a generic multi-token router (not restricted to a token allowlist by this code), this is a straightforward, deterministic trigger for any attacker who deposits such a token.

### Recommendation
Port the same-chain balance-snapshot fix already present in `evm/src/apps/IntentGatewayV2.sol` to the Tron contract: measure `IERC20(token).balanceOf(address(this))` immediately before and after each `safeTransferFrom`/predispatch sweep, use the delta (not the requested `amount`) as the basis for `reducedInputs`/commitment/escrow crediting, and emit `DustCollected` for any excess exactly as the mainline contract does. Add the equivalent fee-on-transfer regression tests (mirroring `IntentGatewayV2SameChainTest.sol`) for the Tron contract to prevent regression.

### Proof of Concept
1. Deploy the Tron `IntentGatewayV2` and a mock ERC-20 with a non-zero transfer fee (e.g., 1%, as in `FeeOnTransferToken` from `evm/tests/foundry/IntentGatewayV2SameChainTest.sol:2502-2547`).
2. User A calls `placeOrder` with `inputs[0] = {token: FOT, amount: 1000e18}` and `protocolFeeBps = 0`. `reducedInputs[0].amount = 1000e18` is computed before transfer; `safeTransferFrom` moves `1000e18` from A but the gateway only receives `990e18`. `_orders[commitmentA][FOT] += 1000e18` — a 10e18 shortfall is created (gateway holds 990e18 total but owes 1000e18 to commitment A alone).
3. User B repeats step 2 with a fresh order, doubling the deficit; the gateway now holds `1980e18` but the ledger claims `2000e18` is owed across the two commitments.
4. Solver fills order A first: `withdraw()` decrements `_orders[commitmentA][FOT]` by `1000e18` and transfers `1000e18` out of the gateway's real balance — this succeeds by consuming part of the balance that was actually backing order B.
5. When order B is later filled/cancelled, the gateway no longer holds enough FOT to cover the `1000e18` still recorded in `_orders[commitmentB][FOT]`; the `transfer` call fails or, if a partial-balance token allows it, B receives less than the escrow record promised — demonstrating fund loss/lock caused purely by the un-patched fee-on-transfer accounting gap.

### Citations

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L407-440)
```text
            // Execute the call dispatcher with predispatch call
            ICallDispatcher(dispatcher).dispatch(order.predispatch.call);

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

**File:** evm/src/apps/IntentGatewayV2.sol (L333-343)
```text
        // Phase 3: Credit escrow.
        for (uint256 i; i < inputsLen;) {
            address token = address(uint160(uint256(order.inputs[i].token)));
            // Reject duplicate input tokens
            if (_orders[commitment][token] != 0) revert InvalidInput();
            _orders[commitment][token] = reducedInputs[i].amount;

            unchecked {
                ++i;
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
