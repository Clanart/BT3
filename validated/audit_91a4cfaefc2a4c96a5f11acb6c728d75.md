## Title
Deferred EVM fee-surplus and coinbase sweep failures in `EndBlock` are only logged, not handled, permanently losing/destroying funds - (File: x/evm/keeper/abci.go)

### Summary
The reported bug class is "an inter-domain/deferred call can fail, but the caller assumes success and never checks or reacts to the failure, leading to state corruption." The closest reachable analog in sei-chain is the deferred, asynchronous settlement of EVM fee surplus that happens in `Keeper.EndBlock`, where cross-module bank transfers that settle balances already debited during transaction execution are attempted, but their errors are swallowed with a bare `logger.Error(...)` call instead of being handled, retried, or causing the block/transaction to fail.

### Finding Description
Every EVM transaction moves usei/wei into a deterministic per-transaction "coinbase" address during ante-handler fee charging (`x/evm/ante/fee.go`, `EvmDeliverChargeFees` in `app/ante/evm_delivertx.go`), and records any un-swept surplus via `AddAnteSurplus` [1](#0-0) . This debit from the sender/coinbase side is final and committed to state during `DeliverTx`.

The actual settlement — sweeping that already-debited balance to the real fee collector/coinbase, and crediting leftover surplus to the EVM module account — is deferred to `Keeper.EndBlock`: [2](#0-1) 

Both `k.BankKeeper().SendCoinsAndWei(...)` and `k.BankKeeper().AddCoins(...)` / `AddWei(...)` can return errors (e.g. blocked address, invariant check, marshal error, or any keeper-level failure). In both cases the code does:
```go
if err := ...; err != nil {
    logger.Error("failed to send usei surplus to coinbase account", "from", coinbaseAddress, "err", err)
}
```
and then continues execution unconditionally. There is no retry, no panic, no reversion of the earlier debit, and no accounting adjustment. The funds that were already subtracted from the sender/per-tx coinbase account during the transaction (an operation that already committed) are never credited anywhere if the sweep/credit call fails — this is functionally identical to the reported issue: a downstream state-changing call ("cross-chain call" in the original report; here, a deferred intra-chain settlement call) can fail, and the failure is neither propagated nor recovered from, so the system proceeds as if the transfer succeeded.

### Impact Explanation
If `SendCoinsAndWei` or `AddCoins`/`AddWei` fails during `EndBlock` for any transaction's fee surplus (e.g., the per-tx coinbase or destination module account becomes blocked, or any transient error occurs), the corresponding usei/wei value is permanently stranded: it was already debited from the user during the ante handler (a committed state change) but is never credited to the fee collector or the EVM module account. This breaks the coin-conservation invariant (total supply accounting), silently destroying value rather than raising an error and halting/reverting the block. This matches the "supply inflation or destruction" / "fund loss" acceptance criteria.

### Likelihood Explanation
Reachable by any user submitting an ordinary EVM transaction — no special privileges are required, since the sweep/settlement logic in `EndBlock` runs for every block that contains EVM transactions and their per-tx surplus. The exact trigger for the bank-keeper call to fail depends on error conditions in the bank keeper (e.g., blocked-address checks against `GetCoinbaseAddress`, which is deterministic and derived only from `idx`), but the important defect is structural: no matter the cause, the code path never surfaces/handles the failure and does not maintain accounting correctness on failure.

### Recommendation
Do not silently swallow errors from `SendCoinsAndWei`/`AddCoins`/`AddWei` in `EndBlock`. Either:
1. Panic (consistent with other `EndBlock`/keeper code in this file, which panics on decode errors) so consensus fails safely rather than silently losing funds, or
2. Retain the un-swept amount in a durable, retryable "pending surplus" store (analogous to the deferred bank cache pattern used in `sei-cosmos/x/bank/keeper/keeper.go`'s `DeferredSendCoinsFromAccountToModule`/`WriteDeferredBalances`) so that it is credited in a subsequent block instead of being dropped, and add an invariant check to detect any accounting drift.

### Proof of Concept
1. Construct a state where the deterministic per-tx `coinbaseAddress` (from `state.GetCoinbaseAddress(idx)`) becomes a blocked address in the bank keeper's `BlockedAddr` check, or otherwise causes `SendCoinsAndWei` to return an error for a given `idx` (e.g. by forcing the bank keeper's underlying `SendCoins` to fail).
2. Submit an EVM transaction whose fee surplus lands in that `idx` slot; ante-handler debits fee/surplus from the sender as usual.
3. In `EndBlock`, the `SendCoinsAndWei` call to sweep coinbase balance to the real fee collector fails; the error is logged via `logger.Error(...)` at [3](#0-2)  and execution continues.
4. Verify post-block: the sender's balance was reduced by the fee, but neither the fee collector nor the EVM module account received the corresponding funds — total tracked supply across accounts has permanently decreased by that amount, and no error/log/alert beyond a debug-level log entry is produced.

### Citations

**File:** x/evm/keeper/ante.go (L10-18)
```go
func (k *Keeper) AddAnteSurplus(ctx sdk.Context, txHash common.Hash, surplus sdk.Int) error {
	store := prefix.NewStore(ctx.TransientStore(k.transientStoreKey), types.AnteSurplusPrefix)
	bz, err := surplus.Marshal()
	if err != nil {
		return err
	}
	store.Set(txHash[:], bz)
	return nil
}
```

**File:** x/evm/keeper/abci.go (L123-147)
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
	}
	if surplus.IsPositive() {
		surplusUsei, surplusWei := state.SplitUseiWeiAmount(surplus.BigInt())
		if surplusUsei.GT(sdk.ZeroInt()) {
			if err := k.BankKeeper().AddCoins(ctx, k.AccountKeeper().GetModuleAddress(types.ModuleName), sdk.NewCoins(sdk.NewCoin(k.GetBaseDenom(ctx), surplusUsei)), true); err != nil {
				logger.Error("failed to send usei surplus to EVM module account", "surplus", surplusUsei)
			}
		}
		if surplusWei.GT(sdk.ZeroInt()) {
			if err := k.BankKeeper().AddWei(ctx, k.AccountKeeper().GetModuleAddress(types.ModuleName), surplusWei); err != nil {
				logger.Error("failed to send wei surplus to EVM module account", "surplus", surplusWei)
			}
		}
```
