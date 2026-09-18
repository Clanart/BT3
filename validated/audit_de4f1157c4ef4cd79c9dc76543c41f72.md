### Title
Unbounded balance-migration DoS in EVM address-association ante handler - ([File: utils/helpers/associate.go])

### Summary
The `ProtectionPool`/`DefaultStateManager` bug in the referenced report is a classic "unbounded array populated by permissionless user actions, later fully iterated by a critical, always-executed function" pattern. The closest reachable analog in `sei-chain` is the EVM cast-address balance-migration path executed by `EVMPreprocessDecorator` in the EVM ante-handler pipeline. Any unprivileged user can grow the number of distinct coin balances held under an address (via the permissionless `x/tokenfactory` denom-creation flow) and then force that unbounded balance set to be fully iterated inside the ante handler for every subsequent transaction from the associated `cast` address, running under an *infinite* gas meter.

### Finding Description
When an EVM transaction arrives from a Sei address that has not yet been associated with an EVM address, `EVMPreprocessDecorator.AnteHandle` sets an infinite gas meter and then calls `associateHelper.AssociateAddresses`: [1](#0-0) 

`AssociateAddresses` unconditionally calls `MigrateBalance`, which — unless `migrateUseiOnly` is set — calls `p.bankKeeper.SpendableCoins(ctx, castAddr)`: [2](#0-1) 

`SpendableCoins`/`spendableCoins` calls `GetAllBalances`, which is backed by `IterateAllBalances`/`IterateAccountBalances`, a full KV-store iterator over every denom balance entry held by that one account: [3](#0-2) 

Because `x/tokenfactory` allows any account to permissionlessly create new denoms (`addDenomFromCreator`) with no cap on the number of denoms a creator can mint: [4](#0-3) 

an attacker can:
1. Create an arbitrarily large number of tokenfactory denoms (each creation is a normal metered transaction, paid for individually by the attacker).
2. Send a minimal amount of each of these denoms to the "cast" Cosmos address derived from an EVM address that has not yet been `Associate`d (`sdk.AccAddress(evmAddr[:])`).
3. Submit (or have anyone submit) a single ordinary EVM transaction from that EVM address.

That single transaction's ante-handler pass will then execute `MigrateBalance` → `SpendableCoins` → `IterateAllBalances`, which must iterate over and unmarshal every one of the attacker-inflated balance entries, and then `SendCoins` must move all of them, one denom at a time, from the cast address to the newly associated Sei address. This entire chain runs **before** the transaction's real EVM gas is applied — the surrounding context already carries `sdk.NewInfiniteGasMeterWithMultiplier(ctx)`, so the cost of this store scan is not capped by the sender's declared gas limit the way ordinary state operations are (it still consumes wall-clock CPU time during `DeliverTx`, but is not subject to an EVM-side execution-time or gas ceiling that would otherwise abort the operation early).

This mirrors the reported bug precisely: an attacker-controlled, unboundedly-growing collection (there: `activeProtectionIndexes`; here: the per-account balance-denom set) is later fully iterated by a function that is unconditionally invoked as part of normal, expected chain operation (there: `_assessState()`; here: the EVM ante-handler's address-association/migration step), with no upper bound or pagination on the collection size.

### Impact Explanation
A sufficiently large balance-migration workload executed inside the ante handler for a single transaction increases per-transaction processing time (proportional to the number of distinct denoms held by the cast address). If an attacker inflates this set enough (thousands to tens of thousands of tokenfactory denoms, each individually cheap to create), the resulting single-transaction ante-handler execution time can grow large enough to materially delay block processing for the block containing that transaction, since it must complete synchronously during `DeliverTx`/`ProcessBlock` before the rest of the pipeline (fee charging, EVM execution) proceeds. This falls under the accepted "block delay beyond 2.5 seconds" impact category for a public, permissionless attack path (no validator/operator/p2p involvement required).

### Likelihood Explanation
Likelihood is moderate-to-high in terms of feasibility: creating many tokenfactory denoms and sending dust amounts to a target cast address are both ordinary, permissionless operations available to any account, and the migration path is unconditionally triggered on the first transaction from any not-yet-associated EVM address (a very common occurrence for new users, and one that can also be forced against a victim's own future cast address by simply front-loading it with junk balances before the victim ever transacts). The attacker fully controls the size of the balance set and thus the magnitude of the resulting delay.

### Recommendation
- Cap or paginate the number of denoms considered during `MigrateBalance`/`AssociateAddresses` (e.g., migrate a bounded number of the largest-value denoms per call, or require a metered/gas-limited execution path rather than performing full iteration under an infinite gas meter).
- Alternatively, apply the real (non-infinite) EVM gas meter to the balance-migration step so an attacker-inflated denom set naturally causes the triggering transaction to run out of gas and fail cheaply and predictably, rather than consuming unbounded ante-handler CPU time.
- Consider disallowing or rate-limiting sending large numbers of distinct low-value tokenfactory denoms to a single account, or lazily migrating balances denom-by-denom over multiple transactions instead of doing so eagerly and atomically in the ante handler.

### Proof of Concept
1. Attacker uses `x/tokenfactory` `MsgCreateDenom` repeatedly to create N (e.g., 20,000) distinct denoms, each a normal, individually-gas-metered transaction.
2. Attacker sends 1 unit of each of the N denoms to `castAddr = sdk.AccAddress(targetEvmAddr[:])`, where `targetEvmAddr` is an EVM address that has not yet called `Associate`.
3. Attacker (or the address owner) submits any ordinary EVM transaction signed by `targetEvmAddr`.
4. `EVMPreprocessDecorator.AnteHandle` sets an infinite gas meter and invokes `AssociateAddresses` → `MigrateBalance` → `SpendableCoins(ctx, castAddr)`, which iterates and unmarshals all N balance entries via `IterateAllBalances`, then calls `SendCoins` to move all N coin denominations — an O(N) operation executed synchronously inside `DeliverTx` for that single block-included transaction, with N fully controlled by the attacker and no cap enforced anywhere in the path.

### Citations

**File:** x/evm/ante/preprocess.go (L58-101)
```go
func (p *EVMPreprocessDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (sdk.Context, error) {
	msg := evmtypes.MustGetEVMTransactionMessage(tx)
	if err := Preprocess(ctx, msg, p.evmKeeper.ChainID(ctx), p.evmKeeper.EthBlockTestConfig.Enabled); err != nil {
		return ctx, err
	}

	// use infinite gas meter for EVM transaction because EVM handles gas checking from within
	ctx = ctx.WithGasMeter(sdk.NewInfiniteGasMeterWithMultiplier(ctx))

	derived := msg.Derived
	seiAddr := derived.SenderSeiAddr
	evmAddr := derived.SenderEVMAddr
	ctx.EventManager().EmitEvent(sdk.NewEvent(evmtypes.EventTypeSigner,
		sdk.NewAttribute(evmtypes.AttributeKeyEvmAddress, evmAddr.Hex()),
		sdk.NewAttribute(evmtypes.AttributeKeySeiAddress, seiAddr.String())))
	pubkey := derived.PubKey
	isAssociateTx := derived.IsAssociate
	associateHelper := helpers.NewAssociationHelper(p.evmKeeper, p.evmKeeper.BankKeeper(), p.accountKeeper)
	_, isAssociated := p.evmKeeper.GetEVMAddress(ctx, seiAddr)
	if isAssociateTx && isAssociated {
		return ctx, sdkerrors.Wrap(sdkerrors.ErrInvalidRequest, "account already has association set")
	} else if isAssociateTx {
		// check if the account has enough balance (without charging)
		if !p.IsAccountBalancePositive(ctx, seiAddr, evmAddr) {
			assocErr := evmtypes.NewAssociationMissingErr(seiAddr.String())
			evmAnteMetrics.associationError.Add(ctx.Context(), 1, otelmetric.WithAttributes(attribute.String("scenario", "associate_tx_insufficient_funds"), attribute.String("type", assocErr.AddressType())))
			return ctx, sdkerrors.Wrap(sdkerrors.ErrInsufficientFunds, "account needs to have at least 1 wei to force association")
		}
		if err := associateHelper.AssociateAddresses(ctx, seiAddr, evmAddr, pubkey, false); err != nil {
			return ctx, err
		}

		return ctx.WithPriority(antedecorators.EVMAssociatePriority), nil // short-circuit without calling next
	} else if isAssociated {
		// noop; for readability
	} else {
		// not associatedTx and not already associated
		if err := associateHelper.AssociateAddresses(ctx, seiAddr, evmAddr, pubkey, false); err != nil {
			return ctx, err
		}
		if p.evmKeeper.EthReplayConfig.Enabled {
			p.evmKeeper.PrepareReplayedAddr(ctx, evmAddr)
		}
	}
```

**File:** utils/helpers/associate.go (L57-82)
```go
func (p AssociationHelper) MigrateBalance(ctx sdk.Context, evmAddr common.Address, seiAddr sdk.AccAddress, migrateUseiOnly bool) error {
	castAddr := sdk.AccAddress(evmAddr[:])
	if castAddr.Equals(seiAddr) {
		return nil
	}
	var castAddrBalances sdk.Coins
	if migrateUseiOnly {
		castAddrBalances = sdk.Coins{p.bankKeeper.GetBalance(ctx, castAddr, "usei")}
	} else {
		castAddrBalances = p.bankKeeper.SpendableCoins(ctx, castAddr)
	}
	if !castAddrBalances.IsZero() {
		if err := p.bankKeeper.SendCoins(ctx, castAddr, seiAddr, castAddrBalances); err != nil {
			return err
		}
	}
	castAddrWei := p.bankKeeper.GetWeiBalance(ctx, castAddr)
	if !castAddrWei.IsZero() {
		if err := p.bankKeeper.SendCoinsAndWei(ctx, castAddr, seiAddr, sdk.ZeroInt(), castAddrWei); err != nil {
			return err
		}
	}
	if p.bankKeeper.LockedCoins(ctx, castAddr).IsZero() {
		p.accountKeeper.RemoveAccount(ctx, authtypes.NewBaseAccountWithAddress(castAddr))
	}
	return nil
```

**File:** sei-cosmos/x/bank/keeper/view.go (L110-194)
```go
// IterateAccountBalances iterates over the balances of a single account and
// provides the token balance to a callback. If true is returned from the
// callback, iteration is halted.
func (k BaseViewKeeper) IterateAccountBalances(ctx sdk.Context, addr sdk.AccAddress, cb func(sdk.Coin) bool) {
	accountStore := k.getAccountStore(ctx, addr)

	iterator := accountStore.Iterator(nil, nil)
	defer func() { _ = iterator.Close() }()

	for ; iterator.Valid(); iterator.Next() {
		var balance sdk.Coin
		k.cdc.MustUnmarshal(iterator.Value(), &balance)

		if cb(balance) {
			break
		}
	}
}

// IterateAllBalances iterates over all the balances of all accounts and
// denominations that are provided to a callback. If true is returned from the
// callback, iteration is halted.
func (k BaseViewKeeper) IterateAllBalances(ctx sdk.Context, cb func(sdk.AccAddress, sdk.Coin) bool) {
	store := ctx.KVStore(k.storeKey)
	balancesStore := prefix.NewStore(store, types.BalancesPrefix)

	iterator := balancesStore.Iterator(nil, nil)
	defer func() { _ = iterator.Close() }()

	for ; iterator.Valid(); iterator.Next() {
		address, err := types.AddressFromBalancesStore(iterator.Key())
		if err != nil {
			logger.Error("failed to get address from balances store", "key", iterator.Key(), "err", err)
			// TODO: revisit, for now, panic here to keep same behavior as in 0.42
			// ref: https://github.com/cosmos/cosmos-sdk/issues/7409
			panic(err)
		}

		var balance sdk.Coin
		k.cdc.MustUnmarshal(iterator.Value(), &balance)

		if cb(address, balance) {
			break
		}
	}
}

// LockedCoins returns all the coins that are not spendable (i.e. locked) for an
// account by address. For standard accounts, the result will always be no coins.
// For vesting accounts, LockedCoins is delegated to the concrete vesting account
// type.
func (k BaseViewKeeper) LockedCoins(ctx sdk.Context, addr sdk.AccAddress) sdk.Coins {
	acc := k.ak.GetAccount(ctx, addr)
	if acc != nil {
		vacc, ok := acc.(vestexported.VestingAccount)
		if ok {
			return vacc.LockedCoins(ctx.BlockTime())
		}
	}

	return sdk.NewCoins()
}

// SpendableCoins returns the total balances of spendable coins for an account
// by address. If the account has no spendable coins, an empty Coins slice is
// returned.
func (k BaseViewKeeper) SpendableCoins(ctx sdk.Context, addr sdk.AccAddress) sdk.Coins {
	spendable, _ := k.spendableCoins(ctx, addr)
	return spendable
}

// spendableCoins returns the coins the given address can spend alongside the total amount of coins it holds.
// It exists for gas efficiency, in order to avoid to have to get balance multiple times.
func (k BaseViewKeeper) spendableCoins(ctx sdk.Context, addr sdk.AccAddress) (spendable, total sdk.Coins) {
	total = k.GetAllBalances(ctx, addr)
	locked := k.LockedCoins(ctx, addr)

	spendable, hasNeg := total.SafeSub(locked)
	if hasNeg {
		spendable = sdk.NewCoins()
		return
	}

	return
}
```

**File:** x/tokenfactory/keeper/creators.go (L1-37)
```go
package keeper

import (
	sdk "github.com/sei-protocol/sei-chain/sei-cosmos/types"
	"github.com/sei-protocol/sei-chain/sei-cosmos/types/query"
)

func (k Keeper) addDenomFromCreator(ctx sdk.Context, creator, denom string) {
	store := k.GetCreatorPrefixStore(ctx, creator)
	store.Set([]byte(denom), []byte(denom))
}

func (k Keeper) getDenomsFromCreator(ctx sdk.Context, creator string, pagination *query.PageRequest) ([]string, *query.PageResponse, error) {
	store := k.GetCreatorPrefixStore(ctx, creator)
	var denoms []string
	pageRes, err := query.Paginate(ctx, store, pagination, func(key []byte, _ []byte) error {
		denoms = append(denoms, string(key))
		return nil
	})
	if err != nil {
		return nil, nil, err
	}
	return denoms, pageRes, nil
}

// GetAllDenomsFromCreator returns every denom for a creator with no page cap.
// Safe to use in the wasm query path: gas metering bounds execution cost, so unbounded iteration does not pose a DoS risk.
func (k Keeper) GetAllDenomsFromCreator(ctx sdk.Context, creator string) []string {
	store := k.GetCreatorPrefixStore(ctx, creator)
	iterator := store.Iterator(nil, nil)
	defer func() { _ = iterator.Close() }()
	var denoms []string
	for ; iterator.Valid(); iterator.Next() {
		denoms = append(denoms, string(iterator.Key()))
	}
	return denoms
}
```
