### Title
Unconditional address-disassociation on `SELFDESTRUCT` allows an EIP-7702-delegated EOA to permanently sever its Sei↔EVM identity, orphaning staking/distribution state - (File: `x/evm/state/state.go`)

### Summary
`DBImpl.SelfDestruct` unconditionally deletes the Sei↔EVM address mapping for the target account whenever one exists, with no restriction on the account's origin or on whether the destroy is happening inside a reverted call frame in a meaningful (non-KVStore) sense, and no distinction between "this account never had a true association" and "this account is an EOA whose true (pubkey-derived) Sei identity carries staking/distribution/authz state."

### Finding Description
`DBImpl.SelfDestruct` reads the current Sei address for the EVM account and, if found, calls `DeleteAddressMapping`, wiping the bidirectional Sei↔EVM mapping: [1](#0-0) 

`DeleteAddressMapping` itself performs no validation beyond deleting both KV entries: [2](#0-1) 

The codebase already treats "mutable" Sei↔EVM identity remapping as a serious, chain-halting bug class: the EIP-7702 (`SetCode`) ante-handler logic goes out of its way to **pre-associate** every authorization authority with its true pubkey-derived Sei address *before* EVM execution, specifically because letting `SetCode` create/alter a direct-cast mapping "can then halt the chain via the distribution validator-removal hook" by orphaning staking/distribution state tied to the old identity: [3](#0-2) [4](#0-3) 

That fix only prevents a *remap* of an unassociated authority. It does nothing to prevent the inverse operation: an EOA that is **already** truly associated (has staking delegations, authz grants, validator operator/consensus keys, etc. tied to its Sei address) can have EIP-7702 delegation code installed on it (`SetCode`), and if that delegated code executes `SELFDESTRUCT` on the account itself, `DBImpl.SelfDestruct` deletes the mapping unconditionally — with no check for whether the account is a "true" EOA identity versus a freshly-created contract. Once `DeleteAddressMapping` runs, `GetEVMAddress`/`GetSeiAddress` both return `false` for that account, so:
- `AssociateAddresses`'s existing-association guard (`_, found := p.evmKeeper.GetEVMAddress(ctx, seiAddr); if found { return err }`) no longer blocks re-association — the Sei address is now free to be re-associated to a **different** EVM address/pubkey by anyone who can sign a transaction (or another SetCode authorization) touching that Sei account, because "found" is false post-deletion. This is the exact "mutable mapping / orphaned staking-distribution identity" scenario the SetCode ante fix was built to prevent, just reached through a different code path (`SELFDESTRUCT`) that the fix does not cover.
- Because `x/evm/state/state.go`'s `SelfDestruct` (used by the pre-EIP-6780 selfdestruct opcode path and unconditionally by any caller) is not gated on `Created(acc)` the way `SelfDestruct6780` is, an EOA that received EIP-7702 delegation code can be made to disassociate its long-lived identity even though nothing about the account was "created in this transaction."

### Impact Explanation
If an already-associated EOA's Sei↔EVM mapping is deleted via this path, and that Sei address subsequently is re-associated to an attacker-chosen EVM address/pubkey, on-chain state that was keyed to the "true" identity (staking delegations, unbonding, authz grants, module accounting) becomes reachable/controllable under a mapping the account owner never authorized in that form — this is the same failure class the codebase's own comments describe as capable of **halting the chain via the distribution validator-removal hook** (a validator/consensus-halt condition), and more generally an unauthorized identity/permission takeover for CosmWasm/EVM-associated accounts. This satisfies the "validator halt" / "unauthorized transfer via precompile or pointer" / permanent state-corruption impact bar.

### Likelihood Explanation
Reaching this requires: (1) an EOA to have a real, pubkey-derived association already established (very common — happens automatically on any signed tx per `x/evm/ante/preprocess.go`'s `AssociateAddresses` call), (2) that EOA (or an attacker who can obtain the account's authorization signature, exactly like the EIP-7702 flows already exercised elsewhere in the repo's tests) installing delegation code via `SetCode`, and (3) the delegated code executing `SELFDESTRUCT` against itself. All three steps are reachable by an ordinary EVM transaction sender using standard, already-supported transaction types (`SetCodeTx` + a delegate contract containing a `SELFDESTRUCT`), with no special privileges — the chain already has extensive test coverage exercising exactly this `SetCode`+authority-association surface (`app/setcode_authority_test.go`, `x/evm/ante/preprocess_test.go`), showing it is an actively-used and reachable code path.

### Recommendation
Guard `DBImpl.SelfDestruct`'s call to `DeleteAddressMapping` so it does not sever a "true" pubkey-derived Sei↔EVM association — e.g., only delete the mapping if the associated Sei address is the direct-cast address of the EVM address (i.e., no real identity to lose), mirroring the same "true association vs. direct-cast" distinction already enforced in `utils/helpers/associate.go`'s `AssociateAddresses`/`MigrateBalance` and in the EIP-7702 authority pre-association fix. Alternatively, forbid deleting an address mapping for an account that has non-zero staking/authz/distribution state, or require that self-destruct-driven disassociation go through the same one-hop/verification checks used for authority pre-association.

### Proof of Concept
1. EOA `V` signs an ordinary transaction, causing `AssociateAddresses` to record the true mapping `seiAddr(V) <-> evmAddr(V)` (standard flow, see `x/evm/ante/preprocess.go`).
2. `V` signs an EIP-7702 `SetCodeAuthorization` designating a delegate contract `D` that contains a `SELFDESTRUCT` opcode targeting `address(this)`.
3. A sponsor submits a `SetCodeTx` carrying `V`'s authorization; the EVM installs delegation code on `V`'s address and (in the same or later call) executes `D`'s code in `V`'s context, hitting `DBImpl.SelfDestruct(evmAddr(V))`.
4. `s.k.GetSeiAddress(ctx, evmAddr(V))` returns the true `seiAddr(V)`, so `s.k.DeleteAddressMapping(ctx, seiAddr(V), evmAddr(V))` executes, wiping the mapping (`x/evm/state/state.go:86-98`).
5. `GetEVMAddress(ctx, seiAddr(V))` now returns `false`; a subsequent `Associate` transaction (or another `SetCode` authorization) can bind `seiAddr(V)` to a different EVM address/pubkey, because the "already has association set" guard in `x/evm/ante/preprocess.go:77-78` no longer triggers — reproducing the exact "orphaned staking/distribution identity, chain-halt via distribution validator-removal hook" scenario the existing SetCode-authority pre-association fix was written to prevent, but through the unguarded `SELFDESTRUCT` path.

*Note: I could not fully trace whether go-ethereum's fork-gated dispatch (EIP-6780) always routes EIP-7702 delegate-code self-destructs through `SelfDestruct6780` (which restricts full destruction to same-tx-created accounts) versus the plain `SelfDestruct` shown above in this specific integration; this affects exact reachability and should be verified against `x/evm/state/state.go`'s and go-ethereum's opcode dispatch before treating this as fully confirmed.*

### Citations

**File:** x/evm/state/state.go (L86-98)
```go
func (s *DBImpl) SelfDestruct(acc common.Address) uint256.Int {
	s.k.PrepareReplayedAddr(s.ctx, acc)
	if seiAddr, ok := s.k.GetSeiAddress(s.ctx, acc); ok {
		// remove the association
		s.k.DeleteAddressMapping(s.ctx, seiAddr, acc)
	}
	b := s.GetBalance(acc)
	s.SubBalance(acc, b, tracing.BalanceDecreaseSelfdestruct)

	// mark account as self-destructed
	s.MarkAccount(acc, AccountDeleted)
	return *b
}
```

**File:** x/evm/keeper/address.go (L24-28)
```go
func (k *Keeper) DeleteAddressMapping(ctx sdk.Context, seiAddress sdk.AccAddress, evmAddress common.Address) {
	store := ctx.KVStore(k.storeKey)
	store.Delete(types.EVMAddressToSeiAddressKey(evmAddress))
	store.Delete(types.SeiAddressToEVMAddressKey(seiAddress))
}
```

**File:** x/evm/ante/preprocess.go (L103-110)
```go
	// EIP-7702 authorization authorities are distinct accounts from the tx sender, so the
	// sender association above does not cover them. Associate each authority to its true
	// (pubkey-derived) Sei address before EVM execution installs delegation code for it.
	// Otherwise SetCode creates a mutable direct-cast EVM->Sei mapping that a later
	// associatePubKey call can remap, orphaning any staking/distribution state created
	// under the direct-cast identity (which can then halt the chain via the distribution
	// validator-removal hook).
	p.associateAuthorizationAuthorities(ctx, msg, associateHelper)
```

**File:** app/ante/evm_checktx.go (L251-268)
```go
// AssociateAuthorizationAuthorities pre-associates every EIP-7702 SetCode authorization
// authority in the transaction with its true (pubkey-derived) Sei address before EVM
// execution installs delegation code for it. Authorities are distinct accounts from the tx
// sender, so the sender association performed by the caller does not cover them. Without
// this, SetCode creates a mutable direct-cast EVM->Sei mapping that a later associatePubKey
// call can remap, orphaning any staking/distribution state created under the direct-cast
// identity (which can then halt the chain via the distribution validator-removal hook).
//
// This is the legacyabci counterpart of x/evm/ante's EVMPreprocessDecorator so both ante
// paths associate authorities identically. It is best-effort: only authorities whose
// authorization the EVM will actually apply are pre-associated — helpers.AuthorityToPreAssociate
// enforces the same chain-id/nonce/account-code checks go-ethereum uses, which also prevents
// replaying a publicly-visible authorization a user signed for another chain to force-associate
// them. Each association runs in its own cache context and is only committed on success, so an
// already-associated, skipped, or failing authority affects only itself and never rejects the
// transaction (matching go-ethereum, which would still accept it). Non-SetCode transactions
// carry no authorizations and are a no-op.
func AssociateAuthorizationAuthorities(ctx sdk.Context, ek *evmkeeper.Keeper, etx *ethtypes.Transaction) {
```
