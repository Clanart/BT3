## Finding

`InternalLazyField.ensureInitialized()` in `java/core/src/main/java/com/google/protobuf/InternalLazyField.java` implements the double-checked-locking pattern **incorrectly** — it checks the guard variable (`value != null`) once *outside* the lock, but never re-checks it *inside* the synchronized block before re-parsing, unlike the sibling class `LazyFieldLite.ensureInitialized()` and `InternalLazyField.toByteString()` in the same file which both correctly re-check their guard after acquiring the monitor. [1](#0-0) 

Compare with the correct pattern used elsewhere in the same file: [2](#0-1) [3](#0-2) 

### Title
Broken double-checked-locking guard in `InternalLazyField.ensureInitialized` allows concurrent re-parsing and violates the class's stated immutability invariant - (File: `java/core/src/main/java/com/google/protobuf/InternalLazyField.java`)

### Summary
`InternalLazyField` is used for lazy parsing of message-typed extension fields (`MessageSet`/lazy extensions) that are lazily materialized from attacker-supplied bytes on first access. Its `ensureInitialized()` method is meant to guarantee the parsed `value` is set exactly once. It performs a fast-path check of `value != null` outside the `synchronized(this)` block (correct), but — unlike the analogous method `toByteString()` in the same class and `LazyFieldLite.ensureInitialized()` — it never re-checks `value != null` after acquiring the lock. It only checks the unrelated `corrupted` flag before unconditionally re-parsing `bytes` and reassigning `value`.

### Finding Description
The invariant documented at the top of the class states:
> "1) once value or bytes is set, they will not be changed"

`ensureInitialized()` is the guard meant to enforce that a lazily-parsed field is initialized exactly once:
```java
private void ensureInitialized() throws InvalidProtocolBufferException {
    if (value != null) {
      return;                       // fast path, unsynchronized
    }
    synchronized (this) {
      if (corrupted) {               // <-- wrong re-check: guards against a
        throw ...;                   //     different condition, not the one
      }                              //     that gated entry into this method
      ...
      value = ...parse bytes...;     // executes unconditionally even if
                                      // another thread already set `value`
    }
}
```
This is exactly the class of bug described in the external report: a protection mechanism is present syntactically (`synchronized`, a flag check) but does not actually re-validate the very condition (`value == null`) that was used to decide whether to enter the protected/critical region. Two threads that both observe `value == null` on the fast path will serialize on the monitor, but the second thread proceeds to redundantly reparse `bytes` and overwrite `value` with a brand-new object — silently breaking the "set exactly once" invariant the lock was supposed to enforce. This mirrors the zBanc report where `_protected()` only checked `locked` but never set it, so the guard provided no actual mutual exclusion against re-entry into the guarded logic.

### Impact Explanation
This lazy-field code path is reached whenever an application concurrently accesses a `MessageSet`/lazy extension field parsed from untrusted, attacker-controlled bytes (e.g., via `getValue()`, `hashCode()`, `equals()`, `toString()`, or extension merge operations) from multiple threads — a common pattern in multithreaded RPC servers that parse a single message once and then read fields from multiple worker threads. Because the guard is ineffective:
- `value` can be silently replaced by a second, independently-parsed object even though the class guarantees it is set only once, which can surprise any code that caches a reference to `value` (e.g., `LazyEntry`) expecting stability.
- Redundant parsing work is performed under contention, which is wasted CPU driven directly by attacker-supplied lazy-field bytes (though this repository's rules exclude pure resource-exhaustion claims, the correctness violation itself is the primary issue).
- If parsing ever becomes non-deterministic in the future (e.g., due to registry state, or in derived subclasses), a losing thread could theoretically set `corrupted = true` and throw after another thread already successfully published a non-null `value`, contradicting invariant #3 ("If corrupted is true, value must be null").

This is best characterized as a Medium-severity concurrency/correctness defect exposed by attacker-controlled input (any bytes destined for a lazy extension field), not a memory-corruption or RCE-class bug — no bounds/allocation issue is present, and Java's memory model (via the `volatile` field) prevents torn/unsafe reads, so there's no undefined behavior, only a violated once-only-initialization contract.

### Likelihood Explanation
Reachable by any Protobuf consumer parsing untrusted `MessageSet`/lazy-extension-bearing binary Protobuf when `ExtensionRegistryLite.lazyExtensionEnabled()` lazy extensions are used and the parsed message is subsequently accessed by more than one thread — a realistic pattern for server applications. Because the fast-path check on `value` is unsynchronized and racy by design (only the slow path is supposed to be authoritative), any two threads racing to initialize the same lazy field for the first time will trigger this.

### Recommendation
Add the missing double-checked-locking re-verification inside the synchronized block, mirroring `LazyFieldLite.ensureInitialized()` and `InternalLazyField.toByteString()`:
```java
private void ensureInitialized() throws InvalidProtocolBufferException {
    if (value != null) {
      return;
    }
    synchronized (this) {
      if (value != null) {   // <-- add this re-check
        return;
      }
      if (corrupted) {
        throw new InvalidProtocolBufferException("Repeat access to corrupted lazy field");
      }
      ... // existing parse logic
    }
}
```

### Proof of Concept
Race demonstration (conceptual, matches the code at `InternalLazyField.java:202-228`):
1. Construct an `InternalLazyField` from attacker-supplied bytes for a lazy `MessageSet` extension (`value == null`, `bytes != null`).
2. Two threads T1 and T2 call `getValue()` (which calls `ensureInitialized()`) concurrently before either has parsed anything; both observe `value == null` on the unsynchronized fast path.
3. T1 acquires the monitor, parses `bytes`, sets `value = v1`, releases the monitor.
4. T2 was blocked on the monitor; upon acquiring it, it checks only `corrupted` (still `false`) — not `value != null` — and proceeds to reparse the same `bytes`, producing a new object `value = v2`, overwriting `v1`.
5. Any code (e.g. `LazyEntry`) that captured a reference to `v1` before step 4 now holds a stale object no longer reachable via `field.value`, violating the documented "set once" invariant, and the redundant parse of attacker-controlled bytes runs twice under lock contention triggered purely by concurrent access timing.

### Citations

**File:** java/core/src/main/java/com/google/protobuf/InternalLazyField.java (L202-228)
```java
  private void ensureInitialized() throws InvalidProtocolBufferException {
    if (value != null) {
      return;
    }

    synchronized (this) {
      if (corrupted) {
        throw new InvalidProtocolBufferException("Repeat access to corrupted lazy field");
      }
      try {
        // `bytes` is guaranteed to be non-null since `value` was null.
        CodedInputStream input = bytes.newCodedInput();
        input.enableAliasing(/* enabled= */ true);
        // When lazyExtensionEnabled() returns true, it means all extensions including MessageSet's
        // will be fully parsed. When it returns false, it basically implies this can only be a
        // MessageSet extension, and we should fall back to the old behavior of silently returning
        // the default instance on corrupted extensions i.e. a full parse.
        value =
            extensionRegistry.lazyExtensionEnabled()
                ? defaultInstance.getParserForType().parsePartialFrom(input, extensionRegistry)
                : defaultInstance.getParserForType().parseFrom(input, extensionRegistry);
        input.checkLastTagWas(0);
      } catch (InvalidProtocolBufferException e) {
        corrupted = true;
        throw e;
      }
    }
```

**File:** java/core/src/main/java/com/google/protobuf/InternalLazyField.java (L259-270)
```java
  ByteString toByteString() {
    if (bytes != null) {
      return bytes;
    }
    synchronized (this) {
      if (bytes != null) {
        return bytes;
      }
      bytes = value.toByteString();
      return bytes;
    }
  }
```

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L469-496)
```java
  protected void ensureInitialized(MessageLite defaultInstance) {
    if (value != null) {
      return;
    }
    synchronized (this) {
      if (value != null) {
        return;
      }
      try {
        if (delayedBytes != null) {
          // The extensionRegistry shouldn't be null here since we have delayedBytes.
          MessageLite parsedValue =
              defaultInstance.getParserForType().parseFrom(delayedBytes, extensionRegistry);
          this.value = parsedValue;
          this.memoizedBytes = delayedBytes;
        } else {
          this.value = defaultInstance;
          this.memoizedBytes = ByteString.EMPTY;
        }
      } catch (InvalidProtocolBufferException e) {
        // Nothing is logged and no exceptions are thrown. Clients will be unaware that this proto
        // was invalid.
        this.corrupted = true;
        this.value = defaultInstance;
        this.memoizedBytes = ByteString.EMPTY;
      }
    }
  }
```
