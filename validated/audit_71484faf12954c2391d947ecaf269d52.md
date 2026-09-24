## Analysis Result



### Title
Broken double-checked locking in `InternalLazyField.ensureInitialized()` causes lazy field re-parse/overwrite race - (File: `java/core/src/main/java/com/google/protobuf/InternalLazyField.java`)

### Summary
`InternalLazyField.ensureInitialized()` reads `value` outside its `synchronized(this)` lock as a fast-path check, but — unlike its sibling `LazyFieldLite.ensureInitialized(MessageLite)` in the same package — it does **not** re-check `value != null` after acquiring the lock. Two threads that both observe `value == null` before the lock is taken will serialize on the monitor, but the second thread proceeds to re-parse `bytes` and unconditionally overwrite the already-published `value`, violating the class's own documented invariant that "once value or bytes is set, they will not be changed."

### Finding Description
The kernel CVE's invariant failure is: a shared pointer guarded by a lock is read/tested without holding that lock, and code downstream assumes the earlier check is still valid. The Protobuf analog is structurally identical, just manifesting as a broken double-checked-locking (DCL) idiom instead of a raw missing-lock read: [1](#0-0) 

```java
private void ensureInitialized() throws InvalidProtocolBufferException {
  if (value != null) {          // fast-path check, NOT under lock
    return;
  }
  synchronized (this) {
    if (corrupted) { ... }       // <-- missing: no re-check of `value != null` here
    // `bytes` is guaranteed to be non-null since `value` was null.
    CodedInputStream input = bytes.newCodedInput();
    ...
    value = ...parseFrom(...);   // unconditionally overwrites value
  }
}
```

Contrast this with the correct implementation right in the same package, `LazyFieldLite.ensureInitialized(MessageLite)`, which performs the standard, safe DCL pattern: [2](#0-1) 

```java
protected void ensureInitialized(MessageLite defaultInstance) {
  if (value != null) {
    return;
  }
  synchronized (this) {
    if (value != null) {      // <-- correct re-check under lock
      return;
    }
    ...
```

`InternalLazyField` is used to lazily parse extension/message-field bytes obtained directly from parsing untrusted wire-format input (`mergeFrom(InternalLazyField, CodedInputStream, ExtensionRegistryLite)`), and `getValue()` is the public accessor invoked from `hashCode()`, `equals()`, `toString()`, and normal field getters: [3](#0-2) [4](#0-3) 

Consuming-application exposure assumption: after a server parses one untrusted request message (`ParseFrom`), it is common practice to hand the resulting immutable message to multiple worker threads that read its fields (including lazily-encoded extension/message fields) concurrently — exactly the scenario `InternalLazyField`/`LazyFieldLite` were designed to support safely.

### Impact Explanation
When two threads call `getValue()`/`ensureInitialized()` concurrently on the same `InternalLazyField` right after it is constructed from parsed bytes:
1. Thread A wins the monitor, parses `bytes`, sets `value = v1`, and returns `v1` to its caller before releasing the lock (Java `synchronized` releases at block exit, but `value` is visible to A's caller once the method returns).
2. Thread B was blocked on entry to the `synchronized` block. When it enters, it does not see the (missing) `value != null` check, re-parses the same `bytes`, and overwrites the field with a new instance `v2`.

This breaks the class's stated invariant ("once value ... is set, it will not be changed") and can hand out two different parsed `MessageLite` instances (`v1` seen by A's caller, `v2` permanently stored in the field and later observed by other callers) for what should be a single stable field value. Downstream code relying on reference stability of the cached lazy value (e.g., identity-sensitive caching, `toBuilder()`/`mergeFrom()` sequences chained across calls as seen at line 118 and line 172) can silently diverge or duplicate parsing work. Because the JVM is memory-safe, this cannot escalate to memory corruption/RCE the way the kernel NULL-pointer race could, so the impact is a correctness/data-integrity violation under concurrency rather than a crash — the closest legitimate class of harm transferable from the CVE (broken lock-guarded invariant on a lazily-materialized value reachable directly from untrusted parse input).

### Likelihood Explanation
Triggering requires the same message field to be accessed by two threads concurrently before it has ever been parsed — a realistic pattern in server code that parses a request once and dispatches processing across a thread pool, which is precisely the concurrency model the class's own Javadoc anticipates ("LazyFieldLite is thread-compatible: concurrent reads are safe..."). No malicious schema, plugin, or privileged access is needed; only two ordinary application threads racing on `getValue()`/`hashCode()`/`equals()`/`toString()` of a message parsed from attacker-supplied bytes.

### Recommendation
Add the missing re-check of `value != null` immediately inside the `synchronized (this)` block in `InternalLazyField.ensureInitialized()`, mirroring `LazyFieldLite.ensureInitialized()`:
```java
synchronized (this) {
  if (value != null) {
    return;
  }
  if (corrupted) { ... }
  ...
}
```

### Proof of Concept
Given the current code at [1](#0-0) , a minimal reproduction:

```java
InternalLazyField field = new InternalLazyField(defaultInstance, registry, bytesFromUntrustedParse);
Runnable r = () -> field.getValue();  // triggers ensureInitialized()
Thread t1 = new Thread(r);
Thread t2 = new Thread(r);
t1.start(); t2.start();
t1.join(); t2.join();
```
With sufficient scheduling interleaving (reliably reproducible by inserting a small sleep/barrier before the `parseFrom` call to widen the race window, as is standard for demonstrating this class of Java data race), `field.value` after both threads complete is a *different* object instance than the one returned to whichever thread first called `getValue()`, and `bytes.newCodedInput()`/`parseFrom` is executed twice for a single logical field — demonstrating the violated "set once" invariant documented at the top of the file (lines 18–20).

### Citations

**File:** java/core/src/main/java/com/google/protobuf/InternalLazyField.java (L89-93)
```java
  static InternalLazyField mergeFrom(
      InternalLazyField lazyField, CodedInputStream input, ExtensionRegistryLite extensionRegistry)
      throws IOException {
    if (lazyField.isEmpty()) {
      return new InternalLazyField(lazyField.defaultInstance, extensionRegistry, input.readBytes());
```

**File:** java/core/src/main/java/com/google/protobuf/InternalLazyField.java (L202-229)
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
  }
```

**File:** java/core/src/main/java/com/google/protobuf/InternalLazyField.java (L238-251)
```java
  MessageLite getValue() {
    try {
      ensureInitialized();
      return value;
    } catch (InvalidProtocolBufferException e) {
      if (extensionRegistry.lazyExtensionEnabled()) {
        // New behavior: runtime exception on corrupted extensions.
        throw new InvalidProtobufRuntimeException(e);
      } else {
        // Old behavior: silently return the default instance.
        return defaultInstance;
      }
    }
  }
```

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L469-476)
```java
  protected void ensureInitialized(MessageLite defaultInstance) {
    if (value != null) {
      return;
    }
    synchronized (this) {
      if (value != null) {
        return;
      }
```
