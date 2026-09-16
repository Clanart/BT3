### Title
`AddressBookV2` (Kaia's UUPS-upgradeable system contract) bakes `epochBlockInterval` as an immutable constructor argument, causing epoch-boundary state divergence on implementation upgrade - (File: `blockchain/system/permissionless.go`, `blockchain/system/addressbook_v2.go`)

### Summary
`AddressBookV2` is deployed behind an ERC1967 UUPS proxy at the fixed system address `0x400` [1](#0-0)  and exposes `upgradeTo`/`upgradeToAndCall` (standard UUPS upgrade entry points) [2](#0-1) . However, its implementation contract is deployed with `epochBlockInterval` passed as a **constructor argument**, which OpenZeppelin's UUPS pattern implies is stored as an `immutable` baked into the implementation bytecode rather than proxy storage:

```go
abv2ImplInput, err := packConstructor(abv2ABI, common.FromHex(addressbookv2contract.AddressBookV2Bin), big.NewInt(epochBlockInterval))
``` [3](#0-2) 

This mirrors the reported `OrderBook` anti-pattern exactly: an upgradeable (proxy-based) contract using a constructor + immutable state variable, whose value silently resets/diverges on any future implementation upgrade that doesn't re-supply the identical constructor argument.

### Finding Description
`EpochBlockInterval()` is read directly off the proxy at `AddressBookAddr` (`0x400`) via `ReadABv2EpochBlockInterval` [4](#0-3) , and this value feeds `kaiax/valset/impl/transition.go`'s epoch-boundary/validator-set transition logic. Because `epochBlockInterval` is fixed into the implementation bytecode at construction time (as an immutable, following the same anti-pattern flagged in the `OrderBook` report), any subsequent UUPS upgrade of the `AddressBookV2` implementation (via `upgradeTo`/`upgradeToAndCall`, exposed on every deployed instance [5](#0-4) ) that is compiled/deployed with a different (or default/zero) `epochBlockInterval` constructor value will cause the proxy to silently return a different epoch interval than before the upgrade — exactly as demonstrated in the external report's PoC where `clearingHouse`/`marginAccount` reset to `0` after upgrading to a new implementation. `blockchain/system/permissionless.go` even shows a fallback default is substituted when the configured interval is zero [6](#0-5) , underscoring that this value is not guaranteed to be consistently reproduced across independent deployments/upgrades.

### Impact Explanation
`epochBlockInterval` is a consensus-critical parameter consumed by validator-set epoch transition logic (`kaiax/valset/impl/transition.go`). If two honest nodes end up running `AddressBookV2` implementations built with different `epochBlockInterval` immutable values (e.g., due to a hard-fork upgrade rollout using inconsistent build parameters, or a future implementation upgrade omitting/miscalculating the original interval), they will compute different epoch boundaries for the same block height. This directly produces state divergence between honest nodes — validator-set transitions, staking/reward epoch calculations, and downstream consensus fields would diverge, which can lead to a chain split or rejection of otherwise valid blocks by a subset of nodes.

### Likelihood Explanation
The trigger condition is not a single unprivileged transaction but relies on the (governance/hardfork-controlled) implementation upgrade path being executed with inconsistent constructor parameters — a scenario made more likely because the interval is silently hardcoded per-implementation-deploy rather than being upgrade-safe proxy storage, and because the codebase already contains logic to substitute a default value when the configured interval is `0` [6](#0-5) , showing the parameter is not always deterministically supplied. Any future upgrade of `AddressBookV2` (a maintained, versioned system contract, as evidenced by `contracts-v3.0`/future-version directory naming) carries this risk by design, not by exceptional misuse.

### Recommendation
Do not encode `epochBlockInterval` as an immutable constructor argument in the upgradeable `AddressBookV2` implementation. Instead, store it in regular (proxy) contract storage set via the `initialize()` function (or a dedicated governance-gated setter), consistent with OpenZeppelin's upgradeable-contract guidance, so that its value is preserved automatically across implementation upgrades and cannot silently change unless explicitly and auditably updated through contract logic.

### Proof of Concept
Analogous to the external report's Foundry PoC:
1. Deploy `AddressBookV2` implementation V1 with constructor argument `epochBlockInterval = X`, wire it behind the ERC1967 proxy at `0x400` as done in `InstallAddressBookV2` [7](#0-6) .
2. Call `EpochBlockInterval()` on the proxy — returns `X`.
3. Deploy `AddressBookV2` implementation V2, but with the constructor argument omitted or computed differently (e.g., `Y != X`), then call `upgradeToAndCall` on the proxy [5](#0-4) .
4. Call `EpochBlockInterval()` on the proxy again — it now returns `Y` (or `0` if omitted), silently diverging from the pre-upgrade value, exactly mirroring the `clearingHouse`/`marginAccount` reset-to-zero behavior shown in the original report's trace logs.

### Citations

**File:** blockchain/system/addressbook_v2.go (L52-64)
```go
// InstallAddressBookV2 sets up the AddressBookV2 UUPS proxy at AddressBookAddr (0x400).
// logicAddr is the pre-deployed ABv2 implementation contract address
// (stored in ABv2DataContract.implementation()).
func InstallAddressBookV2(state *state.StateDB, logicAddr common.Address) error {
	// Set ERC1967 proxy code at 0x400
	if err := state.SetCode(AddressBookAddr, ERC1967ProxyV5Code); err != nil {
		return err
	}
	// Point proxy's implementation slot to the logic contract
	state.SetState(AddressBookAddr, common.BytesToHash(ImplementationSlot), lpad32(logicAddr))

	return nil
}
```

**File:** blockchain/system/addressbook_v2.go (L238-249)
```go
// ReadABv2EpochBlockInterval reads the epoch block interval baked into AddressBookV2.
func ReadABv2EpochBlockInterval(backend bind.ContractCaller, num *big.Int) (uint64, error) {
	caller, err := abv2contracts.NewAddressBookV2Caller(AddressBookAddr, backend)
	if err != nil {
		return 0, err
	}
	n, err := caller.EpochBlockInterval(&bind.CallOpts{BlockNumber: num})
	if err != nil {
		return 0, err
	}
	return n.Uint64(), nil
}
```

**File:** contracts/bindings/addressbookv2/AddressBookV2.go (L2328-2340)
```go
// UpgradeToAndCall is a paid mutator transaction binding the contract method 0x4f1ef286.
//
// Solidity: function upgradeToAndCall(address newImplementation, bytes data) payable returns()
func (_AddressBookV2 *AddressBookV2Transactor) UpgradeToAndCall(opts *bind.TransactOpts, newImplementation common.Address, data []byte) (*types.Transaction, error) {
	return _AddressBookV2.contract.Transact(opts, "upgradeToAndCall", newImplementation, data)
}

// UpgradeToAndCall is a paid mutator transaction binding the contract method 0x4f1ef286.
//
// Solidity: function upgradeToAndCall(address newImplementation, bytes data) payable returns()
func (_AddressBookV2 *AddressBookV2Session) UpgradeToAndCall(newImplementation common.Address, data []byte) (*types.Transaction, error) {
	return _AddressBookV2.Contract.UpgradeToAndCall(&_AddressBookV2.TransactOpts, newImplementation, data)
}
```

**File:** blockchain/system/permissionless.go (L226-239)
```go
	// Deploy AddressBookV2 implementation (used by ABv2DataContract and proxy setup)
	abv2ABI, _ := addressbookv2contract.AddressBookV2MetaData.GetAbi()
	if epochBlockInterval == 0 {
		epochBlockInterval = DefaultEpochBlockInterval
	}
	abv2ImplInput, err := packConstructor(abv2ABI, common.FromHex(addressbookv2contract.AddressBookV2Bin), big.NewInt(epochBlockInterval))
	if err != nil {
		return fmt.Errorf("pack ABv2 impl constructor: %w", err)
	}
	abv2ImplAddr, err := evmCreate(cfg, abv2ImplInput)
	if err != nil {
		return fmt.Errorf("deploy ABv2 impl: %w", err)
	}
	result.abv2Impl = abv2ImplAddr
```
