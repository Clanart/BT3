### Title
Unhandled `UnicodeDecodeError` on malformed VC proof keys causes wallet crash - (File: `chia/wallet/vc_wallet/vc_store.py`)

### Summary
`VCProofs.from_program`, which parses a Verified Credential's proof key/value tree from a `Program` (CLVM data), decodes each key and value atom with `.decode("utf-8")` and no error handling. This mirrors the CVE-2025-6966 bug class: a parser reading untrusted structured data (deb822's `TagSection.keys()` vs. here a VC proof binary tree) that assumes UTF-8-encoded keys and dereferences/decodes them without validating the byte content, causing a crash when fed malformed non-UTF-8 bytes.

### Finding Description
`VCProofs.from_program` recursively walks a CLVM `Program` tree and, once it reaches leaf atoms, unconditionally decodes them as UTF-8: [1](#0-0) 

There is no `try/except` around the `.decode("utf-8")` calls, unlike other memo-decoding paths in the codebase that defensively catch `UnicodeError` (e.g. CAT memo decoding in `run_block.py`): [2](#0-1) 

A Verified Credential's proof data (the "proof provider" attached metadata) is produced from puzzle/solution reveals synced from the blockchain and processed by wallet code such as `VerifiedCredential.get_next_from_coin_spend` in `chia/wallet/vc_wallet/vc_drivers.py`, and separately stored/parsed via `VCProofs` in the wallet's VC store. Because CLVM atoms are arbitrary bytes with no UTF-8 constraint enforced anywhere in consensus or puzzle validation, an attacker (e.g. a VC proof provider, or anyone crafting a coin spend that a wallet later parses as VC proof data) can supply a proof tree whose leaf atom bytes are not valid UTF-8. When `VCProofs.from_program` is invoked on that data, `bytes.decode("utf-8")` raises `UnicodeDecodeError`, an uncaught exception that propagates up through wallet processing.

### Impact Explanation
An uncaught `UnicodeDecodeError` during VC proof parsing halts the transaction/sync processing coroutine handling the VC coin state, causing a crash or repeated failure of the wallet's VC sync/processing pipeline — a local, spend-triggered denial-of-service against a wallet client that is tracking or receiving Verified Credential proofs, analogous to the process crash from malformed non-UTF-8 keys in the original CVE.

### Likelihood Explanation
Medium: exploitation requires the victim wallet to be actively tracking/parsing a VC (Verified Credential) whose proof data or update is attacker-influenced (e.g., a malicious/compromised proof provider, or a crafted coin spend mimicking VC proof structure). This is a narrower attack surface than universal mempool code, but reachable without any special privilege beyond issuing a spend or acting as a VC proof provider that the target wallet is configured to trust/sync.

### Recommendation
Wrap the atom decoding in `VCProofs.from_program` with proper exception handling (catching `UnicodeDecodeError`/`ValueError`) and reject or safely skip malformed non-UTF-8 proof keys/values instead of allowing the exception to propagate, consistent with the defensive pattern already used for CAT memo decoding elsewhere in the codebase.

### Proof of Concept
1. Construct a CLVM `Program` proof tree where a leaf atom of the key/value pair contains invalid UTF-8 byte sequences (e.g., `b"\xff\xfe"`).
2. Package this program as the metadata/proof payload for a Verified Credential coin spend (following the structure expected by `VerifiedCredential.get_next_from_coin_spend`) or otherwise supply it to `VCProofs.from_program`.
3. When the victim wallet processes/syncs this VC and calls `VCProofs.from_program(prog)` on the malformed proof tree, `first.atom.decode("utf-8")` at `chia/wallet/vc_wallet/vc_store.py:53` raises an uncaught `UnicodeDecodeError`, crashing/halting the VC processing routine.

Note: I was unable to fully trace the exact wallet-sync call site that feeds attacker-controlled `Program` data into `VCProofs.from_program` (only the RPC-facing `VCProofsRPC` wrapper and store read/write paths were confirmed) due to index depth limits; a Devin session with full repository access would be needed to confirm the precise inbound-data trigger and any existing higher-level guards around VC proof syncing before treating this as fully exploitable end-to-end.

### Citations

**File:** chia/wallet/vc_wallet/vc_store.py (L43-55)
```python
    @staticmethod
    def from_program(prog: Program) -> VCProofs:
        first: Program = prog.at("f")
        rest: Program = prog.at("r")
        if first.atom is None and rest.atom is None:
            final_dict: dict[str, str] = {}
            final_dict.update(VCProofs.from_program(first).key_value_pairs)
            final_dict.update(VCProofs.from_program(rest).key_value_pairs)
            return VCProofs(final_dict)
        elif first.atom is not None and rest.atom is not None:
            return VCProofs({first.atom.decode("utf-8"): rest.atom.decode("utf-8")})
        else:
            raise ValueError("Malformatted VCProofs program")  # pragma: no cover
```

**File:** chia/_tests/util/run_block.py (L104-108)
```python
            if len(condition[3]) >= 2:
                try:
                    memo = condition[3][1].decode("utf-8", errors="strict")
                except UnicodeError:
                    pass  # ignore this error which should leave memo as empty string
```
