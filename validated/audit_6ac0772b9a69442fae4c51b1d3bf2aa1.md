# Silently Swallowed Bank-Transfer Errors During EVM Surplus Settlement Cause Unaccounted Fund Loss - ([File: x/evm/keeper/abci.go])

### Summary
`Keeper.EndBlock` in `x/evm/keeper/abci.go` sweeps per-transaction EVM gas surplus (usei/wei) out of ephemeral per-tx "coinbase" accounts and into the block's real coinbase, and separately credits any un-attributed ante-handler surplus into the EVM module account. Both of these critical fund-movement calls (`SendCoinsAndWei`, `AddCoins`, `AddWei`) only `logger.Error(...)` on failure and continue execution — there is no retry, no re-accounting, no event, and no halt. This mirrors the reported "ineffective try/catch" bug class: a critical operation that can fail is wrapped so that failure is silently absorbed, and (unlike the cited risk-accepted mitigation which added a tracking event) sei-chain records no on-chain signal that the transfer failed.

### Finding Description
In `EndBlock`, for every EVM tx with deferred info, the accumulated usei/wei balance sitting in the deterministic pseudo-account `state.GetCoinbaseAddress(idx)` is swept to the coinbase address: [1](#0-0) 

If `SendCoinsAndWei` returns an error (e.g. a blocked-address check, a `SendEnabledCoin` restriction, an internal invariant violation, or any other bank-keeper failure), the code does nothing but log:
```go
if err := k.BankKeeper().SendCoinsAndWei(ctx, coinbaseAddress, coinbase, balance, weiBalance); err != nil {
    logger.Error("failed to send usei surplus to coinbase account", "from", coinbaseAddress, "err", err)
}
```
Execution proceeds as if the transfer succeeded: the loop still adds `deferredInfo.Surplus` into `surplus` and the funds are left stranded in the pseudo-account `state.GetCoinbaseAddress(idx)`, which is derived only from `CoinbaseAddressPrefix + txIdx` (not from block height), so it is reused deterministically by whichever transaction occupies that index in future blocks. [2](#0-1) 

The same silent-failure pattern is repeated immediately after for crediting the pooled ante-handler surplus (from gas overpayment attribution failures) into the EVM module account: [3](#0-2) 

Here, if `AddCoins`/`AddWei` fails, the usei/wei that was already deducted from the paying account(s) during the ante handler (tracked via `AddAnteSurplus`) is never credited to any account at all — it simply disappears from the accounting for that block, with only a log line and no protocol-level record. [4](#0-3) 

This is the direct Go analog of the audited Solidity `try/catch` issue: the try/catch (here, `if err != nil { logger.Error(...) }`) around a state-critical balance movement swallows the failure instead of halting, reverting, retrying, or recording an event to allow off-chain/governance reconciliation, exactly the risk pattern flagged in the source report.

### Impact Explanation
- When the `AddCoins`/`AddWei` sweep of ante-surplus to the EVM module account fails, usei/wei already debited from user accounts during ante processing is credited nowhere — a real, permanent loss of funds from total circulating balance for that block with no recovery path and no on-chain trace beyond a log.
- When `SendCoinsAndWei` from a per-tx pseudo coinbase account fails, that block's validator fee revenue for the affected transaction is dropped from `evmKeeperMetrics`/coinbase distribution and left in a deterministic, non-bech32 pseudo-account, which is a form of fund misallocation.
- Neither failure emits any event, increments any counter surfaced to operators, or is retried — an attacker or a buggy interaction with `BlockedAddr`/`IsSendEnabledCoin`/allow-list logic (all of which are reachable via ordinary public transactions that alter account or denom send-enablement state) can reliably trigger `SendCoinsAndWei`/`AddCoins` failures on specific addresses, deterministically causing fee/surplus funds to vanish from the ledger every block that touches those addresses.

### Likelihood Explanation
Any unprivileged user can submit ordinary EVM transactions; the surplus-sweep code path in `EndBlock` runs unconditionally for every block containing an EVM transaction. Triggering the failure branch requires only that `SendCoinsAndWei`/`AddCoins`/`AddWei` return an error for the coinbase or EVM module address in a given block (e.g., through denom send-enable toggling, a blocked-address condition, or any other bank-keeper validation failure reachable through governance/tokenfactory/bank state that a normal transaction can influence), which is well within reach of a public RPC client without special privilege.

### Recommendation
Do not silently continue on failure of `SendCoinsAndWei`, `AddCoins`, or `AddWei` in `EndBlock`. At minimum:
- Emit a dedicated event (analogous to the cited `AuthorizationInvoluntaryDecreased` mitigation) recording the failed sweep, amount, and target address so it can be reconciled off-chain or by governance.
- Consider retrying via the module account or accumulating unresolved surplus in a durable, queryable store entry rather than leaving it in an ephemeral per-tx-index pseudo-account that gets silently overwritten in later blocks.
- Add invariant checks / metrics alerts so operators are notified when these transfers fail, since they directly affect total supply accounting.

### Proof of Concept
1. Cause `k.BankKeeper().SendCoinsAndWei` (or `AddCoins`/`AddWei`) to fail for the coinbase address or the EVM module account in a given block — for example, by having the denom be temporarily disabled for sending, or the destination address be added to the blocked-address set through normal bank/tokenfactory governance-driven state that a public transaction can trigger.
2. Submit any EVM transaction so that `EndBlock` in `x/evm/keeper/abci.go` runs the surplus-sweep loop at lines 106-148.
3. Observe that the transfer call returns an error, `logger.Error` is invoked, and execution continues without reverting, emitting an event, or otherwise recording the failure — the surplus usei/wei is permanently unaccounted for (either stranded in the ephemeral `state.GetCoinbaseAddress(idx)` account or entirely uncredited). [5](#0-4)

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

**File:** x/evm/state/utils.go (L15-21)
```go
var CoinbaseAddressPrefix = []byte("evm_coinbase")

func GetCoinbaseAddress(txIdx int) sdk.AccAddress {
	txIndexBz := make([]byte, 8)
	binary.BigEndian.PutUint64(txIndexBz, uint64(txIdx)) //nolint:gosec
	return append(CoinbaseAddressPrefix, txIndexBz...)
}
```

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
