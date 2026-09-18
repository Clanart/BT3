Analog found: the same "unbounded loop scales with attacker-controlled number of denom positions" bug class from dForce's `calcAccountEquity` maps to Sei's cast-address balance migration, which calls `BankKeeper.SpendableCoins` → `GetAllBalances`, an unbounded iteration over every coin denom held by an address [1](#0-0) [2](#0-1) .

### Title
Unbounded balance-migration loop via attacker-inflated denom count on a victim's cast address can freeze EVM association/first transaction - (File: utils/helpers/associate.go)

### Summary
`AssociationHelper.MigrateBalance`, invoked from `AssociateAddresses` on every `Associate`/`RegisterPointer`/first-signed-EVM-tx association flow, calls `bankKeeper.SpendableCoins(ctx, castAddr)` to sweep all of a victim's pre-association ("cast address") balances into their real Sei address. `SpendableCoins` internally calls `GetAllBalances`, which iterates every denom key present under that account's balance prefix in the KV store with no cap [3](#0-2) [1](#0-0) .

### Finding Description
Anyone can permissionlessly create tokenfactory denoms (`factory/{creator}/{subdenom}`) and mint/send arbitrary amounts (down to 1 unit) to any target address [4](#0-3) . There is no cap on the number of distinct denoms a single account may hold in the bank store; `GetAllBalances`/`IterateAccountBalances` iterate the account's full balance-store range with no limit [5](#0-4) .

Every Sei account has a deterministic "cast address" (`sdk.AccAddress(evmAddr[:])`) that exists prior to EVM address association. An attacker can pre-fund a victim's not-yet-associated cast address with dust amounts of thousands of distinct tokenfactory denoms. When the victim later associates their EVM address (via the `addr` precompile's `associate`/`associatePublicKey`, `RegisterPointer`/`AssociateContractAddress`, or simply signs and submits their first EVM transaction, which triggers the same migration path), `AssociationHelper.MigrateBalance` runs `SpendableCoins(ctx, castAddr)` → `GetAllBalances` over the poisoned cast address, iterating and processing every attacker-planted denom before the `SendCoins` sweep can complete [3](#0-2) . This mirrors the dForce report's structure exactly: an attacker-controlled, unbounded set of "positions" (denoms) forces a victim-triggered code path to perform per-position expensive operations (store reads/writes, `SendCoins` per call) with no cap analogous to dForce's uncapped collateral/borrow arrays.

### Impact Explanation
If the number of planted denoms is large enough, the gas/computation cost of a single `Associate`/`RegisterPointer` transaction (or the association step embedded in the victim's first EVM transaction via `EVMPreprocessDecorator`) can be pushed toward or beyond the practical block gas limit, preventing the victim from ever completing association or from having their EVM transaction processed within one block, effectively freezing their EVM-side usage of funds already sent to (or destined for) their cast/associated address. Because this migration is baked into the address-association step that is a prerequisite for most EVM interactions on Sei (transfers, precompile calls, DeFi/liquidation actions routed through the EVM), this can constitute a permanent-freezing-style denial of legitimate EVM operations for the targeted account, analogous to the liquidation-blocking DoS described in the report.

### Likelihood Explanation
Likelihood is constrained by cost: the attacker must pay gas/fees to create N tokenfactory denoms and send dust to the victim's cast address, and tokenfactory denom creation/mint/send each cost real Cosmos gas per denom, so this is not free griefing. It is nonetheless fully permissionless and requires no special privilege — only knowledge of the victim's (soon to be associated) EVM address, which is often known in advance (e.g., new EVM users, contract deployers who haven't yet transacted, or any address visible on-chain before association).

### Recommendation
Cap or bound the number of denoms considered during cast-address balance migration (e.g., migrate only a fixed allow-list/param-capped number of denoms per association, or require the account owner to explicitly claim/sweep individual denoms rather than doing a full `SpendableCoins`/`GetAllBalances` sweep as part of the mandatory association critical path). Alternatively, decouple balance migration from the association ante/precompile hot path so an unbounded number of denoms cannot block association itself, and charge gas proportional to the number of denoms actually iterated so the cost cannot be externally imposed on the victim beyond their own transaction's gas budget.

### Proof of Concept
1. Determine or predict a target EVM address `E` that has not yet associated with a Sei address.
2. Compute `castAddr = sdk.AccAddress(E[:])`.
3. As attacker, submit N `MsgCreateDenom` + `MsgMint` + `MsgSend` transactions (tokenfactory module, permissionless) to send 1 unit of N distinct `factory/{attacker}/{i}` denoms to `castAddr` [4](#0-3) .
4. When the victim eventually calls `addr` precompile's `associate`/`associatePublicKey`, or `RegisterPointer`/`AssociateContractAddress`, or sends their first EVM transaction, `AssociateAddresses` → `MigrateBalance` executes `SpendableCoins(ctx, castAddr)` which iterates all N denoms [6](#0-5) [3](#0-2) .
5. Increase N until the gas cost of this migration step, combined with the rest of the association/EVM tx processing, approaches or exceeds the practical per-tx/per-block gas limit, demonstrating that a victim's association (and therefore EVM usage) can be stalled by attacker-controlled unbounded denom count.

Note: I was unable to fully verify (within available tooling) the precise gas cost per iterated denom in `SendCoins`/`GetAllBalances` at current Sei gas-metering parameters, nor confirm the exact current mempool/block gas ceiling used in production, both of which are needed to state a concrete "N" threshold; a background Devin session with full repo/build access and a local testnet would be needed to empirically measure the exact denom count required to cause a block-delay-class DoS.

### Citations

**File:** sei-cosmos/x/bank/keeper/view.go (L110-127)
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
```

**File:** sei-cosmos/x/bank/keeper/view.go (L132-154)
```go
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
```

**File:** sei-cosmos/x/bank/keeper/view.go (L176-194)
```go
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

**File:** utils/helpers/associate.go (L34-55)
```go
func (p AssociationHelper) AssociateAddresses(ctx sdk.Context, seiAddr sdk.AccAddress, evmAddr common.Address, pubkey cryptotypes.PubKey, migrateUseiOnly bool) error {
	castAddr := sdk.AccAddress(evmAddr[:])
	if !castAddr.Equals(seiAddr) && p.accountKeeper.GetAccount(ctx, seiAddr) == nil {
		castAcc := p.accountKeeper.GetAccount(ctx, castAddr)
		castBaseAcc, ok := castAcc.(*authtypes.BaseAccount)
		if ok && castBaseAcc.GetPubKey() == nil && p.bankKeeper.LockedCoins(ctx, castAddr).IsZero() {
			p.accountKeeper.SetAccount(ctx, authtypes.NewBaseAccount(seiAddr, pubkey, castBaseAcc.GetAccountNumber(), castBaseAcc.GetSequence()))
		}
	}
	p.evmKeeper.SetAddressMapping(ctx, seiAddr, evmAddr)
	acc := p.accountKeeper.GetAccount(ctx, seiAddr)
	if acc == nil {
		acc = p.accountKeeper.NewAccountWithAddress(ctx, seiAddr)
	}
	if acc.GetPubKey() == nil {
		if err := acc.SetPubKey(pubkey); err != nil {
			return err
		}
		p.accountKeeper.SetAccount(ctx, acc)
	}
	return p.MigrateBalance(ctx, evmAddr, seiAddr, migrateUseiOnly)
}
```

**File:** utils/helpers/associate.go (L57-72)
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
```

**File:** x/tokenfactory/keeper/createdenom.go (L12-21)
```go
// CreateDenom creates a new token denom with the given subdenom.
func (k Keeper) CreateDenom(ctx sdk.Context, creatorAddr string, subdenom string) (newTokenDenom string, err error) {
	denom, err := k.validateCreateDenom(ctx, creatorAddr, subdenom)
	if err != nil {
		return "", err
	}

	err = k.createDenomAfterValidation(ctx, creatorAddr, denom)
	return denom, err
}
```
