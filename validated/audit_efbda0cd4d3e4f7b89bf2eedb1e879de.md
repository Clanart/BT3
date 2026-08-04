## Analysis

The external report's core broken invariant: a token issuer retains a unilateral, protocol-external authority (freeze/blacklist) over specific accounts, and the protocol's custody/escrow logic assumes token transfers into or out of a PDA/vault always succeed. When that assumption breaks, funds become permanently locked and settlement (borrow/repay in the original, release/refund in Hyperbridge) is denial-of-serviced with no recovery path.

Candidate Hyperbridge analogs considered:
1. `pallet-hyper-fungible-token` escrow/mint on `on_accept`/`on_timeout` [1](#0-0)  — uses `pallet-assets`/`Currency` transfer, not an externally-blacklistable ERC20-style token; substrate assets don't have issuer-side freeze authority reachable by an unprivileged party. Weaker analog.
2. `SimplexPaymaster` treasury withdrawal — governance-gated, not reachable by an unprivileged attacker. Discarded (governance actor).
3. `IntentGatewayV2`/`IntentsBase` escrow release path (`_withdraw`) on EVM, which uses `IERC20(token).safeTransfer(beneficiary, amount)` to pay out escrowed order inputs and fees to the `beneficiary` address decoded directly from cross-chain message body [2](#0-1) . This is the strongest local analog: intent-gateway orders commonly escrow stablecoins (USDC/USDT-class tokens with issuer-controlled blacklist/freeze authority), and the beneficiary is an arbitrary, attacker-influenced address baked into the order at creation time.

## Title
Unrecoverable escrow lock when a blacklistable/freezable ERC-20 rejects transfer to the order beneficiary during `RedeemEscrow`/`RefundEscrow` — (File: evm/src/apps/intentsv2/IntentsBase.sol)

## Summary
`IntentsBase._withdraw`, invoked from `onAccept` for both `RedeemEscrow` (fill settlement) and `RefundEscrow` (cancellation) message kinds, decrements the `_orders[commitment][token]` escrow ledger and then unconditionally calls `IERC20(token).safeTransfer(beneficiary, amount)` [3](#0-2) . There is no check that the destination token can actually be transferred to `beneficiary`, and no fallback (e.g., pull-payment / escrow-to-claimable-balance) if the transfer reverts.

## Finding Description
Orders created through the Intent Gateway escrow arbitrary ERC-20 tokens supplied by the order creator, including centrally-issued stablecoins such as USDC/USDT that carry an issuer-controlled freeze/blacklist authority — the same class of authority described in the seed report for SPL mints. The `beneficiary` address that ultimately receives the released or refunded funds is set at order-creation time (`body.beneficiary`, decoded from the message) and is not validated against the token's transferability at settlement time [4](#0-3) .

If, by the time the settlement/refund message is delivered, the `beneficiary` address has been blacklisted by the token issuer (or the token's freeze authority is exercised against that specific account), `IERC20(token).safeTransfer(beneficiary, amount)` reverts. Because `_withdraw` is called synchronously from the module's `onAccept`/message-processing path, this revert propagates and aborts the entire incoming message handling — the escrow decrement (`_orders[commitment][token] -= amount`) is rolled back along with it, so the funds remain trapped in the gateway's escrow with no code path that lets anyone else retry with a different beneficiary or claim the funds. Unlike a normal user error, this is unrecoverable because:
- The commitment/order is uniquely tied to the beneficiary encoded at order-placement time; there is no `redirect_beneficiary`/reclaim mechanism in `_withdraw`.
- Re-delivery of the same cross-chain message (if attempted) will hit the exact same beneficiary and fail identically every time.
- The fee sweep to the same beneficiary (`IDispatcher(host()).feeToken()` transfer) has the identical failure mode and further couples fee payout to the same unblockable address [5](#0-4) .

This mirrors the seed report's invariant exactly: the protocol assumes token transfers to/from custody always succeed, but an externally-controlled freeze/blacklist authority on the collateral/settlement token can permanently break that assumption, locking funds that belong to solvers/fillers and order creators.

## Impact Explanation
Funds legitimately owed to a solver (on fill/RedeemEscrow) or back to the original order creator (on cancellation/RefundEscrow) become permanently stuck in the Intent Gateway's escrow with no recovery path once the token issuer freezes the beneficiary address. This is a direct loss/lock of funds for an innocent party (the solver who already delivered outputs, or the user who placed the order) — matching the bounty's "stealing or loss of funds" / "logic attacks" category. It does not require a malicious relayer, prover, or governance actor; the trigger is external (issuer-side blacklist action against an address that happens to be an order beneficiary), which is analogous in class to the accepted-as-Medium finding in the seed report.

## Likelihood Explanation
Requires the escrowed/settlement token to be one with issuer freeze/blacklist capability (USDC, USDT and similar are explicitly in scope for Intent Gateway use given the fee-token/stablecoin-oriented design elsewhere in the codebase, e.g. `feeToken` swaps in `SimplexPaymaster`/`IntentGatewayV2`). Given regulatory blacklisting of addresses is a real, observed event for major stablecoins, and any order's beneficiary is an ordinary externally-controlled address chosen by users/solvers, this is a plausible, not purely theoretical, occurrence — same "rare but real" characterization the original judge gave it.

## Recommendation
- Do not let a failed transfer to the beneficiary abort the whole escrow-release accounting. Wrap the external `safeTransfer` in a try/catch (or use a pull-based claim pattern) so a stuck transfer credits an internal "claimable" balance for the beneficiary instead of reverting the entire settlement.
- Allow a permissioned or time-locked path for the affected beneficiary (or governance, after a delay) to redirect a stuck payout to an alternate address once a transfer has failed a bounded number of times.
- Optionally maintain an allowlist/denylist check (or a `try transferFrom`) before decrementing escrow, so failures are detected and handled before mutating `_orders` state, keeping accounting and token movement atomic and recoverable.

## Proof of Concept
1. A solver fills an order whose input token is USDC and whose `beneficiary` (set by the order creator or filler at order time) is address `B`.
2. Before the settlement message (`RedeemEscrow`) is relayed and processed by `onAccept`, Circle blacklists address `B` on that USDC contract (a real, unilateral, issuer-side action independent of any Hyperbridge actor).
3. The relayer delivers the settlement request; `onAccept` calls `_withdraw(body, false, true)`.
4. Inside `_withdraw`, `IERC20(token).safeTransfer(beneficiary, amount)` reverts because `B` is blacklisted [6](#0-5) .
5. The whole `onAccept` call reverts; `_orders[commitment][token]` is never decremented, but the underlying `IERC20` balance sits in the gateway indefinitely — there is no other function in `IntentsBase`/`IntentGatewayV2` that can move those specific escrowed tokens to any address other than the frozen `B`.
6. The solver who fulfilled the order (or the original depositor, in the refund case) permanently loses access to their funds.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L93-117)
```rust
		// Mint or transfer to beneficiary
		if local_asset_id == T::NativeAssetId::get() {
			<T as Config>::NativeCurrency::transfer(
				&Pallet::<T>::pallet_account(),
				&beneficiary,
				amount,
				ExistenceRequirement::AllowDeath,
			)
			.map_err(|e| HftError::TransferFailed(e.into()))?;
		} else {
			let is_native = NativeAssets::<T>::get(local_asset_id.clone());
			if is_native {
				<T as Config>::Assets::transfer(
					local_asset_id,
					&Pallet::<T>::pallet_account(),
					&beneficiary,
					amount.into(),
					Preservation::Expendable,
				)
				.map_err(|e| HftError::TransferFailed(e.into()))?;
			} else {
				<T as Config>::Assets::mint_into(local_asset_id, &beneficiary, amount.into())
					.map_err(|e| HftError::MintFailed(e.into()))?;
			}
		}
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-410)
```text
    function _withdraw(WithdrawalRequest memory body, bool isRefund, bool finalize) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        if (finalize) _filled[body.commitment] = beneficiary;

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L412-417)
```text
        if (finalize) {
            uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
            if (fees > 0) {
                delete _orders[body.commitment][TRANSACTION_FEES];
                IERC20(IDispatcher(host()).feeToken()).safeTransfer(beneficiary, fees);
            }
```
