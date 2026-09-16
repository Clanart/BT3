Found a concrete analog: `CnStakingV4` and `PublicDelegation` are UUPS/Beacon-proxy implementation contracts deployed by `CnStakingV4Factory` with a bare `constructor() {}` that does **not** call `_disableInitializers()`, yet expose a public `initialize(address _owner)` (selector `0xc4d66de8`) / `initialize(address _baseCnStaking, PDConstructorArgs _args)` (selector `0x26cf277a`) function that can be invoked directly on the implementation address. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

These implementation contracts are deployed once and shared by every validator/staker via `UpgradeableBeacon` + `BeaconProxy` pattern in `deployBeaconInfra`, and any staker/public caller can reach the beacon-referenced implementation address directly since its address is deterministically discoverable (`cnStakingBeacon.implementation()` / `pdBeacon.implementation()`). [5](#0-4) 

By contrast, Kaia's other proxy-based system contracts already implement the exact mitigation the report recommends: `MockUpgradeableSystemContract` calls `_disableInitializers()` in its constructor, and both `KIP113`/`SimpleBlsRegistry` and `AddressBookV2` implementation contracts are confirmed (via test comments) to have `_disableInitializers()`-set storage from their constructors. [6](#0-5) [7](#0-6) 

I could not find the Solidity source for `CnStakingV4.sol` / `PublicDelegation.sol` in the index (only compiled Go bindings are present), so I cannot fully confirm from source whether `initialize`/`initializeWithPD` carry a one-time guard (e.g., an `initializer` modifier or a manual `_owner == address(0)` check) that would still block a *second* legitimate initialization even without `_disableInitializers()`. The bare `constructor() {}` with no state set strongly suggests the guard variable starts at its zero value in the implementation, which is exactly the vulnerable precondition described in the report. Given this residual uncertainty about the exact guard implementation, I'd recommend starting a Devin session with repository/filesystem access to pull the actual `.sol` source for `CnStakingV4` and `PublicDelegation` and verify whether `initialize`/`initializeWithPD` can be called on the raw implementation to hijack the shared logic contract (e.g., set `owner` or `publicDelegation` in the implementation's storage, which could then be relied on if `delegatecall`-based logic ever reads implementation storage, or could be used for phishing/impersonation since `implementation()`-returned addresses are often surfaced in tooling).

### Title
Potential Uninitialized Implementation in CnStakingV4/PublicDelegation Beacon-Proxy Contracts - ([File: contracts/bindings/cnstakingv4/CnStakingV4.go], [File: contracts/bindings/publicdelegation/PublicDelegation.go])

### Summary
`CnStakingV4` and `PublicDelegation`, the shared implementation contracts referenced by `UpgradeableBeacon`s and used by every `BeaconProxy` staking/public-delegation deployment, have empty constructors and do not appear to call `_disableInitializers()`, unlike other Kaia upgradeable system contracts (`AddressBookV2`, KIP113/`SimpleBlsRegistry`, `MockUpgradeableSystemContract`) which explicitly do.

### Finding Description
`CnStakingV4Factory` deploys a single `CnStakingV4` implementation and a single `PublicDelegation` implementation, then wraps each in an `UpgradeableBeacon`; every validator's staking contract and public-delegation contract is a `BeaconProxy` pointing at these shared implementations. Both implementations expose public `initialize`/`initializeWithPD` functions guarded (presumably) only by an internal "already initialized" flag set during proxy-context calls — but their `constructor()` bodies are empty, leaving that flag at its default zero value on the raw implementation contract itself. This mirrors the `RubiconFeeController.sol` bug class: initialization is only performed through proxies, never directly on the implementation.

### Impact Explanation
If the guard is only an `initializer`-style flag (no other protection), any unprivileged caller could call `initialize(owner)` or `initializeWithPD(owner, publicDelegation)` directly on the shared implementation address and become "owner" of the implementation contract's own storage. While delegatecall-based proxies are not directly affected in state, this could enable spoofing/hijacking of the logic contract (e.g., for social-engineering, incorrect explorer/tooling display of "owner", or a foothold if any future code path trusts implementation-contract state).

### Likelihood Explanation
Medium: exploitation requires only a single unprivileged transaction directly targeting the known implementation address (discoverable via `beacon.implementation()`), with no special permissions needed — consistent with an "unprivileged transaction sender" reachable path.

### Recommendation
Add `constructor() { _disableInitializers(); }` (or equivalent explicit lock, e.g. setting the initialized flag / owner to a sentinel) to `CnStakingV4` and `PublicDelegation` implementation contracts, matching the pattern already used in `MockUpgradeableSystemContract.sol` and confirmed for `AddressBookV2`/KIP113.

### Proof of Concept
1. Read `cnStakingBeacon.implementation()` (or `pdBeacon.implementation()`) to obtain the raw implementation address.
2. Send a transaction calling `initialize(address)` (selector `0xc4d66de8`) directly on that implementation address with attacker-controlled `_owner`.
3. If no additional guard exists in source, the call succeeds and sets `owner` in the implementation's own storage, hijacking the shared logic contract instance.

**Confidence caveat:** This analysis is based on generated Go bindings and constructor ABI signatures only; the actual Solidity source for `CnStakingV4.sol`/`PublicDelegation.sol` was not available in the index to confirm the exact initializer-guard logic. Confirming exploitability requires inspecting the source directly (recommend a Devin session with full repo/filesystem access).

### Citations

**File:** contracts/bindings/cnstakingv4/CnStakingV4.go (L33-35)
```go
var CnStakingV4MetaData = &bind.MetaData{
	ABI: "[{\"type\":\"constructor\",\"inputs\":[],\"stateMutability\":\"nonpayable\"},{\"type\":\"receive\",\"stateMutability\":\"payable\"},{\"type\":\"function\",\"name\":\"CONTRACT_TYPE\",\"inputs\":[],\"outputs\":[{\"name\":\"\",\"type\":\"string\",\"internalType\":\"string\"}],\"stateMutability\":\"view\"},{\"type\":\"function\",\"name\":\"STAKE_LOCKUP\",\"inputs\":[],\"outputs\":[{\"name\":\"\",\"type\":\"uint256\",\"internalType\":\"uint256\"}],\"stateMutability\":\"view\"},{\"type\":\"function\",\"name\":\"VERSION\",\"inputs\":[],\"outputs\":[{\"name\":\"\",\"type\":\"uint256\",\"internalType\":\"uint256\"}],\"stateMutability\":\"view\"},{\"type\":\"function\",\"name\":\"approveStakingWithdrawal\",\"inputs\":[{\"name\":\"_to\",\"type\":\"address\",\"internalType\":\"address\"},{\"nam ... (truncated)
	Bin: "0x6080604052348015600e575f80fd5b5060156019565b60c9565b7ff0c57e16840df040f15088dc2f81fe391c3923bec73e23a9662efc9c229c6a00805468010000000000000000900460ff161560685760405163f92ee8a960e01b815260040160405180910390fd5b80546001600160401b039081161460c65780546001600160401b0319166001600160401b0390811782556040519081527fc7f505b2f371ae2175ee4913f4499e1f2633a7b5936321eed1cdaeb6115181d29060200160405180910390a15b50565b611bec806100d65f395ff3fe608060405260043610610155575f3560e01c8063715018a6116100be578063c4d66de811610078578063c4d66de81461039f578063c804b115146103be578063c89e4361146103dd578063d2569eb9146103e5578063e1a12d3514610411578063f2fde38b14610425578063ffa1ad7414610444575f80fd5b8063715018a61461030b578063725c05031461031f5780638cf57cb91461034e5780638da5cb5b1461036257806396106ae414610376578063a006e90 ... (truncated)
```

**File:** contracts/bindings/cnstakingv4/CnStakingV4.go (L750-769)
```go
// Initialize is a paid mutator transaction binding the contract method 0xc4d66de8.
//
// Solidity: function initialize(address _owner) returns()
func (_CnStakingV4 *CnStakingV4Transactor) Initialize(opts *bind.TransactOpts, _owner common.Address) (*types.Transaction, error) {
	return _CnStakingV4.contract.Transact(opts, "initialize", _owner)
}

// Initialize is a paid mutator transaction binding the contract method 0xc4d66de8.
//
// Solidity: function initialize(address _owner) returns()
func (_CnStakingV4 *CnStakingV4Session) Initialize(_owner common.Address) (*types.Transaction, error) {
	return _CnStakingV4.Contract.Initialize(&_CnStakingV4.TransactOpts, _owner)
}

// Initialize is a paid mutator transaction binding the contract method 0xc4d66de8.
//
// Solidity: function initialize(address _owner) returns()
func (_CnStakingV4 *CnStakingV4TransactorSession) Initialize(_owner common.Address) (*types.Transaction, error) {
	return _CnStakingV4.Contract.Initialize(&_CnStakingV4.TransactOpts, _owner)
}
```

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L41-43)
```go
var PublicDelegationMetaData = &bind.MetaData{
	ABI: "[{\"type\":\"constructor\",\"inputs\":[],\"stateMutability\":\"nonpayable\"},{\"type\":\"receive\",\"stateMutability\":\"payable\"},{\"type\":\"function\",\"name\":\"COMMISSION_DENOMINATOR\",\"inputs\":[],\"outputs\":[{\"name\":\"\",\"type\":\"uint256\",\"internalType\":\"uint256\"}],\"stateMutability\":\"view\"},{\"type\":\"function\",\"name\":\"CONTRACT_TYPE\",\"inputs\":[],\"outputs\":[{\"name\":\"\",\"type\":\"string\",\"internalType\":\"string\"}],\"stateMutability\":\"view\"},{\"type\":\"function\",\"name\":\"MAX_COMMISSION_RATE\",\"inputs\":[],\"outputs\":[{\"name\":\"\",\"type\":\"uint256\",\"internalType\":\"uint256\"}],\"stateMutability\":\"view\"},{\"type\":\"function\",\"name\":\"VERSION\",\"inputs\":[],\"outputs\":[{\"name\":\"\",\"type\":\"uint256\",\"internalType\":\" ... (truncated)
	Bin: "0x6080604052348015600e575f80fd5b5060156019565b60c9565b7ff0c57e16840df040f15088dc2f81fe391c3923bec73e23a9662efc9c229c6a00805468010000000000000000900460ff161560685760405163f92ee8a960e01b815260040160405180910390fd5b80546001600160401b039081161460c65780546001600160401b0319166001600160401b0390811782556040519081527fc7f505b2f371ae2175ee4913f4499e1f2633a7b5936321eed1cdaeb6115181d29060200160405180910390a15b50565b6129b0806100d65f395ff3fe608060405260043610610277575f3560e01c80634cdad5061161014a578063c6e6f592116100be578063e659d7d711610078578063e659d7d71461071e578063ef8b30f71461073d578063f29177c31461075c578063f2fde38b1461077b578063f3fef3a31461079a578063ffa1ad74146107b9575f80fd5b8063c6e6f59214610664578063c804b11514610683578063ce96cb77146106a2578063d905777e146106c1578063dd62ed3e146106e0578063e15fc35 ... (truncated)
```

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L1178-1197)
```go
// Initialize is a paid mutator transaction binding the contract method 0x26cf277a.
//
// Solidity: function initialize(address _baseCnStaking, (address,address,uint256,string) _args) returns()
func (_PublicDelegation *PublicDelegationTransactor) Initialize(opts *bind.TransactOpts, _baseCnStaking common.Address, _args IPublicDelegationPDConstructorArgs) (*types.Transaction, error) {
	return _PublicDelegation.contract.Transact(opts, "initialize", _baseCnStaking, _args)
}

// Initialize is a paid mutator transaction binding the contract method 0x26cf277a.
//
// Solidity: function initialize(address _baseCnStaking, (address,address,uint256,string) _args) returns()
func (_PublicDelegation *PublicDelegationSession) Initialize(_baseCnStaking common.Address, _args IPublicDelegationPDConstructorArgs) (*types.Transaction, error) {
	return _PublicDelegation.Contract.Initialize(&_PublicDelegation.TransactOpts, _baseCnStaking, _args)
}

// Initialize is a paid mutator transaction binding the contract method 0x26cf277a.
//
// Solidity: function initialize(address _baseCnStaking, (address,address,uint256,string) _args) returns()
func (_PublicDelegation *PublicDelegationTransactorSession) Initialize(_baseCnStaking common.Address, _args IPublicDelegationPDConstructorArgs) (*types.Transaction, error) {
	return _PublicDelegation.Contract.Initialize(&_PublicDelegation.TransactOpts, _baseCnStaking, _args)
}
```

**File:** blockchain/system/permissionless.go (L191-224)
```go
func deployBeaconInfra(cfg *runtime.Config, owner common.Address, epochBlockInterval int64, result *allocPermissionlessResult) error {
	// Deploy CnStakingV4 implementation
	cnImplAddr, err := evmCreate(cfg, common.FromHex(cnstakingv4.CnStakingV4Bin))
	if err != nil {
		return fmt.Errorf("deploy CnStakingV4 impl: %w", err)
	}

	// Deploy UpgradeableBeacon for CnStaking
	beaconABI, _ := beaconcontract.UpgradeableBeaconMetaData.GetAbi()
	cnBeaconInput, err := packConstructor(beaconABI, common.FromHex(beaconcontract.UpgradeableBeaconBin), cnImplAddr, owner)
	if err != nil {
		return fmt.Errorf("pack CnStaking beacon constructor: %w", err)
	}

	// Deploy PublicDelegation implementation + UpgradeableBeacon
	cnBeaconAddr, err := evmCreate(cfg, cnBeaconInput)
	if err != nil {
		return fmt.Errorf("deploy CnStaking beacon: %w", err)
	}
	result.cnStakingBeacon = cnBeaconAddr

	pdImplAddr, err := evmCreate(cfg, common.FromHex(pdcontract.PublicDelegationBin))
	if err != nil {
		return fmt.Errorf("deploy PD impl: %w", err)
	}
	pdBeaconInput, err := packConstructor(beaconABI, common.FromHex(beaconcontract.UpgradeableBeaconBin), pdImplAddr, owner)
	if err != nil {
		return fmt.Errorf("pack PD beacon constructor: %w", err)
	}
	pdBeaconAddr, err := evmCreate(cfg, pdBeaconInput)
	if err != nil {
		return fmt.Errorf("deploy PD beacon: %w", err)
	}
	result.pdBeacon = pdBeaconAddr
```

**File:** contracts/testing/system_contracts/MockUpgradeableSystemContract.sol (L23-34)
```text
contract MockUpgradeableSystemContract is Initializable, UUPSUpgradeable {
    uint256 public number;

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    function initialize(uint256 _number) public initializer {
        __UUPSUpgradeable_init();
        number = _number;
    }
```

**File:** blockchain/system/permissionless_test.go (L156-162)
```go
	// ABv2 logic contract: read address from ERC1967 implementation slot, verify it's in alloc with code
	implSlotValue := alloc[AddressBookAddr].Storage[common.BytesToHash(ImplementationSlot)]
	abv2LogicAddr := common.BytesToAddress(implSlotValue.Bytes())
	assert.NotEqual(t, common.Address{}, abv2LogicAddr, "ABv2 implementation slot should be set")
	require.Contains(t, alloc, abv2LogicAddr, "ABv2 logic should be in alloc")
	assert.NotEmpty(t, alloc[abv2LogicAddr].Code, "ABv2 logic should have code")
	// ABv2 impl has storage from constructor (_disableInitializers sets initializable slot)
```
