Based on my research, I found a concrete analog in this repository.

### Title
`BlockedAddresses()` omits dynamically-registered ERC20/WERC20 precompile addresses, allowing user funds to be permanently locked - ([File: config/evmd_config.go])

### Summary
The external report describes `Harvester.rewardTokens()` returning a hardcoded, incomplete list of token addresses (missing LDO), causing value to be silently dropped from an otherwise-automatic collection flow. The same "hardcoded/incomplete address list" pattern exists in `config.BlockedAddresses()` [1](#0-0) , which is used by the bank module to decide which addresses may never receive coins via `bank.MsgSend`/`SendCoins`.

### Finding Description
`BlockedAddresses()` builds its blocklist from only two hardcoded sources: the static `maccPerms` module accounts and `evmtypes.AvailableStaticPrecompiles` (plus go-ethereum's Prague precompiles) [2](#0-1) . It never queries the `x/erc20` keeper's dynamically registered `NativePrecompiles` / `DynamicPrecompiles` stores [3](#0-2) , which include the WERC20 precompile for the native token (e.g. `WEVMOSContractMainnet`, set at genesis via `EnableNativePrecompile`) [4](#0-3)  and every IBC-token ERC20 extension registered later via governance (`RegisterERC20Extension`) [5](#0-4) .

These precompile addresses are stateless EVM extensions with no private key and no logic to recover an ordinary bank-level coin transfer: the ERC20 precompile only guards against EVM `msg.value` transfers (`contract.Value()` check in `Execute`) [6](#0-5) , and the WERC20 precompile's `deposit()`/`withdraw()` logic is only invoked through EVM calls, not through a plain Cosmos `MsgSend` [7](#0-6) . Because these addresses are absent from `BlockedAddresses()`, a plain `bank.MsgSend` to the bech32 form of a dynamic/native precompile address is not rejected by `x/bank`'s `BlockedAddr` check (the same check that protects `x/precisebank`'s reserve account, as shown by `TestBlockedRecipient`) [8](#0-7) . Coins sent this way land in the precompile's underlying bank balance with no mechanism to mint a corresponding ERC20/wrapped balance to any account and no way for the precompile to move them back out, since the precompile only acts on `MsgEthereumTx` calls it receives, not on unsolicited bank transfers.

### Impact Explanation
Funds sent via ordinary `MsgSend` to a dynamic ERC20 or native WERC20 precompile address become permanently inaccessible: no account holds a claim on them (no ERC20 mint occurs), and the precompile itself cannot originate a `MsgSend`/`withdraw` in response to funds it never "sees" via an EVM call. This matches the "Critical permanent freezing/locking of user funds" impact category, since it is triggerable in ordinary transaction flow by any unprivileged user without any attacker interaction with a victim required beyond knowing/deriving the precompile's bech32 address (which is public/derivable from the token-pair registry).

### Likelihood Explanation
Every chain running this stack registers at least one native precompile (the wrapped native token, e.g. WEVMOS) at genesis and typically registers additional dynamic precompiles for IBC-token pairs over time via governance/permissionless registration. Any of these addresses is a valid, well-known target (`GetDynamicPrecompiles`/`GetNativePrecompiles` are queryable) [9](#0-8) , so the precondition (a live registered precompile address not in `BlockedAddresses()`) is met on essentially every deployment, and the trigger is a single ordinary `MsgSend`.

### Recommendation
Update `config.BlockedAddresses()` (and the equivalent duplicated helper in test utilities) to also block all currently registered `x/erc20` `NativePrecompiles` and `DynamicPrecompiles` addresses by querying the ERC20 keeper (or by keeping the blocklist derived dynamically instead of via a hardcoded snapshot), and/or enforce the block via the `x/erc20`/`x/bank` `BlockedAddr` hook so that newly registered token pairs are automatically protected without requiring `BlockedAddresses()` (evaluated once at app wiring time) to be re-derived.

### Proof of Concept
1. Start a chain with the default genesis, which registers the WEVMOS/WERC20 native precompile via `EnableNativePrecompile` [10](#0-9) .
2. Compute the bech32 account address corresponding to the WERC20 precompile's hex address (`cosmosevmutils.Bech32StringFromHexAddress`).
3. Confirm this address is absent from `app.BlockedAddresses()` — verify it is not derived from `maccPerms` or `evmtypes.AvailableStaticPrecompiles` [2](#0-1) .
4. From any funded account, submit `banktypes.MsgSend` sending native coins to that bech32 address. The transaction succeeds because `BlockedAddr` returns false for this address (contrast with the precisebank reserve account, which is blocked and rejected, per `TestBlockedRecipient`) [8](#0-7) .
5. Observe that no ERC20/WERC20 balance is minted to the sender or any account, and there is no message or precompile call path that can move these coins back out — the funds are permanently stuck.

### Citations

**File:** config/evmd_config.go (L52-82)
```go
// BlockedAddresses returns all the app's blocked account addresses.
//
// Note, this includes:
//   - module accounts
//   - Ethereum's native precompiled smart contracts
//   - Cosmos EVM' available static precompiled contracts
func BlockedAddresses() map[string]bool {
	blockedAddrs := make(map[string]bool)

	maccPerms := GetMaccPerms()
	accs := make([]string, 0, len(maccPerms))
	for acc := range maccPerms {
		accs = append(accs, acc)
	}
	sort.Strings(accs)

	for _, acc := range accs {
		blockedAddrs[authtypes.NewModuleAddress(acc).String()] = true
	}

	blockedPrecompilesHex := evmtypes.AvailableStaticPrecompiles
	for _, addr := range corevm.PrecompiledAddressesPrague {
		blockedPrecompilesHex = append(blockedPrecompilesHex, addr.Hex())
	}

	for _, precompile := range blockedPrecompilesHex {
		blockedAddrs[cosmosevmutils.Bech32StringFromHexAddress(precompile)] = true
	}

	return blockedAddrs
}
```

**File:** x/erc20/keeper/precompiles.go (L92-140)
```go
// EnableNativePrecompile adds the address of the given precompile to the prefix store
func (k Keeper) EnableNativePrecompile(ctx sdk.Context, addr common.Address) error {
	k.Logger(ctx).Info("Added new precompiles", "addresses", addr)
	if err := k.RegisterCodeHash(ctx, addr, PrecompileTypeNative); err != nil {
		return err
	}
	k.SetNativePrecompile(ctx, addr)
	return nil
}

// Only to be used by ExportGenesis, not to be directly used
func (k Keeper) GetNativePrecompiles(ctx sdk.Context) []string {
	iterator := storetypes.KVStorePrefixIterator(ctx.KVStore(k.storeKey), types.KeyPrefixNativePrecompiles)
	defer iterator.Close()

	nps := make([]string, 0)
	for ; iterator.Valid(); iterator.Next() {
		key := iterator.Key()[len(types.KeyPrefixNativePrecompiles):]
		nps = append(nps, string(key))
	}

	slices.Sort(nps)
	return nps
}

func (k Keeper) IsNativePrecompileAvailable(ctx sdk.Context, precompile common.Address) bool {
	store := prefix.NewStore(ctx.KVStore(k.storeKey), types.KeyPrefixNativePrecompiles)
	return store.Has([]byte(precompile.Hex()))
}

func (k Keeper) SetNativePrecompile(ctx sdk.Context, precompile common.Address) {
	store := prefix.NewStore(ctx.KVStore(k.storeKey), types.KeyPrefixNativePrecompiles)
	store.Set([]byte(precompile.Hex()), isTrue)
}

func (k Keeper) DeleteNativePrecompile(ctx sdk.Context, precompile common.Address) {
	store := prefix.NewStore(ctx.KVStore(k.storeKey), types.KeyPrefixNativePrecompiles)
	store.Delete([]byte(precompile.Hex()))
}

// EnableDynamicPrecompile adds the address of the given precompile to the prefix store
func (k Keeper) EnableDynamicPrecompile(ctx sdk.Context, address common.Address) error {
	k.Logger(ctx).Info("Added new precompiles", "addresses", address)
	if err := k.RegisterCodeHash(ctx, address, PrecompileTypeDynamic); err != nil {
		return err
	}
	k.SetDynamicPrecompile(ctx, address)
	return nil
}
```

**File:** x/erc20/genesis.go (L40-49)
```go
	for _, precompile := range data.NativePrecompiles {
		if err := k.EnableNativePrecompile(ctx, common.HexToAddress(precompile)); err != nil {
			panic(fmt.Errorf("error registering native precompiles %s", err))
		}
	}
	for _, precompile := range data.DynamicPrecompiles {
		if err := k.EnableDynamicPrecompile(ctx, common.HexToAddress(precompile)); err != nil {
			panic(fmt.Errorf("error registering dynamic precompiles %s", err))
		}
	}
```

**File:** x/erc20/keeper/dynamic_precompiles.go (L19-31)
```go
func (k Keeper) RegisterERC20Extension(ctx sdk.Context, denom string) (*types.TokenPair, error) {
	pair, err := k.CreateNewTokenPair(ctx, denom)
	if err != nil {
		return nil, err
	}

	// Add to existing EVM extensions
	if err := k.EnableDynamicPrecompile(ctx, pair.GetERC20Contract()); err != nil {
		return nil, err
	}

	return &pair, err
}
```

**File:** precompiles/erc20/erc20.go (L148-156)
```go
func (p Precompile) Execute(ctx sdk.Context, stateDB vm.StateDB, contract *vm.Contract, readOnly bool) ([]byte, error) {
	// ERC20 precompiles cannot receive funds because they are not managed by an
	// EOA and will not be possible to recover funds sent to an instance of
	// them.This check is a safety measure because currently funds cannot be
	// received due to the lack of a fallback handler.
	if value := contract.Value(); value.Sign() == 1 {
		return nil, fmt.Errorf(ErrCannotReceiveFunds, contract.Value().String())
	}

```

**File:** precompiles/werc20/tx.go (L26-57)
```go
// Deposit handles the payable deposit function. It retrieves the deposited amount
// and sends it back to the sender using the bank keeper.
func (p Precompile) Deposit(
	ctx sdk.Context,
	contract *vm.Contract,
	stateDB vm.StateDB,
) ([]byte, error) {
	caller := contract.Caller()
	depositedAmount := contract.Value()

	callerAccAddress := sdk.AccAddress(caller.Bytes())
	precompileAccAddr := sdk.AccAddress(p.Address().Bytes())

	// Send the coins back to the sender
	if err := p.BankKeeper.SendCoins(
		ctx,
		precompileAccAddr,
		callerAccAddress,
		sdk.NewCoins(sdk.Coin{
			Denom:  evmtypes.GetEVMCoinExtendedDenom(),
			Amount: math.NewIntFromBigInt(depositedAmount.ToBig()),
		}),
	); err != nil {
		return nil, err
	}

	if err := p.EmitDepositEvent(ctx, stateDB, caller, depositedAmount.ToBig()); err != nil {
		return nil, err
	}

	return nil, nil
}
```

**File:** tests/integration/x/precisebank/test_mint_integration.go (L23-44)
```go
func (s *KeeperIntegrationTestSuite) TestBlockedRecipient() {
	// Tests that sending funds to x/precisebank is disallowed.
	// x/precisebank balance is used as the reserve funds and should not be
	// directly interacted with by external modules or users.
	msgServer := bankkeeper.NewMsgServerImpl(s.network.App.GetBankKeeper())

	fromAddr := sdk.AccAddress{1}

	// To x/precisebank
	toAddr := s.network.App.GetAccountKeeper().GetModuleAddress(types.ModuleName)
	amount := cs(c(types.IntegerCoinDenom(), 1000))

	msg := banktypes.NewMsgSend(fromAddr, toAddr, amount)

	_, err := msgServer.Send(s.network.GetContext(), msg)
	s.Require().Error(err)

	s.Require().EqualError(
		err,
		fmt.Sprintf("%s is not allowed to receive funds: unauthorized", toAddr.String()),
	)
}
```
