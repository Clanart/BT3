### Title
Shared per-token escrow pool has no reserve accounting against live balance, so a rebasing/deflationary input token can let earlier withdrawers drain the pool and lock/lose funds for later solvers or refund beneficiaries - (File: `evm/src/apps/IntentGatewayV2.sol`, `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
This is the same broken invariant as the VTVL `M-06` report: an escrow ledger records a *fixed* per-order amount, but the actual token balance backing that ledger can drift away from the sum of ledger entries because it is a *variable-balance* ERC20 (rebasing, or one that can be reduced by mechanisms outside a `transfer` call). `IntentGatewayV2`/`IntentsBase` already defends against fee-on-transfer tokens *at deposit time* (it measures `balanceOf` deltas in `placeOrder`), but it never re-checks or reserves against the *pooled* balance at withdrawal time, and it lets multiple unrelated orders share one physical token balance per contract.

### Finding Description
`placeOrder` in `evm/src/apps/IntentGatewayV2.sol:281-298` correctly measures actual-received amounts for fee-on-transfer tokens: [1](#0-0) 

and stores that measured amount as a per-commitment ledger entry: [2](#0-1) 

Every order's input tokens land in the *same* contract's *shared* `IERC20(token)` balance, but escrow accounting is tracked independently per commitment (`_orders[commitment][token]`) with no global invariant such as "sum of all `_orders[*][token]` <= `IERC20(token).balanceOf(address(this))`" enforced at write time, and no re-snapshot at withdrawal time.

`_withdraw` in `IntentsBase.sol` releases tokens purely based on the stored ledger value, decrementing it and calling `safeTransfer`: [3](#0-2) 

There is no check here that `IERC20(token).balanceOf(address(this)) >= amount` before transfer (this is exactly the same shape of gap flagged in the original VTVL report — the `require` at deposit time can pass, but nothing guarantees the balance stays sufficient by withdrawal time). Because `placeOrder` only takes a per-deposit balance snapshot, it cannot detect or protect against balance shrinkage that happens *after* escrow (a downward rebase, an admin-controlled supply mechanism on the token, or a token that silently burns/mutates non-transfer balances — the same class of token the VTVL report calls out with the stETH `balanceOf` example).

Since the Intent Gateway protocol documentation states any ERC20 can be used as an input/output token (`docs/content/developers/evm/intent-gateway/overview.mdx`), and multiple concurrent orders for the same variable-balance token share one physical balance:
- If the token balance for the contract decreases without a corresponding `transfer` out (e.g. rebase-down), the sum of all live `_orders[commitment][token]` entries can exceed the real `balanceOf(address(this))`.
- The first solver/beneficiary to call `withdraw`/`_withdraw` (via `onAccept`/`onGetResponse` settlement, cancellation refund, etc.) will successfully pull their full escrowed amount out of the shared pool, effectively taking more than their pro-rata share of the shrunken pool.
- Any later withdrawer for a different order on the same token will have their `safeTransfer` revert (`ERC20InsufficientBalance`), since the ledger says they're owed `amount` but the physical balance is now short — their funds are locked with no built-in recovery path other than someone topping up the contract, exactly the "loss/lock" outcome M-06 describes.

The same structural gap exists in the identical `escrow`/`withdraw` logic in `evm/tron/contracts/apps/IntentGatewayV2.sol` (`_orders[commitment][token] -= amount` then `token.call(transfer(...))`, lines 682-721), which shares no cross-order balance reservation either.

### Impact Explanation
This falls squarely under "stealing or loss of funds" per the bounty gate: a legitimate beneficiary (solver claiming a redeemed escrow, or a user recovering a refund on cancellation) can have their withdrawal permanently revert due to a balance shortfall they had no way to cause or prevent, while an earlier withdrawer on the same shared-balance token effectively receives an outsized share of the remaining pool. No malicious peer, relayer, prover, or admin action is required — this is triggered purely by the intrinsic behavior of the chosen ERC20 token combined with the unprivileged `placeOrder`/`withdraw`/`onAccept`/`onGetResponse` flow that any user or solver can invoke.

### Likelihood Explanation
The protocol's own documentation and test suite (`evm/tests/foundry/IntentGatewayV2SameChainTest.sol` `FeeOnTransferToken` tests) show the team explicitly intends to support atypical ERC20s and has already built partial mitigation for one variant of variable-balance token (fee-on-transfer at deposit). This demonstrates awareness of the general bug class but the fix only covers the deposit-time discrepancy, not the withdrawal-time/shared-pool discrepancy — meaning any deflationary/rebasing token integrated as an order input, combined with ordinary concurrent order flow, will trigger this without any adversarial relayer/prover assumption.

### Recommendation
- Enforce a running per-token reserved-balance invariant (analogous to VTVL's suggested fix): before crediting `_orders[commitment][token]` in `placeOrder`, check `IERC20(token).balanceOf(address(this)) >= totalReservedForToken(token) + creditedAmount`, and maintain a `totalReservedForToken` accumulator per token across all open orders.
- At withdrawal (`_withdraw` / `withdraw`), verify `IERC20(token).balanceOf(address(this)) >= amount` immediately before `safeTransfer`, and fail predictably (with a clear revert reason) rather than allowing an unrelated victim order to silently starve.
- Consider disallowing or explicitly flagging rebasing/deflationary tokens as unsupported inputs, since per-order share-based accounting (like a vault's shares-to-assets model) would be required to safely support them, as recommended by the original C4 judge for VTVL.

### Proof of Concept
1. Deploy `IntentGatewayV2` with a token `T` that can arbitrarily reduce any holder's `balanceOf` without a `transfer` call (e.g., a rebase-down function callable by its own admin/mechanism, analogous to stETH's share-based `balanceOf`).
2. User A places `orderA` with 1000 `T` as input; `placeOrder` measures actual received (1000) and sets `_orders[commitmentA][T] = 1000` (`evm/src/apps/IntentGatewayV2.sol:333-343`). Contract's `T.balanceOf(address(this))` is now 1000.
3. User B places `orderB` with 1000 `T` as input the same way; `_orders[commitmentB][T] = 1000`. Contract's `T.balanceOf(address(this))` is now 2000.
4. Token `T` rebases down by 25% for all holders including the gateway contract; `T.balanceOf(address(this))` becomes 1500, while `_orders[commitmentA][T] + _orders[commitmentB][T]` still sums to 2000.
5. Solver fills `orderA` cross-chain; settlement triggers `onAccept` → `_withdraw` (`evm/src/apps/intentsv2/IntentsBase.sol:390-410`), which calls `safeTransfer(solverA, 1000)`. This succeeds because 1500 ≥ 1000, draining most of the remaining pool.
6. Solver fills `orderB`; settlement triggers `_withdraw` with `amount = 1000`, but the contract now holds only 500 `T`. `safeTransfer` reverts, permanently locking solver B's/user B's owed 1000 `T` (or, on cancellation, the refund to user B reverts the same way) — funds are lost/locked with no on-chain recovery, matching the exact failure mode described in the referenced VTVL `M-06` report.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L288-292)
```text
                } else {
                    uint256 balBefore = IERC20(token).balanceOf(address(this));
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                    order.inputs[i].amount = IERC20(token).balanceOf(address(this)) - balBefore;
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L394-410)
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
        }
```
