### Title
Permissionless, fee-less `tokenfactory` denom creation permits unbounded, permanent storage bloat - (File: x/tokenfactory/keeper/createdenom.go)

### Summary
`MsgCreateDenom` lets any account create a `factory/{creator}/{subdenom}` denom for the price of a normal transaction fee. Unlike the HydraDX Omnipool case — where a bloating attacker at least had to lock `MinimumPoolLiquidity` and could shrink (but not fully remove) a position — the `x/tokenfactory` module has **no minimum value/deposit requirement at all** for creating a denom, and **no message or code path exists to delete a denom** once created. Every call permanently writes multiple new KV-store entries for the module and the bank module, at a fixed, arbitrarily-cheap gas cost, unbounded per creator.

### Finding Description
`CreateDenom` validates only that the subdenom string is well-formed and not already used, then unconditionally persists three separate pieces of permanent state: [1](#0-0) 

```
func (k Keeper) CreateDenom(ctx sdk.Context, creatorAddr string, subdenom string) (newTokenDenom string, err error) {
	denom, err := k.validateCreateDenom(ctx, creatorAddr, subdenom)
	...
	err = k.createDenomAfterValidation(ctx, creatorAddr, denom)
	return denom, err
}
```

`createDenomAfterValidation` writes bank denom metadata, tokenfactory authority metadata, and a creator-index entry, unconditionally: [2](#0-1) 

There is no economic floor comparable to HydraDX's `MinimumPoolLiquidity`: `validateCreateDenom` only checks for name collisions and IBC-denom collisions, not any minimum mint/stake/bond: [3](#0-2) 

Historically the module *did* have a denom-creation fee, but it was explicitly removed in a store migration and the fee parameter deleted, leaving `Params` with only an unrelated allow-list size field: [4](#0-3) [5](#0-4) 

Critically, there is no `MsgDeleteDenom` / `RemoveDenom` anywhere in the module (confirmed by search), so denom metadata and authority-metadata entries created this way are **permanent** for the life of the chain — strictly worse than the Omnipool case, where at least a dust-sized position (not a fully persistent, undeletable entry) was possible.

### Impact Explanation
An unprivileged transaction sender can call `MsgCreateDenom` repeatedly with unique subdenoms (bounded only by the 44-byte subdenom length limit, giving an enormous namespace), each time permanently adding entries to:
- `x/bank` `DenomMetaData` store,
- `x/tokenfactory` `DenomAuthorityMetadata` store,
- `x/tokenfactory` creator-prefix index store.

None of these entries can ever be reclaimed. Because there is no minimum value requirement and no cleanup path, the cost to bloat state is only the transaction fee for a `MsgCreateDenom` (observed at ~2,000–40,000 usei in the test fixtures), independent of any value locked. Sustained abuse increases IAVL/state size indefinitely, degrading state-sync, snapshot, pruning, and full-node disk/memory costs over time — a state-growth DoS vector analogous to, and less mitigated than, the referenced Omnipool dust-position issue.

### Likelihood Explanation
Likelihood is high: `MsgCreateDenom` is a normal, permissionless message reachable by any account with a minimal fee balance; no governance, validator, or privileged role is required, and the attack is trivially scriptable (loop of `create-denom` calls with incrementing subdenoms).

### Recommendation
Reintroduce an economically meaningful denom-creation cost (e.g., a `DenomCreationFee` param burned/sent to the community pool, as in the original design before `Migrate2to3` removed it) and/or a per-creator denom cap, and consider adding a `MsgDeleteDenom` path allowing a denom with zero supply and no admin activity to be pruned, mirroring the recommended fix pattern from the referenced finding (enforce a floor, and allow full removal only when genuinely abandoned).

### Proof of Concept
1. Fund an account with a small `usei` balance (only fee funds needed, no minimum deposit).
2. Loop: submit `MsgCreateDenom{Sender: attacker, Subdenom: fmt.Sprintf("d%d", i)}` for `i` in a large range, paying only the standard tx fee each time (see the load-test client and CLI fixture, which show these calls costing ~2,000–40,000 usei with no value floor): [6](#0-5) [7](#0-6) 
3. Query `denoms-from-creator` to confirm each call creates a new permanent entry (and confirm no burn/delete-denom message exists to reverse it).
4. Repeating this at scale permanently grows `x/bank` and `x/tokenfactory` KV-store size with no way to reclaim it, at negligible per-entry cost.

### Citations

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

**File:** x/tokenfactory/keeper/createdenom.go (L25-50)
```go
func (k Keeper) createDenomAfterValidation(ctx sdk.Context, creatorAddr string, denom string) (err error) {
	denomMetaData := banktypes.Metadata{
		DenomUnits: []*banktypes.DenomUnit{{
			Denom:    denom,
			Exponent: 0,
		}},
		Base: denom,
		// The following is necessary for x/bank denom validation
		Display: denom,
		Name:    denom,
		Symbol:  denom,
	}

	k.bankKeeper.SetDenomMetaData(ctx, denomMetaData)

	authorityMetadata := types.DenomAuthorityMetadata{
		Admin: creatorAddr,
	}
	err = k.setAuthorityMetadata(ctx, denom, authorityMetadata)
	if err != nil {
		return err
	}

	k.addDenomFromCreator(ctx, creatorAddr, denom)
	return nil
}
```

**File:** x/tokenfactory/keeper/createdenom.go (L52-70)
```go
func (k Keeper) validateCreateDenom(ctx sdk.Context, creatorAddr string, subdenom string) (newTokenDenom string, err error) {
	// Temporary check until IBC bug is sorted out
	if k.bankKeeper.HasSupply(ctx, subdenom) {
		return "", fmt.Errorf("temporary error until IBC bug is sorted out, " +
			"can't create subdenoms that are the same as a native denom")
	}

	denom, err := types.GetTokenDenom(creatorAddr, subdenom)
	if err != nil {
		return "", err
	}

	_, found := k.bankKeeper.GetDenomMetaData(ctx, denom)
	if found {
		return "", types.ErrDenomExists
	}

	return denom, nil
}
```

**File:** x/tokenfactory/keeper/migrations.go (L25-42)
```go
// Migrate2to3 migrates from version 2 to 3.
func (m Migrator) Migrate2to3(ctx sdk.Context) error {
	// Reset params after removing the denom creation fee param
	defaultParams := types.Params{}
	m.keeper.paramSpace.SetParamSet(ctx, &defaultParams)

	// We remove the denom creation fee whitelist in this migration
	store := ctx.KVStore(m.keeper.storeKey)
	oldCreateDenomFeeWhitelistKey := "createdenomfeewhitelist"

	oldCreateDenomFeeWhitelistPrefix := []byte(strings.Join([]string{oldCreateDenomFeeWhitelistKey, ""}, KeySeparator))
	iter := sdk.KVStorePrefixIterator(store, oldCreateDenomFeeWhitelistPrefix)
	defer func() { _ = iter.Close() }()
	for ; iter.Valid(); iter.Next() {
		store.Delete(iter.Key())
	}
	return nil
}
```

**File:** x/tokenfactory/types/params.go (L17-22)
```go
// DefaultParams default tokenfactory module parameters.
func DefaultParams() Params {
	return Params{
		DenomAllowlistMaxSize: DefaultDenomAllowListMaxSize,
	}
}
```

**File:** loadtest/main.go (L416-429)
```go
	case Tokenfactory:
		denomCreatorAddr := sdk.AccAddress(key.PubKey().Address()).String()
		// No denoms, let's mint
		randNum := r.Float64()
		denom, ok := c.TokenFactoryDenomOwner[denomCreatorAddr]
		switch {
		case !ok || randNum <= 0.33:
			subDenom := fmt.Sprintf("tokenfactory-created-denom-%d", time.Now().UnixMilli())
			denom = fmt.Sprintf("factory/%s/%s", denomCreatorAddr, subDenom)
			msgs = []sdk.Msg{&tokenfactorytypes.MsgCreateDenom{
				Sender:   denomCreatorAddr,
				Subdenom: subDenom,
			}}
			c.TokenFactoryDenomOwner[denomCreatorAddr] = denom
```

**File:** integration_test/tokenfactory_module/create_tokenfactory_test.yaml (L1-13)
```yaml
- name: Test creating a denom
  inputs:
    # Get admin
    - cmd: printf "12345678\n" | seid keys list --output json | jq ".[] | select (.name==\"admin\")" | jq -r .address
      env: ADMIN_ADDR
    # create new admin addr
    - cmd: printf "12345678\ny\n" | seid keys add new_admin_addr --output json | jq -r ".address"
      env: NEW_ADMIN_ADDR
    # create uuid for tokenfactory denom
    - cmd: uuidgen
      env: TKF_UUID
    # Create denom
    - cmd: printf "12345678\n" | seid tx tokenfactory create-denom $TKF_UUID --from admin --fees 2000usei -y -b block
```
