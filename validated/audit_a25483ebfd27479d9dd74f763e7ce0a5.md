### Title
`placeOrder()` escrows the nominal `order.fees` value instead of the fee token amount actually received - ([File: evm/src/apps/IntentGatewayV2.sol])

### Summary
`IntentGatewayV2.placeOrder()` already demonstrates that it knows tokens can deliver less than requested (it measures actual balance deltas for order inputs to guard against fee-on-transfer tokens), but it does not apply that same protection to the relayer-fee deposit. The `TRANSACTION_FEES` escrow ledger is credited with the caller-supplied `order.fees` value rather than the amount the contract actually received, exactly mirroring the `MarginalV1LBLiquidityReceiver` bug class: bookkeeping is updated from a "desired" number instead of the delta actually captured by the contract.

### Finding Description
In the non-swap branch of `placeOrder()`: [1](#0-0) 

```solidity
if (order.fees > 0) {
    address feeToken = IDispatcher(hostAddr).feeToken();
    if (msgValue > 0) {
        ...
        uint256[] memory amounts = IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msgValue}(
            order.fees, path, address(this), block.timestamp
        );
        msgValue -= amounts[0];
    } else {
        IERC20(feeToken).safeTransferFrom(msg.sender, address(this), order.fees);
    }

    _orders[commitment][TRANSACTION_FEES] = order.fees;
}
```

Regardless of which branch runs, `_orders[commitment][TRANSACTION_FEES]` is always set to the *requested* `order.fees`, never to a measured post-transfer delta. Contrast this with Phase 1 of the same function, just above, which explicitly snapshots balances before and after moving `order.inputs` tokens specifically because "For fee-on-transfer tokens, the gateway receives less than the requested amount": [2](#0-1) 

That actual-balance-delta discipline is conspicuously absent for the fee-token deposit path.

At settlement, `_withdraw()` reads this ledger entry and pays it out of the gateway's aggregate `feeToken` balance — a balance shared across all outstanding orders, not segregated per commitment: [3](#0-2) 

```solidity
if (finalize) {
    uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
    if (fees > 0) {
        delete _orders[body.commitment][TRANSACTION_FEES];
        IERC20(IDispatcher(host()).feeToken()).safeTransfer(beneficiary, fees);
    }
    ...
```

If the governance-configured `feeToken` (or, in the swap branch, the token returned by the pair) ever deducts anything on transfer/output (deflationary/tax token, upgradeable proxy token, rebasing token, or a router path where `IUniswapV2Router02.swapETHForExactTokens`'s guaranteed output can itself be undercut by a fee-on-transfer output token), the gateway's ledger entry (`order.fees`) will exceed what actually landed in the gateway's balance. Because the escrow is paid from a common pool at withdrawal time, this creates a first-withdrawn-first-served insolvency: orders whose `_withdraw()` runs earlier drain real balance that was actually deposited by other orders, and later orders' fee payouts fail or come up short — a direct loss/misappropriation of escrowed relayer fees between users, with no separate custody boundary protecting them.

### Impact Explanation
This is an accounting-invariant break in bridge custody: the ledger (`_orders[commitment][TRANSACTION_FEES]`) can diverge upward from the actual token balance the contract holds for that entry, while `_withdraw()` unconditionally trusts the ledger value and moves real funds. That is precisely the "bridged assets ... must move exactly once and only to the rightful beneficiary and amount" pivot — one order's finalize can pay out fees it never actually collected, at the expense of other orders' genuinely escrowed fees, producing fund loss for those other users.

### Likelihood Explanation
The path is reachable by any unprivileged user simply by calling `placeOrder()` with a non-zero `order.fees` — no relayer, prover, or admin collusion is required to *trigger* the mis-accounting. However, real-world exploitability is gated by whether the deployed `feeToken` (an operator/governance-chosen address, typically a standard stablecoin) has any transfer-fee/deflationary/rebasing behavior; with a plain ERC-20 fee token the delta is always zero and the bug is latent. This is a lower-likelihood, config-dependent variant of the exact same class of bug the external report flagged (trusting an intended amount instead of a measured one), and the codebase's own Phase-1 comment shows the authors are aware such tokens are a real threat model for this contract, yet did not extend the same guard to the fee-token deposit.

### Recommendation
Mirror the Phase-1 pattern for the fee deposit: snapshot the gateway's `feeToken` balance before the `transferFrom`/swap and after, and set `_orders[commitment][TRANSACTION_FEES]` to the measured delta rather than `order.fees`. In the swap branch, use the actual post-swap balance increase (or `amounts[amounts.length - 1]`, verified against a real balance check) instead of trusting `order.fees` as the assumed output.

### Proof of Concept
1. Governance configures a `feeToken` that charges a transfer fee (e.g., a token with a 1% fee-on-transfer hook), or a future upgrade to the fee token introduces such behavior.
2. Alice calls `placeOrder()` with `order.fees = 100`. The `safeTransferFrom` moves 100 nominal units, but the gateway's actual `feeToken` balance only increases by 99 (1% burned by the token). `_orders[commitmentA][TRANSACTION_FEES]` is nonetheless set to `100`.
3. Bob calls `placeOrder()` similarly with `order.fees = 100`, actually depositing 99.
4. Alice's order finalizes first: `_withdraw()` pays out `fees = 100` to Alice's beneficiary from the gateway's pooled `feeToken` balance (which only actually holds 99 + 99 = 198, not 200).
5. When Bob's order finalizes, the gateway's `feeToken` balance is short by the accumulated fee-on-transfer losses, and Bob's fee payout is either front-run into insolvency or the transfer reverts, leaving Bob's escrowed fee undeliverable — a direct fund-loss/misappropriation via mis-accounted ledger vs. actual custody, the same broken invariant as the Marginal report's ratio-based reserve tracking versus what Uniswap actually consumed.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L198-202)
```text
        // Phase 1: Transfer tokens and record actual received amounts.
        // For fee-on-transfer tokens, the gateway receives less than the requested amount.
        // We mutate order.inputs to reflect actual received so the commitment and escrow
        // are consistent with what the gateway holds.
        uint256 msgValue = msg.value;
```

**File:** evm/src/apps/IntentGatewayV2.sol (L345-362)
```text
        if (order.fees > 0) {
            address feeToken = IDispatcher(hostAddr).feeToken();
            if (msgValue > 0) {
                address uniswapV2 = IDispatcher(hostAddr).uniswapV2Router();
                address WETH = IUniswapV2Router02(uniswapV2).WETH();
                address[] memory path = new address[](2);
                path[0] = WETH;
                path[1] = IDispatcher(hostAddr).feeToken();
                uint256[] memory amounts = IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msgValue}(
                    order.fees, path, address(this), block.timestamp
                );
                msgValue -= amounts[0];
            } else {
                IERC20(feeToken).safeTransferFrom(msg.sender, address(this), order.fees);
            }

            _orders[commitment][TRANSACTION_FEES] = order.fees;
        }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L412-417)
```text
        if (finalize) {
            uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
            if (fees > 0) {
                delete _orders[body.commitment][TRANSACTION_FEES];
                IERC20(IDispatcher(host()).feeToken()).safeTransfer(beneficiary, fees);
            }
```
