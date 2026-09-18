Based on my investigation, the strongest reachable analog is CPU cost from processing invalid/never-executable EIP-7702 authorization lists during the mempool `CheckTx` path, performed before the transaction's fee/gas-purchase check.

### Title
CPU DoS via unbounded EIP-7702 authorization ECDSA recovery in CheckTx before fee validation - (File: app/ante/evm_checktx.go)

### Summary
`EvmCheckTxAnte`, the ante path used for every `CheckTx`/`ReCheckTx` mempool admission of an EVM transaction, calls `AssociateAuthorizationAuthorities` to iterate the transaction's EIP-7702 `SetCodeAuthorizations()` list and run a full ECDSA signature recovery (`RecoverAddressesFromAuthorization` → `crypto.Ecrecover`) plus multiple state lookups for every authorization tuple — before `EvmCheckAndChargeFees` validates that the sender can even pay for the transaction.

### Finding Description
The `EvmCheckTxAnte` flow is:
1. `EvmStatelessChecks` (cheap format/field validation)
2. `CheckAndDecodeSignature` (1 ECDSA recovery for the sender)
3. `AssociateAddress`
4. `AssociateAuthorizationAuthorities(ctx, ek, etx)` — **loops over every authorization in the SetCode tx and performs an `Ecrecover` + several keeper reads per entry**
5. `EvmCheckAndChargeFees` (fee/balance validation — the point where an unfunded or fee-invalid tx would normally be rejected cheaply) [1](#0-0) 

`AssociateAuthorizationAuthorities` performs the expensive recovery per authorization entry unconditionally: [2](#0-1) 

Each authorization triggers `RecoverAddressesFromAuthorization`, which does an RLP encode, a Keccak256 hash, and a full secp256k1 `Ecrecover` call, plus `AuthorityToPreAssociate` does additional `GetCode`/`GetNonce`/`GetEVMAddress` keeper reads: [3](#0-2) 

A go-ethereum `SetCodeTx` authorization list has no protocol-level cap in this code path prior to this point — the only implicit bound is transaction byte size, but each authorization tuple is small (chainID + address + nonce + signature ≈ ~100 bytes), so a transaction near the gossip/tx-size limit can carry thousands of authorization entries. Because this work happens during `CheckTx`, under Tendermint/CometBFT's mempool `CheckTx` runs with a fresh cache-store context (`app.CacheTxContext`) and is never actually charged EVM gas for the CPU spent — gas purchase (`EvmCheckAndChargeFees`/`BuyGas`) only happens *after* this authorization loop, so if the tx is ultimately rejected there for insufficient fee/balance (which an attacker can trivially arrange, e.g. a zero-balance sender), the recovery work has already been paid for by every node's CheckTx execution with zero cost to the attacker.

This structurally matches the CVE-2025-46598 bug class: expensive validation work performed on an unconfirmed transaction that will never be admitted/confirmed, driven by attacker-controlled input size, unmetered against the eventual rejection.

### Impact Explanation
An attacker can broadcast a stream of `MsgEVMTransaction` SetCode (type-4) transactions, each near the max gossip/tx size and packed with the maximum number of syntactically-valid-looking (or garbage, since `RecoverAddressesFromAuthorization` failures are silently skipped via `ok=false`) authorization tuples, using a sender account with no funds so the transaction is rejected quickly after the loop by `EvmCheckAndChargeFees`. Every full node's `CheckTx` (and `ReCheckTx`, which reprocesses mempool contents on every block) will burn CPU doing thousands of `Ecrecover` calls per submitted transaction, at effectively no cost to the attacker (no gas is charged, no valid fee needs to be paid, and CheckTx failures are cheap/free to resubmit). Sustained submission can starve node CheckTx throughput, delaying block production and mempool responsiveness across the network — a public-RPC/mempool-reachable CPU DoS.

### Likelihood Explanation
Likelihood is high: this is reachable by any unprivileged party via ordinary `CheckTx`/broadcast-tx submission (or via public RPC `eth_sendRawTransaction`), requires no special account setup beyond a valid-looking signature/RLP encoding to reach `AssociateAuthorizationAuthorities`, and requires no funds to be spent (the tx will be rejected by the fee check afterward, so the attacker only pays for tx broadcast, not gas). The behavior is deterministic and repeatable with each resubmission.

### Recommendation
Reorder ante processing so fee/balance viability (`EvmCheckAndChargeFees`) and a cheap upper bound on `len(etx.SetCodeAuthorizations())` are checked before iterating authorizations for expensive `Ecrecover`-based pre-association; enforce a maximum authorization-list length consistent with go-ethereum's tx-pool policy; and consider metering/charging CPU-equivalent gas for authorization recovery even in `CheckTx` (or skip pre-association entirely for a tx that hasn't yet proven it can pay fees).

### Proof of Concept
1. Construct an EIP-7702 `SetCodeTx` (`ethtx.SetCodeTx`) whose sender account has zero balance and whose `AuthorizationList` is filled with the maximum number of authorization tuples that fit under `types.MaxGossipTxBytes` / `constraints.MaxDataBytes` (each tuple is small, allowing thousands per tx).
2. Sign the outer transaction normally so it passes `EvmStatelessChecks` and `CheckAndDecodeSignature`.
3. Submit the transaction repeatedly via `broadcast_tx` / public JSON-RPC to multiple nodes.
4. Observe: for each submission, `AssociateAuthorizationAuthorities` performs one `Ecrecover` plus several state reads per authorization entry before `EvmCheckAndChargeFees` rejects the tx for insufficient balance — attacker pays nothing (no valid fee is ever charged) while every receiving node's CheckTx CPU time is dominated by the authorization-recovery loop, and `ReCheckTx` repeats this cost on every subsequent block.

### Citations

**File:** app/ante/evm_checktx.go (L52-68)
```go
	etx := ethtypes.NewTx(txData.AsEthereumData())
	evmAddr, seiAddr, seiPubkey, version, err := CheckAndDecodeSignature(ctx, txData, chainID, false)
	if err != nil {
		return ctx, err
	}
	if err := AssociateAddress(ctx, ek, evmAddr, seiAddr, seiPubkey); err != nil {
		return ctx, err
	}
	// Mirror the deliver-tx path and pre-associate EIP-7702 authorization authorities so the
	// mempool's check state matches how the transaction will execute. CheckTx runs on a
	// throwaway cache (its writes are never committed) and never applies authorizations, so
	// this cannot itself cause the direct-cast halt; it is here purely for mempool consistency
	// with EvmDeliverTxAnte.
	AssociateAuthorizationAuthorities(ctx, ek, etx)
	if _, err := EvmCheckAndChargeFees(ctx, evmAddr, ek, upgradeKeeper, txData, etx, msg, version, false); err != nil {
		return ctx, err
	}
```

**File:** app/ante/evm_checktx.go (L268-281)
```go
func AssociateAuthorizationAuthorities(ctx sdk.Context, ek *evmkeeper.Keeper, etx *ethtypes.Transaction) {
	auths := etx.SetCodeAuthorizations()
	if len(auths) == 0 {
		return
	}
	associateHelper := helpers.NewAssociationHelper(ek, ek.BankKeeper(), ek.AccountKeeper())
	for _, auth := range auths {
		evmAddr, seiAddr, pubkey, ok := helpers.AuthorityToPreAssociate(ctx, ek, auth)
		if !ok {
			continue
		}
		cacheCtx, write := ctx.CacheContext()
		if err := associateHelper.AssociateAddresses(cacheCtx, seiAddr, evmAddr, pubkey, false); err == nil {
			write()
```

**File:** utils/helpers/address.go (L116-188)
```go
// RecoverAddressesFromAuthorization recovers the EVM address, Sei address, and public
// key of the account that signed an EIP-7702 SetCode authorization (the "authority").
// The authorization sig hash is keccak256(0x05 || rlp([chainId, address, nonce])) and
// the recovery id is carried directly in auth.V (yParity, 0 or 1), which GetAddresses
// expects bumped by 27. This mirrors go-ethereum's SetCodeAuthorization.Authority(), but
// additionally returns the recovered public key so the authority can be associated with
// its true Sei address.
func RecoverAddressesFromAuthorization(auth ethtypes.SetCodeAuthorization) (common.Address, sdk.AccAddress, cryptotypes.PubKey, error) {
	var buf bytes.Buffer
	buf.WriteByte(eip7702MagicPrefix)
	if err := rlp.Encode(&buf, []any{auth.ChainID, auth.Address, auth.Nonce}); err != nil {
		return common.Address{}, sdk.AccAddress{}, nil, err
	}
	sigHash := crypto.Keccak256Hash(buf.Bytes())
	v := new(big.Int).SetUint64(uint64(auth.V) + 27)
	return GetAddresses(v, auth.R.ToBig(), auth.S.ToBig(), sigHash)
}

// AuthorizationStateReader exposes the EVM state lookups needed to decide whether an
// EIP-7702 authorization would be applied. Both the standard and giga EVM keepers satisfy it.
type AuthorizationStateReader interface {
	ChainID(ctx sdk.Context) *big.Int
	GetCode(ctx sdk.Context, addr common.Address) []byte
	GetNonce(ctx sdk.Context, addr common.Address) uint64
	GetEVMAddress(ctx sdk.Context, seiAddr sdk.AccAddress) (common.Address, bool)
}

// AuthorityToPreAssociate returns the authority of an EIP-7702 authorization that should be
// associated with its true (pubkey-derived) Sei address before execution, or ok=false.
//
// It mirrors go-ethereum's StateTransition.validateAuthorization (chain id, nonce overflow,
// authority code, and account-nonce checks) so that pre-association happens only for
// authorizations the EVM will actually apply, and additionally skips authorities that are
// already associated. Mirroring validateAuthorization is essential to security: the
// authorization sig hash is computed from the auth's own ChainID, so recovery and
// auth.Authority() succeed for an authorization signed for ANY chain. Without these checks a
// publicly-visible authorization a user signed for another chain (e.g. Ethereum mainnet)
// could be replayed in a sponsored Sei SetCode tx to force-associate them — migrating their
// direct-cast balance and orphaning staking/distribution state — even though the EVM skips
// the wrong-chain authorization and installs no delegation.
func AuthorityToPreAssociate(ctx sdk.Context, k AuthorizationStateReader, auth ethtypes.SetCodeAuthorization) (common.Address, sdk.AccAddress, cryptotypes.PubKey, bool) {
	// Chain ID must be null or match the local chain.
	if !auth.ChainID.IsZero() && auth.ChainID.CmpBig(k.ChainID(ctx)) != 0 {
		return common.Address{}, nil, nil, false
	}
	// Nonce must not overflow (EIP-2681).
	if auth.Nonce+1 < auth.Nonce {
		return common.Address{}, nil, nil, false
	}
	evmAddr, seiAddr, pubkey, err := RecoverAddressesFromAuthorization(auth)
	if err != nil {
		return common.Address{}, nil, nil, false
	}
	// Cross-check against go-ethereum's authoritative recovery so we only ever act on the
	// exact address SetCode would target during execution.
	if authAddr, aerr := auth.Authority(); aerr != nil || authAddr != evmAddr {
		return common.Address{}, nil, nil, false
	}
	// Authority must have no code, or only an existing delegation designator.
	if code := k.GetCode(ctx, evmAddr); len(code) != 0 {
		if _, ok := ethtypes.ParseDelegation(code); !ok {
			return common.Address{}, nil, nil, false
		}
	}
	// Authority account nonce must match the authorization nonce.
	if k.GetNonce(ctx, evmAddr) != auth.Nonce {
		return common.Address{}, nil, nil, false
	}
	// Already-associated authorities need no pre-association (and cannot be re-mapped).
	if _, associated := k.GetEVMAddress(ctx, seiAddr); associated {
		return common.Address{}, nil, nil, false
	}
	return evmAddr, seiAddr, pubkey, true
```
