Confirmed: `SpendableCoins` = `total - LockedCoins`, explicitly excluding vesting-locked balances. This confirms the analog vulnerability.

### Title
Address-association balance migration skips vesting-locked balances, permanently freezing funds at the un-signable direct-cast address - (File: `utils/helpers/associate.go`)

### Summary
When a Sei address is associated with an EVM address (either automatically on first tx signing, via the `addr` precompile, or via `MsgAssociate`), the protocol calls `AssociationHelper.MigrateBalance` to move funds held under the "direct-cast" address (the raw EVM address bytes reinterpreted as an `sdk.AccAddress`) to the user's real Sei address. This function only migrates `SpendableCoins` and the `wei` balance — it never migrates any vesting-locked balance sitting on the direct-cast address. Because the direct-cast address has no valid signature-verifiable private key under the SDK's standard account-derivation scheme, once association happens, any locked/unvested funds left behind on that address become permanently unrecoverable.

### Finding Description
`AssociationHelper.MigrateBalance` in [1](#0-0)  computes the coins to move as:
```go
castAddrBalances = p.bankKeeper.SpendableCoins(ctx, castAddr)
```
`SpendableCoins` is explicitly defined as `total - LockedCoins`, i.e. it excludes any vesting-locked amount [2](#0-1) . `LockedCoins` in turn returns the full locked balance for any account implementing `VestingAccount` [3](#0-2) .

After computing this partial (spendable-only) balance and sending it, the helper only removes the direct-cast account if its `LockedCoins` is zero:
```go
if p.bankKeeper.LockedCoins(ctx, castAddr).IsZero() {
    p.accountKeeper.RemoveAccount(ctx, authtypes.NewBaseAccountWithAddress(castAddr))
}
```
If the direct-cast address is a vesting account with a non-zero locked amount, the account is intentionally preserved but its locked coins are never transferred to the real Sei address — they remain permanently attached to `castAddr`.

This helper is invoked on the routine, unprivileged path executed for every first-time signer:
- Cosmos tx signing: `UpdateSigners` in `app/ante/cosmos_checktx.go` calls `SetAddressMapping` then `associationHelper.MigrateBalance(ctx, evmAddr, signer, false)` [4](#0-3) .
- EVM tx signing: `EVMAddressDecorator.AnteHandle` performs the identical sequence [5](#0-4) .
- Explicit association via the `addr` precompile also delegates to `AssociationHelper.AssociateAddresses` → `MigrateBalance` with `migrateUseiOnly=false` [6](#0-5) .

The same gap is reproduced in the chain-upgrade sweep `MigrateCastAddressBalances`, which likewise only moves `SpendableCoins` and wei, ignoring locked coins [7](#0-6) .

The critical enabling fact is that `castAddr := sdk.AccAddress(evmAddr[:])` is not a normally-derived Cosmos account address — it is the raw 20-byte EVM address reinterpreted as bech32. A valid Cosmos SDK signature for that specific address would require a pubkey whose SDK-standard hash (not keccak256) collides with that value, which is computationally infeasible. Consequently, once association occurs and the vesting-locked remainder is left behind, there is no way for the legitimate owner (or anyone) to ever produce a transaction that spends from `castAddr`, even after the vesting schedule fully unlocks.

### Impact Explanation
Any principal that arranges vesting grants using the direct-cast (EVM-address-derived) account form — a realistic and even encouraged pattern for teams doing cross-chain/EVM-facing airdrops or vesting to a user's known EVM address before that user ever associates on Sei — will have the locked (not-yet-vested) portion of the grant permanently and irrecoverably frozen the moment the recipient associates their address (which happens automatically the first time they sign any Sei tx or EVM tx). This is a direct, permanent freezing of user funds satisfying the "permanent freezing" impact criterion, reachable purely by an ordinary user's routine, unprivileged action (their very first signed transaction).

### Likelihood Explanation
Address association is not a rare or opt-in event — it is silently triggered for every account's first Cosmos or EVM transaction via `UpdateSigners`/`EVMAddressDecorator`, and is also explicitly callable via the `addr` precompile's `associate`/`associatePubKey` methods. Any vesting account created under the EVM-address-cast form (which is a natural address format to target during airdrop/vesting setup for future EVM users) will trigger this loss automatically and without any special attacker action — it is a systemic gap in the standard migration path, not an edge case requiring adversarial conditions.

### Recommendation
`MigrateBalance` should migrate the full account balance (`GetAllBalances`, not just `SpendableCoins`) when the destination is a full ownership transfer, and correspondingly re-create the vesting schedule (or an equivalent locked-balance construct) under the new Sei address rather than leaving the vesting account and its locked coins behind at the unreachable `castAddr`. At minimum, before removing/retaining the direct-cast account, any vesting/locked balance must be migrated to the associated Sei address using the same vesting schedule parameters, mirroring how `TrufVesting.migrateUser` should copy over unclaimed reward state to the new user's `VirtualStakingRewards` balance.

### Proof of Concept
1. A vesting grant is created (e.g. via governance-authorized module or genesis vesting account) for `castAddr = sdk.AccAddress(evmAddr[:])`, where `evmAddr` is the future EVM address of a real user, with e.g. 1,000,000 usei vesting linearly, none yet vested.
2. The user later sends any ordinary Sei transaction (bank send, delegate, anything) signed with the private key corresponding to `evmAddr`/their real Sei pubkey. `UpdateSigners` (or `EVMAddressDecorator.AnteHandle` for an EVM tx) detects the account is unassociated, calls `evmKeeper.SetAddressMapping(ctx, signer, evmAddr)` and then `associationHelper.MigrateBalance(ctx, evmAddr, signer, false)` [4](#0-3) .
3. `MigrateBalance` computes `castAddrBalances = SpendableCoins(castAddr)`, which is `0` (nothing vested yet), so no funds move; `LockedCoins(castAddr)` is non-zero (1,000,000 usei locked), so the account is preserved as-is [8](#0-7) .
4. The user's real Sei address (`signer`) now owns the EVM association, but the 1,000,000 usei vesting grant remains permanently attached to `castAddr`. Because `castAddr` is not a validly-signable address (no known private key maps to it under the SDK's standard address derivation), the funds can never be spent by anyone as they vest — a permanent loss confirmable by observing `k.BankKeeper().GetAllBalances(ctx, castAddr)` remaining non-zero forever with no reachable signer, exactly as demonstrated in the existing `TestMigrateBalance` test pattern in `x/evm/ante/preprocess_test.go` (lines 423-441), which only asserts spendable balances move and never exercises the locked-coin case.

### Citations

**File:** utils/helpers/associate.go (L57-83)
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
}
```

**File:** sei-cosmos/x/bank/keeper/view.go (L157-171)
```go
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
```

**File:** sei-cosmos/x/bank/keeper/view.go (L173-194)
```go
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

**File:** app/ante/cosmos_checktx.go (L559-564)
```go
		evmKeeper.SetAddressMapping(ctx, signer, evmAddr)
		associationHelper := helpers.NewAssociationHelper(evmKeeper, evmKeeper.BankKeeper(), accountKeeper)
		if err := associationHelper.MigrateBalance(ctx, evmAddr, signer, false); err != nil {
			logger.Error("failed to migrate EVM address balance", "address", evmAddr, "err", err)
			return nil, err
		}
```

**File:** x/evm/ante/preprocess.go (L341-346)
```go
		p.evmKeeper.SetAddressMapping(ctx, signer, evmAddr)
		associationHelper := helpers.NewAssociationHelper(p.evmKeeper, p.evmKeeper.BankKeeper(), p.accountKeeper)
		if err := associationHelper.MigrateBalance(ctx, evmAddr, signer, false); err != nil {
			logger.Error("failed to migrate EVM address balance", "address", evmAddr, "err", err)
			return ctx, err
		}
```

**File:** precompiles/addr/addr.go (L246-251)
```go
	// Associate Addresses:
	associationHelper := helpers.NewAssociationHelper(p.evmKeeper, p.bankKeeper, p.accountKeeper)
	err = associationHelper.AssociateAddresses(ctx, seiAddr, evmAddr, pubkey, false)
	if err != nil {
		return nil, 0, err
	}
```

**File:** x/evm/migrations/migrate_cast_address_balances.go (L9-27)
```go
func MigrateCastAddressBalances(ctx sdk.Context, k *keeper.Keeper) (rerr error) {
	k.IterateSeiAddressMapping(ctx, func(evmAddr common.Address, seiAddr sdk.AccAddress) bool {
		castAddr := sdk.AccAddress(evmAddr[:])
		if balances := k.BankKeeper().SpendableCoins(ctx, castAddr); !balances.IsZero() {
			if err := k.BankKeeper().SendCoins(ctx, castAddr, seiAddr, balances); err != nil {
				logger.Error("error migrating balances from cast to real for address", "address", evmAddr, "err", err)
				rerr = err
				return true
			}
		}
		if wei := k.BankKeeper().GetWeiBalance(ctx, castAddr); !wei.IsZero() {
			if err := k.BankKeeper().SendCoinsAndWei(ctx, castAddr, seiAddr, sdk.ZeroInt(), wei); err != nil {
				logger.Error("error migrating wei from cast to real for address", "address", evmAddr, "err", err)
				rerr = err
				return true
			}
		}
		return false
	})
```
