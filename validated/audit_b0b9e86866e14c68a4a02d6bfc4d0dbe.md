### Title
Silent swallowing of fund-movement errors during EVM `EndBlock` surplus sweep instead of halting - (File: `x/evm/keeper/abci.go`)

### Summary
The upstream Rollkit patch fixes a case where a critical, should-never-happen error (header validation failure after a block was just produced) was only logged instead of halting the node, which could let corrupted/invalid state continue to be processed. The same anti-pattern — using `logger.Error(...)` and silently continuing instead of halting on a critical failure in a fund-accounting invariant — exists in sei-chain's EVM `EndBlock` surplus-sweep logic.

### Finding Description
`Keeper.EndBlock` in [1](#0-0)  iterates every EVM transaction's deferred info for the block and moves fee "surplus" funds:

1. Any residual `usei`/`wei` balance sitting in each transaction's deterministic coinbase address is swept to the real fee collector via `BankKeeper().SendCoinsAndWei(...)`. If this call errors, the code only does `logger.Error("failed to send usei surplus to coinbase account", ...)` and continues — the funds are left stranded in the per-tx coinbase address, which is never read again except by the next transaction reusing the same coinbase index, and the surplus already computed into `surplus.Add(deferredInfo.Surplus)` still gets credited to the EVM module account below (see next point), effectively double counting / losing track of state.
2. The block-level residual surplus is credited to the EVM module account via `BankKeeper().AddCoins(...)` and `BankKeeper().AddWei(...)`. If either fails, again only `logger.Error(...)` is emitted and processing continues — the minted/tracked surplus amount is silently dropped, i.e. funds that should have been credited to the module account (and eventually distributed/burned per protocol rules) simply vanish from the ledger with no chain halt and no error propagated to the caller.

This mirrors the exact bug class in the report: a code path that "if this ever happens... is FUBAR" is handled with a log line instead of a panic/halt, letting the node keep committing state that has silently diverged from the accounting invariant (total surplus in = total surplus swept out). In the original Rollkit report, the fix was to `panic` immediately because continuing would corrupt state; here the equivalent invariant-breaking failure is downgraded to a log message and the block continues to be finalized and committed normally, per `ProcessBlock`'s call to `app.EndBlock(...)` at [2](#0-1)  with no error return path back to the caller (`EndBlock` has no error return type at all).

### Impact Explanation
Every EVM transaction that pays a fee surplus (essentially any ordinary EVM transaction, since fee-vs-base-fee surplus is generic) feeds this code path via `AppendToEvmTxDeferredInfo` [3](#0-2) . If `SendCoinsAndWei`, `AddCoins`, or `AddWei` ever return an error (e.g., a blocked/restricted address check, a module-account send restriction, or any bank-keeper invariant rejection introduced now or in the future), the corresponding surplus amount is permanently and silently lost from the ledger instead of halting the chain for operator intervention. This can manifest as unaccounted destruction of funds (a user's paid fee surplus disappears rather than reaching the fee collector or module account) — a direct violation of the fund-conservation invariant, without any signal beyond a log line, so it can go unnoticed and compound across many blocks/transactions.

### Likelihood Explanation
This is only reachable if the bank-keeper calls actually error, which is not the common case, but it is not prevented by any invariant on this path (unlike other "serious" cases in the same package, e.g. `AppendToEvmTxDeferredInfo`'s marshal failure explicitly panics because "unable to marshal deferred info is serious, because it could cause balance surplus to be mishandled and thus affect total supply" — see [4](#0-3) ). The inconsistency — panicking for a marshal failure on the same surplus-accounting data, but only logging for an actual bank-keeper transfer failure moving the same surplus — shows the swallow here was likely an oversight rather than a deliberate design choice, matching the report's bug class of "should panic but only logs."

### Recommendation
Treat `SendCoinsAndWei`, `AddCoins`, and `AddWei` failures in `EndBlock`'s surplus-sweep logic as consensus-critical the same way the neighboring marshal-failure check already is: panic (or otherwise halt block finalization) rather than merely logging, so operators are forced to investigate before the chain continues committing state with a broken fund-conservation invariant.

### Proof of Concept
Not applicable as a standalone exploit — this is a defense-in-depth/fail-safe gap rather than a directly triggerable exploit by a normal transaction under current bank-keeper behavior. It becomes concretely exploitable/impactful only if a future or existing bank-keeper restriction (blocklist, module-account send guard, or invariant check) can be made to reject one of these transfers for a coinbase or module address reachable in this loop, at which point any ordinary EVM transaction with a fee surplus would trigger silent fund loss instead of a halt.

### Citations

**File:** x/evm/keeper/abci.go (L106-148)
```go
	evmTxDeferredInfoList := k.GetAllEVMTxDeferredInfo(ctx)
	denom := k.GetBaseDenom(ctx)
	surplus := k.GetAnteSurplusSum(ctx)
	for _, deferredInfo := range evmTxDeferredInfoList {
		txHash := common.BytesToHash(deferredInfo.TxHash)
		if deferredInfo.Error != "" && txHash.Cmp(ethtypes.EmptyTxsHash) != 0 {
			if !k.GetNonceBumped(ctx, deferredInfo.TxIndex) {
				continue
			}
			_ = k.SetTransientReceipt(ctx, txHash, &types.Receipt{
				TxHashHex:        txHash.Hex(),
				TransactionIndex: deferredInfo.TxIndex,
				VmError:          deferredInfo.Error,
				BlockNumber:      uint64(ctx.BlockHeight()), // nolint:gosec
			})
			continue
		}
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
	}
```

**File:** app/app.go (L1833-1833)
```go
	endBlockResp = app.EndBlock(ctx, req.Height, evmTotalGasUsed)
```

**File:** x/evm/keeper/deferred.go (L51-67)
```go
func (k *Keeper) AppendToEvmTxDeferredInfo(ctx sdk.Context, bloom ethtypes.Bloom, txHash common.Hash, surplus sdk.Int) {
	key := make([]byte, 8)
	binary.BigEndian.PutUint64(key, uint64(ctx.TxIndex())) //nolint:gosec
	val := &types.DeferredInfo{
		TxIndex: uint32(ctx.TxIndex()), //nolint:gosec
		TxBloom: bloom[:],
		TxHash:  txHash[:],
		Surplus: surplus,
	}
	bz, err := val.Marshal()
	if err != nil {
		// unable to marshal deferred info is serious, because it could cause
		// balance surplus to be mishandled and thus affect total supply
		panic(err)
	}
	prefix.NewStore(ctx.TransientStore(k.transientStoreKey), types.DeferredInfoPrefix).Set(key, bz)
}
```
