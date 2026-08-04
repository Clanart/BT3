### Title
Tron `IntentGatewayV2.placeOrder()` Credits Nominal (Not Actual-Received) Token Amounts to Escrow — Fee-on-Transfer Token Under-Collateralization — (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron deployment of `IntentGatewayV2` escrows the *requested* input amount instead of the *actually received* amount when pulling ERC20/TRC20 tokens from the user, unlike the EVM mainline version of the same contract which was hardened against exactly this class of bug. If a fee-on-transfer token is used as an order input, the escrow ledger (`_orders[commitment][token]`) records more value than the contract actually holds, letting an attacker drain the token pool at the expense of other legitimate order holders — the same "Cally" fee-token conduit bug from the external report.

### Finding Description
In the mainline `IntentGatewayV2.placeOrder()` (`evm/src/apps/IntentGatewayV2.sol:288-291`), the non-predispatch path snapshots the balance before and after `safeTransferFrom`, then overwrites `order.inputs[i].amount` with the delta so escrow and commitment reflect what the contract actually received: [1](#0-0) 

This fix is validated by dedicated tests in `IntentGatewayV2SameChainTest.sol` confirming escrow "matches received" for fee-on-transfer tokens: [2](#0-1) 

However, the Tron variant of the same contract (`evm/tron/contracts/apps/IntentGatewayV2.sol`) does **not** apply this fix. In its non-predispatch branch, tokens are pulled with a plain `safeTransferFrom` using the caller-specified `order.inputs[i].amount`, and the escrow ledger is credited with `reducedInputs[i].amount` — a value derived only by subtracting the protocol fee from the *nominal requested* amount, never checked against actual balance received: [3](#0-2) 

The predispatch branch of the same Tron file has the identical flaw — it checks `balance < requiredAmount` (a lower-bound check) but still credits `_orders[commitment][token] += reducedInputs[i].amount`, the nominal value, not the measured swept balance delta: [4](#0-3) 

At payout time, `withdraw()` transfers the escrow-ledger `amount` (the nominal, fee-inclusive figure) directly to the beneficiary via a raw `token.call` to `transfer`, without any check that the contract's real balance can cover it: [5](#0-4) 

**Corrupted value:** `_orders[commitment][token]`, which is set to the user-requested amount (minus protocol fee) rather than the amount the contract actually custodies after any token-level transfer fee.

**Why existing guards don't stop it:** The only guard present is `if (_orders[commitment][token] == 0) revert UnknownOrder();` — a zero-check, not a solvency check. There is no assertion anywhere in the Tron contract that `sum(_orders[*][token])` for a given token is ≤ `IERC20(token).balanceOf(address(this))`. Unlike the mainline EVM contract, no `balanceOf` pre/post delta is ever computed to correct the ledger.

### Impact Explanation
This falls squarely within the "stealing or loss of funds" and "logic attacks / duplicate settlement" impact categories: the escrow accounting becomes globally shared and over-counted per fee-charging token. An attacker who places a second order (or fills their own order first) using the same fee-on-transfer token can withdraw the *full nominal* escrow value even though the contract's actual balance for that token is short by the cumulative transfer fees from all deposits. Whichever withdrawal is processed first succeeds and effectively siphons value contributed by other users' deposits; a later legitimate order-holder's withdrawal then reverts (`TransferFailed()`) or drains the remaining balance to zero, causing fund loss/lock for that user. This directly mirrors the Cally exploit primitive: an unprivileged actor observes/creates deposits of the same fee token and extracts more than they contributed, at the expense of the pooled contract balance.

### Likelihood Explanation
This is a real, unprivileged, publicly reachable code path (`placeOrder` → `withdraw`/`onAccept` RedeemEscrow/RefundEscrow), requiring only that (a) the Tron IntentGateway supports a TRC20/ERC20-style token with a transfer fee/tax and (b) more than one order uses that token. No relayer, prover, or admin collusion is needed — the attacker is simply another order-placer/filler using the standard entrypoints. The risk is bounded by how many fee-on-transfer tokens are actually onboarded to the Tron gateway, but the code contains no on-chain restriction preventing such tokens from being used, and the identical bug was explicitly fixed in the sibling EVM contract, confirming the team is aware of and treats this bug class as relevant/exploitable.

### Recommendation
Apply the same balance-delta pattern already used in `evm/src/apps/IntentGatewayV2.sol` to the Tron contract: snapshot `IERC20(token).balanceOf(address(this))` (or `dispatcher` in the predispatch case) immediately before and after each transfer, and use that delta — not the caller-specified amount — as the basis for `reducedInputs[i].amount` and the value credited into `_orders[commitment][token]`. Reject or explicitly disallow tokens whose received amount doesn't match the requested amount if fee-on-transfer support is not desired.

### Proof of Concept
1. Governance/permissionless token onboarding allows a fee-on-transfer TRC20 token `FOT` (1% fee) as an order input on the Tron `IntentGatewayV2`.
2. User A calls `placeOrder` with `inputs = [{token: FOT, amount: 1000}]`. The gateway calls `safeTransferFrom(A, this, 1000)` but due to the 1% fee only actually receives 990 `FOT`. Per `evm/tron/contracts/apps/IntentGatewayV2.sol:453-457`, the escrow ledger nonetheless records `_orders[commitmentA][FOT] = 1000` (minus protocol fee, if any) instead of `990`.
3. Attacker B repeats the same call with `inputs = [{token: FOT, amount: 1000}]`, contract now holds `990 + 990 = 1980` real `FOT`, but ledger totals `2000` (minus fees) in escrow obligations across both commitments.
4. Attacker B's order settles/fills first (or B self-cancels/refunds immediately since same-chain cancel path calls `withdraw()` directly per `cancelOrder`/`onAccept`), and `withdraw()` transfers the full nominal `1000` (`evm/tron/contracts/apps/IntentGatewayV2.sol:693-699`) to B, leaving the contract with `980` `FOT`.
5. When User A's legitimate order is later withdrawn/filled for the nominal `1000`, the transfer either reverts (insufficient balance) — locking A's funds — or, if further deposits from other users have topped up the pool, silently pays A using value that rightfully belonged to other depositors, propagating the shortfall to whoever withdraws last.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L288-292)
```text
                } else {
                    uint256 balBefore = IERC20(token).balanceOf(address(this));
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                    order.inputs[i].amount = IERC20(token).balanceOf(address(this)) - balBefore;
                }
```

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L2292-2307)
```text
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
