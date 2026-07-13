### Title
Missing `Preinstalls` Export in `ExportGenesis` Causes Permanent Loss of Preinstall Registry After Chain Upgrade - (File: x/evm/genesis.go)

### Summary

`ExportGenesis` in `x/evm/genesis.go` returns a `GenesisState` with only `Accounts` and `Params`, silently omitting the `Preinstalls` field that is a first-class member of `GenesisState`. After any chain upgrade that uses genesis export/import, the `Preinstalls` list is permanently zeroed out in the new chain's genesis, corrupting the EVM module's committed state.

### Finding Description

`GenesisState` is defined with three fields:

```go
type GenesisState struct {
    Accounts    []GenesisAccount `protobuf:"bytes,1,..."`
    Params      Params           `protobuf:"bytes,2,..."`
    Preinstalls []Preinstall     `protobuf:"bytes,3,..."`
}
``` [1](#0-0) 

`InitGenesis` correctly processes all three fields, including deploying preinstalls via `k.AddPreinstalls`: [2](#0-1) 

However, `ExportGenesis` only populates two of the three fields:

```go
return &types.GenesisState{
    Accounts: ethGenAccounts,
    Params:   k.GetParams(ctx),
    // Preinstalls: ← MISSING
}
``` [3](#0-2) 

`DefaultPreinstalls` includes five critical system contracts: Create2 factory, Multicall3, Permit2, Safe singleton factory, and the EIP-2935 history storage contract (`params.HistoryStorageAddress`): [4](#0-3) 

The example app explicitly sets these preinstalls in genesis: [5](#0-4) 

### Impact Explanation

After a chain upgrade (genesis export → `InitChain` on new chain):

1. `ExportGenesis` emits `Preinstalls: nil`. The exported genesis JSON contains no preinstall entries.
2. On the new chain, `InitGenesis` calls `k.AddPreinstalls(ctx, data.Preinstalls)` with an empty slice — no preinstalls are re-registered through the preinstall path.
3. The preinstall accounts' bytecode is still captured in the `Accounts` list (since `ExportGenesis` iterates all `EthAccountI` accounts), so the contracts remain callable. However, the authoritative `Preinstalls` registry is permanently zeroed.
4. Any subsequent governance `MsgRegisterPreinstalls` targeting an existing preinstall address will be rejected by `AddPreinstalls` with `ErrInvalidPreinstall` ("already has a codehash"), permanently blocking re-registration or replacement of preinstall contracts via the intended governance path.
5. The EIP-2935 history storage contract (`params.HistoryStorageAddress`) is among the lost preinstalls. `GetHeaderHash` checks `acct != nil && acct.IsContract()` to decide whether to use the EIP-2935 path; if the account's code hash is inconsistently set after re-import, block hash serving behavior diverges from pre-upgrade, risking consensus nondeterminism. [6](#0-5) [7](#0-6) 

### Likelihood Explanation

Every chain upgrade that uses `ExportGenesis` (the standard Cosmos SDK upgrade path) triggers this bug. No attacker action is required — the bug fires automatically on any upgrade. The `evmd` reference app explicitly populates `DefaultPreinstalls` at genesis, making this a guaranteed regression on the first upgrade.

### Recommendation

Add `Preinstalls` to the returned `GenesisState` in `ExportGenesis`. Since the keeper does not maintain a separate KV-store list of preinstalls, the preinstall list must be reconstructed from the accounts that were deployed as preinstalls, or a dedicated store key must be added. The simplest fix is to store the preinstall list in the keeper's KV store during `AddPreinstalls` and read it back in `ExportGenesis`:

```go
return &types.GenesisState{
    Accounts:    ethGenAccounts,
    Params:      k.GetParams(ctx),
    Preinstalls: k.GetPreinstalls(ctx), // new keeper method
}
``` [8](#0-7) 

### Proof of Concept

1. Start a chain with `NewEVMGenesisState()` (sets `DefaultPreinstalls`).
2. Call `ExportGenesis` — inspect the returned `GenesisState.Preinstalls`: it is `nil`/empty.
3. Feed the exported genesis into `InitGenesis` on a new chain.
4. `AddPreinstalls` is called with an empty slice; no preinstalls are deployed through the preinstall path.
5. Submit a governance `MsgRegisterPreinstalls` for `0x4e59b44847b379578588920ca78fbf26c0b4956c` (Create2): rejected with "already has a codehash" because the account was imported via `Accounts` but not via the preinstall path.
6. The `Preinstalls` field remains empty in all subsequent exports, permanently corrupting the module's genesis state. [4](#0-3) [9](#0-8)

### Citations

**File:** x/evm/types/genesis.pb.go (L28-35)
```go
type GenesisState struct {
	// accounts is an array containing the ethereum genesis accounts.
	Accounts []GenesisAccount `protobuf:"bytes,1,rep,name=accounts,proto3" json:"accounts"`
	// params defines all the parameters of the module.
	Params Params `protobuf:"bytes,2,opt,name=params,proto3" json:"params"`
	// preinstalls defines a set of predefined contracts
	Preinstalls []Preinstall `protobuf:"bytes,3,rep,name=preinstalls,proto3" json:"preinstalls"`
}
```

**File:** x/evm/genesis.go (L85-87)
```go
	if err := k.AddPreinstalls(ctx, data.Preinstalls); err != nil {
		panic(fmt.Errorf("error adding preinstalls: %s", err))
	}
```

**File:** x/evm/genesis.go (L92-119)
```go
// ExportGenesis exports genesis state of the EVM module
func ExportGenesis(ctx sdk.Context, k *keeper.Keeper, ak types.AccountKeeper) *types.GenesisState {
	var ethGenAccounts []types.GenesisAccount
	ak.IterateAccounts(ctx, func(account sdk.AccountI) bool {
		ethAccount, ok := account.(ethermint.EthAccountI)
		if !ok {
			// ignore non EthAccounts
			return false
		}

		addr := ethAccount.EthAddress()

		storage := k.GetAccountStorage(ctx, addr)

		genAccount := types.GenesisAccount{
			Address: addr.String(),
			Code:    common.Bytes2Hex(k.GetCode(ctx, ethAccount.GetCodeHash())),
			Storage: storage,
		}

		ethGenAccounts = append(ethGenAccounts, genAccount)
		return false
	})

	return &types.GenesisState{
		Accounts: ethGenAccounts,
		Params:   k.GetParams(ctx),
	}
```

**File:** x/evm/types/preinstall.go (L13-39)
```go
var DefaultPreinstalls = []Preinstall{
	{
		Name:    "Create2",
		Address: "0x4e59b44847b379578588920ca78fbf26c0b4956c",
		Code:    "0x7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffe03601600081602082378035828234f58015156039578182fd5b8082525050506014600cf3",
	},
	{
		Name:    "Multicall3",
		Address: "0xcA11bde05977b3631167028862bE2a173976CA11",
		Code:    "0x6080604052600436106100f35760003560e01c80634d2301cc1161008a578063a8b0574e11610059578063a8b0574e1461025a578063bce38bd714610275578063c3077fa914610288578063ee82ac5e1461029b57600080fd5b80634d2301cc146101ec57806372425d9d1461022157806382ad56cb1461023457806386d516e81461024757600080fd5b80633408e470116100c65780633408e47014610191578063399542e9146101a45780633e64a696146101c657806342cbb15c146101d957600080fd5b80630f28c97d146100f8578063174dea711461011a578063252dba421461013a57806327e86d6e1461015b575b600080fd5b34801561010457600080fd5b50425b6040519081526020015b60405180910390f35b61012d610128366004610a85565b6102ba565b6040516101119190610bbe565b61014d610148366004610a85565b6104ef565b604051610111929190610bd8565b34801561016757600080fd5b50437fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff ... (truncated)
	},
	{
		Name:    "Permit2",
		Address: "0x000000000022D473030F116dDEE9F6B43aC78BA3",
		Code:    "0x6040608081526004908136101561001557600080fd5b600090813560e01c80630d58b1db1461126c578063137c29fe146110755780632a2d80d114610db75780632b67b57014610bde57806330f28b7a14610ade5780633644e51514610a9d57806336c7851614610a285780633ff9dcb1146109a85780634fe02b441461093f57806365d9723c146107ac57806387517c451461067a578063927da105146105c3578063cc53287f146104a3578063edd9444b1461033a5763fe8ec1a7146100c657600080fd5b346103365760c07ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffc3601126103365767ffffffffffffffff833581811161033257610114903690860161164b565b60243582811161032e5761012b903690870161161a565b6101336114e6565b9160843585811161032a5761014b9036908a016115c1565b98909560a43590811161032657610164913691016115c1565b969095815190610173826113ff565b606b82527f5065726d697442617463685769746e65 ... (truncated)
	},
	{
		Name:    "Safe singleton factory",
		Address: "0x914d7Fec6aaC8cd542e72Bca78B30650d45643d7",
		Code:    "0x7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffe03601600081602082378035828234f58015156039578182fd5b8082525050506014600cf3",
	},
	{
		Name:    "EIP-2935 - Serve historical block hashes from state",
		Address: params.HistoryStorageAddress.String(),
		Code:    common.Bytes2Hex(params.HistoryStorageCode),
	},
}
```

**File:** evmd/genesis.go (L22-26)
```go
func NewEVMGenesisState() *evmtypes.GenesisState {
	evmGenState := evmtypes.DefaultGenesisState()
	evmGenState.Preinstalls = evmtypes.DefaultPreinstalls

	return evmGenState
```

**File:** x/evm/keeper/keeper.go (L374-401)
```go
func (k Keeper) GetHeaderHash(ctx sdk.Context, height uint64) common.Hash {
	// check if history contract has been deployed
	acct := k.GetAccount(ctx, ethparams.HistoryStorageAddress)
	if acct != nil && acct.IsContract() {
		window := types.DefaultHistoryServeWindow
		params := k.GetParams(ctx)
		if params.HistoryServeWindow > 0 {
			window = params.HistoryServeWindow
		}

		ringIndex := height % window
		var key common.Hash
		binary.BigEndian.PutUint64(key[24:], ringIndex)
		hash := k.GetState(ctx, ethparams.HistoryStorageAddress, key)

		if hash.Cmp(common.Hash{}) != 0 {
			return hash
		}
	}
	// fall back to old behavior for retro compatibility
	// TODO can be removed along with DeleteHeaderHash once HistoryStorage has been filled up in next protocol upgrade
	store := ctx.KVStore(k.storeKey)
	hashByte := store.Get(types.GetHeaderHashKey(height))
	if len(hashByte) > 0 {
		return common.BytesToHash(hashByte)
	}
	return common.Hash{}
}
```

**File:** x/evm/keeper/keeper.go (L409-471)
```go
func (k *Keeper) AddPreinstalls(ctx sdk.Context, preinstalls []types.Preinstall) error {
	for _, preinstall := range preinstalls {
		address := common.HexToAddress(preinstall.Address)
		accAddress := sdk.AccAddress(address.Bytes())

		if len(preinstall.Code) == 0 {
			return errorsmod.Wrapf(types.ErrInvalidPreinstall,
				"preinstall %s, address %s has no code", preinstall.Name, preinstall.Address)
		}

		// check that the address does not conflict with the precompiles
		cfg, err := k.EVMBlockConfig(ctx, k.ChainID())
		if err != nil {
			return err
		}
		for _, fn := range k.customContractFns {
			c := fn(ctx, cfg.Rules)
			if address == c.Address() {
				return errorsmod.Wrapf(types.ErrInvalidPreinstall,
					"preinstall %s, address %s already exists as a precompile", preinstall.Name, preinstall.Address)
			}
		}

		codeHash := crypto.Keccak256Hash(common.FromHex(preinstall.Code))
		codeHashBytes := codeHash.Bytes()
		if types.IsEmptyCodeHash(codeHashBytes) {
			k.Logger(ctx).Error("preinstall has empty code hash",
				"preinstall address", preinstall.Address)
			return errorsmod.Wrapf(types.ErrInvalidPreinstall,
				"preinstall %s, address %s has empty code hash", preinstall.Name, preinstall.Address)
		}

		acct := k.accountKeeper.GetAccount(ctx, accAddress)
		if acct == nil {
			// create account with the account keeper
			acct = k.accountKeeper.NewAccountWithAddress(ctx, accAddress)
		}

		if ethAcct, ok := acct.(ethermint.EthAccountI); ok {
			// check that code hash and nonce is empty
			if !types.IsEmptyCodeHash(ethAcct.GetCodeHash().Bytes()) {
				return errorsmod.Wrapf(types.ErrInvalidPreinstall,
					"preinstall %s, address %s already has a codehash", preinstall.Name, preinstall.Address)
			}
			if ethAcct.GetSequence() != 0 {
				return errorsmod.Wrapf(types.ErrInvalidPreinstall,
					"preinstall %s, address %s already has a sequence", preinstall.Name, preinstall.Address)
			}

			// set code hash
			if err := ethAcct.SetCodeHash(codeHash); err != nil {
				return err
			}
			k.accountKeeper.SetAccount(ctx, acct)
			k.SetCode(ctx, codeHashBytes, common.FromHex(preinstall.Code))
		} else {
			return errorsmod.Wrapf(types.ErrInvalidAccount,
				"account %s is not an EthAccount", accAddress.String())
		}

		// We are not setting any storage for preinstalls, so we skip that step.
	}
	return nil
```

**File:** x/evm/keeper/msg_server.go (L156-173)
```go
// RegisterPreinstalls implements the gRPC MsgServer interface. When a RegisterPreinstalls
// proposal passes, it creates the preinstalls. The registration can only be
// performed if the requested authority is the Cosmos SDK governance module
// account.
func (k *Keeper) RegisterPreinstalls(goCtx context.Context, req *types.MsgRegisterPreinstalls) (*types.
	MsgRegisterPreinstallsResponse, error,
) {
	if k.authority.String() != req.Authority {
		return nil, errorsmod.Wrapf(govtypes.ErrInvalidSigner, "invalid authority, expected %s, got %s", k.authority.String(), req.Authority)
	}

	ctx := sdk.UnwrapSDKContext(goCtx)
	if err := k.AddPreinstalls(ctx, req.Preinstalls); err != nil {
		return nil, err
	}

	return &types.MsgRegisterPreinstallsResponse{}, nil
}
```
