## Confirmed: Tron `IntentGatewayV2.sol` lacks the fee-on-transfer accounting fix present in the main EVM contract

The main EVM `IntentGatewayV2.sol` explicitly hardens `placeOrder` against deflationary/fee-on-transfer ERC-20 inputs by measuring the actual balance delta after each transfer and mutating `order.inputs[i].amount` to the *received* amount before computing protocol fees and crediting escrow: [1](#0-0) 

This is directly validated by dedicated tests (`testPlaceOrder_FeeOnTransferToken_EscrowMatchesReceived`, `..._WithProtocolFee`, `..._Predispatch`) proving escrow is meant to equal actual received balance, not nominal input. [2](#0-1) 

The Tron variant of the same contract (`evm/tron/contracts/apps/IntentGatewayV2.sol`), however, skips this measurement entirely in its non-predispatch branch: it computes `reducedInputs` (protocol-fee-adjusted escrow amounts) from the **nominal** `order.inputs[i].amount` *before* any transfer occurs, then just calls `safeTransferFrom` and unconditionally credits the nominal-derived amount to escrow, with no balance-before/balance-after check: [3](#0-2) [4](#0-3) 

### Title
Tron `IntentGatewayV2.sol` credits escrow for the full nominal input amount of fee-on-transfer tokens instead of the amount actually received, allowing escrow-pool insolvency and fund theft from other users — (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
Unlike the audited/fixed main EVM `IntentGatewayV2.sol`, the Tron port's `placeOrder` never checks the gateway's actual token balance delta after `safeTransferFrom`. For any ERC-20 with a transfer fee/burn (deflationary token), `_orders[commitment][token]` is credited with the full nominal amount specified by the user (minus protocol fee, itself computed from the nominal amount), even though the contract's real token balance increased by less. Because `_orders` is a shared, per-token global ledger drawn against the gateway's pooled token balance, this creates a persistent gap between "tokens the ledger says are owed" and "tokens actually held." An unprivileged attacker can exploit this gap to withdraw more of a given token than they deposited, at the expense of other users' escrowed balances of that same token.

### Finding Description
`placeOrder` in the Tron contract (lines 332-497) computes `reducedInputs[i].amount` purely from `order.inputs[i].amount` (the user-declared amount) before any tokens move: [5](#0-4) 

Then, in the non-predispatch escrow branch, it transfers `order.inputs[i].amount` via `safeTransferFrom` and immediately adds `reducedInputs[i].amount` to the token's escrow ledger — without ever reading `IERC20(token).balanceOf(address(this))` before/after: [4](#0-3) 

Compare this to the main EVM contract, which computes the *received* delta and uses that for both the commitment hash and escrow, precisely to prevent this class of bug: [6](#0-5) 

Because `_orders[commitment][token]` participates in a shared contract-wide token balance (all orders using the same ERC-20 draw from one pooled `balanceOf(address(this))`), over-crediting one order's escrow directly consumes headroom that legitimately belongs to other users' orders in the same token. When `withdraw()` (called from `onAccept` for `RedeemEscrow`/`RefundEscrow`, or directly from `cancelOrder` for same-chain orders) pays out `_orders[body.commitment][token]` via a raw `token.call(transfer.selector, ...)`, it transfers the inflated nominal amount, which further drains real balance beyond what was actually deposited for that order: [7](#0-6) 

### Impact Explanation
This is a real, unprivileged fund-loss primitive: an attacker deposits `X` units of a fee-on-transfer token (paying a fee, so the gateway receives `X - fee`), but the ledger credits their order with the full `X` (minus protocol fee computed on `X`, not `X - fee`). The attacker then cancels/fills their own order (same-chain path requires no counterparty and no proof — just `cancelOrder` → `withdraw`), withdrawing more tokens of that ERC-20 than they actually contributed. The excess is siphoned from whatever real balance of that token other users have escrowed via legitimate orders, so a later legitimate order (fill or cancel) for the same token can fail to fully pay out or revert, or a later victim's `withdraw()` drains the last of the real balance, leaving other pending orders permanently under-collateralized/unpayable — a direct loss/lock of funds belonging to other unprivileged users.

### Likelihood Explanation
High for any deployment of this Tron contract that accepts a fee-on-transfer/deflationary ERC-20 as an order input token (TRC-20 equivalents of such tokens exist on Tron). No relayer, prover, or governance compromise is required — a single unprivileged actor can trigger it with `placeOrder` + `cancelOrder` (same-chain path) using only a standard deflationary token and no special permissions.

### Recommendation
Port the same fix that exists in `evm/src/apps/IntentGatewayV2.sol` into the Tron contract: measure `IERC20(token).balanceOf(address(this))` before and after each `safeTransferFrom` in `placeOrder` (both the predispatch and non-predispatch branches), mutate the escrowed/committed amount to the actually-received delta, and only then compute protocol fees and the commitment hash — consistent with the fee-on-transfer tests already present for the main EVM contract.

### Proof of Concept
1. Deploy the Tron `IntentGatewayV2.sol` with a TRC-20 `FeeOnTransferToken` (e.g. 5% transfer fee) registered as a valid input token, and fund the gateway pool with several legitimate users' orders in that token.
2. Attacker calls `placeOrder` with `order.inputs[0] = {token: FOT, amount: 1000e18}`. `safeTransferFrom` moves 1000e18 from attacker but the gateway actually receives 950e18 (5% burned). `_orders[commitment][FOT]` is nonetheless credited with `~1000e18` (minus protocol fee on 1000e18), not 950e18.
3. Attacker immediately calls `cancelOrder` (same-chain path, `orderSource == orderDest == currentChain`), which calls `withdraw(body, true)` and transfers the full credited ledger amount (~1000e18-fee) back to the attacker — 50e18 more than the attacker deposited.
4. Repeat/scale: the gateway's `FOT` balance is now short by the accumulated deflation delta relative to what other users' `_orders[...][FOT]` entries claim, so the next legitimate order's `fillOrder`/`withdraw`/`cancelOrder` for `FOT` either reverts (insufficient balance for `token.call(transfer...)`) or drains the pool early, leaving remaining orders unpayable — fund loss for those other users.

### Citations

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
