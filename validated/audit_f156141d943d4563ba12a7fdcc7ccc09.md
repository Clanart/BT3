### Title
Vesting/locked coins stranded permanently in an EVM cast address after association - ([File: utils/helpers/associate.go])

### Summary
Sei's dual address model maps every EVM address to an implicit "cast" Cosmos address (`sdk.AccAddress(evmAddr[:])`) until an explicit association is made. `AssociateAddresses`/`MigrateBalance` in [1](#0-0)  migrates spendable balances and wei from the cast address to the newly associated Sei address, but explicitly refuses to reclaim or migrate the cast address's locked (vesting) coins, and refuses to reuse/remove the cast account when it still holds locked coins.

### Finding Description
When an unassociated EVM address receives funds while it is a vesting account (e.g., a `DelayedVestingAccount` created under the direct-cast address, as exercised by `TestAssociateAddressesDoesNotReuseVestingCastAccount` [2](#0-1) ), calling `AssociateAddresses` (triggered automatically for any first transaction from that EVM address, per the EVM ante preprocessor [3](#0-2) ) only migrates `SpendableCoins` and wei: [4](#0-3) 

If `LockedCoins(ctx, castAddr)` is non-zero, `MigrateBalance` does **not** move those coins and does **not** remove the cast account — it is left in place holding the still-vesting balance, confirmed by `TestMigrateBalance` [5](#0-4) , which shows the locked coin remains attached to `sdk.AccAddress(evmAddr[:])` (1 usei) after migration, entirely separate from the newly associated `seiAddr`.

The `castAddr` is a raw byte-cast of the EVM address (`sdk.AccAddress(evmAddr[:])`), not an address derivable from any real secp256k1 public key via standard Cosmos address derivation (RIPEMD160(SHA256(pubkey))). Its only legitimate route to being controlled is through the EVM-to-Sei association mechanism in the ante handler. Once that association is set, `SetAddressMapping` is called and future transactions from the same EVM key resolve to `seiAddr`, not `castAddr` — this is the same helper used by the ante decorator on every subsequent EVM transaction. There is no other reachable code path in the association helper, EVM ante pipeline, or its legacy variants (`utils/helpers/legacy/v575/associate.go`, `utils/helpers/legacy/v600/asssociate.go`, and other precompile `Associate`/`AssociatePubKey` implementations under `precompiles/addr/legacy/*`) that later re-checks or drains `castAddr`'s locked balance once it eventually vests.

This mirrors the Frankencoin `deny`/`cooldown` root cause: an action reachable by an unprivileged actor (submitting any first EVM transaction, which forces association) transitions the account into a state (locked coins parked at an address with no future signer/authorization) from which the owner has no path to recover funds, even though the code technically preserves and never destroys the balance.

### Impact Explanation
Coins in the vesting schedule attached to the cast address become permanently unreachable: no future transaction can be authenticated as `castAddr` because (a) it's not derivable from any keypair's canonical bech32 hash, and (b) once EVM address mapping is set, all EVM-signed transactions from that key resolve to `seiAddr`. When the vesting schedule eventually completes and the coins theoretically become "spendable" per `LockedCoins`, nobody can construct a valid transaction to move them, so they are permanently frozen — a genuine "permanent freezing of funds" outcome affecting any ordinary user who happens to receive vesting/locked coins at their EVM address before performing their first association-triggering transaction.

### Likelihood Explanation
Any unprivileged actor can trigger association (it happens automatically on the sender's first EVM transaction, per `EVMPreprocessDecorator.AnteHandle`). The precondition — a vesting/locked account existing at the raw cast address before association — can arise through normal vesting-account creation flows (e.g., airdrops, grants, or `MsgCreateVestingAccount`/gringotts-style flows targeting the pre-association cast address) combined with the user later sending any ordinary EVM transaction. This does not require attacker cooperation from any other party; it's triggered purely by the account owner's own normal usage pattern, making it moderately likely to occur for real users of vesting programs on EVM-derived addresses.

### Recommendation
In `MigrateBalance` (and its legacy counterparts), when the cast address holds a vesting account, either (a) transfer/re-parent the vesting account state itself to `seiAddr` so future vesting release remains addressable by the newly associated identity, or (b) force-migrate the locked balance's underlying vesting schedule metadata alongside the coins so `seiAddr` becomes the account that will unlock them, instead of leaving an unspendable stub account at `castAddr` holding coins that can never again be authorized.

### Proof of Concept
1. Fund `castAddr = sdk.AccAddress(evmAddr[:])` with a `DelayedVestingAccount` holding e.g. 1 `usei` locked until `endTime`, plus 2 spendable `usei`, as in `TestMigrateBalance` [6](#0-5) .
2. Have the corresponding EVM key submit any ordinary transaction; the ante preprocessor calls `AssociateAddresses(ctx, seiAddr, evmAddr, pubkey, false)` [7](#0-6) .
3. `MigrateBalance` moves the 2 spendable `usei` to `seiAddr`, but leaves the 1 locked `usei` and the vesting account at `castAddr` [8](#0-7) , confirmed by post-conditions in `TestMigrateBalance` (locked coin remains at `sdk.AccAddress(evmAddr[:])`, none at `seiAddr`).
4. Once `endTime` passes, the coin becomes nominally "spendable" from `castAddr`'s perspective, but no valid Cosmos or EVM signature can ever be produced for `castAddr` (all further transactions from the owner's EVM key are routed through the `seiAddr` mapping), permanently freezing that balance.

### Citations

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

**File:** utils/helpers/associate_test.go (L265-288)
```go
func TestAssociateAddressesDoesNotReuseVestingCastAccount(t *testing.T) {
	ctx := sdk.Context{}
	evmAddr := common.HexToAddress("0x1111111111111111111111111111111111111111")
	castAddr := sdk.AccAddress(evmAddr[:])
	seiAddr := sdk.AccAddress(common.HexToAddress("0x2222222222222222222222222222222222222222").Bytes())
	pubkey := secp256k1.GenPrivKey().PubKey().(*secp256k1.PubKey)

	ak := &mockAccountKeeper{accounts: map[string]authtypes.AccountI{}}
	baseAcc := authtypes.NewBaseAccount(castAddr, nil, 42, 7)
	ak.SetAccount(ctx, vestingtypes.NewDelayedVestingAccount(baseAcc, sdk.NewCoins(sdk.NewInt64Coin("usei", 100)), 123, nil))
	bk := &mockBankKeeper{}
	ek := &mockEVMKeeper{}

	helper := NewAssociationHelper(ek, bk, ak)
	require.NoError(t, helper.AssociateAddresses(ctx, seiAddr, evmAddr, pubkey, false))

	require.Equal(t, 1, ak.newAccountCalls)
	require.Equal(t, evmAddr, ek.mappings[seiAddr.String()])
	acc := ak.GetAccount(ctx, seiAddr)
	require.NotNil(t, acc)
	require.Zero(t, acc.GetAccountNumber())
	require.Zero(t, acc.GetSequence())
	require.Equal(t, pubkey.Bytes(), acc.GetPubKey().Bytes())
}
```

**File:** x/evm/ante/preprocess.go (L74-101)
```go
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

**File:** x/evm/ante/preprocess_test.go (L423-441)
```go
func TestMigrateBalance(t *testing.T) {
	k := &testkeeper.EVMTestApp.EvmKeeper
	ctx := testkeeper.EVMTestApp.GetContextForDeliverTx(nil)
	admin, _ := testkeeper.MockAddressPair()
	seiAddr, evmAddr := testkeeper.MockAddressPair()
	k.BankKeeper().AddCoins(ctx, sdk.AccAddress(evmAddr[:]), sdk.NewCoins(sdk.NewCoin("usei", sdk.NewInt(2))), false)
	// set a vesting account of 1
	k.AccountKeeper().SetAccount(ctx, vestingtypes.NewDelayedVestingAccountRaw(
		vestingtypes.NewBaseVestingAccount(
			k.AccountKeeper().NewAccountWithAddress(ctx, sdk.AccAddress(evmAddr[:])).(*authtypes.BaseAccount),
			sdk.NewCoins(sdk.NewCoin("usei", sdk.NewInt(1))), math.MaxInt64, admin),
	))
	associateHelper := helpers.NewAssociationHelper(k, k.BankKeeper(), k.AccountKeeper())
	require.Nil(t, associateHelper.MigrateBalance(ctx, evmAddr, seiAddr, false))
	require.Equal(t, int64(1), k.BankKeeper().SpendableCoins(ctx, seiAddr).AmountOf("usei").Int64())
	require.Equal(t, int64(0), k.BankKeeper().LockedCoins(ctx, seiAddr).AmountOf("usei").Int64())
	require.Equal(t, int64(0), k.BankKeeper().SpendableCoins(ctx, sdk.AccAddress(evmAddr[:])).AmountOf("usei").Int64())
	require.Equal(t, int64(1), k.BankKeeper().LockedCoins(ctx, sdk.AccAddress(evmAddr[:])).AmountOf("usei").Int64())
}
```
