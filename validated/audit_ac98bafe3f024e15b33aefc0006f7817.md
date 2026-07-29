[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** precompiles/types/defaults.go (L65-88)
```go
func DefaultStaticPrecompiles(
	stakingKeeper stakingkeeper.Keeper,
	distributionKeeper distributionkeeper.Keeper,
	bankKeeper cmn.BankKeeper,
	erc20Keeper *erc20Keeper.Keeper,
	transferKeeper *transferkeeper.Keeper,
	channelKeeper *channelkeeper.Keeper,
	govKeeper govkeeper.Keeper,
	slashingKeeper slashingkeeper.Keeper,
	codec codec.Codec,
	opts ...Option,
) map[common.Address]vm.PrecompiledContract {
	precompiles := NewStaticPrecompiles().
		WithPraguePrecompiles().
		WithP256Precompile().
		WithBech32Precompile().
		WithStakingPrecompile(stakingKeeper, bankKeeper, opts...).
		WithDistributionPrecompile(distributionKeeper, stakingKeeper, bankKeeper, opts...).
		WithICS20Precompile(bankKeeper, stakingKeeper, transferKeeper, channelKeeper).
		WithBankPrecompile(bankKeeper, erc20Keeper).
		WithGovPrecompile(govKeeper, bankKeeper, codec, opts...).
		WithSlashingPrecompile(slashingKeeper, bankKeeper, opts...)

	return map[common.Address]vm.PrecompiledContract(precompiles)
```

**File:** precompiles/ics20/ics20.go (L40-71)
```go
type Precompile struct {
	cmn.Precompile

	abi.ABI
	bankKeeper     cmn.BankKeeper
	stakingKeeper  cmn.StakingKeeper
	transferKeeper cmn.TransferKeeper
	channelKeeper  cmn.ChannelKeeper
}

// NewPrecompile creates a new ICS-20 Precompile instance as a
// PrecompiledContract interface.
func NewPrecompile(
	bankKeeper cmn.BankKeeper,
	stakingKeeper cmn.StakingKeeper,
	transferKeeper cmn.TransferKeeper,
	channelKeeper cmn.ChannelKeeper,
) *Precompile {
	return &Precompile{
		Precompile: cmn.Precompile{
			KvGasConfig:           storetypes.KVGasConfig(),
			TransientKVGasConfig:  storetypes.TransientGasConfig(),
			ContractAddress:       common.HexToAddress(evmtypes.ICS20PrecompileAddress),
			BalanceHandlerFactory: cmn.NewBalanceHandlerFactory(bankKeeper),
		},
		ABI:            ABI,
		bankKeeper:     bankKeeper,
		transferKeeper: transferKeeper,
		channelKeeper:  channelKeeper,
		stakingKeeper:  stakingKeeper,
	}
}
```
