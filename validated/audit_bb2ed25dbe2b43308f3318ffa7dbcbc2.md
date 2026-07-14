### Title
Silent Zip Truncation in `BlsCache::py_aggregate_verify` Bypasses Aggregate Signature Verification When `pks` and `msgs` Lists Have Mismatched Lengths — (File: `crates/chia-bls/src/bls_cache.rs`)

---

### Summary

`BlsCache::py_aggregate_verify` zips two separately-supplied Python lists (`pks`, `msgs`) without first asserting they have equal length. Rust's `Iterator::zip` silently stops at the shorter list, so any extra (pk, msg) pairs are dropped from the pairing computation. A caller that passes lists of unequal length receives a verification result computed over fewer pairs than intended, enabling a partial-signature bypass. The sibling binding `AugSchemeMPL::aggregate_verify` in `wheel/src/api.rs` performs an explicit length guard and returns a hard error on mismatch; `BlsCache::py_aggregate_verify` does not.

---

### Finding Description

`BlsCache::py_aggregate_verify` collects `pks` and `msgs` into two independent `Vec`s and then zips them:

```rust
Ok(self.aggregate_verify(pks.into_iter().zip(msgs), sig))
``` [1](#0-0) 

Rust's `zip` terminates as soon as either iterator is exhausted. If `pks.len() < msgs.len()`, the trailing messages are silently dropped. If `msgs.len() < pks.len()`, the trailing public keys are silently dropped. In either case the underlying `aggregate_verify` call sees a shorter (pk, msg) sequence than the caller intended.

The parallel binding `AugSchemeMPL::aggregate_verify` in `wheel/src/api.rs` explicitly guards against this:

```rust
if pks.len() != msgs.len() {
    return Err(PyRuntimeError::new_err(
        "aggregate_verify expects the same number of public keys as messages",
    ));
}
``` [2](#0-1) 

No equivalent guard exists in `BlsCache::py_aggregate_verify`. [1](#0-0) 

---

### Impact Explanation

BLS aggregate signature verification in the augmented scheme checks:

```
e(agg_sig, G1) == ∏ e(H(pkᵢ ‖ msgᵢ), pkᵢ)
```

If the product on the right is computed over only a strict subset of the intended (pk, msg) pairs — because zip silently dropped the rest — then an aggregate signature that covers only that subset will pass. Concretely:

- Intended check: `[(pk1, msg1), (pk2, msg2)]` against `agg_sig = sign(sk1,msg1) + sign(sk2,msg2)`
- Actual check (if `msgs = [msg1]`): `[(pk1, msg1)]` against `agg_sig = sign(sk1,msg1)`
- Result: verification passes; `pk2`/`msg2` are never checked.

This is a signature validation bypass at the Python binding boundary. Any Python-layer code that calls `BLSCache.aggregate_verify` with lists of unequal length — whether due to a bug in list construction or attacker-influenced input — will receive an incorrect `True` result, allowing a spend bundle with a partial or missing signature to be accepted as valid.

The impact maps to: **High — Signature/aggregate-signature validation bypass enables unauthorized spend acceptance.**

---

### Likelihood Explanation

The Python full node uses `BLSCache.aggregate_verify` for mempool pre-validation of spend bundles. The `pks` and `msgs` lists are normally constructed from the same condition list and should be equal in length. However:

1. The binding itself provides no safety net; any caller mistake is silent.
2. The inconsistency with `AugSchemeMPL.aggregate_verify` (which does error) means developers may not expect this behavior.
3. A crafted spend bundle that causes the Python layer to produce lists of different lengths (e.g., through a condition-parsing edge case) would silently pass signature verification.

Likelihood is **medium** — requires the Python caller to produce mismatched lists, but the binding provides no defense-in-depth.

---

### Recommendation

Add the same length guard that `AugSchemeMPL::aggregate_verify` already uses:

```rust
#[pyo3(name = "aggregate_verify")]
pub fn py_aggregate_verify(
    &self,
    pks: &Bound<'_, PyList>,
    msgs: &Bound<'_, PyList>,
    sig: &Signature,
) -> PyResult<bool> {
    if pks.len() != msgs.len() {
        return Err(PyValueError::new_err(
            "aggregate_verify expects the same number of public keys as messages",
        ));
    }
    let pks = pks
        .try_iter()?
        .map(|item| Ok(item?.extract()?))
        .collect::<PyResult<Vec<PublicKey>>>()?;
    let msgs = msgs
        .try_iter()?
        .map(|item| Ok(item?.extract()?))
        .collect::<PyResult<Vec<PyBackedBytes>>>()?;
    Ok(self.aggregate_verify(pks.into_iter().zip(msgs), sig))
}
```

The check should be placed **before** the expensive collection step, mirroring the pattern in `wheel/src/api.rs`. [3](#0-2) 

---

### Proof of Concept

```python
from chia_rs import BLSCache, G1Element, G2Element, AugSchemeMPL, PrivateKey

seed1 = b"\x01" * 32
seed2 = b"\x02" * 32
sk1 = AugSchemeMPL.key_gen(seed1)
sk2 = AugSchemeMPL.key_gen(seed2)
pk1 = sk1.get_g1()
pk2 = sk2.get_g1()
msg1 = b"hello"
msg2 = b"world"

# Aggregate signature covers ONLY msg1
partial_sig = AugSchemeMPL.sign(sk1, msg1)

cache = BLSCache()

# Correct call: 2 pks, 2 msgs — should FAIL (partial_sig doesn't cover msg2)
result_correct = cache.aggregate_verify([pk1, pk2], [msg1, msg2], partial_sig)
print("Correct (should be False):", result_correct)  # False

# Mismatched call: 2 pks, 1 msg — zip silently drops pk2
# partial_sig covers exactly (pk1, msg1), so verification PASSES
result_bypass = cache.aggregate_verify([pk1, pk2], [msg1], partial_sig)
print("Bypass  (should be False):", result_bypass)   # True — BYPASS

# AugSchemeMPL raises an error on mismatch (correct behavior)
try:
    AugSchemeMPL.aggregate_verify([pk1, pk2], [msg1], partial_sig)
except RuntimeError as e:
    print("AugSchemeMPL correctly errors:", e)
```

The `BLSCache.aggregate_verify` call with `[pk1, pk2]` and `[msg1]` returns `True` for a signature that only covers `(pk1, msg1)`, silently ignoring `pk2`. [1](#0-0)

### Citations

**File:** crates/chia-bls/src/bls_cache.rs (L160-178)
```rust
    #[pyo3(name = "aggregate_verify")]
    pub fn py_aggregate_verify(
        &self,
        pks: &Bound<'_, PyList>,
        msgs: &Bound<'_, PyList>,
        sig: &Signature,
    ) -> PyResult<bool> {
        let pks = pks
            .try_iter()?
            .map(|item| Ok(item?.extract()?))
            .collect::<PyResult<Vec<PublicKey>>>()?;

        let msgs = msgs
            .try_iter()?
            .map(|item| Ok(item?.extract()?))
            .collect::<PyResult<Vec<PyBackedBytes>>>()?;

        Ok(self.aggregate_verify(pks.into_iter().zip(msgs), sig))
    }
```

**File:** wheel/src/api.rs (L350-369)
```rust
    pub fn aggregate_verify(
        py: Python<'_>,
        pks: &Bound<'_, PyList>,
        msgs: &Bound<'_, PyList>,
        sig: &Signature,
    ) -> PyResult<bool> {
        let mut data = Vec::<(PublicKey, Vec<u8>)>::new();
        if pks.len() != msgs.len() {
            return Err(PyRuntimeError::new_err(
                "aggregate_verify expects the same number of public keys as messages",
            ));
        }
        for (pk, msg) in zip(pks, msgs) {
            let pk = pk.extract::<PublicKey>()?;
            let msg = msg.extract::<Vec<u8>>()?;
            data.push((pk, msg));
        }

        py.detach(|| Ok(chia_bls::aggregate_verify(sig, data)))
    }
```
