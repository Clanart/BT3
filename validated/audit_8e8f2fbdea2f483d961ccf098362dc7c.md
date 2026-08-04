### Title
Pooled escrow accounting for rebasable/balance-mutable ERC-20 tokens allows insolvent withdrawals in IntentGatewayV2/IntentsBase - (File: `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
`IntentGatewayV2`'s escrow ledger, `_orders[commitment][token]`, records per-order escrowed amounts, but all orders that use the same ERC-20 `token` share a single pooled contract balance. Nothing ever reconciles the sum of outstanding `_orders[...][token]` entries against `IERC20(token).balanceOf(address(this))` at withdrawal time. If a token's `balanceOf` can decrease independently of an outbound transfer initiated by the gateway — the exact "rebasable token" class flagged in the external `LOB` report — the ledger becomes overstated relative to the real balance, and whichever order is settled first can drain funds that later orders' records still claim.

### Finding Description
Escrow is credited in `placeOrder` via `_orders[commitment][token] = reducedInputs[i].amount` [1](#0-0) , using the actual balance delta measured around the `safeTransferFrom` call — this correctly compensates for fee-on-transfer tokens at deposit time, as shown by the FOT tests [2](#0-1) .

However, on the withdrawal side, `_withdraw` in `IntentsBase.sol` treats the recorded `_orders[commitment][token]` value as ground truth and transfers that exact `amount` out, decrementing the ledger accordingly, with no comparison to the contract's live token balance: [3](#0-2) 

Because `_orders` is keyed by `(commitment, token)` and the underlying token balance for `token` is a single shared pool across every open order that used that token as input or output, the invariant `sum(_orders[*][token]) <= balanceOf(token)` is never enforced or re-derived. Rebasable tokens (Ampleforth-style), any token with a negative rebase, admin-triggered supply contraction, or other mechanisms that reduce `balanceOf(gateway)` without an outbound `transfer` from the gateway will silently break this invariant. There is no token allowlist in the contract restricting `order.inputs`/`order.output.assets` to well-behaved fixed-supply tokens — `placeOrder` accepts an arbitrary `address token` for any input/output [4](#0-3) .

Once the pool is short, the first solver/user to call `fillOrder`/`cancelOrder` for any order still recorded with escrow in that token will successfully drain the remaining real balance (their own escrow record is still "correct" relative to what they personally deposited, but the shared pool no longer has enough for everyone). Every subsequent order still holding a non-zero `_orders[commitment][token]` entry will have its `safeTransfer`/native `.call` in `_withdraw` fail against insufficient real balance, permanently locking that order's escrow — the user/solver can never redeem their recorded share, exactly mirroring the `LOB` finding: "the remaining users attempting to withdraw may not be able to receive their full share of assets because the contract's actual balance is less than the sum of the recorded balances."

### Impact Explanation
This causes loss/lock of user or solver funds that were legitimately escrowed in good faith, triggered purely by the token's own supply mechanics rather than by any malicious peer, relayer, or admin action — it is reachable by any ordinary user simply placing an order denominated in a rebasable or balance-mutable ERC-20, which the contract permits without restriction. Later orders sharing that token become unrecoverable once the pool is drawn down by an earlier, unrelated withdrawal.

### Likelihood Explanation
Likelihood depends on adoption of rebasable/elastic-supply tokens (e.g., Ampleforth-class assets, some yield-bearing/auto-compounding wrappers with negative-yield edge cases) as intent inputs/outputs. Since the contract does not restrict token types and multiple independent orders can concurrently escrow the same token, any negative-rebase event affecting the gateway's holdings is sufficient to trigger the shortfall for whichever order settles last.

### Recommendation
Do not treat `_orders[commitment][token]` as an absolute claim on a shared pool for tokens that can change balance outside of transfers. Options: (1) maintain a per-token "total escrowed" counter and reconcile against `balanceOf` before crediting new escrow or during withdrawal, socializing any shortfall proportionally rather than paying earlier claimants in full; (2) disallow/allowlist only tokens verified to have a static 1:1 balance-to-transfer relationship (reject rebasable/elastic-supply tokens); (3) at minimum, re-measure the actual transferable amount at withdrawal time (mirroring the deposit-side balance-delta pattern already used in `placeOrder`) and cap payouts to what is actually available, emitting a clear insolvency event instead of leaving later orders permanently stuck.

### Proof of Concept
1. Deploy a rebasable ERC-20 `RBT` where a negative rebase event scales down every holder's `balanceOf` including the gateway's.
2. User A calls `placeOrder` with `RBT` as input, amount 1000 → `_orders[commitmentA][RBT] = 1000`; gateway `balanceOf(RBT) = 1000`.
3. User B calls `placeOrder` with `RBT` as input, amount 1000 → `_orders[commitmentB][RBT] = 1000`; gateway `balanceOf(RBT) = 2000`.
4. A negative rebase occurs, cutting the gateway's `RBT` balance to 1000 (both `_orders` entries are untouched at 1000 each; total recorded = 2000 > actual 1000).
5. Order A is filled/refunded first: `_withdraw` succeeds, transferring the full 1000 recorded amount, leaving gateway `balanceOf(RBT) = 0`.
6. Order B's fill/refund now calls `_withdraw`, which attempts `IERC20(RBT).safeTransfer(beneficiary, 1000)` per the untouched `_orders[commitmentB][RBT] = 1000` record — this reverts (or, for tokens with silent failure, transfers 0) because the gateway has zero actual `RBT` balance, permanently locking User B's escrowed order.

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

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L2287-2307)
```text
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
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L394-409)
```text
        uint256 len = body.tokens.length;
        for (uint256 i; i < len; i++) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (amount == 0) continue;

            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
```
