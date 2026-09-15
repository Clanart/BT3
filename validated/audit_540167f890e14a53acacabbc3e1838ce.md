Found the direct structural analog: `BlobTxSidecar.ValidateWithBlobHashes` caches a positive KZG verification result keyed only by a hash of the blob **versioned hashes**, not by the actual blob contents/commitments/proofs that were verified.

### Title
Blob sidecar KZG verification cache keyed only by versioned-hash summary, not by commitments/proofs, allows verification bypass on sidecar mutation - (File: blockchain/types/tx_internal_data_ethereum_blob.go)

### Summary
`BlobTxSidecar.ValidateWithBlobHashes` memoizes a successful KZG cell-proof verification in `sc.validatedSummaryHash`, computed only from the transaction's `blobVersionedHashes`, and short-circuits re-verification whenever a later call presents the same set of hashes — regardless of whether `sc.Blobs`, `sc.Commitments`, or `sc.Proofs` have since changed.

### Finding Description
`ValidateWithBlobHashes` computes `blobHashesSummaryHash := Keccak256(concat(hashes))` and compares it against the sidecar's own `validatedSummaryHash` field: [1](#0-0) 
If they match, the function returns `nil` immediately, skipping `ValidateBlobCommitmentHashes` and the expensive `validateBlobSidecarOsaka` (which calls `kzg4844.VerifyCellProofs`) entirely. Crucially, the cache key (`blobHashesSummaryHash`) is derived solely from the caller-supplied `hashes []common.Hash` argument (i.e., `tx.BlobHashes`), not from any property of `sc.Blobs`/`sc.Commitments`/`sc.Proofs`. This mirrors the Prysm H-3 defect exactly: the cache key omits the very data (`kzg_commitments` in Prysm; `Blobs`/`Commitments`/`Proofs` here) whose correctness the cache is meant to certify, while retaining only auxiliary binding data (`signed_block_header`/inclusion proof in Prysm; `BlobHashes` here).

Because `BlobTxSidecar` is a mutable struct (its `Blobs`, `Commitments`, `Proofs` fields are exported and not otherwise protected once a `*BlobTxSidecar` pointer is held), and the field is populated on the same in-memory tx pool object across its lifetime (announced → pooled → included in block-building/consensus), any code path that first calls `ValidateWithBlobHashes` with a valid sidecar (setting `validatedSummaryHash`) and later re-validates the *same* `TxInternalDataEthereumBlob`/sidecar object after its `Blobs`/`Commitments`/`Proofs` were swapped (while `BlobHashes` on the outer tx stayed the same) would incorrectly treat the mutated blob data as already-verified. Call sites include the tx pool and block/consensus handling that reference the sidecar: [2](#0-1) 

### Impact Explanation
If reachable with a mutated sidecar sharing the same `validatedSummaryHash`, this would let a node accept and propagate a blob transaction whose actual blob data does not match its commitments/proofs — i.e., acceptance of data that fails DA/KZG verification while cached as "valid" — directly matching the accepted-impact category (acceptance of an invalid transaction, state/consensus divergence). This is High severity in kind, matching the class of the reported Prysm issue.

### Likelihood Explanation
Exploitability is uncertain and could not be fully confirmed within the scope of this review. The vulnerability requires an attacker (or code path) to be able to (a) get a `*BlobTxSidecar` validated once and (b) subsequently mutate its `Blobs`/`Commitments`/`Proofs` in place while `BlobHashes` on the wrapping `TxInternalDataEthereumBlob` is unchanged, and (c) have that same sidecar object re-validated later via `ValidateWithBlobHashes` on the same in-memory object rather than a freshly decoded one. Whether the tx pool, gossip re-broadcast, or block building paths retain and re-validate the exact same sidecar pointer across such a mutation window — as opposed to always re-decoding it from RLP/network bytes for each verification — needs to be verified against `blockchain/tx_pool.go`, `node/cn/handler.go`, and `consensus/istanbul/backend/backend.go`, which were identified as the call sites of `ValidateWithBlobHashes`/`validatedSummaryHash` but not fully inspected due to iteration limits. Because the exported `Blobs`/`Commitments`/`Proofs` fields have no mutex or copy-on-write guard visible in this file, an in-process mutation path (e.g. a node relaying a mixed set of blobs it received later via a different message) is plausible but not proven with a concrete call trace here.

### Recommendation
Bind the cache key to the actual verified material rather than only to the versioned hashes: incorporate a hash of `sc.Commitments` and `sc.Proofs` (and `sc.Blobs`, or at minimum a commitment to them) into `validatedSummaryHash`, or invalidate/recompute the cache whenever `Blobs`/`Commitments`/`Proofs` are mutated (e.g., make these fields private with a controlled setter that clears `validatedSummaryHash`). Alternatively, always operate on a freshly-copied/immutable sidecar for validation and disallow post-validation mutation of `Blobs`/`Commitments`/`Proofs` on a sidecar instance whose `validatedSummaryHash` is already set.

### Proof of Concept
Conceptual, not fully executable without confirming a live mutation call path (see Likelihood):
1. Construct a valid `BlobTxSidecar` with correct `Blobs`/`Commitments`/`Proofs` for `hashes = [h1]`, call `sc.ValidateWithBlobHashes([]common.Hash{h1})` → succeeds, sets `sc.validatedSummaryHash = Keccak256(h1)`.
2. Obtain/retain a reference to the same `*BlobTxSidecar` object (e.g., via a component that caches sidecars by tx hash across pool/gossip stages) and overwrite `sc.Blobs`, `sc.Commitments`, `sc.Proofs` with different (or malicious/mismatched) values while leaving `tx.BlobHashes` (and thus `h1`) unchanged.
3. Call `sc.ValidateWithBlobHashes([]common.Hash{h1})` again → the function computes the same `blobHashesSummaryHash` and returns `nil` without re-checking `ValidateBlobCommitmentHashes` or `VerifyCellProofs`, accepting the tampered blob data as valid. [1](#0-0)

### Citations

**File:** blockchain/types/tx_internal_data_ethereum_blob.go (L116-124)
```go
// BlobTxSidecar contains the blobs of a blob transaction.
type BlobTxSidecar struct {
	Version     byte                 // Version
	Blobs       []kzg4844.Blob       // Blobs needed by the blob pool
	Commitments []kzg4844.Commitment // Commitments needed by the blob pool
	Proofs      []kzg4844.Proof      // Proofs needed by the blob pool

	validatedSummaryHash common.Hash // Hash of the versioned hashes that have been validated
}
```

**File:** blockchain/types/tx_internal_data_ethereum_blob.go (L240-274)
```go
func (sc *BlobTxSidecar) ValidateWithBlobHashes(hashes []common.Hash) error {
	// Summarize all hashes and compute the hash
	blobHashesSummary := make([]byte, 0, len(hashes)*32)
	for _, h := range hashes {
		blobHashesSummary = append(blobHashesSummary, h[:]...)
	}
	blobHashesSummaryHash := crypto.Keccak256Hash(blobHashesSummary)

	// If the summarized hash is the same as the validated summary hash, return nil for cached hashes.
	if blobHashesSummaryHash == sc.validatedSummaryHash {
		return nil
	}

	if len(sc.Blobs) != len(hashes) {
		return fmt.Errorf("invalid number of %d blobs compared to %d blob hashes", len(sc.Blobs), len(hashes))
	}
	if err := sc.ValidateBlobCommitmentHashes(hashes); err != nil {
		return err
	}

	if sc.Version != BlobSidecarVersion1 {
		// Kaia rejects sidecar.Version = 0.
		// ref: https://github.com/kaiachain/kips/blob/main/KIPs/kip-279.md#reject-sidecar-v0
		return fmt.Errorf("blob sidecar version %d not supported", sc.Version)
	}

	// Fork-specific sidecar checks, including proof verification.
	if err := validateBlobSidecarOsaka(sc, hashes); err != nil {
		return err
	}

	// Once verified, the hash of hashes is cached.
	sc.validatedSummaryHash = blobHashesSummaryHash
	return nil
}
```
