### Title
Unbounded signature accumulation in `SchnorrAggregator` enables remote memory exhaustion - ([File: crypto/schnorr/src/aggregate.rs](crypto/schnorr/src/aggregate.rs))

### Summary
`SchnorrAggregator::aggregate` stores every submitted `SchnorrSignature` in `self.sigs` without an aggregate-size limit, eviction policy, duplicate rejection, or validity check. An unprivileged party able to submit signatures for aggregation can grow the retained `Vec<SchnorrSignature<C>>` indefinitely across a long-running aggregation session. Completion additionally allocates a second vector containing one nonce commitment per retained signature, temporarily amplifying memory usage before the aggregate is emitted. [1](#0-0) 

### Finding Description
The public parsing path accepts an arbitrary attacker-controlled nonce commitment and scalar through `SchnorrSignature::read`. [2](#0-1)  Once parsed, `SchnorrAggregator::aggregate` appends the challenge to the transcript and unconditionally pushes the signature into `self.sigs`. [3](#0-2)  No maximum number of signatures is enforced, and the signatures are retained until `complete` consumes the aggregator.

The memory cost is worsened during completion: `complete` allocates `aggregate.Rs` with capacity equal to the number of retained signatures while the original `self.sigs` vector remains alive, then copies every nonce commitment into the new vector. [4](#0-3)  Consequently, an aggregation endpoint which accepts attacker-submitted signatures must retain enough memory for all submitted signatures and temporarily allocate an additional proportional vector when completing.

### Impact Explanation
A remote signer can cause monotonically increasing memory consumption during an aggregation session by submitting a large sequence of signatures. With enough inputs, allocation growth or the extra allocation performed by `complete` can exhaust available memory and terminate the long-running verification or coordination process. [5](#0-4) 

This matches the externally reported mismanaged-array class: attacker-controlled entries are retained in a growing array rather than being bounded or discarded, ultimately turning ordinary public protocol messages into process termination. [6](#0-5) 

### Likelihood Explanation
The attack requires an integration to expose `SchnorrAggregator` to externally supplied signatures without imposing its own aggregate-size bound. The library itself places no limit on repeated `aggregate` calls, and the attacker does not need a validator key, leaked secret, malformed encoding, or invalid curve point: signatures only need to deserialize into `SchnorrSignature` values. [7](#0-6)  Each call consumes memory proportional to a signature and retains it until aggregation completes. [8](#0-7) 

The issue is therefore plausible in public aggregation flows, although the exact crash threshold depends on deployment memory limits and the maximum number of network messages accepted.

### Recommendation
Enforce a protocol-defined maximum aggregate size before calling `SchnorrAggregator::aggregate`, and return an error once the bound is reached. Prefer streaming the nonce commitments into the final aggregate representation or otherwise avoid retaining every full signature until completion. If signatures must be retained for accountability, bound them by the maximum legitimate participant count and reject duplicates according to the caller’s participant model.

### Proof of Concept
1. Expose an aggregation request that accepts repeated serialized Schnorr signatures.
2. For each request, parse `(R, s)` with `SchnorrSignature::read`; the reader requires only canonical point and scalar encodings. [7](#0-6) 
3. Invoke `aggregator.aggregate(challenge, signature)` for each parsed signature. Every invocation pushes another `SchnorrSignature` into `self.sigs`. [3](#0-2) 
4. Continue submitting signatures. Memory grows linearly with no library-enforced bound.
5. Call `complete`; it allocates a second `Vec` with capacity equal to the number of retained signatures and copies each `R`, producing a temporary memory spike while the original signatures remain allocated. [5](#0-4)

### Citations

**File:** crypto/schnorr/src/aggregate.rs (L152-185)
```rust
pub struct SchnorrAggregator<C: Ciphersuite> {
  digest: DigestTranscript<C::H>,
  sigs: Vec<SchnorrSignature<C>>,
}

impl<C: Ciphersuite> SchnorrAggregator<C> {
  /// Create a new aggregator.
  ///
  /// The DST used here must prevent a collision with whatever hash function produced the
  /// challenges.
  pub fn new(dst: &'static [u8]) -> Self {
    let mut res = Self { digest: DigestTranscript::<C::H>::new(dst), sigs: vec![] };
    res.digest.domain_separate(b"signatures");
    res
  }

  /// Aggregate a signature.
  pub fn aggregate(&mut self, challenge: C::F, sig: SchnorrSignature<C>) {
    self.digest.append_message(b"challenge", challenge.to_repr());
    self.sigs.push(sig);
  }

  /// Complete aggregation, returning None if none were aggregated.
  pub fn complete(mut self) -> Option<SchnorrAggregate<C>> {
    if self.sigs.is_empty() {
      return None;
    }

    let mut aggregate = SchnorrAggregate { Rs: Vec::with_capacity(self.sigs.len()), s: C::F::ZERO };
    for i in 0 .. self.sigs.len() {
      aggregate.Rs.push(self.sigs[i].R);
      aggregate.s += self.sigs[i].s * weight::<_, C::F>(&mut self.digest);
    }
    Some(aggregate)
```

**File:** crypto/schnorr/src/lib.rs (L49-58)
```rust
impl<C: Ciphersuite> SchnorrSignature<C> {
  /// Read a SchnorrSignature from something implementing Read.
  pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    Ok(SchnorrSignature { R: C::read_G(reader)?, s: C::read_F(reader)? })
  }

  /// Write a SchnorrSignature to something implementing Read.
  pub fn write<W: Write>(&self, writer: &mut W) -> io::Result<()> {
    writer.write_all(self.R.to_bytes().as_ref())?;
    writer.write_all(self.s.to_repr().as_ref())
```
