[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** modules/utils/bls-utils/src/bls.rs (L34-52)
```rust
/// Convert a compressed BLS public key to a projective point.
pub fn pubkey_to_projective(compressed_key: &BlsPublicKey) -> Result<G1ProjectivePoint, BLSError> {
	let affine_point = bls::pubkey_to_point(&compressed_key.to_vec())?;
	Ok(affine_point.into())
}

/// Aggregate multiple BLS public keys into a single public key.
///
/// Any input key that fails to decompress is propagated as an error
/// rather than silently dropped — otherwise a caller's participant
/// count would diverge from the aggregate, letting an attacker pad the
/// quorum with junk keys that contribute nothing to the signature.
pub fn aggregate_public_keys(keys: &[BlsPublicKey]) -> Result<Vec<u8>, BLSError> {
	let mut aggregate = G1ProjectivePoint::default();
	for key in keys {
		aggregate = aggregate + pubkey_to_projective(key)?;
	}
	Ok(bls::point_to_pubkey(aggregate.into()))
}
```

**File:** modules/utils/bls-utils/src/bls.rs (L58-66)
```rust
	#[test]
	fn aggregate_public_keys_rejects_malformed_key() {
		// An all-zeros 48-byte buffer is not a valid compressed G1 point.
		let junk: BlsPublicKey = vec![0u8; BLS_PUBLIC_KEY_BYTES_LEN].try_into().unwrap();
		let err = aggregate_public_keys(&[junk]).expect_err("malformed key must error");
		// Any BLSError is acceptable — the point is that the error is surfaced
		// rather than silently dropped.
		let _ = err;
	}
```

**File:** modules/consensus/sync-committee/verifier/src/crypto.rs (L1-21)
```rust
use alloc::vec::Vec;
use bls::{errors::BLSError, types::G1ProjectivePoint};
use sync_committee_primitives::constants::BlsPublicKey;

pub fn pubkey_to_projective(compressed_key: &BlsPublicKey) -> Result<G1ProjectivePoint, BLSError> {
	let affine_point = bls::pubkey_to_point(&compressed_key.to_vec())?;
	Ok(affine_point.into())
}

pub fn subtract_points_from_aggregate(
	aggregate: &BlsPublicKey,
	points: &[BlsPublicKey],
) -> Result<G1ProjectivePoint, BLSError> {
	let aggregate = pubkey_to_projective(aggregate)?;
	let points = points
		.iter()
		.map(|point| pubkey_to_projective(point))
		.collect::<Result<Vec<_>, _>>()?;
	let subset_aggregate = points.into_iter().fold(aggregate, |acc, point| acc - point);
	Ok(subset_aggregate)
}
```
