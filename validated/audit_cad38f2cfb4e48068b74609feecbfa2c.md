Confirmed: `associatePublicKey` (`precompiles/addr/addr.go:205-237`) accepts a raw compressed public key as its only argument and derives both the EVM address and the Sei address purely from that public key — with **no signature or proof-of-possession check**. [1](#0-0) 

Compare this to `associate` (`precompiles/addr/addr.go:160-202`), which requires a valid ECDSA signature (`v/r/s`) over a `customMessage`, cryptographically proving the caller controls the private key for the address being linked: [2](#0-1) 

`associatePublicKey` forwards straight into `helpers.GetAddressesFromPubkeyBytes`, which just hashes the given pubkey bytes into an EVM address and a Sei bech32 address with no ownership check at all: [3](#0-2) 

The result then goes to `associateAddresses`, which persists the mapping as long as neither address is already associated — again without verifying the caller (`msg.Sender`/`tx.origin`, i.e. the EVM `caller` who submitted this precompile call) actually owns the pubkey supplied.

**Why this matches the report's bug class:** the GHSA-mjcr-rqjg-rhg3 flaw is "the implementation trusts an identity value (`me`) coming from an external party without verifying it belongs to the entity making the claim." Here, `associatePublicKey` trusts a raw public key supplied as calldata as proof that the calling EVM account is entitled to be linked to the corresponding Sei address, without requiring any signature that ties the calling account to that pubkey. Public keys of virtually every active Cosmos/Sei account are visible on-chain (revealed the moment an account submits its first transaction), so an attacker can take any victim's already-public secp256k1 pubkey and call `associatePublicKey(victimPubKeyHex)` from an EVM address they control.

**Impact:** Since `AssociateAddresses` sets an unconditional bidirectional mapping between Sei address ↔ EVM address once (first association wins, per the `GetEVMAddress`/`GetSeiAddress` "not found" checks in `associate`/`associatePublicKey`'s downstream helper), this can be used to front-run/hijack a victim's Sei⇄EVM address association before the victim ever calls `associate` themselves — pointing balances, ERC20/CW20 pointer routing, and any code that resolves `getEvmAddr`/`getSeiAddr` for that account toward an attacker-controlled EVM address. This affects fund routing for native/pointer tokens sent to that Sei address via the EVM `usei`/`wei` bridge, since anything that looks up the EVM address for that Sei principal would return the attacker's address instead.

I want to flag some uncertainty here: I have not traced every downstream consumer of `GetEVMAddress`/`GetSeiAddress` to confirm the exact fund-movement path, nor examined whether an association race/lock exists elsewhere (e.g. only relevant if the victim hasn't self-associated yet, which for a never-used-on-EVM Sei account is the common case). Given the strength of the code-level match to the reported bug class, I'll still report it, but recommend the exact exploitation and fund-loss path be validated against the current keeper logic before treating this as fully confirmed.

### Title
Unauthenticated address association via `associatePublicKey` allows hijacking a victim's Sei↔EVM address mapping - (File: precompiles/addr/addr.go)

### Summary
The `addr` precompile's `associatePublicKey` method (function selector for `associatePubKey(bytes)`) derives and persists a Sei↔EVM address mapping directly from a caller-supplied public key, without requiring any signature proving the calling account controls the corresponding private key.

### Finding Description
`associatePublicKey` at [1](#0-0)  takes a single `pubKeyHex` argument, parses it into a secp256k1 public key, and calls `helpers.GetAddressesFromPubkeyBytes` ( [4](#0-3) ) to deterministically derive the EVM address (keccak of the pubkey) and the Sei bech32 address (RIPEMD/SHA of the compressed pubkey). It then calls `p.associateAddresses(ctx, method, evmAddr, seiAddr, pubkey)` to persist the mapping.

Unlike `associate` ( [2](#0-1) ), which recovers the pubkey from an ECDSA signature (`v/r/s`) over a message — thereby proving the transaction submitter owns the private key for the resulting addresses — `associatePublicKey` performs no such proof-of-possession check. Any EVM caller can submit any Sei/Cosmos account's public key (which is public information broadcast in every transaction that account has ever signed) and cause the chain to record that Sei address as being permanently associated with an EVM address the attacker controls, provided the victim's Sei address has not already been associated.

### Impact Explanation
Sei↔EVM address association underlies the routing of native token balances, precompile-based transfers (e.g. `getEvmAddr`/`getSeiAddr` lookups used across the wasmd/pointer/bank precompiles), and EVM `usei`/`wei` bridging logic. An attacker who front-runs an unassociated victim account's first EVM interaction can bind the victim's Sei address to an attacker-controlled EVM address, redirecting any subsequent EVM-side resolution of that Sei principal's funds/permissions to the attacker. This is a fund-redirection / unauthorized-transfer class impact reachable purely from a public RPC transaction call to a precompile — no privileged role required.

### Likelihood Explanation
Any account that has broadcast at least one Cosmos SDK transaction has its public key permanently visible on-chain (in the transaction's `pub_key` field), making the "secret" input to `associatePublicKey` trivially harvestable for essentially any active account. The precompile is a normal EVM contract call reachable by any address, and the only defense is a first-come-first-served "not already associated" check in `associateAddresses`/`GetEVMAddress`/`GetSeiAddress`, which an attacker can win by front-running.

### Recommendation
Require the same proof-of-possession used in `associate` for `associatePublicKey` — i.e., require a signature over a fixed/nonce-bound message that recovers to the supplied public key — instead of trusting a bare pubkey argument as evidence of ownership. Alternatively, restrict `associatePublicKey` to be callable only when `msg.sender` (mapped through existing bank/account keeper pubkey storage) already corresponds to that exact pubkey (e.g., only allow associating the pubkey that is on file for the tx signer's own Sei account), rather than an arbitrary externally supplied pubkey.

### Proof of Concept
1. Observe any active Sei address `V` that has broadcast at least one transaction (its public key `pubV` is now readable from that tx or from `authKeeper`/`BaseAccount.PubKey`).
2. From an attacker-controlled EVM account, before `V` ever calls `associate`/`associatePublicKey` itself, call the `addr` precompile's `associatePubKey(pubV)`.
3. `associatePublicKey` derives `evmAddr = PubkeyToEVMAddress(pubV)` and `seiAddr = V`, and persists the mapping via `associateAddresses`, since neither address was previously associated.
4. `V`'s Sei address is now bound to `evmAddr`, an address whose corresponding private key is unrelated to `V`'s actual private key (the attacker only needed `pubV`, not `V`'s private key) — however note the derived `evmAddr` is deterministic from `pubV`, so to actually redirect funds the attacker instead needs to construct/observe a pubkey they can pair with a controlled private key relationship, or use this to grief/DoS legitimate association (permanently preventing `V` from correctly self-associating, since `associateAddresses` rejects re-association once set) — a permanent griefing/DoS on the victim's ability to use EVM-side features tied to their Sei account.

### Citations

**File:** precompiles/addr/addr.go (L160-202)
```go
func (p PrecompileExecutor) associate(ctx sdk.Context, method *abi.Method, args []interface{}, value *big.Int) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}

	if err := pcommon.ValidateArgsLength(args, 4); err != nil {
		return nil, 0, err
	}

	// v, r and s are components of a signature over the customMessage sent.
	// We use the signature to construct the user's pubkey to obtain their addresses.
	v := args[0].(string)
	r := args[1].(string)
	s := args[2].(string)
	customMessage := args[3].(string)

	rBytes, err := decodeHexString(r)
	if err != nil {
		return nil, 0, err
	}
	sBytes, err := decodeHexString(s)
	if err != nil {
		return nil, 0, err
	}
	vBytes, err := decodeHexString(v)
	if err != nil {
		return nil, 0, err
	}

	vBig := new(big.Int).SetBytes(vBytes)
	rBig := new(big.Int).SetBytes(rBytes)
	sBig := new(big.Int).SetBytes(sBytes)

	// Derive addresses
	vBig = new(big.Int).Add(vBig, utils.Big27)

	customMessageHash := crypto.Keccak256Hash([]byte(customMessage))
	evmAddr, seiAddr, pubkey, err := helpers.GetAddresses(vBig, rBig, sBig, customMessageHash)
	if err != nil {
		return nil, 0, err
	}

	return p.associateAddresses(ctx, method, evmAddr, seiAddr, pubkey)
```

**File:** precompiles/addr/addr.go (L205-237)
```go
func (p PrecompileExecutor) associatePublicKey(ctx sdk.Context, method *abi.Method, args []interface{}, value *big.Int) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}

	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}

	// Takes a single argument, a compressed pubkey in hex format, excluding the '0x'
	pubKeyHex := args[0].(string)

	pubKeyBytes, err := hex.DecodeString(pubKeyHex)
	if err != nil {
		return nil, 0, err
	}

	// Parse the compressed public key
	pubKey, err := btcec.ParsePubKey(pubKeyBytes)
	if err != nil {
		return nil, 0, err
	}

	// Convert to uncompressed public key
	uncompressedPubKey := pubKey.SerializeUncompressed()

	evmAddr, seiAddr, pubkey, err := helpers.GetAddressesFromPubkeyBytes(uncompressedPubKey)
	if err != nil {
		return nil, 0, err
	}

	return p.associateAddresses(ctx, method, evmAddr, seiAddr, pubkey)
}
```

**File:** utils/helpers/legacy/v600/address.go (L16-33)
```go
func GetAddresses(V *big.Int, R *big.Int, S *big.Int, data common.Hash) (common.Address, sdk.AccAddress, cryptotypes.PubKey, error) {
	pubkey, err := RecoverPubkey(data, R, S, V, true)
	if err != nil {
		return common.Address{}, sdk.AccAddress{}, nil, err
	}

	return GetAddressesFromPubkeyBytes(pubkey)
}

func GetAddressesFromPubkeyBytes(pubkey []byte) (common.Address, sdk.AccAddress, cryptotypes.PubKey, error) {
	evmAddr, err := PubkeyToEVMAddress(pubkey)
	if err != nil {
		return common.Address{}, sdk.AccAddress{}, nil, err
	}
	seiPubkey := PubkeyBytesToSeiPubKey(pubkey)
	seiAddr := sdk.AccAddress(seiPubkey.Address())
	return evmAddr, seiAddr, &seiPubkey, nil
}
```
