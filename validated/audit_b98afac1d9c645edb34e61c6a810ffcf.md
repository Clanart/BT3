### Title
Silently swallowed errors in `EndBlock` fee/surplus settlement cause permanent loss of already-debited EVM fee funds - (File: `x/evm/keeper/abci.go`)

### Summary
`Keeper.EndBlock` in the EVM module performs two deferred, error-prone bank operations — sweeping the per-tx synthetic coinbase balance and crediting the aggregated ante-handler fee "surplus" into the `x/evm` module account — but only logs the error from each call instead of propagating it. Execution continues as if the transfer succeeded, exactly the bug class described in the referenced report (a fallible external/deferred call whose failure is only logged, after which the caller proceeds as though it had succeeded).

### Finding Description
During ante handling, `EVMFeeCheckDecorator.AnteHandle` (`x/evm/ante/fee.go`) calls `st.BuyGas()`, which actually debits usei/wei from the sender's real account, then computes a `surplus` via `stateDB.Finalize()` and records it in transient storage with `AddAnteSurplus` (`x/evm/keeper/ante.go`): [1](#0-0) 

At `EndBlock`, this recorded surplus is summed with `GetAnteSurplusSum` and, along with a per-tx coinbase-address balance sweep, is supposed to land in the fee collector / `x/evm` module account: [2](#0-1) 

If `SendCoinsAndWei` fails while sweeping the per-tx synthetic coinbase balance, the error is only logged — the loop still unconditionally adds `deferredInfo.Surplus` to the running `surplus` total: [3](#0-2) 

Then, when crediting the aggregated surplus to the `x/evm` module account, both the `AddCoins` (usei) and `AddWei` (sub-usei remainder) calls also only log the error instead of returning it: [4](#0-3) 

Because the corresponding usei/wei was already subtracted from the sender's real account inside the ante handler's `BuyGas`/`Finalize` step, a failure of either `AddCoins` or `AddWei` here means those funds are debited from the user but never credited to any account — the same "fire, log-only-on-error, assume success" pattern as the original report's blockless API call, just applied to a bank-keeper call instead of an HTTP call.

### Impact Explanation
A failed `AddWei`/`AddCoins` call is not a purely cosmetic bug: it represents a permanent, unrecoverable loss of already-debited protocol fee funds (total supply accounting divergence — coins deducted from a sender's balance vanish rather than landing in the `x/evm` module account / fee collector). This matches the accepted impact category of concrete fund loss/permanent freezing or supply destruction.

### Likelihood Explanation
`AddWei` internally calls `k.CanSendTo(ctx, addr)` which iterates registered `RecipientChecker`s, and `AddCoins`/`setBalance` go through the standard blocked-address / invariant checks: [5](#0-4) 
While the module address for `x/evm` is not itself typically blocked, any registered recipient checker (e.g. a tokenfactory/denom-based restriction, or a future guard added via `RegisterRecipientChecker`) that rejects the fee-collector-derived address, or any transient failure of `setBalance`/marshalling, will trigger this silently-swallowed path on every block that has a positive surplus — i.e., essentially every block with EVM traffic. This makes the failure mode systemic rather than a one-off edge case once such a condition is reachable.

### Recommendation
Propagate the errors from the `SendCoinsAndWei`, `AddCoins`, and `AddWei` calls in `Keeper.EndBlock` (`x/evm/keeper/abci.go`) instead of only logging them, so a failure causes `EndBlock` to abort/panic (consistent with the Cosmos SDK convention that `EndBlock` errors must crash the node rather than silently diverge state, per `sei-tendermint/spec/abci++/abci++_basic_concepts_002_draft.md`), or alternatively persist the un-swept/un-credited amount for retry rather than dropping it.

### Proof of Concept
1. Register (or have registered by another module) a `RecipientChecker` via `BankKeeper.RegisterRecipientChecker` that rejects the `x/evm` module address or the fee-collector-derived coinbase address under some condition reachable at runtime (e.g., a denom-allowlist rule keyed on an address prefix that happens to match the module account).
2. Submit a normal EVM transaction; the ante handler's `EVMFeeCheckDecorator` debits gas fee from the sender and records a positive `surplus` via `AddAnteSurplus`.
3. At `EndBlock`, `k.BankKeeper().AddCoins(...)`/`AddWei(...)` for the `x/evm` module address fails due to the registered checker; the error is only logged via `logger.Error(...)`.
4. `EndBlock` returns normally with no panic, block commits successfully, and the surplus usei/wei debited from the sender in step 2 is now permanently unaccounted for — never held by any account, i.e., destroyed from tracked supply.

### Citations

**File:** x/evm/ante/fee.go (L90-100)
```go
	if err := st.BuyGas(); err != nil {
		return ctx, sdkerrors.Wrap(sdkerrors.ErrInsufficientFunds, err.Error())
	}
	if !ctx.IsCheckTx() && !ctx.IsReCheckTx() {
		surplus, err := stateDB.Finalize()
		if err != nil {
			return ctx, err
		}
		if err := fc.evmKeeper.AddAnteSurplus(ctx, etx.Hash(), surplus); err != nil {
			return ctx, err
		}
```

**File:** x/evm/keeper/abci.go (L106-135)
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
```

**File:** x/evm/keeper/abci.go (L136-148)
```go
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

**File:** sei-cosmos/x/bank/keeper/send.go (L412-437)
```go
func (k BaseSendKeeper) AddWei(ctx sdk.Context, addr sdk.AccAddress, amt sdk.Int) (err error) {
	if !k.CanSendTo(ctx, addr) {
		return sdkerrors.ErrInvalidRecipient
	}
	if amt.Equal(sdk.ZeroInt()) {
		return nil
	}
	defer func() {
		if err == nil {
			ctx.EventManager().EmitEvent(
				types.NewWeiReceivedEvent(addr, amt),
			)
		}
	}()
	currentWeiBalance := k.GetWeiBalance(ctx, addr)
	postWeiBalance := currentWeiBalance.Add(amt)
	if postWeiBalance.LT(OneUseiInWei) {
		// no need to change usei balance
		return k.setWeiBalance(ctx, addr, postWeiBalance)
	}
	currentUseiBalance := k.GetBalance(ctx, addr, sdk.MustGetBaseDenom()).Amount
	useiCredit, weiBalance := SplitUseiWeiAmount(postWeiBalance)
	if err := k.setBalance(ctx, addr, sdk.NewCoin(sdk.MustGetBaseDenom(), currentUseiBalance.Add(useiCredit)), true); err != nil {
		return err
	}
	return k.setWeiBalance(ctx, addr, weiBalance)
```
