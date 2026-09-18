### Title
EndBlock aggregates deferred EVM fee surplus into the module account even when coinbase→fee-collector sweep fails, permanently stranding the swept funds - ([File: x/evm/keeper/abci.go])

### Summary
`Keeper.EndBlock` mirrors the pattern in the referenced report: it computes a value from state that is about to be moved by an external call (`BankKeeper().SendCoinsAndWei`), but continues to use/aggregate a *separate* accounting value (`deferredInfo.Surplus`) unconditionally, regardless of whether that external call succeeded. If `SendCoinsAndWei` errors, the error is only logged — execution proceeds as though the sweep succeeded.

### Finding Description
In `x/evm/keeper/abci.go`, `EndBlock` iterates `evmTxDeferredInfoList` and, for each successfully-executed tx, tries to sweep the per-tx coinbase account's balance to the real fee collector: [1](#0-0) 

```go
idx := int(deferredInfo.TxIndex)
coinbaseAddress := state.GetCoinbaseAddress(idx)
useiBalance := k.BankKeeper().GetBalance(ctx, coinbaseAddress, denom).Amount
lockedUseiBalance := k.BankKeeper().LockedCoins(ctx, coinbaseAddress).AmountOf(denom)
balance := useiBalance.Sub(lockedUseiBalance)
weiBalance := k.BankKeeper().GetWeiBalance(ctx, coinbaseAddress)
if !balance.IsZero() || !weiBalance.IsZero() {
    if err := k.BankKeeper().SendCoinsAndWei(ctx, coinbaseAddress, coinbase, balance, weiBalance); err != nil {
        logger.Error("failed to send usei surplus to coinbase account", "from", coinbaseAddress, "err", err)
    }
}
surplus = surplus.Add(deferredInfo.Surplus)
```

`SendCoinsAndWei`'s failure path is swallowed — only `logger.Error` is called, with no retry, no skip, and no rollback of any bookkeeping tied to that transaction: [2](#0-1) 

`coinbaseAddress` is a deterministic per-tx-index address (`state.GetCoinbaseAddress`) with `BlockedAddr` returning `true` for the `evm_coinbase` prefix, meaning it has no private key and cannot be recovered by any external actor: [3](#0-2) [4](#0-3) 

If `SendCoinsAndWei` fails for any reason (e.g. `AddWei`'s `CanSendTo` recipient-checker rejecting the destination, or a `SubWei`/`AddCoins` invariant failure), the usei/wei balance that was supposed to move out of `coinbaseAddress` remains stuck there — an address nobody can sign for. This is directly analogous to the reported `PoolKeeper` bug: a value/state transition is computed and used to drive subsequent bookkeeping (`surplus.Add(...)`, later minted via `AddCoins` to the EVM module account) without gating on whether the corresponding external operation actually succeeded.

### Impact Explanation
Funds credited to a per-tx `evm_coinbase_*` account (representing a user's EVM gas overpayment/fee remainder for that tx) can become permanently unrecoverable if the sweep to the fee collector fails, since:
- The address is deliberately blocked from being sent to elsewhere (`BlockedAddr` returns true for the coinbase prefix), so it can only be moved by this exact `SendCoinsAndWei` call in `EndBlock`.
- No retry or alerting mechanism exists beyond a log line; the block still commits normally.
- This qualifies as permanent freezing of user funds (fee overpayment amounts), a fund-loss condition reachable purely by submitting ordinary EVM transactions whose fee/gas accounting triggers this code path.

### Likelihood Explanation
Likelihood is dependent on `SendCoinsAndWei` actually failing in production, which requires a `CanSendTo` recipient-check rejection or an internal invariant failure (e.g. `SubWei`/`AddWei` insufficient-funds edge cases from decimal rounding). These are not attacker-triggerable directly, and I could not find a confirmed code path where a normal transaction sender can force this specific call to fail deterministically — this makes the analog plausible but the concrete trigger unconfirmed.

### Recommendation
Do not add `deferredInfo.Surplus` to the aggregate `surplus` (which is later minted to the EVM module account) when the corresponding `SendCoinsAndWei` sweep fails, or treat a sweep failure as a hard error that halts/queues the block rather than merely logging it. Ensure that fee-surplus mint and the physical fund sweep either both succeed or both fail atomically, analogous to the audit's recommendation of tying `executionPrice` (or here, `surplus`) update strictly to the success of the corresponding external interaction.

### Proof of Concept
Not reproducible without confirming a concrete way to force `SendCoinsAndWei` to return an error for a `coinbaseAddress → feeCollector` transfer under normal operator configuration; this requires further investigation (e.g., a registered `RecipientChecker` rejecting the fee collector address, or a wei/usei rounding edge case) that I was unable to verify within the available context.

### Citations

**File:** x/evm/keeper/abci.go (L123-134)
```go
		idx := int(deferredInfo.TxIndex)
		coinbaseAddress := state.GetCoinbaseAddress(idx)
		useiBalance := k.BankKeeper().GetBalance(ctx, coinbaseAddress, denom).Amount
		lockedUseiBalance := k.BankKeeper().LockedCoins(ctx, coinbaseAddress).AmountOf(denom)
		balance := useiBalance.Sub(lockedUseiBalance)
		weiBalance := k.BankKeeper().GetWeiBalance(ctx, coinbaseAddress)
		if !balance.IsZero() || !weiBalance.IsZero() {
			if err := k.BankKeeper().SendCoinsAndWei(ctx, coinbaseAddress, coinbase, balance, weiBalance); err != nil {
				logger.Error("failed to send usei surplus to coinbase account", "from", coinbaseAddress, "err", err)
			}
		}
		surplus = surplus.Add(deferredInfo.Surplus)
```

**File:** sei-cosmos/x/bank/keeper/send.go (L372-381)
```go
// BlockedAddr checks if a given address is restricted from
// receiving funds.
func (k BaseSendKeeper) BlockedAddr(addr sdk.AccAddress) bool {
	if len(addr) == len(CoinbaseAddressPrefix)+8 {
		if bytes.Equal(CoinbaseAddressPrefix, addr[:len(CoinbaseAddressPrefix)]) {
			return true
		}
	}
	return k.blockedAddrs[addr.String()]
}
```

**File:** sei-cosmos/x/bank/keeper/send.go (L440-459)
```go
func (k BaseSendKeeper) SendCoinsAndWei(ctx sdk.Context, from sdk.AccAddress, to sdk.AccAddress, amt sdk.Int, wei sdk.Int) error {
	if err := k.SubWei(ctx, from, wei); err != nil {
		return err
	}
	if err := k.AddWei(ctx, to, wei); err != nil {
		return err
	}
	ctx.EventManager().EmitEvents(sdk.Events{
		sdk.NewEvent(
			types.EventTypeWeiTransfer,
			sdk.NewAttribute(types.AttributeKeyRecipient, to.String()),
			sdk.NewAttribute(types.AttributeKeySender, from.String()),
			sdk.NewAttribute(sdk.AttributeKeyAmount, wei.String()),
		),
	})
	if amt.GT(sdk.ZeroInt()) {
		return k.SendCoinsWithoutAccCreation(ctx, from, to, sdk.NewCoins(sdk.NewCoin(sdk.MustGetBaseDenom(), amt)))
	}
	return nil
}
```

**File:** x/evm/state/utils.go (L15-21)
```go
var CoinbaseAddressPrefix = []byte("evm_coinbase")

func GetCoinbaseAddress(txIdx int) sdk.AccAddress {
	txIndexBz := make([]byte, 8)
	binary.BigEndian.PutUint64(txIndexBz, uint64(txIdx)) //nolint:gosec
	return append(CoinbaseAddressPrefix, txIndexBz...)
}
```
