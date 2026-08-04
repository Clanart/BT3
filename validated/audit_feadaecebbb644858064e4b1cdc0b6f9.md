## Analysis

The seed report's core broken invariant: *code assumes `amount` requested == `amount` actually received/held*, and that assumption is falsified by fee‑on‑transfer/rebalancing ERC‑20 tokens, corrupting escrow accounting and causing fund loss/lock.

Hyperbridge's main EVM `IntentGatewayV2` (`evm/src/apps/IntentGatewayV2.sol`) was hardened against exactly this class of bug: it measures `balanceOf` before/after every `safeTransferFrom` and mutates `order.inputs[i].amount` to the actually-received amount before crediting escrow [1](#0-0) , and this is explicitly covered by fee-on-transfer regression tests [2](#0-1) .

However, the parallel Tron deployment of the same contract, `evm/tron/contracts/apps/IntentGatewayV2.sol`, was **not** updated with this fix and still contains the original vulnerable pattern.

### Title
Fee-on-transfer/rebalancing ERC20 inputs corrupt escrow accounting in the Tron `IntentGatewayV2.placeOrder` — (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`placeOrder`'s non-predispatch token path credits escrow with the nominal (fee-reduced-by-protocol-fee-only) amount instead of the token amount actually received by the contract, exactly like the seeded `DepositVault.deposit()` bug. Because the ERC20 balance held by the gateway is a single shared pool across all commitments, this creates a persistent deficit between accounted escrow and real token balance that can be exploited to lock or drain other users' funds.

### Finding Description
In the token branch of `placeOrder`, the contract calls `safeTransferFrom` for the full nominal `order.inputs[i].amount`, but then credits escrow using `reducedInputs[i].amount`, which is derived purely from `order.inputs[i].amount` minus the protocol fee — never from the token's actual post-transfer balance: [3](#0-2) 

Contrast with the fixed main-line contract, which measures `balanceOf` deltas and mutates `order.inputs[i].amount` to the real received amount before it's used to compute `reducedInputs`/commitment/escrow: [4](#0-3) 

For a fee-on-transfer or rebalancing token, the Tron gateway's real `IERC20(token).balanceOf(address(this))` increases by less than `order.inputs[i].amount`, yet `_orders[commitment][token]` is incremented as if the full (fee-reduced-only) amount was received. On withdrawal (`withdraw()`), the contract transfers out `body.tokens[i].amount` using a raw low-level `.call` and only checks `_orders[body.commitment][token] == 0` as an existence guard — it never validates that the aggregate outstanding escrow across all commitments is covered by the actual token balance: [5](#0-4) 

Since the token balance is a single shared pool (not segregated per commitment), any deficit introduced by one order using a fee-on-transfer input token reduces the pool available to satisfy *other, legitimate* orders' escrow credits.

### Impact Explanation
This causes real loss/lock of user funds without requiring any malicious relayer, prover, or admin:
- An unprivileged user can `placeOrder` with a fee-on-transfer (or rebalancing) token as input, causing the contract to credit itself more escrow than it actually holds.
- Whoever redeems/cancels first (attacker's own order or a race) drains the shared token pool.
- Subsequent legitimate order holders' `cancelOrder`/`fillOrder`→`onAccept`→`withdraw()` calls for the same token then revert (`TransferFailed`) due to insufficient contract balance, permanently locking their escrowed tokens — or, if attacker structures timing, effectively lets one deficit-creating order siphon value contributed by other depositors of the same token.

This matches the bounty's accepted classes: loss/lock of funds and logic attacks on escrow settlement, reached purely through the public `placeOrder`/`cancelOrder`/`fillOrder` entry points.

### Likelihood Explanation
High for any deployment where the Tron gateway accepts arbitrary/permissionless ERC-20 tokens as order inputs (fee-on-transfer and rebalancing tokens are common on TRC-20/BEP-20-style ecosystems). No privileged role, relayer collusion, or governance action is needed — an ordinary user triggers it via `placeOrder`.

### Recommendation
Port the fix already applied to `evm/src/apps/IntentGatewayV2.sol` (balance-before/after measurement of actual received tokens, mutating `order.inputs[i].amount` prior to computing `reducedInputs`/commitment/escrow) into `evm/tron/contracts/apps/IntentGatewayV2.sol`, and add the same fee-on-transfer regression tests used in `evm/tests/foundry/IntentGatewayV2SameChainTest.sol` to the Tron test suite.

### Proof of Concept
1. Deploy a fee-on-transfer ERC20 (e.g., the `FeeOnTransferToken` test helper) and mint balance to `attacker`.
2. `attacker` calls `placeOrder` on the Tron `IntentGatewayV2` with `order.inputs[0]` set to this token and `amount = 1000e18`; the gateway's real balance only increases by ~990e18 (1% fee), but `_orders[commitment][token]` is credited based on `1000e18` minus only the protocol fee (line 435/457) — over-crediting escrow relative to real balance held.
3. Repeat with a second legitimate user placing an order with the *same* token, fully funding their true escrow.
4. Attacker cancels their order first; `withdraw()` (line 682-700) pays out the over-credited nominal amount, pulling extra tokens from the shared pool that were actually contributed by the second, legitimate user.
5. The second user's subsequent `cancelOrder`/`fillOrder` reverts with `TransferFailed` because the pool no longer holds enough of the token — their funds are locked. [3](#0-2) [5](#0-4)

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

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L2257-2293)
```text
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-700)
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

```
